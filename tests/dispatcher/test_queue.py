"""PC-67: one queue for several projects: the Dispatcher core, the runtime paths, the processing log and the settings.

The task adapter (tasks.ProjectsTasks) is written elsewhere; these tests use a fake of its agreed shape (docs/contracts/queue-interfaces.md).
"""
import json
import pathlib
import subprocess
import sys
from dataclasses import dataclass

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from dispatcher import core, plog, runtime as rt  # noqa: E402


def project_of(task_id):
    return task_id.rsplit("-", 1)[0]


@dataclass
class T:
    id: str
    priority: int = 2
    due: str = None
    blocked: bool = False
    unblocks: int = 0
    must: bool = False
    title: str = "title"
    text: str = "text"
    filed_by: str = None

    @property
    def project(self):
        return project_of(self.id)


class FakeProjectsTasks:
    """The shape of tasks.ProjectsTasks: released(), set_state, fetch_branch, reconcile_wip over all listed projects."""

    def __init__(self, tasks):
        self.tasks, self.states, self.fetched, self.reconciled = list(tasks), [], [], []

    def released(self):
        return self.tasks

    def set_state(self, tid, state):
        self.states.append((tid, state))

    def fetch_branch(self, tid, path, branch):
        self.fetched.append((tid, path, branch))

    def reconcile_wip(self, running):
        self.reconciled.append(set(running))
        return []


class QueueRuntime:
    """A runtime with the project-aware signatures of the contract."""

    def __init__(self, area):
        self.cfg = rt.Config(area=str(area), socket="/s")
        self.prepared, self.launched, self.outbox_calls, self.bundle_calls = [], [], [], []
        self.alive_pids = set()

    def task_dir(self, tid, project=None):
        return str(pathlib.Path(self.cfg.area) / project / tid)

    def ensure_container(self):
        pass

    def ensure_relay(self, p):
        pass

    def egress_blocked(self, user):
        return True

    def prepare_task(self, tid, repo, text, project=None):
        self.prepared.append((tid, repo, project))
        pathlib.Path(self.task_dir(tid, project)).mkdir(parents=True)   # as the real one: the folder marks the task as attempted (3m)
        return self.task_dir(tid, project)

    def launch(self, user, tid, agent=None, project=None):
        self.launched.append((user, tid, project))
        return type("P", (), {"pid": 100 + len(self.launched)})()

    def read_outbox(self, tid, project=None):
        self.outbox_calls.append((tid, project))
        return True, {"outcome": "done", "summary": "s"}, None

    def bundle_path(self, tid, project=None):
        self.bundle_calls.append((tid, project))
        return f"/b/{project}/{tid}"

    def alive(self, pid):
        return pid in self.alive_pids

    def kill(self, pid):
        pass

    def stats(self):
        return None


class RecLog:
    def __init__(self):
        self.events = []

    def event(self, tid, folder, state, reason=None, worker=None):
        self.events.append((tid, state))

    def failure(self, tid, folder, reason):
        self.events.append((tid, "failed:" + reason))

    def heartbeat(self, line):
        pass

    def sleep(self, *a):
        pass


PROJECTS = {"cockpit": "/clones/cockpit", "jcrawler": "/clones/jcrawler"}


def world(tmp_path, tasks, projects=PROJECTS, first=("cockpit",)):
    rtm, tk, lg = QueueRuntime(tmp_path), FakeProjectsTasks(tasks), RecLog()
    p = core.Probes(now=lambda: 1000.0, free_disk=lambda: 50 * core.GIB, proxy_last=lambda: {}, transcript_mtime=lambda u: None,
                    ensure_proxy=lambda: None, proxy_listening=lambda: True, start_proxy=lambda: 1, stop_proxy=lambda pid: True,
                    commits_ahead=lambda repo, tid: 1, heartbeat_parts=lambda: {})
    settings = dict(core.DEFAULTS, interval_minutes=0)
    d = core.Dispatcher(settings, str(tmp_path / "state.json"), rtm, tk, lg, p, projects=projects, first=first)
    d._detect_sleep = lambda st, now: None
    return d, rtm, tk, lg


def test_one_global_order_the_first_project_before_all_others(tmp_path):
    tasks = [T("jcrawler-1", priority=1), T("cockpit-9", priority=3), T("other-2", priority=1)]
    d, rtm, _, _ = world(tmp_path, tasks)
    d.fire()
    assert rtm.launched == [("worker1", "cockpit-9", "cockpit")]   # the cockpit first, even at a lower priority


