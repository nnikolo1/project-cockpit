import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import cardmeta_map as mp  # noqa: E402

MAP = {"modules": [{"id": "a"}, {"id": "a/one"}, {"id": "b"}],
       "assign": [{"path": "a/", "module": "a"}, {"path": "a/one/", "module": "a/one"}, {"path": "a/one/x.py", "module": "b"},
                  {"path": "tests/test_top.py", "module": "b"}, {"path": "tests/data/", "module": "a"},
                  {"path": "odd.cfg", "module": None, "candidates": ["a", "b"], "basis": "shared"}]}


def test_longest_prefix_wins_and_exact_file_beats_folder():
    assert mp.module_of("a/z.py", MAP) == "a" and mp.module_of("a/one/y.py", MAP) == "a/one" and mp.module_of("a/one/x.py", MAP) == "b"


def test_null_and_no_match_are_unassigned():
    assert mp.module_of("odd.cfg", MAP) == mp.UNASSIGNED and mp.module_of("nothing.txt", MAP) == mp.UNASSIGNED


def test_tests_follow_the_mirrored_source_then_their_own_entry():
    assert mp.module_of("tests/a/one/test_y.py", MAP) == "a/one"      # mirrors a/one/y.py -> a/one/
    assert mp.module_of("tests/test_top.py", MAP) == "b"             # no mirror: the entry for the test path itself
    assert mp.module_of("tests/data/f.json", MAP) == "a"
    assert mp.module_of("tests/test_unknown.py", MAP) == mp.UNASSIGNED


def test_mirror_candidates():
    assert mp.test_mirror_candidates("tests/a/b/test_c.py") == ["a/b/c.py", "a/b/", "a/"]
    assert mp.test_mirror_candidates("tests/test_c.py") == ["c.py"] and mp.test_mirror_candidates("src/c.py") == []


def test_basis_candidates_and_helpers():
    assert mp.basis_of("odd.cfg", MAP) == "shared" and mp.candidates_of("odd.cfg", MAP) == ["a", "b"] and mp.candidates_of("a/z.py", MAP) is None
    assert (mp.top_of("a/one"), mp.parent_of("a/one"), mp.parent_of("a")) == ("a", "a", None)
    assert mp.in_module("a/one", "a") and mp.in_module("a", "a") and not mp.in_module("ab", "a")
    assert mp.is_test_path("tests/x.py") and mp.is_test_path("kit/test_p.py") and not mp.is_test_path("kit/p.py")


def test_check_map_reports_stale_and_unassigned():
    r = mp.check_map(["a/z.py", "odd.cfg", "new.txt"], MAP)
    assert "a/one/" in r["stale_paths"] and "a/z.py" not in r["stale_paths"] and r["unassigned"] == ["new.txt", "odd.cfg"]
    alias = {"modules": [{"id": "a"}], "assign": [{"path": "ghost", "module": "a", "alias": True}]}
    assert mp.check_map([], alias)["stale_paths"] == []


def test_load_map_rejects_bad_maps(tmp_path):
    def load(d):
        p = tmp_path / "m.json"
        p.write_text(json.dumps(d))
        return mp.load_map(p)
    for bad in ({"modules": [{"id": "a"}, {"id": "a"}], "assign": []},
                {"modules": [{"id": "a/x"}], "assign": []},
                {"modules": [{"id": "a"}], "assign": [{"path": "p", "module": "zz"}]},
                {"modules": [{"id": "a"}], "assign": [{"path": "p", "module": None, "candidates": ["zz"]}]}):
        try:
            load(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")


# ---- guard on the real map of this repository
def test_real_map_every_assigned_path_exists_and_loads():
    mmap = mp.load_map(ROOT / "docs" / "cockpit-modules.json")
    assert [m["id"] for m in mmap["modules"]] == ["core", "web", "docs", "internal", "internal/bootfilling", "internal/bootfilling/dispatcher", "internal/bootfilling/worker", "internal/metadata", "internal/devops", "internal/agents"]
    r = mp.check_map(mp.tracked_files(ROOT), mmap)
    assert r["stale_paths"] == []


def test_real_map_only_explicit_null_files_are_unassigned():
    mmap = mp.load_map(ROOT / "docs" / "cockpit-modules.json")
    explicit = {a["path"] for a in mmap["assign"] if a["module"] is None}
    stray = [f for f in mp.check_map(mp.tracked_files(ROOT), mmap)["unassigned"] if f not in explicit]
    assert stray == [], f"new files with no module: add them to docs/cockpit-modules.json ({stray})"
