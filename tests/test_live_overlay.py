"""PC-119: the cached task table with the Dispatcher's newer facts laid over it, the refresh a newer log event asks for, and "Next up"
by the Dispatcher's own rule. Synthetic cache and log data only; no network."""
import datetime as dt
import json
import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest
import cockpit as C
import logic as L

NOW = dt.datetime(2026, 11, 25, 7, 0, tzinfo=dt.timezone.utc)
FETCHED = NOW.timestamp() - 3600          # the cache's "data from"
SHORTS = {"proj"}


def at(epoch):
    """A processing-log time: local, naive, like dispatcher.plog writes it."""
    return dt.datetime.fromtimestamp(epoch).isoformat(timespec="seconds")


def plog(*recs):
    return "\n".join(json.dumps(dict(time=at(t), task=task, state=state)) for t, task, state in recs) + "\n"


def task(n, stage="released", labels=(), blocked=(), type_="task", created="2026-11-01T00:00:00Z", prio=2):
    return dict(key=f"PJ-{n}", number=n, title=f"t{n}", stage=stage, labels=list(labels), blockedBy=list(blocked), type=type_,
                created=created, prio=prio, running=stage == "wip")


# --- the overlay ---
def test_newer_log_event_overrides_the_cached_stage_and_is_marked():
    tasks = [task(1, "wip", ["wip"]), task(2, "wip", ["wip"])]
    ev = L.live_events(plog((FETCHED + 60, "proj-1", "unmerged"), (FETCHED - 60, "proj-2", "failed")), SHORTS)
    out = L.overlay_live(tasks, FETCHED, ev, held={2}, state_known=True)
    assert out[0]["stage"] == "unmerged" and out[0]["live"] == dict(at=pytest.approx(FETCHED + 60), was="wip") and not out[0]["running"]
    assert out[1]["stage"] == "wip" and "live" not in out[1] and "noWorker" not in out[1]   # older than the cache: the cache stands
    assert tasks[0]["stage"] == "wip" and "live" not in tasks[0]                             # the cached copy is not changed


def test_newest_event_per_task_wins_and_other_projects_and_entries_are_ignored():
    text = plog((FETCHED + 10, "proj-1", "launched"), (FETCHED + 20, "proj-1", "wip"), (FETCHED + 30, "proj-1", "failed"),
                (FETCHED + 40, "other-1", "unmerged"), (FETCHED + 50, "-", "proxy started"),
                (FETCHED + 60, "proj-3", "wip with no Worker -> unmerged")) + "not json\n"
    ev = L.live_events(text, SHORTS)
    assert {n: s for n, (_, s) in ev.items()} == {1: "failed", 3: "unmerged"}
    assert L.live_events(None, SHORTS) == {}


def test_final_stages_stay():
    ev = {1: (FETCHED + 60, "failed")}
    assert L.overlay_live([task(1, "merged")], FETCHED, ev, set(), True)[0]["stage"] == "merged"


def test_cached_wip_without_a_running_worker_is_marked():
    tasks = [task(1, "wip", ["wip"]), task(2, "wip", ["wip"]), task(3, "released")]
    out = L.overlay_live(tasks, FETCHED, {}, held={2}, state_known=True)
    assert out[0].get("noWorker") and not out[1].get("noWorker") and not out[2].get("noWorker")
    ev = {3: (FETCHED + 5, "wip")}   # launched after the cache and held: WIP, live, no marker
    out = L.overlay_live(tasks, FETCHED, ev, held={2, 3}, state_known=True)
    assert out[2]["stage"] == "wip" and out[2]["live"] and not out[2].get("noWorker")
    assert not any(t.get("noWorker") for t in L.overlay_live(tasks, FETCHED, {}, set(), state_known=False))   # no state file: cannot tell