def test_other_projects_are_equal_among_themselves_by_the_existing_rule(tmp_path):
    projects = dict(PROJECTS, third="/clones/third")
    tasks = [T("jcrawler-1", priority=2, due="2026-12-31"), T("third-5", priority=2, due="2026-12-01"), T("jcrawler-2", priority=1)]
    d, rtm, _, _ = world(tmp_path, tasks, projects=projects)
    for _ in range(3):
        d.fire()
    assert [t for _, t, _ in rtm.launched] == ["jcrawler-2", "third-5", "jcrawler-1"]   # no per-project cap, no per-project order


def test_the_bundle_comes_from_the_tasks_own_project_clone_and_the_state_keeps_the_project(tmp_path):
    d, rtm, _, lg = world(tmp_path, [T("jcrawler-1")])
    d.fire()
    assert rtm.prepared == [("jcrawler-1", "/clones/jcrawler", "jcrawler")]
    w = core.load_state(d.state_path)["workers"]["worker1"]
    assert w["project"] == "jcrawler" and w["repo"] == "/clones/jcrawler" and w["folder"] == str(tmp_path / "jcrawler" / "jcrawler-1")


def test_a_project_not_listed_is_never_touched(tmp_path):
    d, rtm, tk, lg = world(tmp_path, [T("stranger-1", priority=1), T("cockpit-2")], projects={"cockpit": "/clones/cockpit"})
    d.fire()
    d.fire()
    assert [t for _, t, _ in rtm.launched] == ["cockpit-2"]
    assert all(not tid.startswith("stranger") for tid, *_ in rtm.prepared)
    assert d.fire() == "nothing to do (no released task)"


def test_an_existing_task_folder_per_project_blocks_reselection(tmp_path):
    (tmp_path / "cockpit" / "cockpit-1").mkdir(parents=True)
    d, rtm, _, _ = world(tmp_path, [T("cockpit-1"), T("jcrawler-1")])
    d.fire()
    assert [t for _, t, _ in rtm.launched] == ["jcrawler-1"]


def test_results_and_bundles_are_read_in_the_project_folder(tmp_path):
    d, rtm, tk, lg = world(tmp_path, [T("jcrawler-1")])
    d.fire()                                            # launches
    d.fire()                                            # pid not alive: finished
    assert ("jcrawler-1", "jcrawler") in rtm.outbox_calls and ("jcrawler-1", "jcrawler") in rtm.bundle_calls
    assert tk.fetched == [("jcrawler-1", "/b/jcrawler/jcrawler-1", "worker/jcrawler-1")]
    assert ("jcrawler-1", "unmerged") in tk.states


def test_wip_reconciliation_runs_through_the_one_adapter_with_all_running_ids(tmp_path):
    d, rtm, tk, _ = world(tmp_path, [T("jcrawler-1")])
    d.fire()
    rtm.alive_pids = {101}
    d.fire()
    assert tk.reconciled[-1] == {"jcrawler-1"}


def test_failed_launch_discards_the_folder_of_the_right_project(tmp_path):
    d, rtm, _, _ = world(tmp_path, [T("jcrawler-1")])
    discarded = []
    rtm.discard_task = lambda tid, project=None: discarded.append((tid, project))
    rtm.launch = lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    assert d.fire().startswith("skipped")
    assert discarded == [("jcrawler-1", "jcrawler")]


# --- the single default project is unchanged ---
def test_without_projects_nothing_new_is_passed_to_the_runtime(tmp_path):
    class Old(QueueRuntime):
        def prepare_task(self, tid, repo, text):
            self.prepared.append((tid, repo))
            return str(tmp_path / tid)

        def launch(self, user, tid):
            self.launched.append((user, tid))
            return type("P", (), {"pid": 5})()

        def read_outbox(self, tid):
            return False, None, "no note"

    d, rtm, tk, lg = world(tmp_path, [T("cockpit-1")], projects=None, first=())
    rtm2 = d.runtime = Old(tmp_path)
    d.repo_dir = "/the/repo"
    d.fire()
    assert rtm2.prepared == [("cockpit-1", "/the/repo")] and rtm2.launched == [("worker1", "cockpit-1")]
    assert "project" not in core.load_state(d.state_path)["workers"]["worker1"]


# --- settings ---
def test_settings_projects_valid_and_default(tmp_path):
    f = tmp_path / "s.json"
    assert "projects" not in core.load_settings(str(f))          # no file: exactly today's defaults
    lst = [{"short": "cockpit", "repo": "o/cockpit", "repo_dir": "/c", "first": True}, {"short": "jc", "repo": "o/jc", "repo_dir": "/j"}]
    f.write_text(json.dumps({"projects": lst}))
    assert core.load_settings(str(f))["projects"] == lst


@pytest.mark.parametrize("bad", [[], "x", [{"short": "a"}], [{"short": "a", "repo": "o/a", "repo_dir": "/a"}] * 2,
                                 [{"short": "a", "repo": "o/a", "repo_dir": "/a", "first": "yes"}], [1]])
