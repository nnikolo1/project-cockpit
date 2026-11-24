#!/usr/bin/env python3
"""churn.json collector: code churn per module over N-day windows (git, read-only).

Runs `git log --numstat -M --no-merges` and maps every changed path to a module of the map (cardmeta_map.module_of). Per module and
window: commits, files, added, removed, net, churn (added + removed), churn split into source / tests / docs, active days (UTC days with
a commit) and the trend against the previous window of the same length (`trend_pct` null when that window was empty). `own` = files
mapped to the module itself, `total` = with its sub-modules. `size` = current code lines per module (non-blank lines that are not whole-line
comments; counted over tracked files of known source types). Paths the map cannot place go to `unassigned`.
History of files that are not in the current tree (deleted; a rename is followed to its new path) is no code of any module: it is
counted as excluded with the reason "deleted files", never in a module or `unassigned`.
Check: lines in all buckets plus the excluded lines must equal the raw numstat total of each window (asserted; exit code 2 otherwise).
Excluded by default (generated files): .coverage, **/package-lock.json, **/yarn.lock, **/poetry.lock, **/uv.lock, docs/screens/**.
Each module and `unassigned` also gets `series.weekly` (own/total, 52 Mondays, from the same records and mapping).
Shape: docs/contracts/card-metadata.md.

Usage: python3 tools/cardmeta_churn.py --project NAME [--repo .] [--days 10 --days 30 --days 90] [--until ISO] [--out-dir DIR] [--format json|md]
"""
from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cardmeta_ambiguity as amb  # noqa: E402  (shared CLI helpers)
import cardmeta_common as cm  # noqa: E402
import cardmeta_map as mp  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import logic as L  # noqa: E402  (CARD_CHURN_DAYS: the window the cards show)

log = logging.getLogger("cardmeta.churn")

DEFAULT_EXCLUDES = [".coverage", "**/package-lock.json", "**/yarn.lock", "**/poetry.lock", "**/uv.lock", "docs/screens/**"]
CATEGORIES = ("source", "tests", "docs")
SOURCE_SUFFIXES = {".py": "#", ".js": "//", ".sh": "#", ".html": None, ".css": None, ".yml": "#", ".yaml": "#"}
WEEKS = 52
DEFAULT_DAYS = [L.CARD_CHURN_DAYS, 30, 90]  # the cards show the first; 30 and 90 stay for other views
_HEADER = "@@@"
_NUMSTAT = re.compile(r"^(\d+|-)\t(\d+|-)\t(.+)$")
_BRACE = re.compile(r"^(.*)\{(.*) => (.*)\}(.*)$")


