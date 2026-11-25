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


@node
def test_page_shows_and_filters_the_project_column(run_node):
    R = json.dumps(L.log_rows("\n".join([ev("alpha-2", "launched", project="alpha", worker="worker1"), ev("proj-7", "failed", reason="no note")])))
    out = run_node("const R=%s;UI.qlog.beats=true;const h=vQlog(R);"
                   "return [h.includes('<th>Project</th>'),h.includes('<select name=\"project\">'),"
                   "qlogPick(R,Object.assign({},UI.qlog,{project:'alpha'})).map(r=>r.task)];" % R)
    assert out == [True, True, ["alpha-2"]]


@node
@pytest.mark.ui_action("form:qlog")
def test_page_filter_form_sets_the_project():
    from test_ui_actions_page import quiet, run
    out = quiet(run("await __submit('qlog',null,{project:'alpha',task:'',worker:'',n:'15'});return Object.assign(__out(),{v:UI.qlog.project});"))
    assert out["v"] == "alpha"
