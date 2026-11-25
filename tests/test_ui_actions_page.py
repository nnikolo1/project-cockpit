"""Every page action, run in the page's own script (PC-137): the script of web/index.html is loaded into node with a fake document, a fake
browser storage and a fake server (fetch); render and reload are replaced by counters. Each test drives the click handler (a click on a
`data-act` button) or the submit handler (a `data-form` form) and checks the page state it leaves and the request it makes, if any; a
state-changing action is also refused by the fake server and must show the server's error text. Skipped where node is not installed."""
import json
import pathlib
import shutil
import subprocess

import pytest

PAGE = (pathlib.Path(__file__).parent.parent / "web" / "index.html").read_text()
SCRIPT = PAGE[PAGE.index("<script>") + len("<script>"):PAGE.rindex("</script>")]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")

# Before the page's script: the browser objects it touches. Names start with __ so that they cannot meet the page's own.
PRELUDE = r"""
const __LOG={fetch:[],reloads:[],renders:0,scrolls:0,listeners:{}},__LS={},__FORMS={};
let __SERVER=()=>[200,{ok:true,msg:'done'}];
globalThis.window={scrollTo:()=>{__LOG.scrolls++;},addEventListener:()=>{}};
globalThis.document={visibilityState:'hidden',addEventListener:(k,f)=>{__LOG.listeners[k]=f;},getElementById:()=>({innerHTML:''}),querySelector:s=>__FORMS[s]||null};
globalThis.localStorage={getItem:k=>k in __LS?__LS[k]:null,setItem:(k,v)=>{__LS[k]=String(v);}};
globalThis.fetch=async(path,opt)=>{opt=opt||{};const body=opt.body?JSON.parse(opt.body):null;if(body){delete body.aid;delete body.page;}__LOG.fetch.push(body||path);  /* the action id and the page id are random: PC-131 and PC-132 test them */
  const r=__SERVER(path,body);return {ok:r[0]<300,status:r[0],json:async()=>r[1]};};
globalThis.setTimeout=()=>0;globalThis.clearTimeout=()=>{};globalThis.setInterval=()=>0;
globalThis.FormData=class{constructor(f){this.v=f.fields;}get(k){return k in this.v?this.v[k]:null;}};
"""

# After the page's script: counters for render and reload, a small task state, and the two ways the owner acts (a click, a form).
POSTLUDE = r"""
render=()=>{__LOG.renders++;};
reload=async p=>{__LOG.reloads.push(p);};
refreshTab=async r=>{__LOG.reloads.push(false);};  /* after an action the page refreshes the tab it is on (PC-135) */
const __tick=()=>new Promise(r=>setImmediate(r));
const __t=(n,stage,x)=>Object.assign({key:'#'+n,number:n,title:'t'+n,stage:stage,module:'alpha',type:'bug',cls:'nice',blockedBy:[],labels:[]},x||{});
async function __setup(){
  await __tick();
  PROJ='proj';
  D={today:'2026-11-25',source:{repo:'o/r',name:'proj',steering:true,simulate:true},modules:[{id:'alpha',short:'alpha',name:'alpha',children:[]}],
     tasks:[__t(1,'embryo'),__t(2,'released'),__t(3,'wip'),__t(6,'embryo',{blockedBy:['#1']})]};
  MODULES=D.modules;
  __LOG.fetch.length=0;__LOG.reloads.length=0;__LOG.renders=0;__LOG.scrolls=0;
}
async function __click(ds,o){o=o||{};const b={dataset:ds,classList:{contains:c=>c==='drawer-bg'&&!!o.bg}};b.closest=()=>b;
  __LOG.listeners.click({target:o.inside?{closest:()=>b}:b});await __tick();}
async function __submit(kind,k,fields,extra){const f={dataset:Object.assign({form:kind},k?{k:k}:{},extra||{}),fields:fields||{},elements:{}};
  for(const x in f.fields)f.elements[x]={value:f.fields[x],checked:f.fields[x]};
  f.closest=()=>f;await __LOG.listeners.submit({target:f,preventDefault(){}});await __tick();}
const __refuse=e=>{__SERVER=()=>[400,{error:e}];};
const __out=()=>({fetch:__LOG.fetch.slice(),reloads:__LOG.reloads.slice(),renders:__LOG.renders,scrolls:__LOG.scrolls,toast:UI.toast,
  armed:UI.armed,drawer:UI.drawer,route:UI.route});
"""


