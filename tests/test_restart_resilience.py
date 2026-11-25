"""PC-131: a restart of the Cockpit no longer looks like a refusal. The server listens before its warm-up and answers from its disk copy
(marked warming); actions work meanwhile; an action is applied once however often the page sends it (a lost answer, a restart in the middle);
the commits cache survives on disk; the page tells a network failure from a refusal, retries, and keeps the recent problems.
The server tests run the real handler on a socket with a fake `gh`; the page tests run the page's own code (skipped without node)."""
import http.client
import json
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import cockpit as C  # noqa: E402
import logic as L  # noqa: E402

PAGE = (ROOT / "web" / "index.html").read_text()
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
M, R = "mgr", "o/r"


def iss(n, labels=(), **kw):
    d = dict(number=n, id=1000 + n, title=f"t{n}", url=f"https://x/issues/{n}", state="open", state_reason=None, assignees=[], created_at="2026-01-01T00:00:00Z",
             closed_at=None, labels=list(labels), parent=None, blocked_by=[], body="", author=M, comments=[])
    d.update(kw)
    return d


def a_state():
    """A state as the disk copy keeps it: task #1 an embryo, #2 released."""
    tasks, modules = L.derive([iss(1), iss(2, ["released", "P2"])], [dict(number=2, name="released", kind="labeled", actor=M, t="2026-01-01T00:00:00Z")], {}, M, [], grow=False)
    return dict(today="2026-11-25", tasks=tasks, modules=modules, modDeadline={}, proposals=[], questions=[], events=[], unauthorised=0,
                source=dict(name="x", repo=R, steering=True, simulate=False, project=None, manager=M, user=M, fetched="10:00:00", fetchedAt=time.time() - 7200))


