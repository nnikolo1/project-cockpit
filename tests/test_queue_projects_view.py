"""PC-67: the Cockpit's view of the one Task Queue. The queue's files (settings, state, processing log) are read from the shared data
root once it holds a settings file, else from the project's own folder; saving the Settings page keeps the list of served projects;
every running Worker and every processing-log row carries its project (the record's field, else the task id's project key, spec 2d),
and the page shows it as a column with a filter. Synthetic data only."""
import json
import pathlib
import shutil
import stat
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cockpit as C  # noqa: E402
import logic as L  # noqa: E402
from dispatcher.core import DEFAULTS, load_settings  # noqa: E402

P = dict(name="proj", repo="o/proj")
LISTED = [{"short": "proj", "repo": "o/proj", "repo_dir": "/r/proj", "first": True}, {"short": "alpha", "repo": "o/alpha", "repo_dir": "/r/alpha"}]
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def ev(task, state, **kw):
    return json.dumps(dict(time="2026-11-25T06:30:00+02:00", task=task, folder="/area/" + task, state=state, **kw))


# ---------- logic ----------
def test_project_key_of_a_task_id():
    assert [L.project_key(x) for x in ("cockpit-44", "my-proj-3", "x", "", None, "a-b")] == ["cockpit", "my-proj", "", "", "", ""]


def test_workers_carry_the_project_from_the_state_or_from_the_task_id():
    st = json.dumps({"workers": {"worker1": {"task_id": "alpha-2", "project": "alpha", "launched": 1, "started": True},
                                 "worker2": {"task_id": "proj-7", "launched": 1}, "worker3": {"task_id": None}}})
    assert [(w["worker"], w["project"]) for w in L.queue_local(st, None)["workers"]] == [("worker1", "alpha"), ("worker2", "proj"), ("worker3", "")]


def test_log_rows_carry_the_project_field_else_the_key_of_the_task_and_heartbeats_their_running_tasks():
    hb = json.dumps(dict(time="2026-11-25T06:31:00+02:00", heartbeat="2026-11-25 06:31:00 | running: worker1=alpha-2, worker2=alpha-3, worker3=proj-1 | nothing to do (all Workers busy)"))
    rows = L.log_rows("\n".join([ev("alpha-2", "launched", project="alpha", worker="worker1"), ev("proj-7", "failed", reason="no note"),
                                 json.dumps(dict(time="2026-11-25T06:30:00+02:00", task="-", state="proxy started")), hb]))
    assert [r["project"] for r in rows] == ["alpha, proj", "", "proj", "alpha"]


# ---------- the server: where the queue's files are read ----------
def test_before_the_migration_the_project_folder_is_used(monkeypatch):
    assert C.queue_dir(P) == C.DATA_DIR / "proj" and C.settings_path(P) == C.DATA_DIR / "proj" / "dispatcher-settings.json"


def migrated(root, state=None, plog=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "dispatcher-settings.json").write_text(json.dumps(dict(DEFAULTS, projects=LISTED)))
    if state:
        (root / "dispatcher-state.json").write_text(state)
    if plog:
        (root / "processing").mkdir()
        (root / "processing" / "processing.jsonl").write_text(plog)


def test_after_the_migration_every_project_reads_the_one_queue():
    migrated(C.QUEUE_ROOT, json.dumps({"workers": {"worker1": {"task_id": "alpha-2", "project": "alpha", "launched": 1, "started": True}}}),
             ev("alpha-2", "launched", project="alpha", worker="worker1"))
    (C.DATA_DIR / "proj" / "processing").mkdir(parents=True)   # the old per-project copy is no longer read
    (C.DATA_DIR / "proj" / "processing" / "processing.jsonl").write_text(ev("proj-1", "failed"))
    for p in (P, dict(name="alpha", repo="o/alpha")):
        assert C.queue_dir(p) == C.QUEUE_ROOT
        q = C.get_queue(p)
        assert [(w["task"], w["project"]) for w in q["workers"]] == [("alpha-2", "alpha")]
        assert [(r["task"], r["project"]) for r in q["log"]] == [("alpha-2", "alpha")]


@pytest.mark.ui_action("server:settings")
def test_saving_the_settings_page_keeps_the_served_projects(monkeypatch):
    monkeypatch.setitem(C.CFG, "steer", True)
    migrated(C.QUEUE_ROOT)
    code, body = C.do_act(dict(action="settings", **{**DEFAULTS, "workers": 2}), p=P)
    f = C.QUEUE_ROOT / "dispatcher-settings.json"
    assert code == 200 and C.settings_path(P) == f and stat.S_IMODE(f.stat().st_mode) == 0o600
    s = load_settings(f)
    assert s["workers"] == 2 and s["projects"] == LISTED
    assert not (C.DATA_DIR / "proj" / "dispatcher-settings.json").exists()


