#!/usr/bin/env python3
"""Reports the gaps in a project's metadata, as the Cockpit would show them. Read-only: files nothing, changes nothing.

    python3 tools/metadata_gaps.py [--project NAME] [--projects-file FILE] [--data-dir DIR] [--max-age-hours 24] [--timing URL] [--timing-limit 3]
                                   [--tool-settings FILE] [--min-retention-days 3650] [--json]

Reads what the Cockpit reads for the project: its entry in the projects file, the module map, the card files (footprint, churn, ambiguity),
the code snapshot (`code`, its collector `code_collect` and the last refresh record), the token-usage files under DATA_DIR/<project>/, with a local clone, `git ls-files` for the Policies, and the coding
tool's settings file for the transcript retention (checked only for a project whose transcripts are collected).
Prints one line per gap (project | module | what | age) or, with --json, the report documented in docs/contracts/metadata-gaps.md.
Exit code: 0 no gap, 1 at least one gap, 2 the project or a file argument cannot be used. The detection itself is logic.metadata_gaps.
"""
import argparse
import datetime as dt
import json
import logging
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import cockpit as C  # noqa: E402
import logic as L  # noqa: E402

log = logging.getLogger("metadata_gaps")
DEFAULT_PROJECTS = "~/.config/project-cockpit/projects.json"  # the example path of docs/deployment.md
# The coding tool's specifics (Claude Code): its user settings file and the key that sets how many days session transcripts are kept.
TOOL_SETTINGS = "~/.claude/settings.json"
RETENTION_KEY = "cleanupPeriodDays"


def _read(path):
    return C._read_text(path)


def _json(path):
    try:
        return json.loads(_read(path))
    except (TypeError, ValueError):
        return None


def collect(p, data_dir, now):
    """The project's files -> the arguments of logic.metadata_gaps after `project` and `now`."""
    d = pathlib.Path(data_dir) / p["name"]
    meta = pathlib.Path(p["meta"]) if p.get("meta") else d / "cardmeta"
    cfgm = _json(p["modules"]) if p.get("modules") else None
    modules = [m["id"] for m in cfgm.get("modules", [])] if isinstance(cfgm, dict) else None
    cards = {k: L.parse_card(_read(meta / f"{k}.json"), k, p["name"], now) for k in L.CARD_KINDS}
    metrics = _json(pathlib.Path(p["code"]) / "metrics.json") if p.get("code") else None
    usage = {"quota": _read(d / C.QUOTA_FILE), "transcript": _read(d / C.TRANSCRIPT_FILE), "proxy": _read(d / C.USAGE_FILE)}
    files = None
    if p.get("git"):
        try:
            r = subprocess.run(["git", "-C", p["git"], "ls-files", "-z"], capture_output=True, text=True, check=True, timeout=30)
            files = [x for x in r.stdout.split("\0") if x]
        except (OSError, subprocess.SubprocessError) as ex:
            log.warning("git ls-files failed for %s: %s", p["name"], ex)
    docs_heads = content = None
    if files is not None:
        docs_heads = {f: (_read(pathlib.Path(p["git"]).expanduser() / f) or "")[:1500] for f in files if f.startswith("docs/") and f.endswith(".md")}
    if p.get("modules") and modules is not None:
        folder = pathlib.Path(p["modules"]).expanduser().parent / "modules"
        content = {m: {e: (folder / f"{L.module_stem(m)}.{e}").is_file() for e in ("txt", "svg")} for m in modules}
    try:
        entries = sorted(x.name for x in d.iterdir())
    except OSError:
        entries = None
    quality = None
    try:
        snap = C.code_snapshot(p) if p.get("code") else dict(wired=False)
        th = (json.loads(_read(d / "cockpit-settings.json") or "{}") or {}).get("quality_thresholds")
        quality = dict(modules=snap["modules"], thresholds=th) if snap.get("wired") else None
    except (ValueError, AttributeError, OSError) as ex:
        log.warning("quality bands not checked for %s: %s", p["name"], ex)
    return dict(quality=quality, modules=modules, cards=cards, metrics=metrics if isinstance(metrics, dict) else None, usage=usage,
                policies=dict(config=L.rules_config(cfgm), files=files), descs={m["id"]: m.get("desc") for m in (cfgm or {}).get("modules", [])},
                tracked=files, assign=(cfgm or {}).get("assign") if cfgm else None, docs_heads=docs_heads, content=content,
                data_root=str(d), data_entries=entries,
                snapshot=dict(configured=bool(p.get("code")), collector=bool(p.get("code_collect")), last=_json(d / C.SNAPSHOT_REFRESH_FILE)))


