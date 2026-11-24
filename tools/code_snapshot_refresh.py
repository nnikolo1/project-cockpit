#!/usr/bin/env python3
"""Refreshes a project's code snapshot when it is stale: runs the project's collector command and records the result. For the daily job.

    python3 tools/code_snapshot_refresh.py [--project NAME] [--projects-file FILE] [--data-dir DIR] [--max-age-hours 24] [--force] [--timeout 1800]

The command is the project's `code_collect` setting in the projects file (a list of arguments or one string), run in the project's clone
(`git`, else the current folder); it must write `metrics.json` with a fresh `generated_at` into the project's `code` folder (contract:
docs/contracts/card-metadata.md, "Code snapshot"). A snapshot younger than --max-age-hours is left alone unless --force is given.
The result is written to DATA_DIR/<project>/code-snapshot-refresh.json (replaced at every run) and read by tools/metadata_gaps.py.
Exit code: 0 refreshed or fresh, 1 the refresh failed, 2 the project has no snapshot or no collector configured, or cannot be used.
"""
import argparse
import datetime as dt
import json
import logging
import os
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import cockpit as C  # noqa: E402
import logic as L  # noqa: E402

log = logging.getLogger("code_snapshot_refresh")
DEFAULT_PROJECTS = "~/.config/project-cockpit/projects.json"  # the example path of docs/deployment.md


def _metrics(p):
    try:
        return json.loads((pathlib.Path(p["code"]).expanduser() / "metrics.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_collector(cmd, cwd, timeout):
    """Runs the command -> exit code, or None when it cannot start or times out. Its output goes to this tool's log (standard error)."""
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as ex:
        log.error("collector command failed to run: %s", ex)
        return None
    for line in (r.stdout + r.stderr).strip().splitlines()[-20:]:
        log.info("collector: %s", line)
    return r.returncode


def refresh(p, data_dir, now, max_age_h=L.GAPS_MAX_AGE_H, force=False, timeout=1800, run=run_collector, clock=None):
    """One project -> the record written to the data folder: {schema, project, ts, action: fresh|refreshed|failed|not_configured, ok, reason,
    command, exit, seconds, generated_at, age_h}."""
    clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
    rec = dict(schema=1, project=p["name"], ts=now.strftime("%Y-%m-%dT%H:%M:%SZ"), command=p.get("code_collect"), exit=None, seconds=None)
    before = _metrics(p) if p.get("code") else None
    if not p.get("code") or not p.get("code_collect"):
        what = "no `code` folder" if not p.get("code") else "no `code_collect` command"
        rec.update(action="not_configured", ok=False, reason=f"code snapshot not refreshable: {what} in the project settings")
    elif not force and isinstance(before, dict) and not L.snapshot_age(before.get("generated_at"), now, max_age_h)["stale"]:
        rec.update(action="fresh", ok=True, reason="")
    else:
        t0 = time.monotonic()
        code = run(p["code_collect"], p.get("git") or None, timeout)
        after = _metrics(p)
        ok, reason = L.refresh_outcome(code, after, clock(), max_age_h)
        rec.update(action="refreshed" if ok else "failed", ok=ok, reason=reason, exit=code, seconds=round(time.monotonic() - t0, 1))
        before = after
    gen = before.get("generated_at") if isinstance(before, dict) else None
    rec.update(generated_at=gen, age_h=L.snapshot_age(gen, clock(), max_age_h)["age_h"])
    out = pathlib.Path(data_dir) / p["name"] / C.SNAPSHOT_REFRESH_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, out)
    return rec


def main(argv=None, now=None, run=run_collector):
    """The command line; `now` and `run` are seams for tests."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="project name (default: the default of the projects file)")
    ap.add_argument("--projects-file", default=DEFAULT_PROJECTS, help="the Cockpit's --projects file (default: %(default)s)")
    ap.add_argument("--data-dir", default=str(C.DATA_DIR), help="the Cockpit's data folder (default: %(default)s)")
    ap.add_argument("--max-age-hours", type=float, default=L.GAPS_MAX_AGE_H, help="a snapshot older than this is refreshed (default: %(default)s)")
    ap.add_argument("--force", action="store_true", help="run the collector even when the snapshot is fresh")
    ap.add_argument("--timeout", type=float, default=1800, help="seconds the collector may run (default: %(default)s)")
    a = ap.parse_args(argv)
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        projects, default = C.projects_from_file(json.loads(pathlib.Path(a.projects_file).expanduser().read_text()))
        p = next((x for x in projects if x["name"] == (a.project or default)), None)
        if p is None:
            raise ValueError(f"unknown project {a.project!r}; configured: {', '.join(x['name'] for x in projects)}")
    except (OSError, ValueError) as ex:
        log.error("%s", ex)
        return 2
    rec = refresh(p, pathlib.Path(a.data_dir).expanduser(), now, a.max_age_hours, a.force, a.timeout, run)
    log.info("project %s: %s%s", p["name"], rec["action"], f" ({rec['reason']})" if rec["reason"] else "")
    return 0 if rec["ok"] else 2 if rec["action"] == "not_configured" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sys.exit(main())
