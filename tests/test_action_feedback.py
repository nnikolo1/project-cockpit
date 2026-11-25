"""Visible outcome of the page's actions (PC-136), on synthetic tasks: a successful action leaves a confirmation that names the task and the
effect until it is dismissed or the next action starts, the row shows the new stage at once and loses the button of the done action, an action
in flight disables its button, and an action without an answer makes the page ask the server for the task's state and say what it found."""
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import cockpit as C  # noqa: E402
import logic as L  # noqa: E402

PAGE = (pathlib.Path(__file__).parent.parent / "web" / "index.html").read_text()
# the whole action block: PC-131 (network failures, action ids), PC-136 (outcome, busy state, lookup) and the post() that joins them with PC-135
BLOCK = PAGE[PAGE.index("/* PC-131 actions begin"):PAGE.index("/* PC-131 actions end */")]
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


# ---------- the server: messages that name the task and the effect, and the state lookup ----------
def state():
    iss = lambda n, labels=(): dict(number=n, id=1000 + n, title=f"[PC-{n}] t{n}", url="u", state="open", state_reason=None, assignees=[],
                                    created_at="2026-01-01T00:00:00Z", closed_at=None, labels=list(labels), parent=None, blocked_by=[], body="",
                                    author="mgr", comments=[])
    tasks, modules = L.derive([iss(126), iss(18)], [], {}, "mgr", [dict(id="a", name="a")], grow=False)
    return dict(tasks=tasks, modules=modules, modDeadline={}, proposals=[])


def test_action_messages_name_the_task_and_the_effect():
    S = state()
    assert C.plan_action(dict(action="cancel", n=126), S, "o/r")[1] == "PC-126 cancelled"
    assert C.plan_action(dict(action="release", n=18, p="P2", d=""), S, "o/r")[1] == "PC-18 released as P2"
    assert C.plan_action(dict(action="edit", n=18, title="new", body="b"), S, "o/r")[1].startswith("PC-18 text saved")


def rest(number=126, state="open", reason=None, labels=(), assignees=()):
    return dict(number=number, title=f"[PC-{number}] t", state=state, state_reason=reason, labels=[dict(name=x) for x in labels],
                assignees=[dict(login=x) for x in assignees])


def test_task_lookup_derives_the_stage_of_one_issue():
    assert L.task_lookup(rest(state="closed", reason="not_planned", labels=["cancelled"]))["stage"] == "cancelled"
    assert L.task_lookup(rest(labels=["cancelled"]))["stage"] == "cancelled"
    assert L.task_lookup(rest(labels=["released", "P2"])) == dict(n=126, stage="released", title="[PC-126] t")
    assert L.task_lookup(rest())["stage"] == "embryo"
    assert L.task_lookup(rest(labels=["released"], assignees=["w"]))["stage"] == "wip"
    assert L.task_lookup(rest(state="closed", reason="completed"))["stage"] == "merged"


def test_get_task_reads_github_now_and_refuses_a_bad_number():
    calls = []
    got = C.get_task(dict(repo="o/r"), "126", run=lambda a: calls.append(a) or json.dumps(rest(state="closed", reason="not_planned")))
    assert calls == [["api", "repos/o/r/issues/126"]] and got["stage"] == "cancelled"
    for bad in ("", "1;x", "../x"):
        with pytest.raises(ValueError):
            C.get_task(dict(repo="o/r"), bad, run=lambda a: pytest.fail("no call for a bad number"))


# ---------- the page, without a browser: wiring that must hold ----------
def test_page_wiring():
    assert "actCells(t)" in PAGE and "busyA(id)" in BLOCK and "spin(id)" in BLOCK
    assert "if(UI.busy)return false;" in BLOCK  # one action at a time
    assert "function setState(s){D=s;applyFx(D);" in PAGE  # every copy of the state gets the effects of the recent actions
    assert "noteHtml()+paneBar(R)" in PAGE and "case 'notex':UI.note=null;render();break;" in PAGE
    for form in ("release", "change", "edit"):  # the forms' buttons show the spinner and are disabled in flight
        assert "busyA('%s:'+t.key)" % form in PAGE and "'%s:'+k)" % form in PAGE
    assert "tr.gone td:nth-child(3) .linkbtn{text-decoration:line-through}" in PAGE and "(t.stage==='cancelled'?' gone':'')" in PAGE
    assert "press Refresh on the pane to see the change" not in PAGE  # the row shows it now