def glob_to_regex(pat: str) -> re.Pattern:
    """'**/' = any directories (or none), '**' = anything, '*' = anything but '/', '?' = one non-'/' char."""
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pat.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pat[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pat[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pat[i]), i + 1
    return re.compile(out + r"\Z")


def rename_target(spec: str) -> str:
    """numstat path of a rename ('a/{b => c}/d', 'old => new') -> the new path."""
    m = _BRACE.match(spec)
    if m:
        pre, _, new, post = m.groups()
        return (pre + new + post).replace("//", "/")
    return spec.split(" => ", 1)[1] if " => " in spec else spec


def parse_log(text: str) -> list[dict]:
    """`git log --format='@@@%H|%ct' --numstat` output -> one record per (commit, file): {sha, ts, path, added, removed}."""
    recs, sha, ts = [], None, 0
    for line in text.splitlines():
        if line.startswith(_HEADER):
            sha, _, t = line[len(_HEADER):].partition("|")
            ts = int(t)
            continue
        m = _NUMSTAT.match(line)
        if not m or sha is None:
            continue
        a, d, spec = m.groups()
        binary = a == "-" or d == "-"
        recs.append({"sha": sha, "ts": ts, "path": rename_target(spec), "added": 0 if binary else int(a), "removed": 0 if binary else int(d)})
    return recs


def category_of(path: str) -> str:
    if path.startswith("docs/") or path.endswith(".md"):
        return "docs"
    return "tests" if mp.is_test_path(path) else "source"


def _empty() -> dict:
    return {"commits": set(), "files": set(), "added": 0, "removed": 0, "days": set(), **{c: [0, 0] for c in CATEGORIES}}


def _merge(dst: dict, src: dict) -> None:
    for k in ("commits", "files", "days"):
        dst[k] |= src[k]
    dst["added"] += src["added"]
    dst["removed"] += src["removed"]
    for c in CATEGORIES:
        dst[c][0] += src[c][0]
        dst[c][1] += src[c][1]


def _finish(raw: dict, prev_churn: int) -> dict:
    churn = raw["added"] + raw["removed"]
    return {"commits": len(raw["commits"]), "files": len(raw["files"]), "added": raw["added"], "removed": raw["removed"],
            "net": raw["added"] - raw["removed"], "churn": churn,
            **{c: {"added": raw[c][0], "removed": raw[c][1], "churn": raw[c][0] + raw[c][1]} for c in CATEGORIES},
            "active_days": len(raw["days"]), "prev_churn": prev_churn,
            "trend_pct": round((churn - prev_churn) * 100.0 / prev_churn, 1) if prev_churn else None}


def aggregate(records: list[dict], mmap: dict, days_list: list[int], until: datetime, patterns: list[re.Pattern],
              existing: set[str] | None = None) -> dict:
    """Pure: records -> {modules, unassigned, excluded, mapping_check}. `unassigned` is {days: window} as in the contract.
    `existing` = paths of the current tree; records of other paths are excluded as "deleted files" (None: no such rule)."""
    until_ts = until.timestamp()
    ids = [m["id"] for m in mmap["modules"]]
    own: dict[tuple, dict] = {}
    check, excl_files, excl_lines = {}, set(), 0
    del_files, del_lines = set(), 0
    for d in days_list:
        cur_lo, prev_lo = until_ts - d * 86400, until_ts - 2 * d * 86400
        raw_lines = bucket_lines = ex_lines = dl_lines = 0
        for r in records:
            if cur_lo < r["ts"] <= until_ts:
                period = "cur"
            elif prev_lo < r["ts"] <= cur_lo:
                period = "prev"
            else:
                continue
            n = r["added"] + r["removed"]
            raw_lines += n if period == "cur" else 0
            if any(p.match(r["path"]) for p in patterns):
                if period == "cur":
                    ex_lines += n
                    excl_files.add(r["path"])
                continue
            if existing is not None and r["path"] not in existing:
                if period == "cur":
                    dl_lines += n
                    del_files.add(r["path"])
                continue
            s = own.setdefault((mp.module_of(r["path"], mmap), d, period), _empty())
            s["commits"].add(r["sha"])
            s["files"].add(r["path"])
            s["days"].add(datetime.fromtimestamp(r["ts"], timezone.utc).strftime("%Y-%m-%d"))
            s["added"] += r["added"]
            s["removed"] += r["removed"]
            cat = category_of(r["path"])
            s[cat][0] += r["added"]
            s[cat][1] += r["removed"]
            bucket_lines += n if period == "cur" else 0
        excl_lines += ex_lines
        del_lines += dl_lines
        check[str(d)] = {"raw_lines": raw_lines, "bucket_lines": bucket_lines, "excluded_lines": ex_lines + dl_lines, "ok": raw_lines == bucket_lines + ex_lines + dl_lines}

    def window(mid: str, d: int, scoped: bool) -> dict:
        cur, prev = _empty(), _empty()
        for (m, dd, p), raw in own.items():
            if dd == d and (mp.in_module(m, mid) if scoped else m == mid):
                _merge(cur if p == "cur" else prev, raw)
        return _finish(cur, prev["added"] + prev["removed"])

    mods = {mid: {"own": {str(d): window(mid, d, False) for d in days_list}, "total": {str(d): window(mid, d, True) for d in days_list}}
            for mid in ids}
    un = {str(d): window(mp.UNASSIGNED, d, False) for d in days_list}
    ser = weekly_series(records, mmap, until, patterns, existing)
    for mid in ids:
        mods[mid]["series"] = {"weekly": ser[mid]}
    un["series"] = {"weekly": {"own": ser[mp.UNASSIGNED]["own"], "total": ser[mp.UNASSIGNED]["own"]}}
    return {"modules": mods, "unassigned": un, "mapping_check": check, "excluded": {"files": len(excl_files | del_files), "lines": excl_lines + del_lines,
                                                                                    "by_reason": {"generated": {"files": len(excl_files), "lines": excl_lines},
                                                                                                  "deleted files": {"files": len(del_files), "lines": del_lines}}}}


def week_starts(until: datetime, weeks: int = WEEKS) -> list[str]:
    """Mondays (UTC, YYYY-MM-DD) of the last `weeks` weeks, oldest first, the last one being the week of `until`."""
    u = until.astimezone(timezone.utc).date()
    monday = u - timedelta(days=u.weekday())
    return [(monday - timedelta(weeks=k)).isoformat() for k in range(weeks - 1, -1, -1)]


def weekly_series(records: list[dict], mmap: dict, until: datetime, patterns: list[re.Pattern], existing: set[str] | None = None) -> dict:
    """Pure: weekly series per module id (and `unassigned`), {id: {"own": [...], "total": [...]}}, same filters and mapping as the windows."""
    weeks = week_starts(until)
    first = datetime.fromisoformat(weeks[0]).replace(tzinfo=timezone.utc).timestamp()
    until_ts = until.timestamp()
    cell: dict[tuple, dict] = {}  # (own module, week) -> {sha set, added, removed, tests, docs}
    for r in records:
        if not first <= r["ts"] <= until_ts or any(p.match(r["path"]) for p in patterns):
            continue
        if existing is not None and r["path"] not in existing:
            continue
        d = datetime.fromtimestamp(r["ts"], timezone.utc).date()
        w = (d - timedelta(days=d.weekday())).isoformat()
        c = cell.setdefault((mp.module_of(r["path"], mmap), w), {"shas": set(), "added": 0, "removed": 0, "tests": 0, "docs": 0})
        c["shas"].add(r["sha"])
        c["added"] += r["added"]
        c["removed"] += r["removed"]
        cat = category_of(r["path"])
        if cat != "source":
            c[cat] += r["added"] + r["removed"]

    def series(match) -> list[dict]:
        out = []
        for w in weeks:
            shas, tot = set(), {"added": 0, "removed": 0, "tests": 0, "docs": 0}
            for (m, ww), c in cell.items():
                if ww == w and match(m):
                    shas |= c["shas"]
                    for k in tot:
                        tot[k] += c[k]
            out.append({"w": w, "commits": len(shas), **tot})
        return out

    res = {m["id"]: {"own": series(lambda x, i=m["id"]: x == i), "total": series(lambda x, i=m["id"]: mp.in_module(x, i))} for m in mmap["modules"]}
    res[mp.UNASSIGNED] = {"own": series(lambda x: x == mp.UNASSIGNED)}
    return res


def count_code_lines(path: str, text: str) -> int | None:
    """Non-blank lines that are not whole-line comments; None for a file type that is not source."""
    suffix = Path(path).suffix
    if suffix not in SOURCE_SUFFIXES:
        return None
    marker = SOURCE_SUFFIXES[suffix]
    return sum(1 for ln in text.splitlines() if ln.strip() and not (marker and ln.strip().startswith(marker)))


def size_by_module(entries: list[tuple[str, int]], mmap: dict) -> dict:
    """Pure: [(path, code lines)] -> {module id: size} with own and total folded as the contract's single `size` (own for the module and
    its sub-modules together = total). Test files count as test_lines."""
    ids = [m["id"] for m in mmap["modules"]]
    own = {i: {"source_lines": 0, "test_lines": 0, "files": 0} for i in ids}
    for path, lines in entries:
        mod = mp.module_of(path, mmap)
        if mod in own:
            own[mod]["test_lines" if mp.is_test_path(path) else "source_lines"] += lines
            own[mod]["files"] += 1
    return {mid: {k: sum(v[k] for m, v in own.items() if mp.in_module(m, mid)) for k in ("source_lines", "test_lines", "files")} for mid in ids}


def collect_code_lines(repo: Path, patterns: list[re.Pattern]) -> list[tuple[str, int]]:
    entries = []
    for f in mp.tracked_files(repo):
        if any(p.match(f) for p in patterns):
            continue
        try:
            c = count_code_lines(f, (repo / f).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            log.warning("tracked file not readable, skipped in the size: %s", f)
            continue
        if c is not None:
            entries.append((f, c))
    return entries


def git_log(repo: Path, since: datetime, until: datetime) -> str:
    cmd = ["git", "-C", str(repo), "-c", "core.quotepath=off", "log", "--numstat", "-M", f"--format={_HEADER}%H|%ct",
           f"--since={since.isoformat()}", f"--until={until.isoformat()}", "--no-merges"]
    log.info("running: git log --numstat over %s .. %s", since.date(), until.date())
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def parse_until(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def render_md(res: dict, days: list[int]) -> str:
    o = ["# Code churn per module", ""]
    for d in map(str, days):
        o += [f"## Last {d} days", "", "| module | commits | files | + | - | churn | trend % |", "|---|--:|--:|--:|--:|--:|--:|"]
        for mid, e in res["modules"].items():
            s = e["total"][d]
            o.append(f"| {mid} | {s['commits']} | {s['files']} | {s['added']} | {s['removed']} | {s['churn']} | {s['trend_pct']} |")
        u, c = res["unassigned"][d], res["mapping_check"][d]
        o += ["", f"unassigned: churn {u['churn']} in {u['files']} files; mapping check {'OK' if c['ok'] else 'MISMATCH'}", ""]
    return "\n".join(o)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    amb.add_common_args(ap)
    ap.add_argument("--days", type=int, action="append", help="window length in days, repeatable (default %s)" % ", ".join(map(str, DEFAULT_DAYS)))
    ap.add_argument("--until", help="end of the windows, ISO date/time (default now, UTC)")
    ap.add_argument("--exclude", action="append", help="glob to exclude (repeatable; replaces the defaults)")
    ap.add_argument("--format", choices=["json", "md"], default="json", help="md prints a summary and writes nothing")
    a = ap.parse_args(argv)
    repo, map_path, project = amb.setup(a)
    out_dir = cm.resolve_out_dir(a.out_dir, repo, project)  # refuse a bad folder before any work
    days = sorted(set(a.days or DEFAULT_DAYS))
    until = parse_until(a.until)
    pats = [glob_to_regex(p) for p in (a.exclude if a.exclude is not None else DEFAULT_EXCLUDES)]
    mmap = mp.load_map(map_path)
    records = parse_log(git_log(repo, until - timedelta(days=max(2 * max(days), 7 * WEEKS + 7)), until))
    log.info("%d file changes parsed", len(records))
    res = aggregate(records, mmap, days, until, pats, set(mp.tracked_files(repo)))
    for d, c in res["mapping_check"].items():
        log.info("mapping check %sd: raw %d = buckets %d + excluded %d -> %s", d, c["raw_lines"], c["bucket_lines"], c["excluded_lines"], "OK" if c["ok"] else "MISMATCH")
        if not c["ok"]:
            log.error("mapping check failed for the %sd window", d)
            return 2
    if a.format == "md":
        sys.stdout.write(render_md(res, days))
        return 0
    for mid, sz in size_by_module(collect_code_lines(repo, pats), mmap).items():
        res["modules"][mid]["size"] = sz
    cm.write_kind(out_dir, "churn", {**cm.header("churn", project, until=cm.iso_utc(until)), "windows": days, **res})
    return 0


if __name__ == "__main__":
    sys.exit(main())
