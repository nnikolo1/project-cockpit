"""One-off reconstruction of a TOKEN-ONLY history from session transcripts (ADR 0017).

    python3 -m bootfill.quota_backfill --glob 'TRANSCRIPT_GLOB' --out FILE.jsonl --since 2026-10-01 [--until DATE] [--own-pattern PAT ...]

Writes hourly buckets (UTC): one JSON line per hour with token sums and message counts, marked "reconstructed": true and "percentages": null
(the quota percentages of the past are not recoverable). APPROXIMATE: it counts only what the transcripts on this machine contain (no other
device, no Claude Web), deduplicates messages by message id and request id, and a rolled-off or deleted transcript is simply missing.
Only timestamps, ids and usage numbers are read. Refuses to write inside the cockpit repository or over an existing file."""
import argparse
import datetime as dt
import json
import logging
import os
import pathlib
import sys

from . import quota_history as Q

log = logging.getLogger("bootfill.backfill")
REPO = pathlib.Path(__file__).resolve().parent.parent


def parse_date(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() if "+" not in s and not s.endswith("Z") else Q.core.parse_time(s)


def run(glob_pat, out, since, until=None, own=(), overwrite=False):
    """-> number of buckets written. Raises ValueError for an unsafe output path."""
    out = pathlib.Path(out).expanduser().resolve()
    if out == REPO or REPO in out.parents:
        raise ValueError("the output file must be outside the repository (generated data is never committed)")
    if out.exists() and not overwrite:
        raise ValueError(f"{out.name} exists; pass --overwrite to replace it")
    sc = Q.TokenScanner(glob_pat, own, None)
    stats = sc.scan(since)
    ev = [e for e in sc.events.values() if until is None or e[0] < until]
    rows = Q.hourly_buckets(ev, since)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    os.replace(tmp, out)
    log.info("backfill: %d files, %d messages, %d hourly buckets, %d unreadable lines, %d unreadable files",
             stats["files"], len(ev), len(rows), stats["bad_lines"], stats["unreadable"])
    return len(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--glob", required=True, help="transcript glob, e.g. ~/.claude/projects/**/*.jsonl")
    ap.add_argument("--out", required=True, help="output JSON-lines file (outside the repository)")
    ap.add_argument("--since", required=True, help="first day to include, YYYY-MM-DD (UTC) or ISO 8601")
    ap.add_argument("--until", help="end (exclusive), default: now")
    ap.add_argument("--own-pattern", action="append", default=[], help="fnmatch pattern of the job's own transcripts (counted in tokens_own); repeatable")
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        n = run(a.glob, a.out, parse_date(a.since), parse_date(a.until) if a.until else None, a.own_pattern, a.overwrite)
    except ValueError as ex:
        sys.exit(f"error: {ex}")
    print(f"{n} hourly buckets written (reconstructed, token-only, approximate)")


if __name__ == "__main__":
    main()
