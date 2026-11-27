"""Staggered refreshes and the offline behaviour (owner, 2026-11-20): the slots are spread with jitter, a restart refreshes old copies one
after another, the read side names the case, the write side refuses and queues nothing, no new task starts from state over an hour old."""
import json
import pathlib
import random
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import cockpit as C
from ckp.api import cache as api_cache, context as api_context, state as api_state
import logic as L


# ---------- stagger (pure) ----------
def test_plan_spreads_keys_evenly_over_the_window_with_jitter_inside_bounds():
    keys = [f"p{i}:{r}" for i in range(3) for r in ("state", "ci")]
    plan = L.stagger_plan(keys + keys[:2], 600, 60, random.Random(1))
    assert sorted(plan) == sorted(keys)
    offs = [plan[k][0] - plan[k][1] for k in sorted(keys)]
    assert offs == [i * 100 for i in range(6)]
    assert all(0 <= j < 60 for _, j in plan.values())


def test_plan_without_jitter_is_deterministic():
    assert L.stagger_plan(["b", "a"], 100, 0, random.Random()) == {"a": (0.0, 0.0), "b": (50.0, 0.0)}


def test_refresh_due_waits_for_the_slot_after_a_start_and_adds_the_jitter_later():
    started = 10_000
    assert L.refresh_due(0, 3600, (300, 20), started) == started + 300            # old copy at a restart: its slot
    assert L.refresh_due(started, 3600, (300, 20), started) == started + 3620       # later: one hour plus its jitter


# ---------- offline cases (pure) ----------
@pytest.mark.parametrize("msg,kind", [
    ("API rate limit exceeded for user", "rate_limit"),
    ("HTTP 401: Bad credentials (https://api.github.com/graphql)\nTry authenticating with:  gh auth login", "token"),
    ("error connecting to api.github.com\ncheck your internet connection", "unreachable"),
    ("dial tcp: lookup api.github.com: no such host", "unreachable"),
    ("HTTP 422: Validation failed", "error"),
])
def test_offline_kind_tells_the_cases_apart(msg, kind):
    assert L.offline_kind(msg) == kind


def test_offline_reason_gives_the_retry_time_of_a_rate_limit():
    assert L.offline_reason("rate_limit", 100, lambda t: "10:05") == "GitHub rate limit reached; next try at 10:05"
    assert "token" in L.offline_reason("token") and "unreachable" in L.offline_reason("unreachable")


def test_new_work_only_from_state_up_to_an_hour_old():
    assert L.may_start_new(0) and L.may_start_new(3600)
    assert not L.may_start_new(3601) and not L.may_start_new(None)


# ---------- the cache honours the slots ----------
@pytest.fixture
def disk(tmp_path, monkeypatch):
    monkeypatch.setattr(api_context, "CACHE_DIR", tmp_path)
    C._cache.clear(), C._refreshing.clear(), C._failures.clear()
    yield tmp_path
    C._cache.clear(), C._refreshing.clear(), C._failures.clear()


def test_restart_with_old_copies_does_not_refresh_before_the_slot(disk, monkeypatch):
    monkeypatch.setattr(api_cache, "_started", time.time())
    monkeypatch.setitem(C._slots, "a:state", (600, 0))
    (disk / "a_state.json").write_text(json.dumps("old"))
    assert C.cached("a:state", lambda: pytest.fail("refreshed before its slot"), 3600, swr=True, persist=True, queue=True) == "old"
    assert not C._refreshing


def test_slot_is_given_to_every_project_and_resource(monkeypatch):
    monkeypatch.setattr(api_context, "PROJECTS", {"a": dict(name="a"), "b": dict(name="b")})
    C._slot("a:state")
    assert {f"{n}:{r}" for n in "ab" for r in C.GH_RESOURCES} <= set(C._slots)
    assert len({o for o, _ in C._slots.values()}) == len(C._slots)


# ---------- write side and read side ----------
def test_actions_refused_and_not_queued_while_github_cannot_be_read(monkeypatch):
    p = dict(name="a", repo="o/a")
    monkeypatch.setitem(C.CFG, "steer", True)
    monkeypatch.setattr(api_cache, "_cache", {C.pk(p, "state"): (0, dict(source=dict(fetchedAt=time.time() - 7200)))})
    monkeypatch.setattr(api_cache, "_failures", {C.pk(p, "state"): dict(at=0, error="HTTP 401: Bad credentials", until=None, rate_limited=False)})
    ran = []
    code, body = C.do_act(dict(action="take", n=1), run=ran.append, p=p)
    assert code == 503 and "token" in body["error"] and "not queued" in body["error"] and ran == []


def test_rate_limit_pause_blocks_steering_with_its_time(monkeypatch):
    monkeypatch.setitem(C._gh_pause, "until", time.time() + 600)
    assert C.steer_block(dict(name="z", repo="o/z")).startswith("GitHub rate limit reached; next try at")


def test_served_state_names_the_case_and_stops_new_work(monkeypatch):
    p = dict(name="a", repo="o/a")
    S = dict(tasks=[dict(key="A-1", pick=True)], source=dict(fetchedAt=time.time() - 7200, fetched="08:00:00"))
    monkeypatch.setitem(C.CFG, "steer", True)
    monkeypatch.setattr(api_state, "get_state", lambda fresh=False, p=None: S)
    monkeypatch.setattr(api_state, "live_state", lambda p, S, fresh: S)
    monkeypatch.setattr(api_cache, "_cache", {C.pk(p, "state"): (0, S)})
    monkeypatch.setattr(api_cache, "_failures", {C.pk(p, "state"): dict(at=0, error="error connecting to api.github.com", until=None, rate_limited=False)})
    out = C.get_state_served(p)
    assert out["staleNote"]["kind"] == "unreachable" and out["staleNote"]["age_s"] >= 7200
    assert out["steerBlock"] == "GitHub is unreachable"
    assert out["newWork"] is False and out["tasks"][0]["pick"] is False