# ---------- the page ----------
@pytest.fixture
def run_node():
    import test_ui_actions_page as page
    return page.run


QL2 = "QL={workers:[{worker:'worker1',task:'alpha-2',project:'alpha',launched:'',started:true}],log:[],projects:%s," \
      "settings:{values:{enabled:true,workers:2,interval_minutes:5}},asOf:0};" % json.dumps([dict(short="proj", repo="o/proj", first=True), dict(short="alpha", repo="o/alpha", first=False)])


@node
def test_page_shows_and_filters_the_project_column(run_node):
    R = json.dumps(L.log_rows("\n".join([ev("alpha-2", "launched", project="alpha", worker="worker1"), ev("proj-7", "failed", reason="no note")])))
    out = run_node(QL2 + "const R=%s;UI.qlog.beats=true;const h=vQlog(R);"
                   "return [h.includes('<th>Project</th>'),h.includes('<select name=\"project\">'),"
                   "qlogPick(R,Object.assign({},UI.qlog,{project:'alpha'})).map(r=>r.task)];" % R)
    assert out == [True, True, ["alpha-2"]]


# ---------- PC-138: the tab over every listed project ----------
def t(n, stage="released", **kw):
    return dict(dict(key=f"K-{n}", number=n, title=f"t{n}", stage=stage, blockedBy=[], labels=[], cls="nice", prio=None, deadline=None,
                     parent=None, pick=stage == "released", url=f"https://example.invalid/{n}"), **kw)


def test_listed_projects_only_with_more_than_one_and_the_first_ones_leading():
    assert L.listed_projects({}) == [] and L.listed_projects(dict(projects=LISTED[:1])) == [] and L.listed_projects(None) == []
    two = [dict(short="alpha", repo="o/alpha", repo_dir="/r/a"), dict(short="proj", repo="o/proj", repo_dir="/r/p", first=True)]
    assert [(p["short"], p["first"]) for p in L.listed_projects(dict(projects=two))] == [("proj", True), ("alpha", False)]


def test_queue_all_ranks_as_the_dispatcher_the_first_project_leading():
    parts = [dict(short="proj", first=True, tasks=[t(1, prio=3), t(2, "embryo"), t(3, "wip")]),
             dict(short="alpha", first=False, tasks=[t(1, prio=1), t(4, deadline="2026-12-01"), t(5, cls="must"), t(6, "embryo", blockedBy=["K-4"]),
                                                     t(7, parent="K-4")])]
    out = L.queue_all(parts)
    assert [(r["project"], r["key"]) for r in out][:3] == [("proj", "K-1"), ("proj", "K-2"), ("proj", "K-3")]   # every task, tagged
    ranked = sorted((r for r in out if r["rank"]), key=lambda r: r["rank"])
    # the first project before all others even at P3; then P1; then the deadline (own, then the parent's: K-4 unblocks one, so before K-7); must before nice
    assert [(r["project"], r["key"]) for r in ranked] == [("proj", "K-1"), ("alpha", "K-1"), ("alpha", "K-4"), ("alpha", "K-7"), ("alpha", "K-5")]
    assert [r["rank"] for r in out if not r.get("pick")] == [None, None, None]


def test_the_queue_files_come_from_the_layout_module(monkeypatch):
    seen = []
    real_q, real_p = C.store_layout.queue, C.store_layout.per_project
    monkeypatch.setattr(C.store_layout, "queue", lambda *a, **k: seen.append(("queue", a)) or real_q(*a, **k))
    monkeypatch.setattr(C.store_layout, "per_project", lambda *a, **k: seen.append(("per_project", a)) or real_p(*a, **k))
    assert C.queue_files(P) == real_p(C.DATA_DIR, "proj") and C.settings_path(P) == real_p(C.DATA_DIR, "proj")["settings"]
    migrated(C.QUEUE_ROOT)
    assert C.queue_files(P) == real_q(C.QUEUE_ROOT) and C.queue_dir(P) == C.QUEUE_ROOT
    assert {s[0] for s in seen} == {"queue", "per_project"}


def test_single_project_queue_has_no_listed_projects_and_no_all_view():
    q = C.get_queue(P)
    assert q["projects"] == [] and C.get_queue_all(P) is None


