"""Sub-modules ordered by size within their module (PC-90): the one ordering function of PC-89 (logic.order_modules), applied among siblings only."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import logic as L

MAP = [dict(id="a", name="A"), dict(id="a/x", name="X"), dict(id="a/y", name="Y"), dict(id="a/z", name="Z"),
       dict(id="b", name="B"), dict(id="b/p", name="P"), dict(id="b/q", name="Q")]
ASSIGN = [dict(path="a/x/", module="a/x"), dict(path="a/y/", module="a/y"), dict(path="a/z/", module="a/z"), dict(path="b/p/", module="b/p"), dict(path="b/q/", module="b/q")]


def ordered(lines):
    return L.order_modules(L.local_modules(MAP), lines)


def kids(mods, mid):
    return next(m for m in mods if m["id"] == mid)["children"]


def test_siblings_of_different_sizes_come_out_biggest_first_with_their_size():
    sizes = {"a/x/f.py": 10, "a/y/f.py": 300, "a/y/g.js": 5, "a/z/f.py": 40, "a/y/notes.md": 9999, "tests/a/y/test_f.py": 9999}
    mods = ordered(L.code_lines(sizes, ASSIGN, [m["id"] for m in MAP]))
    assert kids(mods, "a") == ["a/y", "a/z", "a/x"]
    assert [m["id"] for m in mods if m["id"].startswith("a/")] == ["a/y", "a/z", "a/x"]   # the flat list follows the same order
    assert next(m for m in mods if m["id"] == "a/y")["lines"] == 305   # code files only: no documents, no tests


def test_a_sub_module_with_no_files_comes_last_and_equal_sizes_go_by_name():
    mods = ordered({"a/x": 5, "a/z": 7})   # a/y: no files, so no size
    assert kids(mods, "a") == ["a/z", "a/x", "a/y"]
    assert next(m for m in mods if m["id"] == "a/y")["lines"] is None
    assert kids(ordered({"a/x": 5, "a/y": 5, "a/z": 5}), "a") == ["a/x", "a/y", "a/z"]
    assert kids(ordered({}), "a") == ["a/x", "a/y", "a/z"]   # no sizes: the map's order


def test_the_order_does_not_mix_siblings_of_different_parents():
    mods = ordered({"a/x": 1, "a/y": 2, "a/z": 3, "b/p": 100, "b/q": 200})
    assert kids(mods, "a") == ["a/z", "a/y", "a/x"] and kids(mods, "b") == ["b/q", "b/p"]
    assert all(c.startswith(m["id"] + "/") for m in mods for c in m["children"])
