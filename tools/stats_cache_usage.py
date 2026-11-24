"""Imports the daily aggregates of Claude Code's statistics cache (stats-cache.json) into stats-cache-usage.jsonl, a coarse token backfill.

Reads only the numbers of the cache's `dailyActivity` (messages and sessions per day) and `dailyModelTokens` (a token total per day and
model, no input/output/cache split); nothing else of the cache is kept. A day that has per-message data in one of the `--exact` files
(contract transcript-usage.jsonl) is left out: the exact data wins. Each run rewrites the output atomically (mode 0600); a missing or
malformed cache leaves it untouched. Contract: docs/contracts/stats-cache-usage.md.

    python3 tools/stats_cache_usage.py --cache FILE --out FILE [--exact FILE ...]
"""
import argparse
import datetime as dt
import json
import logging
import os
import sys
import tempfile

log = logging.getLogger("stats_cache_usage")

SOURCE = "stats-cache"


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _day(value):
    """The day as YYYY-MM-DD, or None."""
    try:
        return dt.date.fromisoformat(value[:10]).isoformat() if isinstance(value, str) else None
    except ValueError:
        return None


def exact_days(paths):
    """The UTC days with at least one record in the transcript-usage files `paths` (a missing file has none)."""
    days = set()
    for path in paths:
        try:
            with open(os.path.expanduser(path), encoding="utf-8") as f:
                for line in f:
                    try:
                        days.add(dt.datetime.fromisoformat(json.loads(line)["ts"].replace("Z", "+00:00")).astimezone(dt.timezone.utc).date().isoformat())
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue
        except OSError:
            log.warning("exact file missing or unreadable: %s", os.path.basename(path))
    return days


def records(cache):
    """The cache (parsed JSON) -> the contract's records, oldest first: one per day and model with tokens, or one with model and tokens
    null for a day with activity but no token data. Raises ValueError if the cache is not an object with at least one of the two daily lists."""
    keys = ("dailyActivity", "dailyModelTokens")
    if not isinstance(cache, dict) or not any(k in cache for k in keys) or not all(isinstance(cache.get(k, []), list) for k in keys):
        raise ValueError("the cache is not an object with daily lists")
    activity, tokens = {}, {}
    for e in cache.get("dailyActivity", []):
        d = _day(e.get("date")) if isinstance(e, dict) else None
        if d:
            activity[d] = (_int(e.get("messageCount")), _int(e.get("sessionCount")))
    for e in cache.get("dailyModelTokens", []):
        d = _day(e.get("date")) if isinstance(e, dict) else None
        by = e.get("tokensByModel") if d else None
        if isinstance(by, dict):
            tokens.setdefault(d, {}).update({str(m): n for m, n in by.items() if _int(n) is not None})
    out = []
    for d in sorted(set(activity) | set(tokens)):
        msgs, sess = activity.get(d, (None, None))
        base = dict(day=d, messages=msgs, sessions=sess, source=SOURCE)
        out += [dict(base, model=m, tokens=n) for m, n in sorted(tokens[d].items())] if tokens.get(d) else [dict(base, model=None, tokens=None)]
    return out


def write(recs, out):
    """Replaces `out` atomically with `recs`, mode 0600."""
    out = os.path.expanduser(out)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(out)), prefix=".stats-cache-usage-")
    try:
        with os.fdopen(fd, "w") as f:
            for r in recs:
                f.write(json.dumps({k: r[k] for k in ("day", "model", "tokens", "messages", "sessions", "source")}) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, out)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def run(cache_path, out, exact=()):
    """Imports `cache_path` into `out`; returns the number of lines written. Raises OSError / ValueError (output untouched) on a missing
    or malformed cache."""
    with open(os.path.expanduser(cache_path), encoding="utf-8") as f:
        cache = json.load(f)
    every = records(cache)
    skip = exact_days(exact)
    recs = [r for r in every if r["day"] not in skip]
    write(recs, out)
    log.info("days=%d lines=%d (days left out for exact data: %d)", len({r["day"] for r in recs}), len(recs),
             len({r["day"] for r in every} & skip))
    return len(recs)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cache", required=True, help="the statistics cache file (stats-cache.json)")
    p.add_argument("--out", required=True, help="the aggregate file to write (stats-cache-usage.jsonl in the project's data folder)")
    p.add_argument("--exact", action="append", default=[], metavar="FILE", help="a transcript-usage.jsonl whose days are left out (repeatable)")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s stats_cache_usage %(message)s")
    try:
        run(a.cache, a.out, a.exact)
    except (OSError, ValueError) as e:
        log.error("cache not imported, output untouched: %s", type(e).__name__ if isinstance(e, OSError) else e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