def test_queue_of_all_projects_reads_each_project_from_its_own_state(monkeypatch):
    migrated(C.QUEUE_ROOT)
    s = json.loads((C.QUEUE_ROOT / "dispatcher-settings.json").read_text())
    s["projects"] = LISTED + [{"short": "gone", "repo": "o/gone", "repo_dir": "/r/gone"}]
    (C.QUEUE_ROOT / "dispatcher-settings.json").write_text(json.dumps(s))
    served = {"proj": dict(name="proj", repo="o/proj"), "alpha": dict(name="alpha-cockpit", repo="O/Alpha")}
    states = {"proj": [t(1, prio=3)], "alpha-cockpit": [t(1, prio=1), t(2, "merged")]}
    asked = []
    monkeypatch.setattr(C, "PROJECTS", {q["name"]: q for q in served.values()})
    monkeypatch.setattr(C, "get_state", lambda fresh=False, p=None: asked.append(p["name"]) or dict(tasks=states[p["name"]], source=dict(fetchedAt=7)))
    monkeypatch.setattr(C, "live_state", lambda q, S, fresh=False: S)
    monkeypatch.setattr(C, "_served", lambda q, S: S)
    assert [p["short"] for p in C.get_queue(P)["projects"]] == ["proj", "alpha", "gone"]
    a = C.get_queue_all(P)
    assert asked == ["proj", "alpha-cockpit"]   # each listed project from its own repository's state; never the selected one's for all
    assert [(x["short"], x["name"], x["error"]) for x in a["projects"]] == [("proj", "proj", None), ("alpha", "alpha-cockpit", None), ("gone", None, "not served by this Cockpit")]
    assert [(r["project"], r["key"], r["rank"]) for r in a["tasks"]] == [("proj", "K-1", 1), ("alpha", "K-1", 2), ("alpha", "K-2", None)]


QA2 = "QA={projects:[{short:'proj',name:'proj',first:true},{short:'alpha',name:'alpha',first:false}],tasks:[" \
      "{key:'#1',number:1,title:'mine',project:'proj',stage:'released',pick:true,rank:1,cls:'nice',blockedBy:[],labels:[]}," \
      "{key:'A-2',number:2,title:'theirs',project:'alpha',stage:'released',pick:true,rank:2,cls:'must',prio:1,blockedBy:[],labels:[],url:'https://example.invalid/2'}," \
      "{key:'A-3',number:3,title:'done',project:'alpha',stage:'merged',blockedBy:[],labels:[]}]};"


@node
def test_page_all_projects_table_has_the_project_column_and_filter(run_node):
    out = run_node(QL2 + QA2 + "const h=vBacklog();UI.qtProj='alpha';const f=qAllRows().map(r=>r.key);UI.qtProj='';UI.filter='merged';const g=qAllRows().map(r=>r.key);"
                   "return [h.includes('<th>Project</th>'),h.includes('data-act=\"qtproj\" data-v=\"alpha\"'),h.includes('<b>all projects</b>'),"
                   "h.includes('data-act=\"qscope\" data-v=\"one\"'),h.includes('href=\"https://example.invalid/2\"'),h.includes('data-act=\"open\" data-k=\"#1\"'),f,g];")
    assert out == [True, True, True, True, True, True, ["A-2", "A-3"], ["A-3"]]


@node
def test_page_worker_line_names_its_project_only_with_several_projects(run_node):
    out = run_node(QL2 + "const a=vQueueLocal();QL.projects=[];const b=vQueueLocal();return [a.includes('title=\"Project\">alpha'),b.includes('title=\"Project\"')];")
    assert out == [True, False]


@node
def test_page_single_project_view_is_unchanged(run_node):
    R = json.dumps(L.log_rows(ev("proj-7", "failed", reason="no note")))
    out = run_node("QL={workers:[{worker:'worker1',task:'proj-7',project:'proj',launched:'',started:true}],log:[],projects:[],settings:{values:{enabled:true,workers:2,interval_minutes:5}},asOf:0};"
                   "QA={projects:[{short:'x'}],tasks:[]};const h=vBacklog(),l=vQlog(%s);"
                   "return [h.includes('data-scope'),h.includes('<th>Project</th>'),h.includes('<th>Module</th>'),l.includes('<th>Project</th>'),l.includes('name=\"project\"')];" % R)
    assert out == [False, False, True, False, False]


@node
@pytest.mark.ui_action("page:qscope", "page:qtproj")
def test_page_scope_switch_and_project_filter():
    from test_ui_actions_page import quiet, run
    out = quiet(run(QL2 + QA2 + "await __click({act:'qscope',v:'one'});const a=UI.qAll,h=vBacklog();await __click({act:'qscope',v:'all'});"
                    "await __click({act:'qtproj',v:'alpha'});const b=UI.qtProj;await __click({act:'qtproj',v:''});"
                    "return Object.assign(__out(),{v:[a,h.includes('<b>proj</b> only'),h.includes('data-act=\"qscope\" data-v=\"all\"'),UI.qAll,b,UI.qtProj]});"))
    assert out["v"] == [False, True, True, True, "alpha", ""]


@node
@pytest.mark.ui_action("form:qlog")
def test_page_filter_form_sets_the_project():
    from test_ui_actions_page import quiet, run
    out = quiet(run("await __submit('qlog',null,{project:'alpha',task:'',worker:'',n:'15'});return Object.assign(__out(),{v:UI.qlog.project});"))
    assert out["v"] == "alpha"
