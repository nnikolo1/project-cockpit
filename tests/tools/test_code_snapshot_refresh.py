"""tools/code_snapshot_refresh.py: runs the configured collector only when the snapshot is stale, and records the result (no real command runs)."""
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import code_snapshot_refresh as R  # noqa: E402

NOW = dt.datetime(2026, 11, 24, 12, 0, tzinfo=dt.timezone.utc)
FRESH, OLD = "2026-11-24T10:00:00Z", "2026-11-22T10:00:00Z"


def project(tmp_path, generated_at=OLD, collect=("make", "snapshot")):
    snap = tmp_path / "snap"
    snap.mkdir(exist_ok=True)
    if generated_at:
        (snap / "metrics.json").write_text(json.dumps({"generated_at": generated_at, "modules": {}}))
    return dict(name="demo", code=str(snap), code_collect=list(collect) if collect else None, git=str(tmp_path))


def collector(tmp_path, writes=FRESH, code=0, calls=None):
    """A stand-in for the collector: records its call and writes metrics.json with `writes` as generated_at."""
    def run(cmd, cwd, timeout):
        (calls if calls is not None else []).append((cmd, cwd))
        if writes:
            (tmp_path / "snap" / "metrics.json").write_text(json.dumps({"generated_at": writes, "modules": {}}))
        return code
    return run


def record(tmp_path):
    return json.loads((tmp_path / "data" / "demo" / "code-snapshot-refresh.json").read_text())


def refresh(tmp_path, p, run, **kw):
    return R.refresh(p, tmp_path / "data", NOW, run=run, clock=lambda: NOW, **kw)


def test_stale_snapshot_runs_the_configured_command_in_the_clone_and_records_success(tmp_path):
    calls = []
    rec = refresh(tmp_path, project(tmp_path), collector(tmp_path, calls=calls))
    assert calls == [(["make", "snapshot"], str(tmp_path))]
    assert rec == record(tmp_path)
    assert rec["action"] == "refreshed" and rec["ok"] and rec["exit"] == 0 and rec["generated_at"] == FRESH and rec["age_h"] == 2.0


def test_fresh_snapshot_is_left_alone_unless_forced(tmp_path):
    calls = []
    rec = refresh(tmp_path, project(tmp_path, FRESH), collector(tmp_path, calls=calls))
    assert calls == [] and rec["action"] == "fresh" and rec["ok"]
    assert refresh(tmp_path, project(tmp_path, FRESH), collector(tmp_path, calls=calls), force=True)["action"] == "refreshed" and len(calls) == 1


def test_missing_snapshot_is_refreshed(tmp_path):
    assert refresh(tmp_path, project(tmp_path, None), collector(tmp_path))["action"] == "refreshed"


def test_failing_command_or_a_still_stale_snapshot_is_recorded_as_failed(tmp_path):
    rec = refresh(tmp_path, project(tmp_path), collector(tmp_path, writes=None, code=2))
    assert rec["action"] == "failed" and not rec["ok"] and "exited with 2" in rec["reason"] and record(tmp_path)["ok"] is False
    rec = refresh(tmp_path, project(tmp_path), collector(tmp_path, writes=OLD))
    assert rec["action"] == "failed" and "still stale" in rec["reason"]


def test_project_without_snapshot_or_collector_is_not_configured(tmp_path):
    calls = []
    assert refresh(tmp_path, project(tmp_path, collect=None), collector(tmp_path, calls=calls))["action"] == "not_configured"
    assert refresh(tmp_path, dict(name="demo"), collector(tmp_path, calls=calls))["action"] == "not_configured" and calls == []


def test_command_line_reads_the_projects_file_and_gives_exit_codes(tmp_path):
    pf = tmp_path / "projects.json"
    pf.write_text(json.dumps({"projects": [{"name": "demo", "repo": "o/demo", "code": str(tmp_path / "snap"), "code_collect": "make snapshot"}]}))
    project(tmp_path)
    argv = ["--projects-file", str(pf), "--data-dir", str(tmp_path / "data")]
    calls = []
    assert R.main(argv, now=NOW, run=collector(tmp_path, writes=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), calls=calls)) == 0
    assert calls[0][0] == ["make", "snapshot"]
    assert R.main(argv + ["--force"], now=NOW, run=collector(tmp_path, writes=None, code=1)) == 1
    pf.write_text(json.dumps({"projects": [{"name": "demo", "repo": "o/demo"}]}))
    assert R.main(argv, now=NOW, run=collector(tmp_path)) == 2
    assert R.main(["--projects-file", str(tmp_path / "none.json")], now=NOW) == 2
