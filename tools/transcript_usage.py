"""Collects the exact token counts of Claude Code session transcripts into transcript-usage.jsonl.

Reads the *.jsonl transcripts directly in each source folder and keeps only the usage numbers of each message
(never text, file contents or paths). Rewrites the whole output atomically (mode 0600). Contract:
docs/contracts/transcript-usage.md.

    python3 tools/transcript_usage.py --source LABEL=DIR [--source LABEL=DIR ...] --out FILE
"""
import argparse
import datetime as dt
import json
import logging
import os
import tempfile

log = logging.getLogger("transcript_usage")

COUNTS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _ts(value):
    """The time as ISO 8601 UTC with seconds, or None."""
    d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def parse_line(line, source, session):
    """A record for one transcript line, or None if it carries no usage; raises ValueError if unreadable."""
    obj = json.loads(line)
    msg = obj.get("message") if isinstance(obj, dict) else None
    if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict) or not msg.get("id"):
        return None
    usage = msg["usage"]
    model = msg.get("model")
    return {"ts": _ts(obj["timestamp"]), "source": source, "session": session, "message": str(msg["id"]),
            "model": model if isinstance(model, str) else None, **{k: _count(usage.get(k)) for k in COUNTS}}


def read_transcript(path, source, stats):
    """The records of one transcript, a message seen several times counted once (its last line wins)."""
    session = os.path.splitext(os.path.basename(path))[0]
    seen = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = parse_line(line, source, session)
            except (ValueError, KeyError, TypeError, AttributeError):
                stats["skipped"] += 1
                continue
            if rec:
                seen[rec["message"]] = rec
    return list(seen.values())


def collect(sources, out):
    """Rewrites `out` from the transcripts of `sources` ({label: dir}); returns the counters."""
    stats = {"sources": 0, "files": 0, "messages": 0, "skipped": 0}
    records = []
    for label, folder in sources.items():
        folder = os.path.expanduser(folder)
        if not os.path.isdir(folder):
            log.warning("source %s: folder missing", label)
            continue
        stats["sources"] += 1
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not name.endswith(".jsonl") or not os.path.isfile(path):
                continue
            try:
                records += read_transcript(path, label, stats)
                stats["files"] += 1
            except OSError as e:
                log.warning("source %s: a transcript is unreadable: %s", label, type(e).__name__)
    records.sort(key=lambda r: (r["ts"], r["session"], r["message"]))
    stats["messages"] = len(records)
    out = os.path.expanduser(out)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(out)), prefix=".transcript-usage-")
    try:
        with os.fdopen(fd, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, out)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log.info("sources=%d files=%d messages=%d skipped=%d", stats["sources"], stats["files"],
             stats["messages"], stats["skipped"])
    return stats


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", action="append", required=True, metavar="LABEL=DIR")
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s transcript_usage %(message)s")
    sources = {}
    for s in a.source:
        label, sep, folder = s.partition("=")
        if not sep or not label or not folder:
            p.error("--source needs LABEL=DIR")
        sources[label] = folder
    collect(sources, a.out)


if __name__ == "__main__":
    main()
