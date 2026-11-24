"""Quota history collector: appends the vendor's quota percentages to a JSONL file whenever they change.

Reads the snapshot written by the owner's status-line script and appends one line to the history file when a
value differs from the last line. The history is append-only (mode 0600) and never rewritten.

    python3 -m dispatcher.quota --snapshot F --history F
"""
import argparse
import datetime as dt
import json
import logging
import os

log = logging.getLogger("quota")

DEFAULT_SNAPSHOT = "~/.local/share/pmpoc/quota.json"
WINDOWS = ("five_hour", "seven_day")


def read_snapshot(path):
    """The snapshot as {"ts", "five_hour", "seven_day"}, or None (with a warning) if missing or unreadable."""
    try:
        with open(os.path.expanduser(path)) as f:
            snap = json.load(f)
        limits = snap["rate_limits"]
        ts = dt.datetime.fromtimestamp(float(snap["observed_at"]), dt.timezone.utc).isoformat(timespec="seconds")
        return {"ts": ts, **{w: limits[w] for w in WINDOWS if w in limits}}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        log.warning("snapshot %s unreadable: %s", path, type(e).__name__)
        return None


def last_line(path):
    """The last record of the history, or None."""
    try:
        with open(path) as f:
            lines = f.read().splitlines()
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None


def collect(snapshot, history):
    """One reading; returns True if a line was appended."""
    snap = read_snapshot(snapshot)
    if snap is None:
        return False
    history = os.path.expanduser(history)
    last = last_line(history)
    if last and all(last.get(w) == snap.get(w) for w in WINDOWS):
        return False
    fd = os.open(history, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps({**snap, "source": "snapshot"}) + "\n")
    log.info("quota change recorded ts=%s", snap["ts"])
    return True


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    p.add_argument("--history", required=True)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s quota %(message)s")
    collect(a.snapshot, a.history)


if __name__ == "__main__":
    main()