TIMED = ("projects", "state", "questions", "timeline", "settings", "cardsettings", "usage", "rules", "code", "cardmeta")


def time_requests(url, project, limit):
    """Live check: seconds each read-only /api/ call of a running Cockpit takes (None = failed). The URL's `t` token goes along, never into a log."""
    u = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(u.query))
    q["project"] = project
    out = []
    for name in TIMED:
        full = urllib.parse.urlunsplit((u.scheme, u.netloc, f"/api/{name}", urllib.parse.urlencode(q), ""))
        t0 = time.monotonic()
        try:
            urllib.request.urlopen(full, timeout=max(limit * 4, 10)).read()
            out.append((name, time.monotonic() - t0))
        except (OSError, urllib.error.URLError) as ex:
            log.warning("timing: /api/%s failed: %s", name, type(ex).__name__)
            out.append((name, None))
    return out


def report(project, gaps, max_age_h, now):
    return dict(schema=1, project=project, generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), max_age_hours=max_age_h, count=len(gaps), gaps=gaps)


def text(rep):
    if not rep["gaps"]:
        return f"{rep['project']}: no gaps"
    age = lambda g: "-" if g["age_h"] is None else f"{g['age_h']:g} h old"
    return "\n".join(f"{g['project']} | {g['module'] or '-'} | {g['what']} | {age(g)}" for g in rep["gaps"]) + f"\n{rep['count']} gap(s)"


def main(argv=None, now=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="project name (default: the default of the projects file)")
    ap.add_argument("--projects-file", default=DEFAULT_PROJECTS, help="the Cockpit's --projects file (default: %(default)s)")
    ap.add_argument("--data-dir", default=str(C.DATA_DIR), help="the Cockpit's data folder (default: %(default)s)")
    ap.add_argument("--max-age-hours", type=float, default=L.GAPS_MAX_AGE_H, help="generated data older than this is a gap (default: %(default)s)")
    ap.add_argument("--timing", metavar="URL", help="off by default: also time the /api/ calls of the running Cockpit at URL (with its ?t=TOKEN)")
    ap.add_argument("--timing-limit", type=float, default=3.0, help="seconds a request may take (default: %(default)s)")
    ap.add_argument("--tool-settings", default=TOOL_SETTINGS, help="the coding tool's user settings file (default: %(default)s)")
    ap.add_argument("--min-retention-days", type=int, default=L.RETENTION_MIN_DAYS, help="shortest acceptable transcript retention (default: %(default)s)")
    ap.add_argument("--json", action="store_true", help="print the JSON report instead of text lines")
    a = ap.parse_args(argv)
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        projects, default = C.projects_from_file(json.loads(pathlib.Path(a.projects_file).expanduser().read_text()))
        p = next((x for x in projects if x["name"] == (a.project or default)), None)
        if p is None:
            raise ValueError(f"unknown project {a.project!r}; configured: {', '.join(x['name'] for x in projects)}")
    except (OSError, ValueError) as ex:
        log.error("%s", ex)
        print(f"error: {ex}", file=sys.stderr)
        return 2
    timings = time_requests(a.timing, p["name"], a.timing_limit) if a.timing else None
    settings = pathlib.Path(a.tool_settings).expanduser()
    retention = dict(path=str(settings), text=_read(settings), key=RETENTION_KEY, min_days=a.min_retention_days)
    gaps = L.metadata_gaps(p["name"], now, max_age_h=a.max_age_hours, timings=timings, timing_limit=a.timing_limit, retention=retention,
                           **collect(p, pathlib.Path(a.data_dir).expanduser(), now))
    rep = report(p["name"], gaps, a.max_age_hours, now)
    log.info("project %s: %d gap(s)", p["name"], len(gaps))
    print(json.dumps(rep, indent=1) if a.json else text(rep))
    return 1 if gaps else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sys.exit(main())
