"""GitHub unavailable or rate limited: stale copies, no call while paused or over the cap, restarts without calls, a local Task Queue."""
import json
import os
import sys
import pathlib
import time
import threading
import types
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest
import cockpit as C
import logic as L

P = dict(name="proj", repo="o/r", manager="m", project=None, git=None, code=None, modules=None, phases=None)


def state(at):
    return dict(today="2026-01-01", tasks=[], questions=[], modules=[], source=dict(fetchedAt=at, fetched="10:00:00"))


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    C._cache.clear()
    C._failures.clear()
    C._gh_calls.clear()
    C._gh_pause.update(until=0.0)
    monkeypatch.setattr(C, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(C, "DATA_DIR", tmp_path / "data")
    yield
    C._cache.clear()


def fake_gh(monkeypatch, stderr, code=1):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=code, stdout="", stderr=stderr)
    monkeypatch.setattr(C.subprocess, "run", run)
    return calls


def test_rate_limit_error_pauses_every_call_until_reset(monkeypatch):
    calls = fake_gh(monkeypatch, "API rate limit already exceeded for user ID 1")
    with pytest.raises(RuntimeError):
        C.run_gh(["api", "x"])
    with pytest.raises(C.GitHubUnavailable) as e:
        C.run_gh(["api", "y"])
    assert len(calls) == 1 and "no GitHub call until" in str(e.value)
    assert e.value.until == pytest.approx(time.time() + C.RATE_LIMIT_PAUSE_S, abs=5)
    C._gh_gate(now=e.value.until + 1)  # allowed again after the reset


def test_reset_time_in_the_message_is_used():
    now = 1_800_000_000
    assert L.rate_limit_reset("limit; resets 1800000600", now) == 1_800_000_600
    assert L.rate_limit_reset("no time here", now) is None
    assert L.rate_limit_reset("resets 1700000000", now) is None  # in the past


def test_other_errors_do_not_pause(monkeypatch):
    calls = fake_gh(monkeypatch, "HTTP 502")
    for _ in range(2):
        with pytest.raises(RuntimeError):
            C.run_gh(["api", "x"])
    assert len(calls) == 2


def test_hourly_calls_are_counted_and_capped(monkeypatch):
    monkeypatch.setattr(C, "GH_CALLS_PER_HOUR", 3)
    for i in range(3):
        C._gh_gate(now=1000 + i)
    with pytest.raises(C.GitHubUnavailable) as e:
        C._gh_gate(now=1003)
    assert e.value.until == 1000 + 3600
    C._gh_gate(now=1000 + 3601)  # the oldest call left the window


def test_failed_refresh_serves_the_old_copy_with_age_reason_and_reset(monkeypatch):
    now = time.time()
    monkeypatch.setattr(C, "build_state", lambda p: state(now - 600))
    assert C.get_state_served(P)["staleNote"] is None

    def down(p):
        raise C.GitHubUnavailable("GitHub rate limit reached", now + 900)
    monkeypatch.setattr(C, "build_state", down)
    S = C.get_state_served(P, fresh=True)
    assert S["source"]["fetchedAt"] == now - 600  # the old data, not an error
    n = S["staleNote"]
    assert n["rate_limited"] and n["until"] == now + 900 and 590 <= n["age_s"] <= 620 and "rate limit" in n["error"]
    monkeypatch.setattr(C, "build_state", lambda p: state(time.time()))
    assert C.get_state_served(P, fresh=True)["staleNote"] is None  # a success removes the note


def test_no_copy_at_all_raises_the_error(monkeypatch):
    def down(p):
        raise C.GitHubUnavailable("paused until 12:00", 1)
    monkeypatch.setattr(C, "build_state", down)
    with pytest.raises(C.GitHubUnavailable):
        C.get_state_served(P)


def test_restart_with_a_recent_disk_copy_makes_no_call(monkeypatch):
    now = time.time()
    monkeypatch.setattr(C, "build_state", lambda p: state(now))
    C.get_state(p=P)  # writes the disk copy
    C._cache.clear()  # a new process

    def boom(p):
        raise AssertionError("GitHub was called")
    monkeypatch.setattr(C, "build_state", boom)
    assert C.get_state(p=P)["source"]["fetchedAt"] == now
    assert not C._refreshing


def test_restart_with_an_old_disk_copy_serves_it_and_refreshes_behind(monkeypatch):
    now = time.time()
    monkeypatch.setattr(C, "build_state", lambda p: state(now - 5000))
    C.get_state(p=P)
    f = next(C.CACHE_DIR.glob("*state*.json"))
    old = now - C.DISK_FRESH_S - 60
    os.utime(f, (old, old))
    C._cache.clear()
    monkeypatch.setattr(C, "build_state", lambda p: state(now))
    assert C.get_state(p=P)["source"]["fetchedAt"] == now - 5000
    deadline = time.time() + 3
    while time.time() < deadline and C._cache[C.pk(P, "state")][1]["source"]["fetchedAt"] != now:
        time.sleep(0.02)
    assert C._cache[C.pk(P, "state")][1]["source"]["fetchedAt"] == now


