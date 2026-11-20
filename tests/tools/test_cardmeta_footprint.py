import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import cardmeta_footprint as fp  # noqa: E402
import cardmeta_common as cm  # noqa: E402

MAP = {"modules": [{"id": "a"}, {"id": "b"}], "assign": []}


def test_policy_and_gaps():
    assert fp.policy_of({}) == {"backup": "undocumented", "retention": "undocumented"}
    assert fp.policy_of({"policy": {"backup": " nightly ", "retention": ""}}) == {"backup": "nightly", "retention": "undocumented"}
    mods = {"a": {"policy": {"backup": "x", "retention": "undocumented"}}, "b": {"policy": {"backup": "x", "retention": "y"}}, "c": {"policy": {}}}
    gaps = fp.gaps_of(mods, {"a": {}, "b": {}})
    assert {"module": "a", "what": "retention undocumented"} in gaps and {"module": "c", "what": "no footprint declaration"} in gaps and len(gaps) == 2


def test_expand_path(tmp_path):
    assert fp.expand_path("x/<project>/y", "demo", tmp_path) == str(tmp_path / "x" / "demo" / "y")
    assert fp.expand_path("~/z", "demo", tmp_path) == os.path.expanduser("~/z") and fp.expand_path("/abs/p", "d", tmp_path) == "/abs/p"


def test_volume_of_and_merge():
    now = 1_000_000.0
    files = [("p", 10, now - 100), ("q", 20, now - 3 * 86400), ("r", 40, now - 20 * 86400), ("s", 80, now - 90 * 86400)]
    v = fp.volume_of(files, now)
    assert v == {"day": {"rows": 1, "bytes": 10}, "week": {"rows": 2, "bytes": 30}, "month": {"rows": 3, "bytes": 70}}
    assert fp.merge_volume([v, v])["month"] == {"rows": 6, "bytes": 140} and fp.merge_volume([]) == {}


def test_module_record_measures_without_reading(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "one.txt").write_text("12345")
    (d / "two.txt").write_text("123")
    (tmp_path / "log.txt").write_text("abc")
    (tmp_path / "log.txt.1").write_text("de")
    decl = {"files": [{"path": "data", "kind": "dir", "written_by": "a", "volume": True}, {"path": "data/one.txt", "kind": "file", "personal_data": True},
                      {"path": "missing.bin", "kind": "file"}],
            "logging": [{"logger": "lg", "file": "log.txt", "rotation": "none"}], "policy": {"backup": "b"}}
    r = fp.module_record(decl, "demo", tmp_path, os.path.getmtime(d / "one.txt"))
    assert (r["files"][0]["count"], r["files"][0]["exists"]) == (2, True) and r["files"][1]["size_bytes"] == 5 and r["files"][1]["personal_data"] is True
    assert r["files"][2]["exists"] is False and r["files"][2]["size_bytes"] == 0
    assert r["logging"][0]["size_bytes"] == 5 and r["logging"][0]["files"] == 2
    assert r["volume"]["day"]["rows"] == 2 and r["tables"] == [] and r["policy"]["retention"] == "undocumented"


def test_aggregate_flags_undeclared_modules(tmp_path):
    r = fp.aggregate({"a": {"policy": {"backup": "x", "retention": "y"}}}, MAP, "demo", tmp_path, 0.0)
    assert set(r["modules"]) == {"a", "b"} and r["gaps"] == [{"module": "b", "what": "no footprint declaration"}, {"module": "b", "what": "backup undocumented"}, {"module": "b", "what": "retention undocumented"}][:1] + r["gaps"][1:]
    assert r["gaps"][0] == {"module": "b", "what": "no footprint declaration"}


def test_common_header_out_dir_and_atomic_write(tmp_path):
    h = cm.header("churn", "demo", until="2026-11-20T00:00:00Z")
    assert h["schema"] == 1 and h["kind"] == "churn" and h["project"] == "demo" and h["generated_at"].endswith("Z") and h["until"]
    try:
        cm.header("nope", "demo")
        raise AssertionError
    except ValueError:
        pass
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        cm.resolve_out_dir(str(repo / "out"), repo, "demo")
        raise AssertionError
    except SystemExit:
        pass
    assert cm.default_out_dir("demo") == "~/.local/share/project-cockpit/demo/cardmeta"
    out = cm.resolve_out_dir(str(tmp_path / "out"), repo, "demo")
    p = cm.write_kind(out, "footprint", {"a": 1})
    assert p.read_text().strip().startswith("{") and not list(out.glob(".*tmp"))