def run(code):
    """Run `code` (the body of an async function, after __setup) in node with the page; -> the JSON it returns."""
    js = PRELUDE + SCRIPT + POSTLUDE + "(async()=>{await __setup();" + code + "})().then(r=>console.log(JSON.stringify(r===undefined?null:r)))" \
        ".catch(e=>{console.error(e&&e.stack||e);process.exit(1);});"
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def body(**kw):
    return dict(project="proj", **kw)


OK_TOAST = "done."


def posted(steps, want, msg="the server's error text"):
    """Run `steps` twice: accepted (one request, a reload, the server's message) and refused (the same request, no reload, the error text)."""
    ok = run(steps + "return __out();")
    assert (ok["fetch"], ok["reloads"], ok["toast"]) == ([want], [False], OK_TOAST), ok
    no = run("__refuse(%s);" % json.dumps(msg) + steps + "return __out();")
    assert (no["fetch"], no["reloads"], no["toast"]) == ([want], [], "Refused: " + msg), no
    return ok, no


def quiet(out):
    """A view-only action: no request at all, the page drawn again."""
    assert out["fetch"] == [] and out["reloads"] == [] and out["renders"] >= 1, out
    return out


# ---------- state-changing page actions: the request, the success and the refusal ----------

@pytest.mark.ui_action("page:arm", "page:arm/refused")
def test_arm_needs_a_second_click_and_posts_release_cancel_or_compliance():
    once = run("await __click({act:'arm',a:'submit',k:'#1'});return __out();")
    assert once["armed"] == "submit:#1" and once["fetch"] == []
    disarmed = run("await __click({act:'arm',a:'submit',k:'#1'});await __click({act:'filter',v:'all'});return __out();")
    assert disarmed["armed"] is None and disarmed["fetch"] == []
    twice = "await __click({act:'arm',a:'%s',k:'%s'});await __click({act:'arm',a:'%s',k:'%s'});"
    ok, _ = posted(twice % ("submit", "#1", "submit", "#1"), body(action="release", n=1, p="P2", d=""), "#1 is released; only embryos can be released")
    assert ok["armed"] is None
    posted(twice % ("cancel", "#2", "cancel", "#2"), body(action="cancel", n=2), "#2 is merged")
    posted(twice % ("compliance", "proj", "compliance", "proj"), body(action="compliance"), "a compliance check of proj is already running")


@pytest.mark.ui_action("page:take", "page:take/refused", "page:complete", "page:complete/refused")
def test_simulated_take_and_complete():
    posted("await __click({act:'take',k:'#2'});", body(action="take", n=2), "simulation is off (start with --simulate-orchestrator)")
    posted("await __click({act:'complete',k:'#3'});", body(action="complete", n=3), "#3 is not WIP")


@pytest.mark.ui_action("page:unblock", "page:unblock/refused")
def test_unblock_posts_the_blockers_number():
    posted("await __click({act:'unblock',k:'#6',b:'#1'});", body(action="unblock", n=6, b=1), "#1 is not a blocker of #6")


@pytest.mark.ui_action("page:accept", "page:accept/refused", "page:dismiss", "page:dismiss/refused")
def test_accept_and_dismiss_a_proposal():
    posted("await __click({act:'accept',n:'2',id:'p1'});", body(action="accept", n=2, id="p1"), "no such open proposal")
    posted("await __click({act:'dismiss',n:'2',id:'p1'});", body(action="dismiss", n=2, id="p1"), "no such open proposal")


@pytest.mark.ui_action("form:release", "form:release/refused")
def test_release_form():
    posted("await __submit('release','#1',{p:'P1',d:''});", body(action="release", n=1, p="P1", d=""), "bad priority: 'P9'")


@pytest.mark.ui_action("form:change", "form:change/refused")
def test_change_form():
    posted("await __submit('change','#2',{p:'',d:'2026-12-01'});", body(action="change", n=2, p="", d="2026-12-01"),
           "#1 is embryo; priority and deadline change only on released tasks")