class FakeGh:
    """run_gh stand-in: records every call; `issue view` answers with `issue`."""
    def __init__(self, issue=None):
        self.calls, self.issue = [], issue or dict(state="OPEN", labels=[], title="t1", body="")

    def __call__(self, args):
        self.calls.append(args)
        return json.dumps(self.issue) if args[:2] == ["issue", "view"] else "{}"

    def edits(self):
        return [c for c in self.calls if c[:2] == ["issue", "edit"]]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """One project 'x' with a disk copy of its state older than the hour; data and cache in tmp; steering on; gh faked."""
    monkeypatch.setattr(C, "PROJECTS", {})
    monkeypatch.setattr(C, "DEFAULT", None)
    C.register([dict(name="x", repo=R, manager=M, project=None, git=None, modules=None, phases=None, code=None, code_collect=None, compliance=None)], "x")
    monkeypatch.setattr(C, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(C, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setitem(C.CFG, "steer", True)
    monkeypatch.setitem(C.CFG, "simulate", False)
    gh = FakeGh()
    monkeypatch.setattr(C, "run_gh", gh)
    C._cache.clear()
    C._refreshing.clear()
    C._disk_save(C.pk(C.proj("x"), "state"), a_state())
    yield gh
    C._cache.clear()
    C._refreshing.clear()
    C.WARM["on"] = False


@pytest.fixture
def server(env, monkeypatch):
    """The real server on a free port, its warm-up held until the test releases `gate`; GitHub hangs as long (a slow first fetch)."""
    gate = threading.Event()
    monkeypatch.setattr(C, "build_state", lambda p: (gate.wait(10), a_state())[1])
    C.H.token = "tok"
    srv = C.serve("127.0.0.1", 0, lambda: gate.wait(10))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, gate, env
    gate.set()
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers={"Cookie": "t=tok", "Content-Type": "application/json"})
    r = c.getresponse()
    out = (r.status, json.loads(r.read() or b"{}"))
    c.close()
    return out


# ---------- listening before the warm-up ----------
def test_the_server_listens_and_answers_from_the_disk_copy_before_the_warm_up_is_done(server):
    srv, gate, _ = server
    t0 = time.time()
    code, S = call(srv, "GET", "/api/state")
    assert code == 200 and time.time() - t0 < 2  # at once, though the warm-up and the GitHub fetch still hang
    assert S["warming"] is True and S["source"]["fetched"] == "10:00:00" and [t["number"] for t in S["tasks"]] == [1, 2]
    gate.set()
    assert wait_for(lambda: not C.WARM["on"])
    assert call(srv, "GET", "/api/state")[1]["warming"] is False  # the page asks again and picks up that the warm-up is over


def test_release_cancel_and_edit_work_during_the_warm_up(server):
    srv, _, gh = server
    assert C.WARM["on"]
    assert call(srv, "POST", "/api/act", dict(action="release", n=1, p="P2", d="", aid="a1"))[0] == 200
    assert call(srv, "POST", "/api/act", dict(action="cancel", n=2, aid="a2"))[0] == 200
    assert call(srv, "POST", "/api/act", dict(action="edit", n=1, title="new title", body="new text", aid="a3"))[0] == 200
    assert ["issue", "edit", "1", "-R", R, "--add-label", "released", "--add-label", "P2"] in gh.calls
    assert ["issue", "close", "2", "-R", R, "--reason", "not planned"] in gh.calls
    assert any(c[:3] == ["api", "-X", "PATCH"] for c in gh.calls)


def test_a_failing_warm_up_still_ends_the_warming_mark(env):
    srv = C.serve("127.0.0.1", 0, lambda: 1 / 0)
    try:
        assert wait_for(lambda: not C.WARM["on"])
    finally:
        srv.server_close()


# ---------- an action is applied once ----------
def test_a_lost_answer_and_the_retry_apply_submit_once(server, monkeypatch):
    """Injected network failure: the server applies the first Submit but the connection breaks before the answer; the page's retry
    (same action id) gets the first answer back, marked as a repeat, and nothing is applied a second time."""
    srv, _, gh = server
    real, dropped = C.H.send, []

    def flaky(self, code, body=b"", *a, **kw):
        if self.path.startswith("/api/act") and not dropped:
            dropped.append(code)
            self.close_connection = True
            raise ConnectionResetError("injected: the connection broke before the answer")
        return real(self, code, body, *a, **kw)
    monkeypatch.setattr(C.H, "send", flaky)
    submit = dict(action="release", n=1, p="P2", d="", aid="sub-1")
    with pytest.raises((http.client.HTTPException, ConnectionError)):
        call(srv, "POST", "/api/act", submit)
    assert dropped == [200] and len(gh.edits()) == 1
    code, body = call(srv, "POST", "/api/act", submit)
    assert code == 200 and body["repeat"] is True and "released" in body["msg"]
    assert len(gh.edits()) == 1  # not applied twice


def test_the_pages_copy_of_the_state_is_not_kept_in_the_action_id_file(env, monkeypatch):
    """Merge of PC-131 and PC-135: an answer carries the served state for the page; the file of the last 200 action ids keeps the rest only."""
    monkeypatch.setattr(C, "_do_act", lambda form, run, state, p: (200, dict(ok=True, msg="done", issues=[1], state=dict(tasks=[1] * 1000))))
    C.do_act(dict(action="release", n=1, aid="big-1"))
    kept = C._acts()["big-1"]["body"]
    assert kept == dict(ok=True, msg="done", issues=[1])


def test_a_repeat_while_the_first_still_runs_waits_for_it_and_applies_once(env, monkeypatch):
    gate, n = threading.Event(), []

    def slow(form, run, state, p):
        n.append(1)
        gate.wait(5)
        return 200, dict(ok=True, msg="#1 released as P2")
    monkeypatch.setattr(C, "_do_act", slow)
    form, out = dict(action="release", n=1, aid="sub-2"), []
    first = threading.Thread(target=lambda: out.append(C.do_act(dict(form))))
    first.start()
    assert wait_for(lambda: n)
    second = threading.Thread(target=lambda: out.append(C.do_act(dict(form))))
    second.start()
    time.sleep(0.1)
    gate.set()
    first.join(5)
    second.join(5)
    assert len(n) == 1 and sorted(c for c, _ in out) == [200, 200] and any(b.get("repeat") for _, b in out)


@pytest.mark.parametrize("labels,applied", [([{"name": "released"}, {"name": "P2"}], True), ([], False)])
def test_a_submit_interrupted_by_a_restart_reads_the_task_before_it_is_repeated(env, labels, applied):
    """The record says 'started' (the Cockpit stopped while it ran): the task's labels on GitHub decide; a released task is not released again."""
    env.issue = dict(state="OPEN", labels=labels, title="t1", body="")
    C._acts_put("sub-3", dict(state="started", action="release"))
    code, body = C.do_act(dict(action="release", n=1, p="P2", d="", aid="sub-3", project="x"))
    assert code == 200 and env.calls[0][:2] == ["issue", "view"]
    assert len(env.edits()) == (0 if applied else 1) and bool(body.get("repeat")) == applied
    assert C.do_act(dict(action="release", n=1, p="P2", d="", aid="sub-3", project="x"))[1]["repeat"] is True  # and from now on it is done
    assert len(env.edits()) == (0 if applied else 1)


def test_an_interrupted_action_whose_effect_cannot_be_read_is_refused_not_repeated(env):
    C._acts_put("new-1", dict(state="started", action="new"))
    code, body = C.do_act(dict(action="new", title="x", type="feature", cls="nice", aid="new-1", project="x"))
    assert code == 409 and "interrupted by a restart" in body["error"] and env.calls == []


def test_the_action_ids_on_disk_are_bounded(env, monkeypatch):
    monkeypatch.setattr(C, "ACTS_KEEP", 3)
    for i in range(5):
        C._acts_put(f"a{i}", dict(state="done", code=200, body={}))
    assert sorted(C._acts()) == ["a2", "a3", "a4"]


def test_action_applied_reads_the_effect_from_the_issue():
    A = L.action_applied
    assert A(dict(action="release"), dict(labels=[{"name": "released"}])) is True
    assert A(dict(action="release"), dict(labels=[{"name": "P2"}])) is False
    assert A(dict(action="cancel"), dict(labels=[{"name": "cancelled"}], state="CLOSED")) is True
    assert A(dict(action="cancel"), dict(labels=[{"name": "cancelled"}], state="OPEN")) is False  # half done: running it again is harmless
    assert A(dict(action="edit", title="T", body="text"), dict(title="[PC-1] T", body="text\n\n<!-- filed-by: x -->\n")) is True  # the kept parts do not count
    assert A(dict(action="edit", title="T", body="text"), dict(title="[PC-1] T", body="text\n")) is True
    assert A(dict(action="edit", title="T", body="text"), dict(title="[PC-1] Old", body="text\n")) is False
    assert A(dict(action="new"), {}) is None and A(dict(action="block"), {}) is None


# ---------- a refusal keeps its reason ----------
def test_a_refusal_is_answered_with_its_reason(server):
    srv, _, gh = server
    code, body = call(srv, "POST", "/api/act", dict(action="release", n=2, p="P2", d="", aid="r1"))
    assert code == 400 and "only embryos can be released" in body["error"] and gh.edits() == []


# ---------- the commits cache ----------
GIT = "@aaa|2026-11-24 10:00:00 +0100|first\n\nsrc/a.py\n@bbb|2026-11-25 09:00:00 +0100|second\n\nsrc/b.py\n"


def test_the_commits_cache_is_written_to_disk_with_iso_dates_and_read_back_alike(env, caplog):
    p, commits = C.proj("x"), L.parse_git_full(GIT)
    k = C.pk(p, "commits")
    C.cached(k, lambda: commits, 60, persist=True)
    assert "not written to disk" not in caplog.text
    on_disk = json.loads(C._disk_path(k).read_text())
    assert [c["date"] for c in on_disk] == ["2026-11-24", "2026-11-25"]
    C._cache.clear()
    back = C.cached(k, lambda: pytest.fail("rebuilt instead of read from disk"), 60, swr=True, persist=True, disk_fresh=3600)
    assign = [dict(path="src/", module="m")]
    assert L.group_commits(back, assign, R) == L.group_commits(commits, assign, R)


def test_a_value_that_cannot_be_kept_as_json_is_still_reported(env, caplog):
    C._disk_save("odd", dict(x=object()))
    assert "not written to disk" in caplog.text


# ---------- the page ----------
def block():
    return PAGE[PAGE.index("/* PC-131 actions begin"):PAGE.index("/* PC-131 actions end */")]


def run_page(tail):
    esc = re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0)
    stubs = ("var PAGE_ID='pg',NOTES=[],D=null;function note(){NOTES.push([].slice.call(arguments))}var store={},localStorage={getItem:k=>store[k]||null,setItem:(k,v)=>{store[k]=v}};var UI={},PROJ='x',SAYS=[],RELOADS=0;"
             "const pq=p=>p;async function reload(){RELOADS++}var TABS=[],STALE=new Set(),FLASH={keys:[],until:0};function setState(s){D=s}async function refreshTab(){RELOADS++}function render(){}function say(m,ms,kind,retry){SAYS.push({m:m,ms:ms,kind:kind||'',retry:!!retry})}"
             "global.setTimeout=(f)=>{f();return 0};")
    r = subprocess.run(["node", "-e", esc + stubs + block() + tail], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def fake_server(script):
    """JS: a fake fetch that answers from `script`, a list of 'net' (the browser's network failure), 'apply' (the server applies the action,
    counted per aid, and answers; a repeat of an applied aid is answered as such), 'lost' (applied, but the answer is lost: a network
    failure) or [status, error] (a refusal); it records the bodies it received."""
    return ("var SCRIPT=%s,SENT=[],APPLIED={};global.fetch=async(path,opt)=>{const b=JSON.parse(opt.body);SENT.push(b);const s=SCRIPT.shift();"
            "if(s==='net')throw new TypeError('Failed to fetch');"
            "if(s==='lost'){APPLIED[b.aid]=(APPLIED[b.aid]||0)+1;throw new TypeError('Failed to fetch');}"
            "if(s==='apply'){const rep=!!APPLIED[b.aid];APPLIED[b.aid]=(APPLIED[b.aid]||0)+(rep?0:1);return {ok:true,status:200,json:async()=>({ok:true,msg:'#1 released as P2',repeat:rep})};}"
            "return {ok:false,status:s[0],json:async()=>({error:s[1]})};};") % json.dumps(script)


OUT = "process.stdout.write(JSON.stringify({says:SAYS,sent:SENT,applied:APPLIED,problems:PROBLEMS,reloads:RELOADS,ok:ok}))"


@node
def test_page_a_network_failure_is_explained_and_retried_with_the_same_action_id():
    o = run_page(fake_server(["net", "net", "apply"]) + "(async()=>{var ok=await post({action:'release',n:1,p:'P2',d:''});" + OUT + "})()")
    assert o["ok"] is True and len(o["sent"]) == 3 and len({b["aid"] for b in o["sent"]}) == 1
    nets = [s for s in o["says"] if s["kind"] == "net"]
    assert [s["m"] for s in nets] == ["The Cockpit could not be reached (it may be restarting). Nothing was changed. Retrying in 5 s (try %d of 3)." % i for i in (2, 3)]
    assert o["says"][-1]["kind"] == "" and o["problems"] == [] and o["reloads"] == 1


@node
def test_page_a_submit_whose_answer_was_lost_is_applied_once_and_says_it_had_reached_the_server():
    o = run_page(fake_server(["lost", "apply"]) + "(async()=>{var ok=await post({action:'release',n:1,p:'P2',d:''});" + OUT + "})()")
    assert o["ok"] is True and list(o["applied"].values()) == [1]
    assert "it had reached the Cockpit before the connection broke and was applied once, not again" in o["says"][-1]["m"]


@node
def test_page_after_three_network_failures_a_final_message_offers_retry_and_is_kept():
    o = run_page(fake_server(["net", "net", "net"]) + "(async()=>{var ok=await post({action:'release',n:1,p:'P2',d:''});" + OUT + "})()")
    last = o["says"][-1]
    assert o["ok"] is False and len(o["sent"]) == 3 and last["kind"] == "net" and last["retry"] and last["ms"] == 0
    assert "could not be reached after 3 tries" in last["m"] and "never applies it twice" in last["m"]
    assert [p["action"] for p in o["problems"]] == ["release"]


@node
def test_page_a_refusal_keeps_its_reason_is_shown_as_a_refusal_and_is_not_retried():
    o = run_page(fake_server([[400, "#2 is released; only embryos can be released"]]) + "(async()=>{var ok=await post({action:'release',n:2});" + OUT + "})()")
    assert o["ok"] is False and len(o["sent"]) == 1
    assert o["says"] == [dict(m="Refused: #2 is released; only embryos can be released", ms=8000, kind="refused", retry=False)]
    assert o["problems"][0]["msg"] == "Refused: #2 is released; only embryos can be released"


@node
def test_page_recent_problems_keep_the_last_20_newest_first_and_show_in_the_footer():
    o = run_page("for(let i=0;i<25;i++)problem('a'+i,'m'+i);UI.problems=true;var ok=footer();process.stdout.write(JSON.stringify({p:PROBLEMS.map(x=>x.action),h:ok}))")
    assert o["p"] == ["a%d" % i for i in range(24, 4, -1)]
    assert "Recent problems (20)" in o["h"] and o["h"].count("<li>") == 20 and "m24" in o["h"]


def test_page_static_the_parts_are_wired():
    assert "h+=footer();" in PAGE and "case 'retryact'" in PAGE and "case 'problems'" in PAGE
    assert ".toast-refused{" in PAGE and ".toast-net{" in PAGE  # a refusal and a network failure look different
    assert "warming up, data from" in PAGE and "D.stale||D.warming" in PAGE  # the restarted Cockpit's copy is marked and the page asks again
    assert "function post(" in block() and PAGE.count("async function post(") == 1


def wait_for(cond, secs=3):
    end = time.time() + secs
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()