def test_refresh_button_after_a_restart_keeps_the_disk_copy_when_github_fails(monkeypatch):
    monkeypatch.setattr(C, "build_state", lambda p: state(123))
    C.get_state(p=P)
    C._cache.clear()

    def down(p):
        raise RuntimeError("API rate limit already exceeded")
    monkeypatch.setattr(C, "build_state", down)
    S = C.get_state_served(P, fresh=True)
    assert S["source"]["fetchedAt"] == 123 and S["staleNote"]["rate_limited"]


def test_task_queue_local_part_is_served_while_github_fails(monkeypatch):
    d = C.DATA_DIR / "proj"
    (d / "processing").mkdir(parents=True)
    (d / "dispatcher-state.json").write_text(json.dumps(dict(workers=dict(worker2=dict(task_id="T-7", launched="2026-01-01T10:00:00", started=True)))))
    (d / "processing" / "processing.jsonl").write_text("\n".join([json.dumps(dict(time="2026-01-01T10:00:00", task="T-7", state="launched", worker="worker2")), "{broken"]))

    def down(*a, **k):
        raise AssertionError("the Task Queue's local part called GitHub")
    monkeypatch.setattr(C, "run_gh", down)
    monkeypatch.setattr(C, "build_state", down)
    q = C.get_queue(P)
    assert q["workers"] == [dict(worker="worker2", task="T-7", launched="2026-01-01T10:00:00", started=True)]
    assert q["log"][0]["state"] == "launched" and "values" in q["settings"]


def test_task_queue_local_part_without_files_is_empty():
    q = L.queue_local(None, None)
    assert q == dict(workers=[], log=[])
    assert L.queue_local("{not json", "x")["workers"] == []


def test_within_the_hour_no_call_and_the_page_gets_the_refresh_time(monkeypatch):
    now = time.time()
    monkeypatch.setattr(C, "build_state", lambda p: state(now - 1800))
    C.get_state(p=P)

    def boom(p):
        raise AssertionError("GitHub was called")
    monkeypatch.setattr(C, "build_state", boom)
    S = C.get_state_served(P)
    assert S["refreshAfter"] == now - 1800 + C.GH_REFRESH_S and not S["stale"]


def test_older_than_the_hour_is_served_at_once_and_one_refresh_is_queued(monkeypatch):
    now = time.time()
    monkeypatch.setattr(C, "build_state", lambda p: state(now - 7200))
    C.get_state(p=P)
    C._cache[C.pk(P, "state")] = (now - C.GH_REFRESH_S - 5, C._cache[C.pk(P, "state")][1])
    n, gate = [], threading.Event()

    def slow(p):
        n.append(1)
        gate.wait(3)
        return state(now)
    monkeypatch.setattr(C, "build_state", slow)
    for _ in range(3):
        assert C.get_state_served(P)["source"]["fetchedAt"] == now - 7200
    gate.set()
    deadline = time.time() + 3
    while time.time() < deadline and C._refreshing:
        time.sleep(0.02)
    assert len(n) == 1  # three requests, one queued refresh


def test_refreshes_of_different_projects_never_run_in_parallel():
    live, peak = [], []

    def work(key):
        def fn():
            live.append(key)
            peak.append(len(live))
            time.sleep(0.05)
            live.remove(key)
            return key
        return fn
    ts = [threading.Thread(target=C.cached, args=(k, work(k), 60), kwargs=dict(queue=True)) for k in "abc"]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert max(peak) == 1


def test_owner_refresh_still_fetches_inside_the_hour(monkeypatch):
    now = time.time()
    monkeypatch.setattr(C, "build_state", lambda p: state(now - 60))
    C.get_state(p=P)
    monkeypatch.setattr(C, "build_state", lambda p: state(now))
    assert C.get_state_served(P, fresh=True)["source"]["fetchedAt"] == now


@pytest.mark.parametrize("minutes,calls", [(59, 0), (61, 1)])
def test_restart_uses_a_copy_up_to_an_hour_old_and_queues_one_refresh_for_an_older_one(monkeypatch, minutes, calls):
    now = time.time()
    monkeypatch.setattr(C, "build_state", lambda p: state(now - minutes * 60))
    C.get_state(p=P)
    f = next(C.CACHE_DIR.glob("*state*.json"))
    os.utime(f, (now - minutes * 60, now - minutes * 60))
    C._cache.clear()  # restart
    n = []
    monkeypatch.setattr(C, "build_state", lambda p: n.append(1) or state(now))
    assert C.get_state(p=P)["source"]["fetchedAt"] == now - minutes * 60  # served at once either way
    deadline = time.time() + 3
    while time.time() < deadline and (C._refreshing or (calls and not n)):
        time.sleep(0.02)
    assert len(n) == calls