@pytest.mark.ui_action("form:block", "form:block/refused")
def test_block_form_posts_only_with_a_blocker():
    posted("await __submit('block','#2',{b:'#1'});", body(action="block", n=2, b=1), "#1 is already a blocker of #2")
    assert run("await __submit('block','#2',{b:''});return __out();")["fetch"] == []


@pytest.mark.ui_action("form:moddl", "form:moddl/refused")
def test_module_deadline_form():
    posted("await __submit('moddl',null,{d:'2026-12-31'},{m:'alpha'});", body(action="moddl", module="alpha", d="2026-12-31"), "bad date: 'x'")


@pytest.mark.ui_action("form:settings", "form:settings/refused")
def test_dispatcher_settings_form_posts_every_row():
    f = dict(enabled=True, interval_minutes="5", retries="1", workers="2", runaway_minutes="60", idle_minutes="15", start_minutes="5")
    posted("await __submit('settings',null,%s);" % json.dumps(f), body(action="settings", **f), "no-activity limit must be at least 10 minutes")


@pytest.mark.ui_action("form:cardsettings", "form:cardsettings/refused")
def test_card_settings_form_reads_the_thresholds_as_numbers():
    setup = "CSET={values:{churn_colour:'size',quality_thresholds:{coverage:{direction:'high',ok:80,warn:60}}}};"
    f = dict(churn_colour="absolute", qt_coverage_ok="90", qt_coverage_warn="70", quota_plan_start="", quota_weak_readings="3", queue_refresh_s="120")
    posted(setup + "await __submit('cardsettings',null,%s);" % json.dumps(f),
           body(action="cardsettings", churn_colour="absolute", quality_thresholds=dict(coverage=dict(direction="high", ok=90, warn=70)),
                quota_plan_start="", quota_weak_readings="3", queue_refresh_s="120"), "churn colour must be one of: size, absolute")


@pytest.mark.ui_action("form:new", "form:new/refused")
def test_new_task_form_opens_the_filed_task_or_keeps_the_form():
    steps = ("UI.drawer={mode:'new'};D.tasks.push(__t(9,'embryo',{title:'Do it'}));"
             "await __submit('new',null,{title:'Do it',desc:'why',module:'alpha',type:'bug',cls:'must',parent:''});")
    ok, no = posted(steps, body(action="new", title="Do it", desc="why", module="alpha", type="bug", cls="must", parent=""),
                    "a title, a known type and must/nice are required")
    assert ok["drawer"] == dict(mode="task", key="#9") and ok["route"] == "backlog"
    assert no["drawer"] == dict(mode="new")


@pytest.mark.ui_action("form:edit", "form:edit/refused")
def test_edit_form_needs_a_confirming_second_save():
    once = run("UI.drawer={mode:'task',key:'#1',edit:true};await __submit('edit','#1',{title:'T',body:'B'});return __out();")
    assert once["fetch"] == [] and once["armed"] == "edit:#1" and once["drawer"]["draft"] == dict(title="T", body="B")
    steps = "UI.drawer={mode:'task',key:'#1',edit:true};await __submit('edit','#1',{title:'T',body:'B'});await __submit('edit','#1',{title:'T',body:'B'});"
    ok, no = posted(steps, body(action="edit", n=1, title="T", body="B"), "a title is required")
    assert ok["drawer"] == dict(mode="task", key="#1")
    assert no["drawer"]["edit"] and no["drawer"]["draft"] == dict(title="T", body="B")   # the text stays in the editor


# ---------- view-only page actions: the page state they leave, and no request ----------

@pytest.mark.ui_action("page:go")
def test_go_switches_the_view_and_closes_the_drawer():
    for r, route, extra in (("backlog", "backlog", ""), ("how", "code", "UI.codeTab"), ("rules", "settings", "UI.setSec")):
        out = quiet(run("UI.drawer={mode:'task',key:'#1'};await __click({act:'go',r:'%s'});return Object.assign(__out(),{x:%s});" % (r, extra or "null")))
        assert (out["route"], out["drawer"], out["scrolls"]) == (route, None, 1)
        assert out["x"] == {"how": "flowchart", "rules": "policies"}.get(r)


