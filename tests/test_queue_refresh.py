"""PC-130: the Task Queue pane refreshes its queue data every queue_refresh_s seconds (default 120, at least 60) while it is visible,
falls back to the hourly refresh when the call budget is low or GitHub pauses the Cockpit, keeps the last data when a refresh fails,
and never loses an open drawer, editor or draft. Injected clock, fake server (build_state) and, for the page, a fake timer and fetch."""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import cockpit as C  # noqa: E402
from ckp.api import context as api_context, health as api_health, state as api_state
import logic as L  # noqa: E402

PAGE = (ROOT / "web" / "index.html").read_text()
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
P = dict(name="proj", repo="o/r", manager="m", project=None, git=None, code=None, modules=None, phases=None)


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    C._cache.clear()
    C._failures.clear()
    C._gh_calls.clear()
    C._gh_pause.update(until=0.0)
    monkeypatch.setattr(api_context, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(api_context, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(api_health, "get_ci", lambda p: dict(state="green"))  # its own hourly cache, tested elsewhere
    yield
    C._cache.clear()


class FakeServer:
    """build_state stand-in: counts the fetches; `down` makes the next ones fail."""
    def __init__(self, now):
        self.now, self.fetches, self.down = now, 0, None

    def __call__(self, p):
        if self.down:
            raise self.down
        self.fetches += 1
        return dict(today="2026-11-25", tasks=[], modules=[], source=dict(fetchedAt=self.now(), fetched="10:00:00"))


@pytest.fixture
def server(monkeypatch):
    t0 = time.time()
    clock = {"now": t0}
    s = FakeServer(lambda: clock["now"])
    s.t0, s.clock = t0, clock
    monkeypatch.setattr(api_state, "build_state", s)
    return s


def poll(s, at, since=0):
    s.clock["now"] = s.t0 + at
    return C.get_queue_poll(P, since=since, now=s.t0 + at)


# ---------- the setting ----------
def test_setting_default_and_minimum(monkeypatch):
    assert L.CARD_SETTING_DEFAULTS["queue_refresh_s"] == 120 and C.get_card_settings(P)["values"]["queue_refresh_s"] == 120
    assert L.validate_queue_refresh(None) == 120 and L.validate_queue_refresh("") == 120
    assert L.validate_queue_refresh(60) == 60 and L.validate_queue_refresh(" 90 ") == 90
    for bad in (59, 0, -1, "abc", 1.5, True, "59"):
        with pytest.raises(ValueError, match="at least 60"):
            L.validate_queue_refresh(bad)
        assert L.validate_queue_refresh(bad, strict=False) == 120  # a bad stored value falls back to the default
    monkeypatch.setitem(C.CFG, "steer", True)
    assert C.do_act(dict(action="cardsettings", churn_colour="size", queue_refresh_s="59"), p=P)[0] == 400
    assert C.do_act(dict(action="cardsettings", churn_colour="size", queue_refresh_s="60"), p=P)[0] == 200
    assert C.get_card_settings(P)["values"]["queue_refresh_s"] == 60


def test_settings_page_offers_the_field_with_its_minimum():
    assert 'name="queue_refresh_s" type="number" step="1" min="60"' in PAGE
    assert "queue_refresh_s:fd.get('queue_refresh_s')" in PAGE


# ---------- the budget guard (pure) ----------
@pytest.mark.parametrize("remaining,paused_until,want", [
    (600, 0, (120, None)), (100, 0, (120, None)), (99, 0, (3600, "call budget low")), (0, 0, (3600, "call budget low")),
    (600, 1000 + 1, (3600, "GitHub rate limit")), (600, 1000, (120, None)),  # a pause that has ended no longer slows
])
def test_queue_interval(remaining, paused_until, want):
    assert L.queue_interval(120, remaining, paused_until, 1000) == want


def test_thirty_refreshes_an_hour_fit_the_cap_with_the_reserve():
    assert 3600 // L.QUEUE_REFRESH_DEFAULT * 12 + L.QUEUE_BUDGET_RESERVE <= C.GH_CALLS_PER_HOUR  # about 12 calls a state load


# ---------- the server's poll ----------
def test_refresh_every_interval_and_state_sent_only_when_newer(server):
    r = poll(server, 0)
    assert server.fetches == 1 and r["state"]["source"]["fetchedAt"] == server.t0 and r["every"] == r["interval"] == 120 and r["slowed"] is None
    assert r["nextAt"] == server.t0 + 120 and "local" in r and r["ci"] == dict(state="green")
    since = r["fetchedAt"]
    for at in (30, 60, 119):
        r = poll(server, at, since)
        assert server.fetches == 1 and "state" not in r and "local" in r  # the live part every time, no call, no task list resent
    r = poll(server, 121, since)
    assert server.fetches == 2 and r["state"]["source"]["fetchedAt"] == server.t0 + 121
    r = poll(server, 241, r["fetchedAt"])
    assert server.fetches == 3


def test_setting_narrows_the_interval(server, monkeypatch):
    monkeypatch.setitem(C.CFG, "steer", True)
    C.do_act(dict(action="cardsettings", churn_colour="size", queue_refresh_s=60), p=P)
    poll(server, 0)
    poll(server, 61)
    assert server.fetches == 2


def test_budget_low_slows_to_the_hour_and_says_so(server):
    poll(server, 0)
    C._gh_calls.extend([server.t0] * (C.GH_CALLS_PER_HOUR - 50))  # 50 calls left: below the reserve
    r = poll(server, 300)
    assert server.fetches == 1 and r["slowed"] == "call budget low" and r["interval"] == C.GH_REFRESH_S
    assert r["nextAt"] == server.t0 + C.GH_REFRESH_S
    C._gh_calls.clear()  # the hour has passed: the plain rhythm again
    poll(server, 360)
    assert server.fetches == 2


def test_rate_limit_pause_slows_to_the_hour(server):
    poll(server, 0)
    C._gh_pause.update(until=server.t0 + 900)
    r = poll(server, 300)
    assert server.fetches == 1 and r["slowed"] == "GitHub rate limit"


def test_failed_refresh_keeps_the_data_and_says_so(server):
    since = poll(server, 0)["fetchedAt"]
    server.down = RuntimeError("HTTP 502")
    r = poll(server, 130, since)
    assert r["fetchedAt"] == since and "state" not in r and r["error"] is None  # the old copy stays: nothing to resend, nothing blanked
    assert r["staleNote"] and "502" in r["staleNote"]["error"]


def test_no_copy_at_all_answers_without_the_task_list(server):
    server.down = RuntimeError("HTTP 502")
    r = poll(server, 0)
    assert "state" not in r and r["fetchedAt"] is None and "502" in r["error"] and "local" in r


def test_endpoint_is_routed():
    assert '"/api/queuepoll"' in (ROOT / "ckp" / "server" / "routes.py").read_text()
    assert C._since({"since": ["12.5"]}) == 12.5 and C._since({"since": ["x"]}) == 0 and C._since({}) == 0


# ---------- the page ----------
def test_other_tabs_keep_their_timer_and_the_queue_is_not_polled_twice():
    t = re.search(r"setInterval\(\(\)=>\{(.*?)\},180000\)", PAGE).group(1)
    assert "UI.route!=='backlog'" in t and "editing()" in t
    assert "document.addEventListener('visibilitychange',()=>QPOLL.wake())" in PAGE


def run_node(code):
    src = PAGE[PAGE.index("/* queue-poll:start"):PAGE.index("/* queue-apply:end */")]
    r = subprocess.run(["node", "-e", src + code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


FAKE = """
let now=0,timers=[],vis=true,route='backlog',calls=[],fail=false,inflight=0,peak=0;
const flush=()=>new Promise(r=>setImmediate(r));
async function advance(s){const end=now+s;for(;;){timers.sort((a,b)=>a.at-b.at);const t=timers[0];if(!t||t.at>end)break;timers.shift();now=t.at;t.f();await flush();await flush();}now=end;}
const P=queuePoller({visible:()=>vis,active:()=>route==='backlog',now:()=>now,every:()=>120,
  setTimer:(f,ms)=>{const t={f,at:now+ms/1000};timers.push(t);return t;},clearTimer:t=>{timers=timers.filter(x=>x!==t);},
  fetch:async()=>{calls.push(now);inflight++;peak=Math.max(peak,inflight);await flush();inflight--;if(fail)throw new Error('HTTP 502');return {};},
  apply:()=>{},fail:()=>{}});
"""


@node
def test_poller_every_120_s_while_visible_none_while_hidden_and_at_once_when_shown():
    out = run_node(FAKE + """(async()=>{
      P.sync();await flush();await flush();             // entering the tab: at once
      await advance(600);                                // 0, 120, ..., 600
      const visible=calls.slice();
      vis=false;P.wake();await advance(1200);            // hidden: quiet
      const hidden=calls.length-visible.length;
      vis=true;P.wake();await flush();await flush();     // shown again: at once
      const back=calls[calls.length-1];
      P.wake();P.wake();P.sync();await flush();await flush();  // one in flight at a time, no second timer
      route='cockpit';P.sync();const n=calls.length;await advance(600);
      console.log(JSON.stringify({visible,hidden,back,peak,timers:timers.length,after:calls.length-n}));})()""")
    assert out["visible"] == [0, 120, 240, 360, 480, 600]
    assert out["hidden"] == 0 and out["back"] == 1800 and out["peak"] == 1
    assert out["timers"] == 0 and out["after"] == 0  # another tab: no poll


@node
def test_poller_failure_is_kept_and_the_rhythm_goes_on():
    out = run_node(FAKE + """(async()=>{
      fail=true;P.sync();await flush();await flush();const e=P.error;
      fail=false;await advance(120);
      console.log(JSON.stringify({e,after:P.error,calls}));})()""")
    assert out["e"] == {"msg": "HTTP 502", "at": 0} and out["after"] is None and out["calls"] == [0, 120]


APPLY = """
let UI={route:'backlog',drawer:null,armed:null},PROJ='p',QL=null,CI=null,TODAY='',MODULES=[],renders=0,patches=[];
let D={today:'d',modules:[],source:{fetchedAt:1}};
const document={activeElement:null,getElementById:()=>null};
function setState(s){D=s;TODAY=D.today;MODULES=D.modules;}  /* the page's own also lays the recent actions' effects over it (PC-136) */
function render(){renders++;if(QPEND&&!UI.drawer)takeState(QPEND);}
function qLive(){return 'live';}function paneBar(){return 'bar';}
patch=(id,h)=>patches.push(id);
const st=t=>({today:'d',modules:[],source:{fetchedAt:t}});
"""


@node
def test_an_open_editor_survives_a_refresh_and_gets_the_data_when_closed():
    out = run_node(APPLY + """
      UI.drawer={mode:'task',key:'PC-1',edit:true,draft:{title:'my draft',body:'x'}};
      qApply({_proj:'p',local:{workers:[1],asOf:5},state:st(2)});
      const held={renders,patches:patches.slice(),d:D.source.fetchedAt,drawer:UI.drawer.draft.title};
      patches=[];qApply({_proj:'p',local:{workers:[1],asOf:9}});        // nothing changed but the time: only the bar
      const quiet=patches.slice();
      UI.drawer=null;render();
      qApply({_proj:'other',state:st(9)});                               // an answer for another project is dropped
      console.log(JSON.stringify({held,quiet,d:D.source.fetchedAt}));""")
    assert out["held"] == {"renders": 0, "patches": ["qlive", "pbar"], "d": 1, "drawer": "my draft"}
    assert out["quiet"] == ["pbar"] and out["d"] == 2


@node
def test_new_task_list_redraws_when_nothing_is_open_but_never_over_a_newer_copy():
    out = run_node(APPLY + """
      qApply({_proj:'p',local:{},state:st(2)});const a=[renders,D.source.fetchedAt];
      D=st(5);qApply({_proj:'p',local:{},state:st(3)});                 // a Refresh click brought a newer copy meanwhile
      console.log(JSON.stringify({a,b:D.source.fetchedAt}));""")
    assert out["a"] == [1, 2] and out["b"] == 5
