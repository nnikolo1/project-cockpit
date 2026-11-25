import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from ckp.store.cache import DiskCache  # noqa: E402


def test_save_load_and_key_sanitising(tmp_path):
    c = DiskCache(tmp_path)
    c.save("proj:state/1", {"n": 1})
    assert c.load("proj:state/1") == {"n": 1} and c.path("proj:state/1").name == "proj_state_1.json"


def test_a_miss_and_no_directory_are_quiet(tmp_path):
    assert DiskCache(tmp_path).load("nope") is None and DiskCache(tmp_path).mtime("nope") is None
    off = DiskCache(None)
    off.save("k", 1)
    assert off.load("k") is None and off.path("k") is None and off.mtime("k") is None
    off.drop("k")


def test_drop_removes_every_key_with_the_prefix_only(tmp_path):
    c = DiskCache(tmp_path)
    for k in ("a:state", "a:ci", "b:state"):
        c.save(k, 1)
    c.drop("a:")
    assert c.load("a:state") is None and c.load("a:ci") is None and c.load("b:state") == 1


def test_mtime_is_the_file_time(tmp_path):
    c = DiskCache(tmp_path)
    c.save("k", 1)
    assert abs(c.mtime("k") - c.path("k").stat().st_mtime) < 1e-6
