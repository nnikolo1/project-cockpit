import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest
import cockpit as C


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
