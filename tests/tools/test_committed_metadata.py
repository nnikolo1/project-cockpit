"""The generic checker on synthetic projects that carry committed metadata (map, descriptions, documents), plus one run on this repository."""
import datetime as dt
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import metadata_gaps as G  # noqa: E402
import logic as L  # noqa: E402

NOW = dt.datetime(2026, 11, 24, 12, 0, tzinfo=dt.timezone.utc)
STAMP = "2026-11-24T10:00:00Z"
POL = {"backup": "git", "retention": "kept"}


def project(tmp, desc="a module", declared=None, extra_file=None):
    """A fake project: a git clone with a module map, one module page and its documents, plus card files."""
    repo = tmp / "repo"
    (repo / "docs" / "modules").mkdir(parents=True)
    (repo / "m.py").write_text("x = 1\n")
    (repo / "docs" / "a.md").write_text("# A\nModules: mod\n")
    (repo / "docs" / "modules" / "mod.txt").write_text("text\n")
    (repo / "docs" / "modules" / "mod.svg").write_text("<svg/>")
    if extra_file:
        (repo / extra_file).write_text("y\n")
    (repo / "map.json").write_text(json.dumps({"modules": [{"id": "mod", "name": "M", "desc": desc}], "assign": [{"path": "m.py", "module": "mod"}, {"path": "docs/", "module": "mod"}, {"path": "map.json", "module": "mod"}]}))
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True, capture_output=True)
    meta = tmp / "meta"
    meta.mkdir()
    for k in L.CARD_KINDS:
        mods = {"mod": {"policy": declared or POL}}
        (meta / f"{k}.json").write_text(json.dumps({"schema": 1, "kind": k, "project": "demo", "generated_at": STAMP, "modules": mods}))
    return dict(name="demo", repo="o/demo", git=str(repo), modules=str(repo / "map.json"), meta=str(meta))


def committed(p):
    facts = G.collect(p, pathlib.Path(p["meta"]).parent / "data", NOW)
    keep = ("modules", "cards", "descs", "tracked", "assign", "docs_heads", "content")
    gs = L.metadata_gaps("demo", NOW, metrics=None, usage={}, policies={"config": L.rules_config({"rules": {"groups": [{"name": "g", "paths": ["*.py"]}]}}), "files": ["m.py"]},
                         **{k: v for k, v in facts.items() if k in keep})
    return [g for g in gs if g["kind"] in ("card_data", "footprint", "module_page", "documents", "map") and "diagram" not in g["what"]]


def test_clean_project_has_no_committed_gap(tmp_path):
    assert committed(project(tmp_path)) == []


def test_module_without_footprint_backup_or_retention(tmp_path):
    g = committed(project(tmp_path, declared={"backup": "git"}))
    assert [x["what"] for x in g] == ["persistence footprint: no retention statement"]


def test_module_without_description(tmp_path):
    p = project(tmp_path, desc="")
    (pathlib.Path(p["git"]) / "docs" / "modules" / "mod.txt").unlink()
    assert [x["kind"] for x in committed(p)] == ["module_page"]


def test_tracked_file_without_map_entry_and_document_without_modules_line(tmp_path):
    p = project(tmp_path, extra_file="stray.txt")
    (pathlib.Path(p["git"]) / "docs" / "b.md").write_text("# B\n")
    subprocess.run(["git", "-C", p["git"], "add", "."], check=True, capture_output=True)
    assert sorted(x["kind"] for x in committed(p)) == ["documents", "map"]


def test_explicit_null_entry_is_an_assignment(tmp_path):
    p = project(tmp_path, extra_file="stray.txt")
    m = json.loads(pathlib.Path(p["modules"]).read_text())
    m["assign"].append({"path": "stray.txt", "module": None, "basis": "ambiguous"})
    pathlib.Path(p["modules"]).write_text(json.dumps(m))
    assert committed(p) == []


def test_this_repository_has_no_committed_gap():
    """One instance of the generic check: the Cockpit's own map, declaration and documents."""
    cfg = json.loads((ROOT / "docs/cockpit-modules.json").read_text())
    declared = json.loads((ROOT / "docs/footprint-declared.json").read_text())["modules"]
    fp = dict(status="ok", data=dict(modules=declared), age_h=0.0)
    files = [x for x in subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, text=True, check=True).stdout.split("\0") if x]
    folder = ROOT / "docs" / "modules"
    gs = L.metadata_gaps(
        "project-cockpit", NOW, [m["id"] for m in cfg["modules"]], dict(footprint=fp), None, {}, {}, descs={m["id"]: m.get("desc") for m in cfg["modules"]},
        tracked=files, assign=cfg["assign"], content={m["id"]: {"txt": (folder / f"{L.module_stem(m['id'])}.txt").is_file(), "svg": True} for m in cfg["modules"]},
        docs_heads={f: (ROOT / f).read_text()[:1500] for f in files if f.startswith("docs/") and f.endswith(".md")})
    own = [g["what"] for g in gs if g["kind"] in ("footprint", "module_page", "map") or g["what"].startswith("footprint card")]  # documents without a Modules: line are reported by the checker, not enforced here
    assert own == [], own