@pytest.mark.ui_action("page:code", "page:codetab", "page:codem", "page:fit")
def test_code_tab_actions():
    out = quiet(run("UI.codeTab='deps';await __click({act:'code',m:'alpha'});return Object.assign(__out(),{v:[UI.route,UI.codeTab,UI.codeMod]});"))
    assert out["v"] == ["code", "quality", "alpha"] and out["scrolls"] == 1
    out = quiet(run("await __click({act:'codetab',v:'deps'});await __click({act:'codem',v:'beta'});await __click({act:'fit',v:'deps'});await __click({act:'fit',v:'flow'});"
                    "return Object.assign(__out(),{v:[UI.codeTab,UI.codeMod,UI.fit.deps,UI.fit.flow]});"))
    assert out["v"] == ["deps", "beta", False, True]


@pytest.mark.ui_action("page:mod", "page:modf", "page:filter", "page:qproj", "page:tlm", "page:tlt", "page:rg", "page:setsec")
def test_filters_and_section_switches():
    out = quiet(run("UI.filter='wip';await __click({act:'mod',m:'alpha',x:'1'});return Object.assign(__out(),{v:[UI.route,UI.mod,UI.ex,UI.filter]});"))
    assert out["v"] == ["backlog", "alpha", True, "all"] and out["scrolls"] == 1
    out = quiet(run("await __click({act:'filter',v:'wip'});await __click({act:'modf',v:'alpha'});await __click({act:'qproj',v:'proj'});"
                    "await __click({act:'tlm',v:'alpha'});await __click({act:'tlt',v:'bug'});await __click({act:'rg',v:'G'});await __click({act:'setsec',v:'cards'});"
                    "return Object.assign(__out(),{v:[UI.filter,UI.mod,UI.ex,UI.qProj,UI.tlMod,UI.tlType,UI.rulesGroup,UI.setSec]});"))
    assert out["v"] == ["wip", "alpha", False, "proj", "alpha", "bug", "G", "cards"]


@pytest.mark.ui_action("page:open", "page:close", "page:edit", "page:noedit")
def test_drawer_open_close_and_the_editor():
    out = quiet(run("await __click({act:'open',k:'#2'});return __out();"))
    assert out["drawer"] == dict(mode="task", key="#2")
    assert quiet(run("await __click({act:'edit',k:'#2'});return __out();"))["drawer"] == dict(mode="task", key="#2", edit=True)
    assert quiet(run("UI.drawer={mode:'task',key:'#2',edit:true};await __click({act:'noedit',k:'#2'});return __out();"))["drawer"] == dict(mode="task", key="#2")
    assert quiet(run("UI.drawer={mode:'task',key:'#2'};await __click({act:'close'});return __out();"))["drawer"] is None
    inside = run("UI.drawer={mode:'task',key:'#2'};await __click({act:'close'},{bg:true,inside:true});return __out();")
    assert inside["drawer"] == dict(mode="task", key="#2")   # a click inside the drawer's backdrop child does not close it
    assert run("UI.drawer={mode:'task',key:'#2'};await __click({act:'close'},{bg:true});return __out();")["drawer"] is None


@pytest.mark.ui_action("page:new", "page:subtask", "page:followup", "page:hotspot")
def test_actions_that_open_the_new_task_form_prefilled():
    assert quiet(run("await __click({act:'new'});return __out();"))["drawer"] == dict(mode="new")
    assert quiet(run("await __click({act:'subtask',k:'#2'});return __out();"))["drawer"] == dict(
        mode="new", prefill=dict(parent=2, module="alpha", title="", type="feature"))
    assert quiet(run("await __click({act:'followup',k:'#2'});return __out();"))["drawer"] == dict(
        mode="new", prefill=dict(module="alpha", title="Follow up on #2", desc="Follow-up to #2 (t2).", type="bug"))
    out = quiet(run("CODE={commit:'abc',modules:{alpha:{hot:[['a.py',7,31]]},unassigned:{hot:[['b.py',2,12]]}}};"
                    "await __click({act:'hotspot',m:'alpha',i:'0'});const a=__out();await __click({act:'hotspot',m:'unassigned',i:'0'});return Object.assign(__out(),{a:a});"))
    assert out["a"]["drawer"] == dict(mode="new", prefill=dict(module="alpha", title="Reduce the complexity of a.py (worst block 31)",
                                                               desc="From the code snapshot abc: 7 commits in the window, worst block complexity 31.", type="devops"))
    assert out["a"]["route"] == "backlog" and out["drawer"]["prefill"]["module"] == ""   # Unassigned is no module to file under