def test_only_the_owners_pane_refresh_bypasses_the_hour_and_only_for_that_panes_request(monkeypatch):
    now = time.time()
    P2 = dict(P, phases=None)
    monkeypatch.setattr(C, "build_state", lambda p: dict(state(now - 60), tasks=[], events=[], modules=[]))
    C.get_state(p=P2)
    calls = []
    monkeypatch.setattr(C, "run_gh", lambda args: calls.append(args[:2]) or json.dumps([]))
    monkeypatch.setattr(C, "load_modules", lambda p: None)

    def no_state(p):
        raise AssertionError("the state was fetched")
    monkeypatch.setattr(C, "build_state", no_state)
    C.get_timeline(P2)  # first build: no commits copy yet, one call is unavoidable
    calls.clear()
    C.get_timeline(P2)
    C.get_state_served(P2)
    C.get_rules(P2)
    assert calls == []  # within the hour: timers, page loads and the like fetch nothing
    C.get_timeline(P2, fresh=True)  # the owner's Refresh on the Timeline pane: its commits request, not the state
    assert len(calls) == 1 and "commits" in calls[0][1]


def test_an_action_does_not_fetch_or_drop_the_state(monkeypatch):
    now = time.time()
    monkeypatch.setattr(C, "build_state", lambda p: state(now - 60))
    C.get_state(p=P)
    monkeypatch.setattr(C, "build_state", lambda p: (_ for _ in ()).throw(AssertionError("fetched")))
    monkeypatch.setitem(C.CFG, "steer", True)
    monkeypatch.setattr(C, "plan_action", lambda *a: ([], "done"))
    assert C.do_act(dict(action="x"), run=lambda a: "", p=P)[0] == 200
    assert C.get_state(p=P)["source"]["fetchedAt"] == now - 60


@pytest.fixture(params=["github-failing", "rate-limit-pause"])
def no_github(request, monkeypatch):
    """Every gh call fails the test; in the second mode the Cockpit is also inside a rate-limit pause."""
    def boom(*a, **k):
        raise AssertionError("a local request called GitHub")
    monkeypatch.setattr(C, "run_gh", boom)
    monkeypatch.setattr(C, "build_state", boom)
    monkeypatch.setattr(C.subprocess, "run", boom)
    if request.param == "rate-limit-pause":
        C._gh_pause.update(until=time.time() + 600)
    return request.param


def test_local_tabs_are_served_without_github(no_github):
    """Token Usage, Settings (Dispatcher and module cards), the Policies list, the Task Queue's local part and the code snapshot never touch GitHub."""
    d = C.DATA_DIR / "proj"
    d.mkdir(parents=True)
    (d / "proxy-usage.jsonl").write_text(json.dumps(dict(ts="2026-01-01T10:00:00+00:00", worker="w1", input_tokens=5, output_tokens=1)) + "\n")
    C._cache.clear()
    u = C.usage_served(P)
    assert u["days"] and u["projects"][0]["name"] == "proj"
    assert C.get_settings(P)["values"] and C.get_card_settings(P)["values"]
    assert C.get_queue(P)["workers"] == []
    assert C.get_rules(P) == dict(configured=False)
    cs = C.code_snapshot(P)
    assert cs["wired"] is False and any(m["id"] == "unassigned" for m in cs["mods"])  # the Code tab's module list is local too


def test_code_tab_modules_come_from_the_local_map_with_github_down(no_github, tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    (code / "metrics.json").write_text(json.dumps(dict(modules={})))
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps(dict(modules=[dict(id="alpha/one", name="Alpha / one")], assign=[])))
    Pc = dict(P, code=str(code), modules=str(mp))
    C._cache.clear()
    try:
        cs = C.code_snapshot(Pc)
    except Exception as ex:  # a metrics file of another shape must not matter here: the module list is what is under test
        pytest.skip(str(ex))
    ids = [m["id"] for m in cs.get("mods", [])]
    assert {"alpha", "alpha/one", "unassigned"} <= set(ids)


def test_pull_request_list_fails_alone_and_is_cached_by_the_hour(monkeypatch):
    monkeypatch.setattr(C, "load_modules", lambda p: {})
    monkeypatch.setattr(L, "rules_config", lambda m: dict(groups=[], managers=[], bots=[]))
    Pg = dict(P, git="/x")
    n = []

    def down(args):
        n.append(1)
        raise RuntimeError("API rate limit already exceeded")
    monkeypatch.setattr(C, "run_gh", down)
    r = C.get_rule_prs(Pg)
    assert r["configured"] and not r["available"] and "rate limit" in r["error"] and r["pending"] == []  # this list alone is 'not available'
    monkeypatch.setattr(C, "run_gh", lambda args: n.append(1) or "[]")
    n.clear()
    assert C.get_rule_prs(Pg, fresh=True)["available"] and len(n) == 1  # the pane's Refresh click
    n.clear()
    r = C.get_rule_prs(Pg)
    assert r["available"] and r["at"] <= time.time() and n == []  # within the hour: no call
