import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import cardmeta_ambiguity as amb  # noqa: E402

MAP = {"modules": [{"id": "a"}, {"id": "a/one"}, {"id": "b"}],
       "assign": [{"path": "a/", "module": "a", "basis": "mixed", "candidates": ["a/one"]},
                  {"path": "a/one/", "module": "a/one"}, {"path": "b/", "module": "b"},
                  {"path": "x.cfg", "module": None, "candidates": ["a", "b"], "basis": "shared"},
                  {"path": "span.cfg", "module": "a", "candidates": ["a", "b"], "basis": "two modules"}]}
FILES = ["a/u.py", "a/one/v.py", "b/w.py", "x.cfg", "span.cfg", "stray.py", "notes.md", "docs/z.txt"]


def test_counts_unsorted_and_unassigned():
    r = amb.aggregate(FILES, MAP)
    a = r["modules"]["a"]
    assert a["sub_files"] == {"a/one": 1} and a["own_files"] == 0
    assert [u["path"] for u in a["unsorted"]] == ["a/u.py", "span.cfg"]
    assert a["unsorted"][0]["candidates"] == ["a/one"] and a["unsorted"][0]["reason"] == "mixed"
    assert r["modules"]["b"] == {"own_files": 1, "sub_files": {}, "unsorted": []}
    assert r["unassigned_nondoc"] == 2   # x.cfg and stray.py; notes.md and docs/ are docs


def test_module_level_lists_null_entries_and_spanning_candidates():
    ml = amb.module_level(FILES, MAP)
    assert [m["path"] for m in ml] == ["x.cfg", "span.cfg"]
    assert ml[0]["reason"] == "shared"
    nulls = {"modules": [{"id": "a"}], "assign": [{"path": "n.cfg", "module": None}, {"path": "m.cfg", "module": None, "candidates": []}]}
    assert [m["path"] for m in amb.module_level(["n.cfg"], nulls)] == ["n.cfg"]     # a null entry is reported only while a tracked file matches
    assert amb.module_level(["other"], nulls) == [] and amb.module_level(["n.cfg"], nulls)[0]["reason"] == "no basis recorded"

def test_render_md_mentions_module_level():
    assert "x.cfg" in amb.render_md(amb.aggregate(FILES, MAP))
