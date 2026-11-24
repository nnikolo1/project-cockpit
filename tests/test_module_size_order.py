"""Modules and sub-modules ordered by size (PC-89, PC-90): one ordering function, biggest first, applied among siblings only."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import logic as L

MAP = [dict(id="a", name="A"), dict(id="a/x", name="X"), dict(id="a/y", name="Y"), dict(id="a/z", name="Z"),
       dict(id="b", name="B"), dict(id="b/p", name="P"), dict(id="b/q", name="Q")]
ASSIGN = [dict(path="a/x/", module="a/x"), dict(path="a/y/", module="a/y"), dict(path="a/z/", module="a/z"), dict(path="b/p/", module="b/p"), dict(path="b/q/", module="b/q")]


def tree():
    return L.local_modules(MAP)


def kids(mods, mid):
    return next(m for m in mods if m["id"] == mid)["children"]


def test_order_by_size_biggest_first_unsized_last_in_given_order_ties_by_name():
    mods = [dict(id="m1", name="Delta"), dict(id="m2", name="Beta"), dict(id="m3", name="Alpha"), dict(id="m4", name="Gamma"), dict(id="m5", name="Echo")]
    got = [m["id"] for m in L.order_by_size(mods, {"m1": 10, "m2": 50, "m4": 50, "m5": 0})]
    assert got == ["m2", "m4", "m1", "m5", "m3"]   # 50 Beta, 50 Gamma (tie by name), 10, 0, then the one without a size
    assert [m["id"] for m in L.order_by_size(mods, {})] == ["m1", "m2", "m3", "m4", "m5"]   # no sizes: the given order


def test_siblings_of_different_sizes_come_out_biggest_first_with_their_size():
    sizes = {"a/x/f.py": 10, "a/y/f.py": 300, "a/y/g.js": 5, "a/z/f.py": 40, "a/y/notes.md": 9999, "tests/a/y/test_f.py": 9999}
    mods = tree()
    L.order_subs_by_size(mods, L.code_lines(sizes, list(sizes), ASSIGN, [m["id"] for m in mods]))
    assert kids(mods, "a") == ["a/y", "a/z", "a/x"]
    assert next(m for m in mods if m["id"] == "a/y")["size"] == 305   # code files only: no documents, no tests


def test_a_sub_module_with_no_files_comes_last():
    mods = tree()
    L.order_subs_by_size(mods, {"a/x": 5, "a/z": 7})   # a/y: no files, so no size
    assert kids(mods, "a") == ["a/z", "a/x", "a/y"]
    assert next(m for m in mods if m["id"] == "a/y")["size"] is None


def test_the_order_does_not_mix_siblings_of_different_parents():
    mods = tree()
    L.order_subs_by_size(mods, {"a/x": 1, "a/y": 2, "a/z": 3, "b/p": 100, "b/q": 200})
    assert kids(mods, "a") == ["a/z", "a/y", "a/x"] and kids(mods, "b") == ["b/q", "b/p"]
    assert all(c.startswith(m["id"] + "/") for m in mods for c in m["children"])