def test_settings_projects_unusable_falls_back_to_the_command_line_project(tmp_path, bad):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"projects": bad, "workers": 2}))
    s = core.load_settings(str(f))
    assert "projects" not in s and s["workers"] == 2


def test_build_tasks_one_project_keeps_github_tasks_and_two_use_the_projects_adapter(monkeypatch):
    from dispatcher import tasks as T_
    built = {}

    class Fake:
        def __init__(self, projects, gh, git, first=(), manager=None, now=None):
            built.update(projects=projects, first=first)

    monkeypatch.setattr(T_, "ProjectsTasks", Fake, raising=False)
    t, projects, first = core.build_tasks({}, "o/r", "/r", None, None)
    assert isinstance(t, T_.GitHubTasks) and projects is None and first == ()
    one = {"projects": [{"short": "x", "repo": "o/x", "repo_dir": "/x"}]}
    t, projects, _ = core.build_tasks(one, "o/r", "/r", None, None)
    assert isinstance(t, T_.GitHubTasks) and t.repo == "o/x" and projects is None
    two = {"projects": [{"short": "cockpit", "repo": "o/c", "repo_dir": "/c", "first": True}, {"short": "jc", "repo": "o/jc", "repo_dir": "/j"}]}
    t, projects, first = core.build_tasks(two, "o/r", "/r", None, None)
    assert isinstance(t, Fake) and projects == {"cockpit": "/c", "jc": "/j"} and first == ("cockpit",)
    assert built["projects"] == [{"short": "cockpit", "repo": "o/c", "repo_dir": "/c"}, {"short": "jc", "repo": "o/jc", "repo_dir": "/j"}]


def test_main_with_a_data_root_takes_its_paths_from_the_root(tmp_path, monkeypatch):
    seen = {}

    class FakeDispatcher:
        def __init__(self, settings, state, runtime, tasks, plog, probes, relay_path, repo_dir, **kw):
            seen.update(state=state, area=runtime.cfg.area, socket=runtime.cfg.socket, plog=plog, tasks=tasks, kw=kw)

        def fire(self):
            return "ok"

    root = tmp_path / "root"
    root.mkdir()
    (root / "dispatcher-settings.json").write_text(json.dumps({"projects": [
        {"short": "cockpit", "repo": "o/c", "repo_dir": "/c", "first": True}, {"short": "jc", "repo": "o/jc", "repo_dir": "/j"}]}))
    from dispatcher import tasks as T_
    monkeypatch.setattr(core, "Dispatcher", FakeDispatcher)
    monkeypatch.setattr(T_, "ProjectsTasks", lambda projects, gh, git, first=(), manager=None, now=None: ("adapter", projects, first), raising=False)
    monkeypatch.setattr(T_, "project_of", project_of, raising=False)
    core.main(["--data-root", str(root), "--token-file", "t"])
    assert seen["state"] == str(root / "dispatcher-state.json") and seen["area"] == str(root / "area") and seen["socket"] == str(root / "proxy.sock")
    assert seen["plog"].dir == root / "processing" and seen["plog"].project_of is project_of
    assert seen["kw"]["projects"] == {"cockpit": "/c", "jc": "/j"} and seen["kw"]["first"] == ("cockpit",)
    assert seen["tasks"][0] == "adapter"


def test_main_without_data_root_still_requires_the_flags():
    with pytest.raises(SystemExit):
        core.main(["--token-file", "t", "--repo", "o/r", "--repo-dir", "/r"])


# --- the runtime ---
def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def clone(tmp_path):
    d = tmp_path / "clone"
    d.mkdir()
    _git("init", "-b", "main", cwd=d)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "c", cwd=d)
    return str(d)


def _runtime(tmp_path):
    calls = []
    return rt.Runtime(rt.Config(area=str(tmp_path / "area"), socket="/tmp/p.sock"), run=lambda a, t: (calls.append(a) or (0, "")),
                      spawn=lambda cmd, **k: ("spawned", cmd)), calls


def test_task_folders_per_project_and_none_is_todays_layout(tmp_path, clone):
    r, _ = _runtime(tmp_path)
    assert r.task_dir("a-1") == str(tmp_path / "area" / "a-1")
    base = pathlib.Path(r.prepare_task("cockpit-1", clone, "txt", project="cockpit"))
    assert base == tmp_path / "area" / "cockpit" / "cockpit-1" and (base / "inbox" / "main.bundle").stat().st_size > 0
    assert r.bundle_path("cockpit-1", "cockpit") == str(base / "outbox" / "result.bundle")
    assert r.read_outbox("cockpit-1", "cockpit") == (False, None, "no note")
    r.discard_task("cockpit-1", "cockpit")
    assert not base.exists() and (tmp_path / "area" / "cockpit").is_dir()