@pytest.mark.ui_action("page:refresh")
def test_refresh_reloads_the_pane_it_belongs_to():
    out = run("UI.route='timeline';await __click({act:'refresh',pane:'backlog'});await __click({act:'refresh'});return __out();")
    assert out["fetch"] == [] and out["reloads"] == ["backlog", "timeline"]


@pytest.mark.ui_action("page:qtdefaults")
def test_restore_defaults_fills_the_form_without_saving():
    out = run("CSET={defaults:{quality_thresholds:{coverage:{direction:'high',ok:80,warn:60}}}};"
              "const f={elements:{qt_coverage_ok:{value:'1'},qt_coverage_warn:{value:'2'}}};__FORMS['[data-form=cardsettings]']=f;"
              "await __click({act:'qtdefaults'});return Object.assign(__out(),{v:[f.elements.qt_coverage_ok.value,f.elements.qt_coverage_warn.value]});")
    assert out["v"] == [80, 60] and out["fetch"] == [] and out["toast"] == "Defaults filled in; press Save to keep them."


@pytest.mark.ui_action("page:rev", "page:unrev", "page:revall")
def test_review_marks_live_in_the_browser_only():
    setup = "RULES={configured:true,groups:[],changes:[{sha:'a',date:'2026-11-24'},{sha:'b',date:'2026-11-23'}]};"
    out = quiet(run(setup + "await __click({act:'rev',id:'a'});const one=JSON.parse(__LS['cockpit-reviewed:o/r']);"
                    "await __click({act:'unrev',id:'a'});const two=JSON.parse(__LS['cockpit-reviewed:o/r']);"
                    "await __click({act:'revall'});return Object.assign(__out(),{v:[one.a,two.a,JSON.parse(__LS['cockpit-reviewed:o/r'])]});"))
    one, two, last = out["v"]
    assert one is True and two is False and last["a"] is True and last["b"] is True


# ---------- the actions of the merged PC-131/132/136 pages: retry, dismiss, recent problems, the action-log filter ----------

@pytest.mark.ui_action("page:retryact", "page:retryact/refused")
def test_retry_sends_the_failed_action_again_with_its_own_id():
    posted("UI.toastRetry={action:'cancel',n:2,aid:'a-1'};UI.toast='not reached';await __click({act:'retryact'});", body(action="cancel", n=2), "#2 is merged")


@pytest.mark.ui_action("page:toastx", "page:notex", "page:problems")
def test_dismissing_a_message_and_opening_the_recent_problems():
    out = quiet(run("UI.toast='x';UI.toastRetry={action:'cancel',n:2};await __click({act:'toastx'});return Object.assign(__out(),{r:UI.toastRetry});"))
    assert out["toast"] == "" and out["r"] is None
    assert quiet(run("UI.note={kind:'ok',msg:'m'};await __click({act:'notex'});return Object.assign(__out(),{n:UI.note});"))["n"] is None
    assert quiet(run("await __click({act:'problems'});return Object.assign(__out(),{p:UI.problems});"))["p"] is True


@pytest.mark.ui_action("form:alog")
def test_action_log_filter_form_asks_for_the_filtered_log():
    out = run("UI.setSec='actionlog';await __submit('alog',null,{action:'cancel',outcome:'ok',task:' PC-1 '});return Object.assign(__out(),{f:UI.alog});")
    assert out["f"] == dict(action="cancel", outcome="ok", task="PC-1")
    assert out["fetch"] == ["/api/actionlog?action=cancel&outcome=ok&task=PC-1&project=proj"]