# ---------- the page, run in node on synthetic tasks ----------
def line(pat):
    return re.search(pat, PAGE, re.M).group(0)


PRELUDE = "\n".join([line(r"^function esc\(s\).*$"), line(r"^const STAGE_LABEL=.*$"), line(r"^function byKey\(d,k\).*$"), line(r"^function isOpen\(t\).*$"),
                     line(r"^const pq=.*$"), PAGE[PAGE.index("async function api(path,opt)"):PAGE.index("/* action-log:start")], line(r"^function setState\(s\).*$")]) + """
const PAGE_ID='pg',TABS=['cockpit','backlog'],NOTES=[];let STALE=new Set(),FLASH={keys:[],until:0};function note(){NOTES.push(1)}function say(){}
var store={},localStorage={getItem:k=>store[k]||null,setItem:(k,v)=>{store[k]=v}};global.setTimeout=(f)=>{f();return 0};
let TODAY,MODULES;const PENDING=[];function flush(){}
let PROJ='p',UI={armed:null,busy:null,note:null},reloads=0,calls=[],seen=[];
let D={source:{fetchedAt:Date.now()/1000-600},tasks:[{key:'PC-126',number:126,stage:'embryo',title:'t126',prio:null,pick:false},
  {key:'PC-18',number:18,stage:'embryo',title:'t18',prio:null,pick:false},{key:'PC-5',number:5,stage:'released',title:'old',prio:2,pick:true}]};
const SNAP=JSON.stringify(D);
function render(){}
async function refreshTab(){reloads++;D=JSON.parse(SNAP);applyFx(D);}  /* the server still serves its cached, older copy (PC-135 refreshes the tab after an action) */
const ok=msg=>async(u,o)=>{calls.push([u,o&&o.body]);return {ok:true,status:200,json:async()=>({ok:true,msg})};};
const t=k=>byKey(D,k);
"""


