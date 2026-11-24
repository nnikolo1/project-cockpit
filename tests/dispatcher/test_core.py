import datetime as dt
import json
import pathlib
import sys
from dataclasses import dataclass

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from dispatcher import core, runtime as rt  # noqa: E402

NOW = 1_000_000.0


@dataclass
class T:
    id: str
    priority: int = 2
    due: str = None
    blocked: bool = False
    unblocks: int = 0
    must: bool = False
    repo: str = "/repo"
    title: str = "title"
    text: str = "text"
    number: int = 1


class FakeTasks:
    def __init__(self, tasks=()):
        self.tasks, self.states, self.fetched, self.fail_fetch = list(tasks), [], [], False

    def released(self):
        return self.tasks

    def set_state(self, tid, state):
        self.states.append((tid, state))

    def fetch_branch(self, tid, path, branch):
        if self.fail_fetch:
            raise RuntimeError("bad bundle")
        self.fetched.append((tid, path, branch))


class FakeLog:
    def __init__(self):
        self.events, self.failures, self.beats = [], [], []

    def event(self, tid, folder, state, reason=None, worker=None):
        self.events.append((tid, state))
        self.workers = getattr(self, "workers", []) + ([(tid, worker)] if worker else [])

    def failure(self, tid, folder, reason):
        self.failures.append((tid, reason))

    def heartbeat(self, line):
        self.beats.append(line)

    def sleep(self, start, end, minutes):
        self.sleeps = getattr(self, "sleeps", []) + [(start, end, minutes)]


class FakeProc:
    pid = 4242


class FakeRuntime:
    def __init__(self, area):
        self.cfg = rt.Config(area=str(area), socket="/s")
        self.outbox = {}                 # task id -> (started, note, reason)
        self.launched, self.prepared = [], []
        self.egress, self.container_error, self.prepare_error = True, None, None
        self.alive_pids, self.killed = set(), []

    def alive(self, pid):
        return pid in self.alive_pids

    def kill(self, pid):
        self.killed.append(pid)

    def ensure_relay(self, relay_path):
        pass

    def ensure_container(self):
        if self.container_error:
            raise self.container_error

    def egress_blocked(self, user):
        return self.egress

    def prepare_task(self, tid, repo, text):
        if self.prepare_error:
            raise self.prepare_error
        self.prepared.append((tid, repo, text))
        return f"{self.cfg.area}/{tid}"

    def launch(self, user, tid):
        self.launched.append((user, tid))
        return FakeProc()

    def read_outbox(self, tid):
        return self.outbox.get(tid, (False, None, "no note"))

    def bundle_path(self, tid):
        return f"/b/{tid}"

    def stats(self):
        return None


class World:
    def __init__(self, tmp_path, tasks=(), **settings):
        self.now, self.free, self.last, self.mtime = NOW, 50 * core.GIB, {}, None
        self.ahead = 1
        self.detect_sleep = False        # the old tests jump the clock with interval 0; only the sleep tests turn it on
        self.runtime, self.tasks, self.plog = FakeRuntime(tmp_path), FakeTasks(tasks), FakeLog()
        self.state = str(tmp_path / "state.json")
        self.settings = dict(core.DEFAULTS, **{"interval_minutes": 0, **settings})

    @property
    def killed(self):
        return self.runtime.killed

    @property
    def alive_pids(self):
        return self.runtime.alive_pids

    @alive_pids.setter
    def alive_pids(self, v):
        self.runtime.alive_pids = v

    def dispatcher(self):
        p = core.Probes(
            now=lambda: self.now, free_disk=lambda: self.free, proxy_last=lambda: dict(self.last),
            transcript_mtime=lambda u: self.mtime, ensure_proxy=lambda: None,
            commits_ahead=lambda repo, tid: self.ahead, heartbeat_parts=lambda: {"memory_pressure": "normal"})
        d = core.Dispatcher(self.settings, self.state, self.runtime, self.tasks, self.plog, p)
        if not self.detect_sleep:
            d._detect_sleep = lambda st, now: None
        return d

    def fire(self):
        return self.dispatcher().fire()

    def worker(self, user="worker1"):
        return core.load_state(self.state)["workers"][user]


# --- settings ---
def test_settings_defaults_when_file_missing(tmp_path):
    assert core.load_settings(str(tmp_path / "none.json")) == core.DEFAULTS


