import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest
import cockpit as C
from ckp.api import context as api_context


def bad():
    raise RuntimeError("rate limit")


def test_stale_copy_served_when_refresh_fails():
    C._cache.clear()
    assert C.cached("k", lambda: "v1", -1) == "v1"
    assert C.cached("k", bad, -1) == "v1"


def test_error_raised_when_nothing_cached():
    C._cache.clear()
    with pytest.raises(RuntimeError):
        C.cached("k2", bad, -1)


import threading
import time


@pytest.fixture
def disk(tmp_path, monkeypatch):
    monkeypatch.setattr(api_context, "CACHE_DIR", tmp_path)
    C._cache.clear()
    C._refreshing.clear()
    yield tmp_path
    C._cache.clear()
    C._refreshing.clear()


def wait_for(cond, secs=3):
    end = time.time() + secs
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_swr_serves_the_expired_copy_at_once_and_refreshes_behind(disk):
    C.cached("s", lambda: "old", -1)
    gate = threading.Event()

    def slow():
        gate.wait(3)
        return "new"
    assert C.cached("s", slow, -1, swr=True) == "old"  # not blocked by the slow refresh
    assert C.refreshing("s")
    gate.set()
    assert wait_for(lambda: not C.refreshing("s"))
    assert C._cache["s"][1] == "new"


def test_swr_without_any_copy_computes_synchronously(disk):
    assert C.cached("first", lambda: "v", 60, swr=True) == "v"


def test_a_slow_key_does_not_block_another_key(disk):
    gate, started = threading.Event(), threading.Event()

    def slow():
        started.set()
        gate.wait(3)
        return "slow"
    t = threading.Thread(target=lambda: C.cached("a", slow, 60))
    t.start()
    assert started.wait(3)
    t0 = time.time()
    assert C.cached("b", lambda: "fast", 60) == "fast"
    assert time.time() - t0 < 1
    gate.set()
    t.join()


def test_persisted_value_survives_a_restart_and_counts_as_expired(disk):
    C.cached("p", lambda: {"x": [1, 2]}, 60, persist=True)
    C._cache.clear()  # a new process
    got = C.cached("p", lambda: {"x": "fresh"}, 60, swr=True, persist=True)
    assert got == {"x": [1, 2]}  # served at once from disk
    assert wait_for(lambda: C._cache["p"][1] == {"x": "fresh"})  # and refreshed behind


def test_unreadable_disk_copy_is_ignored(disk):
    (disk / "q.json").write_text("{not json")
    assert C.cached("q", lambda: "v", 60, persist=True) == "v"


def test_drop_cache_removes_memory_and_disk_copies(disk):
    C.cached("proj:state", lambda: 1, 60, persist=True)
    C.cached("other:state", lambda: 2, 60, persist=True)
    C.drop_cache("proj:")
    assert "proj:state" not in C._cache and not (disk / "proj_state.json").exists()
    assert "other:state" in C._cache and (disk / "other_state.json").exists()


def test_a_failed_background_refresh_keeps_the_old_copy(disk):
    C.cached("f", lambda: "good", -1)
    C.cached("f", bad, -1, swr=True)
    assert wait_for(lambda: not C.refreshing("f"))
    assert C._cache["f"][1] == "good"
