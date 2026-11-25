"""Collects the exact token counts of Claude Code session transcripts into transcript-usage.jsonl.

Reads the *.jsonl transcripts directly in each source folder and the sub-agent transcripts in <session>/subagents/ (attributed to the parent session) and keeps only the usage numbers of each message
(never text, file contents or paths). Updates the output atomically (mode 0600) and never drops a record of an earlier run. Contract:
docs/contracts/transcript-usage.md.

    python3 tools/transcript_usage.py --source [KIND:]LABEL=DIR [--source [KIND:]LABEL=DIR ...] --out FILE

KIND is `interactive` or `headless`; without it, a label starting with `headless` means headless, any other interactive.
"""
import argparse
import datetime as dt
import json
import logging
import os
import tempfile

log = logging.getLogger("transcript_usage")

KINDS = ("interactive", "headless")
COUNTS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def default_kind(label):
    """The kind of a source given without one (and of a record of an earlier run without a `kind`)."""
    return "headless" if label.startswith("headless") else "interactive"


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _ts(value):
    """The time as ISO 8601 UTC with seconds, or None."""
    d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def parse_line(line, source, session, agent=None, kind=None):
    """A record for one transcript line, or None if it carries no usage; raises ValueError if unreadable."""
    obj = json.loads(line)
    msg = obj.get("message") if isinstance(obj, dict) else None
    if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict) or not msg.get("id"):
        return None
    usage = msg["usage"]
    model = msg.get("model")
    return {"ts": _ts(obj["timestamp"]), "kind": kind or default_kind(source), "source": source, "session": session, "agent": agent, "message": str(msg["id"]),
            "model": model if isinstance(model, str) else None, **{k: _count(usage.get(k)) for k in COUNTS}}


def read_transcript(path, source, stats, session=None, agent=None, kind=None):
    """The records of one transcript, a message seen several times counted once (its last line wins). A sub-agent's transcript is
    attributed to its parent session (`session`) and carries the agent id."""
    session = session or os.path.splitext(os.path.basename(path))[0]
    seen = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = parse_line(line, source, session, agent, kind)
            except (ValueError, KeyError, TypeError, AttributeError):
                stats["skipped"] += 1
                continue
            if rec:
                seen[rec["message"]] = rec
    return list(seen.values())


def _transcripts(folder):
    """(path, session, agent) of the session transcripts directly in `folder` and of the sub-agent transcripts in `<session>/subagents/`."""
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if name.endswith(".jsonl") and os.path.isfile(path):
            yield path, None, None
        elif os.path.isdir(os.path.join(path, "subagents")):
            subs = os.path.join(path, "subagents")
            for sub in sorted(os.listdir(subs)):
                if sub.endswith(".jsonl") and os.path.isfile(os.path.join(subs, sub)):
                    yield os.path.join(subs, sub), name, os.path.splitext(sub)[0]


def _key(r):
    return (r["source"], r["session"], r.get("agent"), r["message"])


def _existing(path):
    """The records of an earlier run (unreadable lines are dropped)."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            r = json.loads(line)
            _key(r)
            out.append(r)
        except (ValueError, KeyError, TypeError):
            continue
    return out


def collect(sources, out):
    """Updates `out` from the transcripts of `sources` ({label: dir}, or (label, dir) or (kind, label, dir) tuples, a label may repeat; a
    source without a kind gets default_kind(label)): the records already in `out` are kept
    (Claude Code deletes old transcripts, the history must outlive them), a record seen again is refreshed; returns the counters."""
    stats = {"sources": 0, "files": 0, "messages": 0, "skipped": 0, "kept": 0}
    records = []
    for item in (sources.items() if isinstance(sources, dict) else sources):
        kind, label, folder = item if len(item) == 3 else (default_kind(item[0]), *item)
        folder = os.path.expanduser(folder)
        if not os.path.isdir(folder):
            log.warning("source %s: folder missing", label)
            continue
        stats["sources"] += 1
        for path, session, agent in _transcripts(folder):
            try:
                records += read_transcript(path, label, stats, session, agent, kind)
                stats["files"] += 1
            except OSError as e:
                log.warning("source %s: a transcript is unreadable: %s", label, type(e).__name__)
    out = os.path.expanduser(out)
    merged = {_key(r): r for r in _existing(out)}
    stats["kept"] = len(merged)
    merged.update({_key(r): r for r in records})
    stats["kept"] = len(merged) - len({_key(r) for r in records})
    records = sorted(merged.values(), key=lambda r: (r["ts"], r["session"], r["message"]))
    stats["messages"] = len(records)
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
    log.info("sources=%d files=%d messages=%d (kept from earlier runs %d) skipped=%d", stats["sources"], stats["files"],
             stats["messages"], stats["kept"], stats["skipped"])
    return stats


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", action="append", required=True, metavar="[KIND:]LABEL=DIR")
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s transcript_usage %(message)s")
    sources = []
    for s in a.source:
        label, sep, folder = s.partition("=")
        kind, colon, rest = label.partition(":")
        kind, label = (kind, rest) if colon else (default_kind(label), label)
        if not sep or not label or not folder or kind not in KINDS:
            p.error("--source needs [KIND:]LABEL=DIR with KIND interactive or headless")
        sources.append((kind, label, folder))
    collect(sources, a.out)


if __name__ == "__main__":
    main()