@pytest.mark.parametrize("bad", ["../x", "a/b", "", ".h"])
def test_project_names_are_checked(tmp_path, bad):
    r, _ = _runtime(tmp_path)
    with pytest.raises(ValueError):
        r.task_dir("a-1", bad)


def test_launch_with_a_project_uses_the_project_paths_in_the_container(tmp_path):
    r, calls = _runtime(tmp_path)
    kind, cmd = r.launch("worker1", "jc-5", project="jc")
    line = " ".join(cmd)
    assert "/area/jc/jc-5/inbox/task.md" in line and "/area/jc/jc-5/outbox/started" in line and "WORKER_OUTBOX=/area/jc/jc-5/outbox" in line
    assert cmd[cmd.index(rt.PRIVATE_AREA_PROJECT) + 1:cmd.index(rt.PRIVATE_AREA_PROJECT) + 4] == ["sh", "jc-5", "jc"]
    assert any("/area/jc/jc-5/inbox/main.bundle" in " ".join(c) for c in calls)
    assert (tmp_path / "area" / "jc" / "jc-5" / "worker.log").exists()
    _, old = r.launch("worker1", "T-5")
    assert rt.PRIVATE_AREA in old and rt.PRIVATE_AREA_PROJECT not in old and "/area/T-5/inbox/task.md" in " ".join(old)


def test_private_area_script_for_a_project_isolates_one_task_folder(tmp_path):
    assert rt.PRIVATE_AREA_PROJECT.count("/area/$2/$1") == 3 and rt.PRIVATE_AREA_PROJECT.endswith('shift 2 && exec "$@"')


# --- the processing log ---
def test_log_records_carry_the_project_when_the_queue_serves_several(tmp_path):
    lg = plog.ProcessingLog(tmp_path / "d", project_of=project_of)
    lg.event("jcrawler-3", "/f", "launched", worker="worker1")
    lg.failure("cockpit-4", "/g", "no start")
    lg.event("-", "-", "proxy started")
    recs = [json.loads(x) for x in (tmp_path / "d" / "processing.jsonl").read_text().splitlines()]
    assert [r.get("project") for r in recs] == ["jcrawler", "cockpit", None]


def test_log_without_project_of_is_unchanged(tmp_path):
    lg = plog.ProcessingLog(tmp_path / "d")
    lg.event("T-1", "/f", "wip")
    assert "project" not in json.loads((tmp_path / "d" / "processing.jsonl").read_text())


# --- the LaunchAgent job of the queue ---
def test_queue_job_renders_one_job_for_all_projects_and_the_project_job_is_unchanged():
    import plistlib
    from dispatcher import launchd as L
    d = plistlib.loads(plistlib.dumps(L.dispatcher_plist(None, None, None, "/tok", queue_root="/root", cockpit_dir="/cp")))
    assert d["Label"] == "com.project-cockpit.dispatcher.queue"
    a = d["ProgramArguments"]
    assert a[a.index("--data-root") + 1] == "/root" and a[a.index("--token-file") + 1] == "/tok"
    assert "--repo" not in a and "--repo-dir" not in a and "--settings" not in a
    q = L.quota_plist(None, queue_root="/root", cockpit_dir="/cp")
    assert q["Label"] == "com.project-cockpit.quota.queue" and q["ProgramArguments"][-1] == "/root/quota-history.jsonl"
    old = L.dispatcher_plist("demo", "o/n", "/r", "/t", data_dir="/data", cockpit_dir="/cp")
    assert old["Label"] == "com.project-cockpit.dispatcher.demo" and "--repo" in old["ProgramArguments"]


def test_queue_paths_match_the_shared_root_contract():
    from dispatcher import launchd as L
    p = L.queue_paths("/root")
    assert str(p["state"]) == "/root/dispatcher-state.json" and str(p["area"]) == "/root/area" and str(p["socket"]) == "/root/proxy.sock"
    assert str(p["usage"]) == "/root/proxy-usage.jsonl" and str(p["processing"]) == "/root/processing"


def test_render_cli_queue_writes_two_files(capsys):
    import tempfile
    from dispatcher import launchd as L
    root = pathlib.Path(tempfile.mkdtemp(dir="/tmp", prefix="q"))   # short: the proxy socket path must stay under the 104-byte limit
    L.render(["--queue", "--data-root", str(root), "--token-file", str(root / "tok"), "--python", sys.executable,
              "--cockpit-dir", str(root / "releases" / "cockpit" / "current")])   # production starts from a release, not this clone
    out = capsys.readouterr().out
    assert sorted(f.name for f in (root / "launchd").iterdir()) == ["com.project-cockpit.dispatcher.queue.plist", "com.project-cockpit.quota.queue.plist"]
    assert "dispatcher.queue.plist" in out
    with pytest.raises(SystemExit):
        L.render(["--token-file", "t", "--python", sys.executable])   # neither --queue nor a project