def run_node(code):
    r = subprocess.run(["node", "-e", PRELUDE + BLOCK + "\n(async()=>{" + code + "})().catch(e=>{console.error(e);process.exit(1);});"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@node
def test_successful_cancel_release_and_edit_show_a_lasting_confirmation_and_the_new_row():
    out = run_node("""
      const res={};
      fetch=ok('PC-126 cancelled');await post({action:'cancel',n:126},'cancel:PC-126');
      res.cancel={note:UI.note,t:t('PC-126'),cells:actCells(t('PC-126')),reloads};
      fetch=ok('PC-18 released as P2');await post({action:'release',n:18,p:'P2',d:''},'submit:PC-18');
      res.release={note:UI.note,t:t('PC-18'),cells:actCells(t('PC-18'))};
      fetch=ok('PC-5 text saved; its authority analysis is now stale');await post({action:'edit',n:5,title:'new',body:'b'},'edit:PC-5');
      res.edit={note:UI.note,t:t('PC-5')};
      res.cancelStill=Object.assign({},t('PC-126'));  /* the effect survives every reload of the cached copy */
      D.source.fetchedAt=Date.now()/1000+1;D.tasks[0].stage='embryo';applyFx(D);res.newer=t('PC-126').stage;  /* a newer copy wins */
      console.log(JSON.stringify(res));""")
    c = out["cancel"]
    assert c["note"] == dict(kind="ok", msg="PC-126 cancelled.") and c["reloads"] == 1
    assert c["t"]["stage"] == "cancelled" and c["t"]["pick"] is False and c["t"]["closed"] is True
    assert "data-act=\"arm\"" not in c["cells"] and c["cells"].count("—") == 2  # Cancel and Submit are gone
    r = out["release"]
    assert r["note"]["msg"] == "PC-18 released as P2." and r["t"]["stage"] == "released" and r["t"]["prio"] == 2
    assert 'data-a="submit"' not in r["cells"] and 'data-a="cancel"' in r["cells"]
    e = out["edit"]
    assert e["note"]["msg"].startswith("PC-5 text saved") and e["t"]["title"] == "new" and e["t"]["text"] == "b"
    assert out["cancelStill"]["stage"] == "cancelled" and out["newer"] == "embryo"


@node
def test_an_action_in_flight_disables_its_button_and_blocks_a_second_one():
    out = run_node("""
      let done;fetch=(u,o)=>{calls.push(u);return new Promise(r=>{done=()=>r({ok:true,status:200,json:async()=>({ok:true,msg:'PC-126 cancelled'})});});};
      const p=post({action:'cancel',n:126},'cancel:PC-126');
      const busy=armBtn('cancel',t('PC-126')),other=armBtn('submit',t('PC-18'));
      const second=await post({action:'release',n:18,p:'P2',d:''},'submit:PC-18');
      done();await p;
      console.log(JSON.stringify({busy,other,second,calls:calls.length,after:UI.busy}));""")
    assert "disabled" in out["busy"] and 'aria-busy="true"' in out["busy"] and 'class="spin"' in out["busy"]
    assert "disabled" not in out["other"] and out["second"] is False and out["calls"] == 1 and out["after"] is None


@node
def test_a_refusal_is_said_and_the_next_action_or_dismiss_clears_the_message():
    out = run_node("""
      fetch=async()=>({ok:false,status:400,json:async()=>({error:'PC-5 is merged'})});
      await post({action:'cancel',n:5},'cancel:PC-5');const refused=UI.note,row=t('PC-5').stage;
      let mid;fetch=async()=>{mid=UI.note;return {ok:true,status:200,json:async()=>({ok:true,msg:'PC-126 cancelled'})};};
      await post({action:'cancel',n:126},'cancel:PC-126');
      console.log(JSON.stringify({refused,row,mid,html:noteHtml()}));""")
    assert out["refused"] == dict(kind="bad", msg="Refused: PC-5 is merged") and out["row"] == "released"
    assert out["mid"] is None  # the next action clears the old message
    assert 'data-act="notex"' in out["html"] and "PC-126 cancelled." in out["html"] and 'role="status"' in out["html"]


@node
def test_no_answer_makes_the_page_ask_for_the_state_and_say_what_it_found():
    out = run_node("""
      fetch=async(u,o)=>{calls.push(u);if(u.startsWith('/api/act'))throw new TypeError('network');
        return {ok:true,status:200,json:async()=>({n:126,stage:'cancelled',title:'[PC-126] t126'})};};
      const r=await post({action:'cancel',n:126},'cancel:PC-126');const found=UI.note,row=t('PC-126').stage;
      fetch=async(u,o)=>{calls.push(u);if(u.startsWith('/api/act'))throw new TypeError('network');
        return {ok:true,status:200,json:async()=>({n:18,stage:'embryo',title:'[PC-18] t18'})};};
      await post({action:'release',n:18,p:'P2',d:''},'submit:PC-18');const missed=UI.note;
      fetch=async()=>{throw new TypeError('network');};
      await post({action:'cancel',n:5},'cancel:PC-5');const unknown=UI.note;
      console.log(JSON.stringify({r,calls,found,row,missed,unknown,reloads}));""")
    assert out["r"] is False and "/api/task?n=126&project=p" in out["calls"]
    assert out["found"]["kind"] == "ok" and "PC-126 is cancelled" in out["found"]["msg"] and out["row"] == "cancelled"
    assert out["missed"]["kind"] == "bad" and "PC-18 is embryo" in out["missed"]["msg"] and "did not take effect" in out["missed"]["msg"]
    assert out["unknown"]["kind"] == "unknown" and "PC-5" in out["unknown"]["msg"] and "Refresh" in out["unknown"]["msg"]
