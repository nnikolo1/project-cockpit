import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import module_map_upkeep as up  # noqa: E402

MAP = {"modules": [{"id": "a"}, {"id": "a/one"}, {"id": "b"}],
       "assign": [{"path": "a/", "module": "a"}, {"path": "a/one/", "module": "a/one"}, {"path": "b/x.py", "module": "b"},
                  {"path": "b/y.py", "module": "b"}, {"path": "odd.cfg", "module": None}]}
OLD = ["a/k.py", "a/one/z.py", "b/x.py", "b/y.py", "odd.cfg"]


def run(changes, new):
    return up.classify(changes, OLD, new, MAP)


def test_parse_name_status():
    assert up.parse_name_status("A\tn.py\nD\to.py\nM\tm.py\nR087\told.py\tnew.py\nC100\ts.py\tc.py\n") == [
        ("A", "n.py"), ("D", "o.py"), ("M", "m.py"), ("R", "old.py", "new.py"), ("A", "c.py")]


def test_no_change_is_unchanged():
    assert run([("M", "a/k.py")], OLD)["status"] == "unchanged"


def test_new_file_under_a_module_path_is_incremental_without_edit():
    r = run([("A", "a/one/w.py")], OLD + ["a/one/w.py"])
    assert r["status"] == "incremental" and r["edits"] == [] and r["covered"] == [{"path": "a/one/w.py", "module": "a/one"}]


def test_rename_and_delete_of_exact_entries_are_edits():
    r = run([("R", "b/x.py", "b/x2.py"), ("D", "odd.cfg")], ["a/k.py", "a/one/z.py", "b/x2.py", "b/y.py"])
    assert r["status"] == "incremental" and r["breaking"] == []
    assert r["edits"] == [{"op": "rename", "path": "b/x.py", "to": "b/x2.py", "module": "b"},
                          {"op": "remove", "path": "odd.cfg", "module": None}]
    new = up.apply_edits(MAP, r["edits"])
    assert [a["path"] for a in new["assign"]] == ["a/", "a/one/", "b/x2.py", "b/y.py"] and len(MAP["assign"]) == 5


def test_folder_entry_without_files_goes_but_its_module_disappearing_is_breaking():
    r = run([("D", "a/one/z.py")], ["a/k.py", "b/x.py", "b/y.py", "odd.cfg"])
    assert r["status"] == "breaking" and {"kind": "module disappears", "module": "a/one"} in r["breaking"]


def test_module_disappears_when_its_last_file_goes():
    r = run([("D", "b/x.py"), ("D", "b/y.py")], ["a/k.py", "a/one/z.py", "odd.cfg"])
    assert r["breaking"] == [{"kind": "module disappears", "module": "b"}]


def test_new_top_level_folder_is_breaking():
    r = run([("A", "c/new.py")], OLD + ["c/new.py"])
    assert r["status"] == "breaking" and r["breaking"] == [{"kind": "module appears", "path": "c/new.py", "folder": "c/"}]


def test_uncovered_file_outside_a_new_folder_is_ambiguous_not_guessed():
    r = run([("A", "setup.py"), ("A", "b/z.py")], OLD + ["setup.py", "b/z.py"])
    assert r["status"] == "unchanged" and r["edits"] == [] and [x["path"] for x in r["ambiguous"]] == ["b/z.py", "setup.py"]


def test_dump_map_keeps_the_committed_layout():
    text = (ROOT / "docs/cockpit-modules.json").read_text(encoding="utf-8")
    assert up.dump_map(json.loads(text)) == text


def test_file_task_is_an_unreleased_job_embryo_and_filed_once():
    calls = []

    def fake(open_titles):
        def run_(args):
            calls.append(args)
            if args[1] == "list":
                return json.dumps([{"title": t, "url": "u1"} for t in open_titles])
            return "u2"
        return run_
    rep = {"breaking": [{"kind": "module appears", "path": "c/n.py", "folder": "c/"}], "ambiguous": [{"path": "s.py"}]}
    assert up.file_task("o/r", rep, "a" * 40, "b" * 40, run=fake([])) == "u2"
    create = calls[-1]
    labels = [create[i + 1] for i, x in enumerate(create) if x == "--label"]
    assert labels == ["author:job", "module:internal/metadata"] and "released" not in labels
    body = create[create.index("--body") + 1]
    assert "`c/`" in body and "`s.py`" in body and "unreleased embryo" in body
    calls.clear()
    assert up.file_task("o/r", rep, "a", "b", run=fake([up.TASK_TITLE])) is None and len(calls) == 1


def _git(d, *a):
    subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)


def test_main_writes_incremental_edits_and_refuses_on_breaking(tmp_path, capsys):
    (tmp_path / "docs").mkdir()
    (tmp_path / "b").mkdir()
    for f in ("b/x.py", "b/y.py"):
        (tmp_path / f).write_text("x\n")
    m = {"modules": [{"id": "b"}], "assign": [{"path": "b/x.py", "module": "b"}, {"path": "b/y.py", "module": "b"}, {"path": "docs/", "module": None}]}
    (tmp_path / "docs/map.json").write_text(up.dump_map(m))
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "map")
    _git(tmp_path, "mv", "b/x.py", "b/x2.py")
    _git(tmp_path, "commit", "-qm", "rename")
    assert up.main(["--repo-dir", str(tmp_path), "--map", "docs/map.json", "--write"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "incremental"
    assert [a["path"] for a in json.loads((tmp_path / "docs/map.json").read_text())["assign"]] == ["b/x2.py", "b/y.py", "docs/"]
    _git(tmp_path, "checkout", "-q", "--", "docs/map.json")
    (tmp_path / "c").mkdir()
    (tmp_path / "c/n.py").write_text("n\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "new folder")
    before = (tmp_path / "docs/map.json").read_text()
    assert up.main(["--repo-dir", str(tmp_path), "--map", "docs/map.json", "--write"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "breaking" and (tmp_path / "docs/map.json").read_text() == before