def test_settings_keys_override_and_bad_values_fall_back(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"enabled": False, "workers": 2, "runaway_minutes": "x", "retries": 0, "idle_minutes": 0}))
    s = core.load_settings(str(p))
    assert s["enabled"] is False and s["workers"] == 2
    assert s["runaway_minutes"] == 40 and s["idle_minutes"] == 20


def test_settings_minimums_are_not_enforced_in_code(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"runaway_minutes": 5, "idle_minutes": 3, "start_minutes": 1}))
    s = core.load_settings(str(p))
    assert (s["runaway_minutes"], s["idle_minutes"], s["start_minutes"]) == (5, 3, 1)


def test_settings_garbage_file(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("[1]")
    assert core.load_settings(str(p)) == core.DEFAULTS
    p.write_text("{nope")
    assert core.load_settings(str(p)) == core.DEFAULTS


# --- ranking ---
def test_ranking_order():
    p1, p2, late = T("a", priority=1), T("b"), T("c", due="2027-01-02")
    early, many, must = T("d", due="2027-01-01"), T("e", unblocks=3), T("f", must=True)
    assert core.choose_task([p2, p1], set()).id == "a"
    assert core.choose_task([late, early, p2], set()).id == "d"      # a deadline beats none
    assert core.choose_task([p2, many], set()).id == "e"
    assert core.choose_task([p2, must], set()).id == "f"


def test_blocked_and_active_tasks_are_skipped():
    assert core.choose_task([T("a", blocked=True), T("b")], {"b"}) is None


# --- starting ---
def test_starts_one_worker_per_firing(tmp_path):
    w = World(tmp_path, [T("x-1"), T("x-2", priority=3)])
    assert w.fire() == "started worker1 on x-1"
    assert w.runtime.launched == [("worker1", "x-1")]
    assert w.runtime.prepared[0][2].startswith("# title")
    assert w.worker()["launched"] == NOW and ("x-1", "launched") in w.plog.events and w.plog.workers == [("x-1", "worker1")]
    assert "started worker1 on x-1" in w.plog.beats[0]


def test_second_firing_uses_next_worker_and_task(tmp_path):
    w = World(tmp_path, [T("x-1"), T("x-2")])
    w.fire()
    w.alive_pids = {4242}
    w.now += 30
    assert w.fire() == "started worker2 on x-2"


def test_no_free_worker_and_no_task(tmp_path):
    w = World(tmp_path, [T("x-1")], workers=1)
    w.fire()
    w.alive_pids = {4242}
    assert "all Workers busy" in w.fire()
    (tmp_path / "b").mkdir()
    assert "no released task" in World(tmp_path / "b").fire()


def test_switched_off_starts_nothing(tmp_path):
    w = World(tmp_path, [T("x-1")], enabled=False)
    assert "switched off" in w.fire()
    assert w.runtime.launched == []


def test_interval_skips_early_firing_and_keeps_stored_time(tmp_path):
    w = World(tmp_path, [T("x-1"), T("x-2")], interval_minutes=2)
    assert "started" in w.fire()
    w.now += 60
    assert "skipped: interval" in w.fire()
    assert len(w.runtime.launched) == 1
    assert "skipped: interval" in w.plog.beats[-1]
    w.now += 60            # 120 s after the first firing; the skipped one did not move the stored time
    assert "started" in w.fire()


def test_interval_tolerance_and_first_firing(tmp_path):
    w = World(tmp_path, [T("x-1"), T("x-2")], interval_minutes=2)
    assert "started" in w.fire()
    w.now += 90            # inside the 30 s tolerance
    assert "started" in w.fire()


def test_interval_bad_stored_time_never_skips(tmp_path):
    w = World(tmp_path, [T("x-1")], interval_minutes=2)
    (tmp_path / "state.json").write_text(json.dumps({"workers": {}, "last_fired": "x"}))
    assert "started" in w.fire()


def test_skip_low_disk(tmp_path):
    w = World(tmp_path, [T("x-1")])
    w.free = 5 * core.GIB
    assert w.fire().startswith("skipped: free disk below 10 GiB")
    assert w.runtime.launched == [] and "skipped" in w.plog.beats[0]


def test_skip_when_container_cannot_start_or_times_out(tmp_path):
    w = World(tmp_path, [T("x-1")])
    w.runtime.container_error = rt.ContainerError("boom")
    assert "cannot start proxy, container or relay" in w.fire()
    w.runtime.container_error = rt.ContainerTimeout("slow")
    assert "over its time limit" in w.fire()
    assert w.runtime.launched == []


def test_skip_when_proxy_cannot_start(tmp_path):
    w = World(tmp_path, [T("x-1")])
    d = w.dispatcher()
    d.probes.ensure_proxy = lambda: (_ for _ in ()).throw(core.Skip("proxy down"))
    assert d.fire() == "skipped: cannot start proxy, container or relay: proxy down"


def test_skip_when_egress_open(tmp_path):
    w = World(tmp_path, [T("x-1")])
    w.runtime.egress = False
    assert "egress check failed" in w.fire()
    assert w.runtime.launched == []


def test_preparation_failure_skips_the_firing_and_sets_no_state(tmp_path):
    w = World(tmp_path, [T("x-1")])
    w.runtime.prepare_error = FileExistsError("taken")
    assert w.fire().startswith("skipped: cannot prepare x-1")
    assert w.tasks.states == [] and w.plog.failures == [] and w.plog.events == []
    assert core.load_state(w.state)["workers"] == {}


def test_prepare_gets_the_host_clone_not_the_repository_name(tmp_path):
    w = World(tmp_path, [T("x-1", repo="owner/name")])
    d = w.dispatcher()
    d.repo_dir = "/host/clone"
    d.fire()
    assert w.runtime.prepared[0][1] == "/host/clone"
    assert w.worker()["repo"] == "/host/clone"


def test_task_with_an_existing_folder_is_not_selected_again(tmp_path, caplog):
    (tmp_path / "x-1").mkdir()
    w = World(tmp_path, [T("x-1", priority=1), T("x-2")])
    with caplog.at_level("INFO", logger="dispatcher.core"):
        assert w.fire() == "started worker1 on x-2"
    assert "x-1 skipped" in caplog.text
    assert w.tasks.states == [] and w.plog.failures == []


def test_only_task_with_an_existing_folder_means_nothing_to_do(tmp_path):
    (tmp_path / "x-1").mkdir()
    w = World(tmp_path, [T("x-1")])
    assert "no released task" in w.fire()


def test_launch_timeout_skips(tmp_path):
    w = World(tmp_path, [T("x-1")])
    w.runtime.launch = lambda u, t: (_ for _ in ()).throw(rt.ContainerTimeout("t"))
    assert "over its time limit" in w.fire()


def test_workers_setting_is_capped_at_image(tmp_path):
    w = World(tmp_path, [T(f"x-{i}") for i in range(10)], workers=99)
    for _ in range(8):
        w.alive_pids = {4242}
        w.fire()
    assert len(w.runtime.launched) == w.runtime.cfg.image_workers


# --- running Workers ---
def start_one(tmp_path, **kw):
    w = World(tmp_path, [T("x-1")], **kw)
    w.fire()
    w.alive_pids = {4242}
    w.tasks.tasks = []
    return w


def test_started_file_sets_wip(tmp_path):
    w = start_one(tmp_path)
    w.runtime.outbox["x-1"] = (True, None, "no note")
    w.now += 120
    w.fire()
    assert ("x-1", "wip") in w.tasks.states and w.worker()["started"]
    w.fire()
    assert w.tasks.states.count(("x-1", "wip")) == 1


def test_no_start_within_a_minute_kills(tmp_path):
    w = start_one(tmp_path)
    w.now += 59
    w.fire()
    assert w.killed == []
    w.now += 2
    w.fire()
    assert w.killed == [4242] and ("x-1", "no start") in w.plog.failures
    assert ("x-1", "failed") in w.tasks.states
    assert core.load_state(w.state)["workers"] == {}


def started(w):
    w.runtime.outbox["x-1"] = (True, None, "no note")
    w.now += 30
    w.last = {"worker1": w.now}
    w.fire()                                   # WIP, the proxy saw a request at NOW+30


def test_over_limit_kills(tmp_path):
    w = start_one(tmp_path)
    started(w)
    for _ in range(40):                        # keep it busy with requests
        w.now += 60
        w.last["worker1"] = w.now
        w.fire()
    assert w.killed == [4242] and ("x-1", "over 40 minutes") in w.plog.failures


def test_no_activity_kills_by_proxy_time(tmp_path):
    w = start_one(tmp_path)
    started(w)
    w.now += 20 * 60
    w.fire()
    assert w.killed == []                      # exactly at the limit
    w.now += 1
    w.fire()
    assert w.killed == [4242] and ("x-1", "no activity") in w.plog.failures


def test_transcript_mtime_is_the_fallback_record(tmp_path):
    w = start_one(tmp_path)
    w.runtime.outbox["x-1"] = (True, None, "no note")
    w.now += 30
    w.fire()                                   # the proxy has no request time
    w.now += 25 * 60
    w.mtime = w.now - 60                       # transcript touched a minute ago
    w.fire()
    assert w.killed == []
    w.mtime = w.now - 21 * 60
    w.fire()
    assert w.killed == [4242]


def test_limits_come_from_settings(tmp_path):
    w = start_one(tmp_path, runaway_minutes=5, idle_minutes=2)
    started(w)
    w.now += 3 * 60
    w.fire()
    assert ("x-1", "no activity") in w.plog.failures


def finish(w, note, ahead=1):
    w.runtime.outbox["x-1"] = (True, note, None if note else "no note")
    w.alive_pids = set()
    w.ahead = ahead
    w.now += 100
    w.fire()


def test_done_with_commit_is_unmerged_and_branch_fetched(tmp_path):
    w = start_one(tmp_path)
    finish(w, {"outcome": "done", "summary": "s", "tokens": 5})
    assert w.tasks.fetched == [("x-1", "/b/x-1", "worker/x-1")]
    assert ("x-1", "unmerged") in w.tasks.states and core.load_state(w.state)["workers"] == {}


def test_done_without_commit_fails(tmp_path):
    w = start_one(tmp_path)
    finish(w, {"outcome": "done", "summary": "s", "tokens": 5}, ahead=0)
    assert w.plog.failures == [("x-1", "no commit in the bundle")]


def test_gave_up_fails_even_with_a_commit_and_keeps_the_branch(tmp_path):
    w = start_one(tmp_path)
    finish(w, {"outcome": "gave_up", "summary": "s", "tokens": 5})
    assert w.plog.failures == [("x-1", "the Worker gave up")]
    assert ("x-1", "failed") in w.tasks.states and w.tasks.fetched == [("x-1", "/b/x-1", "worker/x-1")]


def test_no_note_and_unusable_note_fail_with_reason(tmp_path):
    w = start_one(tmp_path)
    finish(w, None)
    assert w.plog.failures == [("x-1", "no note")]
    (tmp_path / "2").mkdir()
    w2 = start_one(tmp_path / "2")
    w2.runtime.outbox["x-1"] = (True, None, "unusable note")
    w2.alive_pids = set()
    w2.fire()
    assert w2.plog.failures == [("x-1", "unusable note")]


def test_fetch_failure_fails_the_task_not_the_firing(tmp_path):
    w = start_one(tmp_path)
    w.tasks.fail_fetch = True
    finish(w, {"outcome": "done", "summary": "s", "tokens": 5})
    assert w.plog.failures == [("x-1", "no commit in the bundle")]


def test_workspace_is_not_torn_down(tmp_path):
    w = start_one(tmp_path)
    (tmp_path / "x-1").mkdir()
    finish(w, None)
    assert (tmp_path / "x-1").exists()


# --- heartbeat ---
def test_heartbeat_line_contents(tmp_path):
    w = start_one(tmp_path)
    d = w.dispatcher()
    d.probes.heartbeat_parts = lambda: {"memory_pressure": "warn", "free_disk": "40.0 GiB", "load": "1.00 2.00 3.00",
                                        "power": "AC", "data_size": "10 MiB", "container": "mem 1G/4G"}
    line = d.heartbeat(core.load_state(w.state), "nothing to do")
    for part in ("worker1=x-1", "nothing to do", "warn", "40.0 GiB", "1.00 2.00 3.00", "AC", "10 MiB", "mem 1G/4G"):
        assert part in line


def test_heartbeat_without_container_report(tmp_path):
    w = World(tmp_path)
    assert "not reported" in w.dispatcher().heartbeat({"workers": {}}, "nothing to do")


# --- host sleep (3i) ---
H = 3600


def sleepy(tmp_path, **settings):
    """A Worker started and 'started' written; 2-minute firings; sleep detection on."""
    w = World(tmp_path, [T("x-1")], interval_minutes=2, **settings)
    w.detect_sleep = True
    w.fire()
    w.runtime.outbox["x-1"] = (True, None, "no note")
    w.alive_pids = {4242}
    return w


def awake(w, seconds, record=False):
    """Real awake time: a firing every 2 minutes (optionally with a fresh proxy record each time)."""
    for _ in range(seconds // 120):
        w.now += 120
        if record:
            w.last = {"worker1": w.now}
        w.fire()


def test_awake_seconds_overlap_edges():
    a = core.awake_seconds
    assert a(0, 100, []) == 100
    assert a(0, 100, [(20, 50)]) == 70
    assert a(0, 100, [(-50, 30)]) == 70           # starts before the window
    assert a(0, 100, [(80, 500)]) == 80           # ends after the window
    assert a(0, 100, [(-5, 500)]) == 0            # covers the window
    assert a(0, 100, [(100, 200), (-9, 0)]) == 100  # touching at the edges only
    assert a(0, 100, [(10, 20), (30, 40)]) == 80  # several
    assert a(100, 0, []) == 0


def test_sleep_does_not_kill_a_young_worker(tmp_path):
    w = sleepy(tmp_path)
    w.now += 8 * H
    w.last = {"worker1": w.now - 8 * H + 60}      # its last record was just before the sleep
    w.fire()
    assert w.killed == [] and w.plog.sleeps[0][2] == 478
    assert w.worker()["task_id"] == "x-1"
    awake(w, 600)
    assert w.killed == []                         # the sleep stays excluded in later firings
    awake(w, 12 * 60)
    assert w.killed == [4242] and ("x-1", "no activity") in w.plog.failures


def test_real_41_awake_minutes_still_kills_as_over_limit(tmp_path):
    w = sleepy(tmp_path)
    w.now += 8 * H                                 # sleep
    w.fire()
    awake(w, 41 * 60, record=True)                 # then 41 awake minutes with records
    assert w.killed == [4242] and ("x-1", "over 40 minutes") in w.plog.failures


def test_no_record_for_21_awake_minutes_still_kills(tmp_path):
    w = sleepy(tmp_path)
    w.last = {"worker1": w.now}
    w.now += 8 * H
    w.fire()
    assert w.killed == []
    awake(w, 21 * 60)
    assert w.killed == [4242] and ("x-1", "no activity") in w.plog.failures


def test_no_record_for_9_hours_of_which_8_9_slept_is_not_killed(tmp_path):
    w = sleepy(tmp_path, runaway_minutes=1000)    # only the no-activity limit is under test
    w.last = {"worker1": w.now}
    w.now += int(8.9 * H)
    w.fire()
    awake(w, 6 * 60)                               # 6 awake minutes more
    assert w.killed == []


def test_sleep_does_not_count_towards_the_start_limit(tmp_path):
    w = World(tmp_path, [T("x-1")], interval_minutes=1)
    w.detect_sleep = True
    w.fire()
    w.alive_pids = {4242}
    w.now += 8 * H                                 # "started" is still missing; one interval of it was awake
    w.fire()
    assert w.killed == []
    w.now += 1
    w.fire()
    assert w.killed == [4242] and ("x-1", "no start") in w.plog.failures


def test_missing_or_bad_last_fired_is_never_a_sleep(tmp_path):
    w = World(tmp_path, interval_minutes=2)
    w.detect_sleep = True
    w.fire()                                       # first firing: no last_fired
    assert not hasattr(w.plog, "sleeps")
    st = core.load_state(w.state)
    st["last_fired"] = "garbage"
    core.save_state(w.state, st)
    w.now += 8 * H
    w.fire()
    assert not hasattr(w.plog, "sleeps") and "slept" not in w.plog.beats[-1]


def test_gap_threshold_and_state_pruning(tmp_path):
    w = World(tmp_path, interval_minutes=2, runaway_minutes=40)
    w.detect_sleep = True
    w.fire()
    w.now += 5 * 60                                # exactly 2 x interval + 1 minute: not a sleep
    w.fire()
    assert not hasattr(w.plog, "sleeps")
    w.now += 5 * 60 + 1
    w.fire()
    assert len(w.plog.sleeps) == 1
    w.now += 3 * H                                 # another sleep; the first lies beyond twice the runaway limit
    w.fire()
    assert core.load_state(w.state)["sleeps"] == [[w.now - 3 * H + 120, w.now]]


def test_sleep_event_and_heartbeat_text(tmp_path):
    w = World(tmp_path, interval_minutes=2)
    w.detect_sleep = True
    w.fire()
    w.now += 2 * H
    w.fire()
    assert w.plog.sleeps == [(NOW + 120, NOW + 2 * H, 118)]
    assert "slept 118 minutes" in w.plog.beats[-1]
    w.now += 120
    w.fire()
    assert "slept" not in w.plog.beats[-1]
    assert core.load_state(w.state)["sleeps"] == [[NOW + 120, NOW + 2 * H]]


def test_processing_log_sleep_event(tmp_path):
    from dispatcher.plog import ProcessingLog
    pl = ProcessingLog(str(tmp_path / "d"))
    pl.sleep(10.0, 20.0, 5)
    rec = json.loads(pl.path.read_text().splitlines()[-1])
    assert rec["event"] == "sleep" and rec["minutes"] == 5


# --- default probes ---
def test_read_proxy_last(tmp_path):
    f = tmp_path / "u.jsonl"
    f.write_text('{"ts":"2026-11-24T10:00:00+00:00","worker":"worker1"}\nbad\n{"worker":"worker1"}\n'
                 '{"ts":"2026-11-24T10:05:00+00:00","worker":"worker1"}\n{"ts":"2026-11-24T10:01:00+00:00","worker":"worker2"}\n'
                 '{"ts":"not a time","worker":"worker2"}\n')
    t = dt.datetime(2026, 11, 24, 10, 0, tzinfo=dt.timezone.utc).timestamp()
    assert core.read_proxy_last(str(f)) == {"worker1": t + 300, "worker2": t + 60}
    assert core.read_proxy_last(str(tmp_path / "none")) == {}


def test_main_builds_tasks_and_plog_from_arguments(tmp_path, monkeypatch):
    seen = {}

    class FakeDispatcher:
        def __init__(self, settings, state, runtime, tasks, plog, probes, relay_path, repo_dir):
            seen.update(tasks=tasks, plog=plog, repo_dir=repo_dir)

        def fire(self):
            return "ok"

    monkeypatch.setattr(core, "Dispatcher", FakeDispatcher)
    data = tmp_path / "data"
    core.main(["--settings", "s.json", "--state", str(tmp_path / "st"), "--area", str(tmp_path), "--socket", "/tmp/s",
               "--token-file", "t", "--usage-file", "u", "--repo", "me/repo", "--repo-dir", str(tmp_path),
               "--data-dir", str(data)])
    assert seen["tasks"].repo == "me/repo" and seen["tasks"].repo_dir == str(tmp_path) and seen["tasks"].short == "repo"
    assert seen["plog"].dir == data
    assert seen["repo_dir"] == str(tmp_path)


def test_gh_and_git_callables_run_with_a_time_limit(tmp_path):
    assert core.make_gh("echo", 5)(["hi"]).strip() == "hi"
    assert core.make_git("pwd", 5)([], str(tmp_path)).strip() == str(tmp_path.resolve())
    with pytest.raises(RuntimeError):
        core.make_gh("false", 5)([])


def test_state_roundtrip_and_missing(tmp_path):
    assert core.load_state(str(tmp_path / "nope")) == {"workers": {}}
    core.save_state(str(tmp_path / "s"), {"workers": {"worker1": {"a": 1}}})
    assert core.load_state(str(tmp_path / "s"))["workers"]["worker1"] == {"a": 1}




def _main_image(tmp_path, monkeypatch, extra):
    seen = {}

    class Fake:
        def __init__(self, settings, state, runtime, *a, **k):
            seen["image"] = runtime.cfg.image

        def fire(self):
            pass

    monkeypatch.setattr(core, "Dispatcher", Fake)
    core.main(["--settings", str(tmp_path / "s.json"), "--state", str(tmp_path / "st.json"), "--area", str(tmp_path),
               "--socket", str(tmp_path / "p.sock"), "--token-file", "t", "--usage-file", "u", "--repo", "o/r",
               "--repo-dir", str(tmp_path), "--data-dir", str(tmp_path / "data"), *extra])
    return seen["image"]


def test_main_image_default_and_override(tmp_path, monkeypatch):
    assert _main_image(tmp_path, monkeypatch, []) == "cockpit-worker:latest"
    assert _main_image(tmp_path, monkeypatch, ["--image", "cockpit-worker:stub"]) == "cockpit-worker:stub"


def test_transcript_mtime_reads_the_agents_glob():
    seen = []

    class Rt:
        def exec(self, user, *cmd):
            seen.append(cmd[-1])
            return 0, "12.5\n"

    class A:
        def activity_glob(self, user):
            return f"/g/{user}/*.log"

    assert core.transcript_mtime(Rt(), "w1", A()) == 12.5 and "/g/w1/*.log" in seen[0]
    core.transcript_mtime(Rt(), "w1")
    assert "/home/w1/.claude/projects/**/*.jsonl" in seen[1]
