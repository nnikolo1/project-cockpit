"""Quota history of the boot-filling job (ADR 0017): one append-only record per poll, with the quota readings of both windows and the
token counts of the same windows, computed from the assistant's session transcripts.

Safety: a transcript line is parsed only for its timestamp, message id, request id and the four `usage` numbers; message content is never
stored, logged or put in a record. The scan is incremental (per-file offset cache in the data folder, files named by a hash)."""
import datetime as dt
import glob
import hashlib
import json
import logging
import os
import pathlib

from . import core

log = logging.getLogger("bootfill")

FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
WINDOWS = (("five_hour", "five", 5 * 3600), ("seven_day", "week", 7 * 86400))
KEEP_DAYS_CACHE = 8  # the event cache keeps a little more than the longest window (7 days)


def _hash(s):
    return hashlib.sha1(str(s).encode()).hexdigest()[:16]


def parse_usage_line(raw):
    """One transcript line (bytes) -> (epoch, message_id, request_id, (in, out, cache_creation, cache_read)) or None.
    None for lines without usage, with a bad timestamp or unreadable JSON. Content fields are never touched."""
    if b'"usage"' not in raw:
        return None
    try:
        o = json.loads(raw)
        m = o["message"]
        u = m["usage"]
        ts = core.parse_time(o["timestamp"])
        nums = tuple(max(int(u.get(f) or 0), 0) for f in FIELDS)
        return ts, m.get("id"), o.get("requestId"), nums
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


class TokenScanner:
    """Incremental token scan over the transcripts matching `glob_pat`.
    own_patterns: fnmatch patterns of transcript paths written by the job itself (counted in the separate `own` field too).
    cache_path: JSON file with per-file offsets and the events of the last KEEP_DAYS_CACHE days; None = in memory only (the backfill)."""

    def __init__(self, glob_pat, own_patterns=(), cache_path=None):
        self.glob, self.own, self.cache_path = glob_pat, list(own_patterns), cache_path
        self.files, self.events = {}, {}  # files: hash -> {offset, size, mtime}; events: key -> [ts, in, out, cc, cr, own]
        self.stats = dict(files=0, read=0, bad_lines=0, unreadable=0)
        if cache_path:
            try:
                c = json.loads(pathlib.Path(cache_path).read_text())
                self.files, self.events = c["files"], {k: v for k, v in c["events"]}
            except FileNotFoundError:
                pass
            except (ValueError, KeyError, TypeError) as ex:
                log.warning("token cache unreadable (%s); rescanning", type(ex).__name__)

    def scan(self, keep_after):
        """Read the new bytes of every transcript; events older than `keep_after` (epoch) are not kept. Returns the stats dict."""
        paths = glob.glob(os.path.expanduser(self.glob), recursive=True)
        seen, changed = set(), False
        self.stats = dict(files=len(paths), read=0, bad_lines=0, unreadable=0)
        for p in paths:
            h = _hash(p)
            seen.add(h)
            try:
                st = os.stat(p)
            except OSError:
                self.stats["unreadable"] += 1
                continue
            f = self.files.get(h)
            if f is None and st.st_mtime < keep_after:
                continue  # an old file nobody wrote to inside the window: skip without opening
            if f and f["size"] == st.st_size and f["mtime"] == st.st_mtime:
                continue
            offset = f["offset"] if f and st.st_size >= f["size"] else 0  # a shrunk file was rewritten: read it again (dedup keeps the totals right)
            try:
                offset = self._read(p, h, offset, keep_after)
            except OSError as ex:
                self.stats["unreadable"] += 1
                log.warning("transcript unreadable (%s)", type(ex).__name__)
                continue
            self.files[h] = dict(offset=offset, size=st.st_size, mtime=st.st_mtime)
            changed = True
        for h in set(self.files) - seen:
            del self.files[h]
            changed = True
        old = [k for k, e in self.events.items() if e[0] < keep_after]
        for k in old:
            del self.events[k]
        if (changed or old) and self.cache_path:
            self._save()
        return self.stats

    def _read(self, path, h, offset, keep_after):
        own = core.transcript_excluded(path, self.own)
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read()
        end = data.rfind(b"\n") + 1  # a partial last line (still being written) is left for the next scan
        pos = offset
        for raw in data[:end].split(b"\n"):
            line_pos, pos = pos, pos + len(raw) + 1
            if not raw.strip():
                continue
            r = parse_usage_line(raw)
            if r is None:
                if b'"usage"' in raw:
                    self.stats["bad_lines"] += 1
                continue
            ts, mid, rid, nums = r
            if ts < keep_after:
                continue
            key = f"{mid}:{rid}" if mid else f"{h}@{line_pos}"
            e = self.events.get(key)
            if e:  # a repeated usage line of the same message: keep the largest figures, not the sum
                e[0] = min(e[0], ts)
                for i, n in enumerate(nums):
                    e[1 + i] = max(e[1 + i], n)
                e[5] = e[5] or own
            else:
                self.events[key] = [ts, *nums, own]
            self.stats["read"] += 1
        return offset + end

    def _save(self):
        p = pathlib.Path(self.cache_path)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(files=self.files, events=[[k, v] for k, v in self.events.items()])))
        os.replace(tmp, p)

    def window(self, start, end):
        return window_totals(self.events.values(), start, end)


