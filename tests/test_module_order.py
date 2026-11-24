"""PC-89: the modules ordered by size (code lines), biggest first, in one function used by every module list. Synthetic map and clone."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import logic as L

MAP = dict(
    modules=[dict(id="small", name="Small"), dict(id="empty", name="Empty"), dict(id="big", name="Big"), dict(id="big/part", name="Part"),
             dict(id="zeta", name="Zeta"), dict(id="alpha", name="Alpha")],
    assign=[dict(path="s/", module="small"), dict(path="b/", module="big"), dict(path="b/p/", module="big/part"), dict(path="z/", module="zeta"),
            dict(path="a/", module="alpha")])
FILES = {"s/x.py": 10, "b/x.py": 30, "b/p/y.js": 25, "b/README.md": 900, "tests/b/test_x.py": 500, "z/x.py": 20, "a/x.py": 20}


def tops(mods):
    return [m["id"] for m in mods if not m.get("parent")]


def test_code_lines_count_code_files_with_sub_modules_not_tests_or_documents():
    got = L.code_lines(FILES, MAP["assign"], ["small", "big", "big/part", "empty"])
    assert got == {"small": 10, "big": 55, "big/part": 25, "empty": 0}


def test_order_biggest_first_ties_by_name_unsized_after_virtual_last():
    mods = L.local_modules(MAP["modules"])
    lines = {"small": 10, "big": 55, "big/part": 25, "zeta": 20, "alpha": 20}  # "empty" has no size
    out = L.order_modules(mods, lines)
    assert [m["id"] for m in out] == ["big", "big/part", "alpha", "zeta", "small", "empty", "unassigned"]
    assert next(m for m in out if m["id"] == "big")["lines"] == 55 and next(m for m in out if m["id"] == "empty")["lines"] is None
    assert len(out) == len(mods) and mods[0]["id"] == "small"  # nothing dropped, the input untouched


def test_unsized_modules_keep_their_map_order_and_no_sizes_keep_the_map():
    mods = L.local_modules(MAP["modules"])
    assert tops(L.order_modules(mods, {})) == ["small", "empty", "big", "zeta", "alpha", "unassigned"]
    assert tops(L.order_modules(mods, {"alpha": 1})) == ["alpha", "small", "empty", "big", "zeta", "unassigned"]


def test_snapshot_lines_leave_tests_out():
    m = {"modules": {"big": {"metrics": {"loc": {"python_source": {"code": 40}, "python_test": {"code": 99}, "javascript": {"code": 5}}}}}}
    assert L.snapshot_lines(m) == {"big": 45} and L.snapshot_lines({}) == {}


def test_every_list_gets_the_same_order_from_the_clone(tmp_path, monkeypatch):
    import cockpit as C
    for f, n in FILES.items():
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("x\n" * n)
    (tmp_path / "map.json").write_text(json.dumps(MAP))
    monkeypatch.setattr(C, "run_git", lambda p, a: "\0".join(FILES))
    p = dict(name="pc89-order", repo="o/r", git=str(tmp_path), modules=str(tmp_path / "map.json"))
    code_tab = C.local_modules_of(p)  # the Code tab's list (local map)
    _, state_mods = L.derive([], [], {}, "boss", MAP["modules"], grow=False)  # the overview, task and timeline lists (GitHub state)
    want = ["big", "alpha", "zeta", "small", "empty"]  # empty: no files, 0 lines, after the sized ones
    assert tops(code_tab)[:5] == want and tops(C.ordered(p, state_mods))[:5] == want
    assert next(m for m in code_tab if m["id"] == "big")["lines"] == 55 and code_tab[-1]["id"] == "unassigned"


def test_without_a_clone_the_snapshot_orders(tmp_path):
    import cockpit as C
    (tmp_path / "metrics.json").write_text(json.dumps({"modules": {"small": {"metrics": {"loc": {"python_source": {"code": 7}}}},
                                                                   "zeta": {"metrics": {"loc": {"python_source": {"code": 3}}}}}}))
    p = dict(name="pc89-snap", repo="o/r", code=str(tmp_path))
    assert tops(C.ordered(p, L.local_modules(MAP["modules"]))) == ["small", "zeta", "empty", "big", "alpha", "unassigned"]