# --- the refresh ---
def test_refresh_only_when_the_log_is_newer_and_not_more_often_than_every_two_minutes():
    now = FETCHED + 600
    newer, older = {1: (FETCHED + 60, "unmerged")}, {1: (FETCHED - 60, "unmerged")}
    assert L.live_refresh_due(newer, FETCHED, 0, now, True)
    assert not L.live_refresh_due(older, FETCHED, 0, now, True)
    assert not L.live_refresh_due({}, FETCHED, 0, now, True)
    assert not L.live_refresh_due(newer, FETCHED, now - L.LIVE_REFRESH_S + 1, now, True)
    assert L.live_refresh_due(newer, FETCHED, now - L.LIVE_REFRESH_S, now, True)
    assert not L.live_refresh_due(newer, FETCHED, 0, now, False)   # paused or over the hourly cap
    assert L.LIVE_REFRESH_S == 120


@pytest.fixture
def cockpit(tmp_path, monkeypatch):
    C._cache.clear(), C._failures.clear(), C._gh_calls.clear(), C._live_try.clear()
    C._gh_pause.update(until=0.0)
    monkeypatch.setattr(C, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(C, "DATA_DIR", tmp_path / "data")
    d = tmp_path / "data" / "proj"
    (d / "processing").mkdir(parents=True)
    yield d
    C._cache.clear()


def test_served_state_refreshes_once_on_a_newer_event_and_respects_pause(cockpit, monkeypatch):
    p = dict(name="proj", repo="o/proj", manager="m")
    now = C.time.time()
    old = dict(tasks=[task(1, "wip", ["wip"])], source=dict(fetchedAt=now - 1800))
    C._cache[C.pk(p, "state")] = (now - 1800, old)
    builds, done = [], threading.Event()
    monkeypatch.setattr(C, "build_state", lambda q: (builds.append(1), done.set(), old)[2])
    (cockpit / "processing" / "processing.jsonl").write_text(plog((now - 60, "proj-1", "unmerged")))
    (cockpit / "dispatcher-state.json").write_text(json.dumps({"workers": {}}))
    C._gh_pause.update(until=now + 600)              # a rate-limit pause: no refresh
    S = C.live_state(p, old)
    assert not builds and S["tasks"][0]["stage"] == "unmerged" and S["tasks"][0]["live"]
    assert S["openCounts"]["all"] == dict(n=1, tip="1 unmerged")   # PC-128: the module chips count the overlaid stage
    C._gh_pause.update(until=0.0)
    C.live_state(p, old)
    assert done.wait(5) and len(builds) == 1
    C._cache[C.pk(p, "state")] = (0, old)            # still older than the event, but within two minutes of the last try
    C.live_state(p, old)
    assert C._live_try[C.pk(p, "state")] <= C.time.time() and len(builds) == 1


# --- Next up: the Dispatcher's own rule ---
def test_next_up_skips_question_decision_blocked_and_held_tasks():
    tasks = [task(1), task(3, labels=["question"]), task(4, labels=["type:decision"]),
             task(5, type_="decision"), task(6, blocked=["PJ-9"]), task(9, "wip", ["wip"]), task(7, blocked=["PJ-8"]), task(8, "merged"),
             task(10, "embryo", ["author:job"]), task(11, "embryo", ["author:owner"]), task(12)]
    assert L.pickable(tasks, NOW, held={12}) == {"PJ-1", "PJ-7", "PJ-11"}


def test_dispatcher_uses_the_same_rule(monkeypatch):
    from dispatcher import tasks as T
    seen = []
    real = L.dispatchable
    monkeypatch.setattr(L, "dispatchable", lambda t, now: (seen.append(t["number"]), real(t, now))[1])
    gh = lambda args: json.dumps({"data": {"repository": {"issues": {"pageInfo": {"hasNextPage": False}, "nodes": [
        {"number": 1, "databaseId": 1, "title": "x", "url": "u", "body": "", "state": "OPEN", "stateReason": None, "createdAt": "2026-11-01T00:00:00Z",
         "updatedAt": None, "closedAt": None, "author": {"login": "own"}, "labels": {"nodes": [{"name": "question"}]},
         "assignees": {"nodes": []}, "parent": None, "blockedBy": {"nodes": []}, "comments": {"nodes": []}, "timelineItems": {"nodes": []}}]}}}})
    assert T.GitHubTasks("own/proj", gh, lambda a, c: "", "/r", now=lambda: NOW).released() == [] and seen == [1]