def window_totals(events, start, end):
    """Sum of the events with start <= timestamp <= end. A window with no events gives zeros and a note."""
    t = dict(input=0, output=0, cache_creation=0, cache_read=0, total=0, messages=0, own_total=0)
    for ts, i, o, cc, cr, own in events:
        if start <= ts <= end:
            t["input"] += i
            t["output"] += o
            t["cache_creation"] += cc
            t["cache_read"] += cr
            t["messages"] += 1
            t["own_total"] += (i + o + cc + cr) if own else 0
    t["total"] = t["input"] + t["output"] + t["cache_creation"] + t["cache_read"]
    if not t["messages"]:
        t["note"] = "no transcript messages in this window"
    return t


def window_bounds(resets_at, length, now):
    """-> (start, basis). The window runs from resets_at minus its length; when the reading is missing or its window is already over
    the bounds fall back to the rolling window ending now (basis 'rolling')."""
    if resets_at is not None and resets_at >= now:
        return resets_at - length, "resets_at"
    return now - length, "rolling"


def build_record(now, mode, idle, idle_reason, decision, readings, scanner_windows):
    """The history record. readings: {five|week: dict(used, resets_at, age, source, reason)} (missing keys = unavailable).
    scanner_windows: {five_hour|seven_day: dict(start, basis, tokens)} or {} when the scan failed (then each window says why)."""
    rec = dict(kind="quota_snapshot", schema=1, time=core.iso(now), mode=mode, idle=None if idle is None else bool(idle),
               idle_reason=str(idle_reason)[:80], decision=str(decision)[:120], windows={})
    for name, short, length in WINDOWS:
        r = readings.get(short) or dict(source="unavailable", reason="no reading")
        w = dict(used_percentage=r.get("used"), resets_at=None if r.get("resets_at") is None else core.iso(r["resets_at"]),
                 reading_age_s=None if r.get("age") is None else round(r["age"]), source=r["source"])
        if r.get("reason"):
            w["reason"] = r["reason"][:120]
        sw = scanner_windows.get(name)
        if sw:
            w.update(window_start=core.iso(sw["start"]), window_basis=sw["basis"], tokens=sw["tokens"])
        else:
            w["tokens"] = None
            w["tokens_note"] = scanner_windows.get("error", "not computed")
        rec["windows"][name] = w
    return rec


def usage_report(now, rec):
    """The budget page's usage.json (docs/contracts/budget-panel.md): tokens_used per window plus compatible extra fields."""
    out = dict(schema=1, generated_at=core.iso(now), windows={})
    for name, w in rec["windows"].items():
        t = w.get("tokens")
        if t:
            out["windows"][name] = dict(tokens_used=t["total"], tokens_input=t["input"], tokens_output=t["output"], tokens_cache_creation=t["cache_creation"],
                                        tokens_cache_read=t["cache_read"], messages=t["messages"], tokens_own=t["own_total"],
                                        window_start=w["window_start"], window_basis=w["window_basis"])
    return out


def token_text(rec):
    """Short token part of the poll line."""
    parts = []
    for name, short in (("five_hour", "five"), ("seven_day", "week")):
        t = rec["windows"][name].get("tokens")
        parts.append(f"{short}_tokens={t['total']} ({t['messages']} msg, own {t['own_total']})" if t else f"{short}_tokens=n/a")
    return " ".join(parts)


def reading_text(rec):
    """Poll line part for the source and age of each reading: five=feed/3min (refreshed = by the minimal call just now)."""
    parts = []
    for name, short in (("five_hour", "five"), ("seven_day", "week")):
        w = rec["windows"][name]
        if w["used_percentage"] is None:
            parts.append(f"{short}=unavailable ({w.get('reason', 'no reading')})")
        else:
            age = "" if w["reading_age_s"] is None else f"/{w['reading_age_s'] / 60:.0f}min"
            parts.append(f"{short}={w['source']}{age}")
    return " ".join(parts)


def prune(path, now, days):
    """Drop history rows older than `days`; atomic rewrite. Returns (kept, dropped). Unreadable lines are dropped and counted."""
    p = pathlib.Path(path)
    try:
        lines = p.read_text().splitlines()
    except FileNotFoundError:
        return 0, 0
    cutoff, keep = now - days * 86400, []
    for line in lines:
        try:
            if core.parse_time(json.loads(line)["time"]) >= cutoff:
                keep.append(line)
        except (ValueError, KeyError, TypeError):
            pass
    if len(keep) != len(lines):
        tmp = p.with_suffix(".tmp")
        tmp.write_text("".join(l + "\n" for l in keep))
        os.replace(tmp, p)
    return len(keep), len(lines) - len(keep)


def hourly_buckets(events, since):
    """Events -> sorted hourly buckets (UTC hour start), each with the token sums; used by the backfill."""
    out = {}
    for ts, i, o, cc, cr, own in events:
        if ts < since:
            continue
        h = int(ts // 3600) * 3600
        b = out.setdefault(h, dict(input=0, output=0, cache_creation=0, cache_read=0, messages=0, own_total=0))
        b["input"] += i
        b["output"] += o
        b["cache_creation"] += cc
        b["cache_read"] += cr
        b["messages"] += 1
        b["own_total"] += (i + o + cc + cr) if own else 0
    return [dict(kind="token_backfill", reconstructed=True, percentages=None, hour_start=core.iso(h), **b,
                 total=b["input"] + b["output"] + b["cache_creation"] + b["cache_read"]) for h, b in sorted(out.items())]
