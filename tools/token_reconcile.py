"""Reconciles the token figures of a project's data folder and explains where they do not match.

Reads only numbers: the vendor's quota readings (quota-history.jsonl), the Workers' requests counted by the proxy (proxy-usage.jsonl), the
exact per-message counts of interactive sessions (transcript-usage.jsonl), the statistics cache's daily aggregates (stats-cache-usage.jsonl
and, optionally, the cache itself with --cache, because the backfill leaves out the days that have transcripts), the processing log and the
good-bye notes' `tokens` (area/<task>/outbox/goodbye.json). Prints, as text or JSON (--json):
  1 coverage: which source covers which days and what it does not count;
  2 statistics cache against transcripts on the days in both: messages, sessions, tokens per model, difference and ratio;
  3 quota against tokens: tokens per percentage point per vendor window, the spread, and the same with cache reads weighted down;
  4 per task, the proxy's sum against the good-bye note;
  5 the same daily figures per UTC day and per local day;
  6 the known reasons for mismatch, each "observed" (with the numbers) or "possible".
The Cockpit's card "How these numbers fit" (Token Usage page) is computed by `reconcile()` and `sentences()` from the same files.

    python3 tools/token_reconcile.py --data-dir DIR [--cache stats-cache.json] [--tz Europe/Berlin|+01:00] [--json]
"""
import argparse
import datetime as dt
import json
import logging
import os
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import logic as L  # noqa: E402
import stats_cache_usage as S  # noqa: E402

log = logging.getLogger("token_reconcile")

FILES = dict(quota="quota-history.jsonl", proxy="proxy-usage.jsonl", transcripts="transcript-usage.jsonl", aggregate="stats-cache-usage.jsonl",
             plog="processing/processing.jsonl")
NOT_COUNTED = dict(
    quota="no tokens: percentages of the account's 5-hour and weekly quota, whole numbers, only while the collector runs",
    proxy="the Workers' requests only: no interactive session, no other device, no web or app use",
    transcripts="interactive sessions on this machine only (the configured source folders): no Workers, no other device, no web or app use, "
                "nothing before the first collection (the tool prunes old transcripts)",
    stats_cache="daily totals per model of this machine's tool, no input/output/cache split, by the tool's local day; the backfill file leaves "
                "out days with transcripts (give --cache for the overlap)",
    notes="one figure per finished task, the Worker's own estimate; no interactive use",
)
WINDOWS = dict(five_hour=5 * 3600, seven_day=7 * 86400)
WEIGHTS = (1.0, 0.5, 0.25, 0.1, 0.0)      # weights of the cache-read tokens tried against the quota percentage
DEFINITIONS = dict(all=L.USAGE_FIELDS, io=("input_tokens", "output_tokens"), io_write=("input_tokens", "output_tokens", "cache_creation_input_tokens"))
DEF_LABEL = dict(all="all four counts", io="input + output", io_write="input + output + cache write")
AGREE, DIFFER = 0.03, 0.20                 # "agree within 3 percent", "differ by more than 20 percent"


def tzinfo(spec=None):
    """A time zone from an IANA name or a fixed offset (+01:00, -0530); None or "" is the system's local zone."""
    if not spec:
        return dt.datetime.now().astimezone().tzinfo
    if spec[0] in "+-":
        hm = spec[1:].replace(":", "")
        if not hm.isdigit() or len(hm) not in (2, 4):
            raise ValueError(f"not an offset: {spec}")
        off = dt.timedelta(hours=int(hm[:2]), minutes=int(hm[2:] or 0))
        return dt.timezone(-off if spec[0] == "-" else off)
    import zoneinfo
    return zoneinfo.ZoneInfo(spec)


def _read(path):
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def note_tokens(data_dir, folder):
    """The `tokens` of a task's good-bye note, read inside the data folder's area (only the folder's name is taken from the log), or None."""
    try:
        t = json.loads(_read(pathlib.Path(data_dir) / "area" / pathlib.PurePath(folder).name / "outbox" / "goodbye.json")).get("tokens")
    except (TypeError, ValueError, AttributeError):
        return None
    return t if isinstance(t, int) and not isinstance(t, bool) else None


def load(data_dir, cache=None):
    """The texts of the data folder's files (None when missing), the parsed cache (None when not given or unreadable) and the note reader."""
    d = pathlib.Path(os.path.expanduser(str(data_dir)))
    src = {k: _read(d / f) for k, f in FILES.items()}
    src["cache"] = None
    if cache:
        try:
            src["cache"] = S.records(json.loads(_read(os.path.expanduser(cache)) or "null"))
        except ValueError:
            log.warning("the statistics cache is missing or malformed; the comparison uses the backfill file only")
    src["notes"] = lambda folder: note_tokens(d, folder)
    return src


# ---------- the parsed sources ----------

def _msgs(text):
    """transcript-usage records -> [(epoch, rec)]."""
    return [(t, r) for r in L._json_lines(text) or [] if (t := L._epoch(r.get("ts"))) is not None]


def _reqs(text):
    return [(t, r) for r in L._json_lines(text) or [] if (t := L._epoch(r.get("ts"))) is not None]


def _reads(text):
    return sorted(((t, r) for r in L._json_lines(text) or [] if (t := L._epoch(r.get("ts"))) is not None), key=lambda x: x[0])


def _cache_days(aggregate_text, cache_recs):
    """{day: dict(messages, sessions, tokens={model: n})} of the backfill file and the cache's own records (the cache wins per day)."""
    out = {}
    for group in ([r for r in L._json_lines(aggregate_text) or [] if r.get("source") == S.SOURCE], cache_recs or []):
        days = {}
        for r in group:
            d = S._day(r.get("day"))
            if not d:
                continue
            e = days.setdefault(d, dict(messages=S._int(r.get("messages")), sessions=S._int(r.get("sessions")), tokens={}))
            if r.get("model") is not None and S._int(r.get("tokens")) is not None:
                e["tokens"][str(r["model"])] = r["tokens"]
        out.update(days)
    return out


def _day(t, tz):
    return dt.datetime.fromtimestamp(t, tz).date().isoformat()


def _sum(rec, fields):
    return sum(L._counts(rec)[k] for k in fields)


def _cmp(a, b):
    """a against b: dict(a, b, diff=a-b, ratio=a/b) (ratio None when b is 0 or a value is missing)."""
    if a is None or b is None:
        return dict(a=a, b=b, diff=None, ratio=None)
    return dict(a=a, b=b, diff=a - b, ratio=round(a / b, 4) if b else None)


def _spread(values):
    if not values:
        return None
    mean = statistics.fmean(values)
    return dict(n=len(values), median=round(statistics.median(values)), low=round(min(values)), high=round(max(values)),
                cv=round(statistics.pstdev(values) / mean, 4) if mean else None)


# ---------- the six parts ----------

def coverage(src):
    """One row per source: present, first and last day (UTC; the statistics cache's day as recorded, the tool's local day), number of days
    and records, what it does not count."""
    def row(name, file, present, days, records):
        days = sorted(days)
        return dict(source=name, file=file, present=present, first=days[0] if days else None, last=days[-1] if days else None,
                    days=len(days), records=records, not_counted=NOT_COUNTED[name])
    utc = dt.timezone.utc
    q, p, m = _reads(src["quota"]), _reqs(src["proxy"]), _msgs(src["transcripts"])
    cd = _cache_days(src["aggregate"], src["cache"])
    tasks = [t for t in (L.usage_report(src["proxy"], src["plog"], src["notes"])["tasks"] or []) if t["note"] is not None]
    return [row("quota", FILES["quota"], src["quota"] is not None, {_day(t, utc) for t, _ in q}, len(q)),
            row("proxy", FILES["proxy"], src["proxy"] is not None, {_day(t, utc) for t, _ in p}, len(p)),
            row("transcripts", FILES["transcripts"], src["transcripts"] is not None, {_day(t, utc) for t, _ in m}, len(m)),
            row("stats_cache", FILES["aggregate"] + (" + cache" if src["cache"] is not None else ""),
                src["aggregate"] is not None or src["cache"] is not None, set(cd), sum(max(1, len(v["tokens"])) for v in cd.values())),
            row("notes", "area/*/outbox/goodbye.json", bool(tasks), {t["start"][:10] for t in tasks}, len(tasks))]


def _transcript_days(msgs, tz):
    """{day: dict(messages, sessions, agents, tokens={definition: {model: n}})} of the transcripts by the day of `tz`."""
    out = {}
    for t, r in msgs:
        e = out.setdefault(_day(t, tz), dict(messages=0, sessions=set(), agents=set(), tokens={k: {} for k in DEFINITIONS}))
        e["messages"] += 1
        e["sessions"].add((r.get("source"), r.get("session")))
        e["agents"].add((r.get("source"), r.get("session"), r.get("agent")))
        model = str(r.get("model"))
        for k, fields in DEFINITIONS.items():
            e["tokens"][k][model] = e["tokens"][k].get(model, 0) + _sum(r, fields)
    return out


def _fit(cache, tdays):
    """The days in both, compared per token definition: {definition: dict(days, agree, differ, median_ratio)}."""
    out = {}
    for k in DEFINITIONS:
        ratios = [sum(cache[d]["tokens"].values()) / sum(tdays[d]["tokens"][k].values())
                  for d in sorted(set(cache) & set(tdays)) if cache[d]["tokens"] and sum(tdays[d]["tokens"][k].values())]
        out[k] = dict(days=len(ratios), agree=sum(abs(r - 1) <= AGREE for r in ratios), differ=sum(abs(r - 1) > DIFFER for r in ratios),
                      median_ratio=round(statistics.median(ratios), 4) if ratios else None)
    return out


def cache_vs_transcripts(src, tz):
    """Part 2: on the days in both (the cache's local day against the transcripts' day in `tz`), messages, sessions and tokens per model
    (the cache against the transcripts), with the token definition that fits best and the fit by UTC day for comparison (part 5)."""
    cache, msgs = _cache_days(src["aggregate"], src["cache"]), _msgs(src["transcripts"])
    local, utc = _transcript_days(msgs, tz), _transcript_days(msgs, dt.timezone.utc)
    fit = _fit(cache, local)
    best = max(fit, key=lambda k: (fit[k]["agree"], -abs((fit[k]["median_ratio"] or 99) - 1)))
    days = []
    for d in sorted(set(cache) & set(local)):
        c, t = cache[d], local[d]
        models = sorted(set(c["tokens"]) | set(t["tokens"][best]))
        days.append(dict(day=d, messages=_cmp(c["messages"], t["messages"]), sessions=_cmp(c["sessions"], len(t["sessions"])),
                         sessions_with_agents=len(t["agents"]),
                         tokens=_cmp(sum(c["tokens"].values()) if c["tokens"] else None, sum(t["tokens"][best].values())),
                         models={m: _cmp(c["tokens"].get(m), t["tokens"][best].get(m, 0)) for m in models}))
    tday = sorted(local)
    return dict(definition=best, definition_label=DEF_LABEL[best], fit=fit, fit_utc=_fit(cache, utc), days=days,
                cache_only=[d for d in sorted(cache) if d not in local and (not tday or d < tday[0])],
                transcripts_only=[d for d in tday if d not in cache])


def _windows(reads, kind):
    """The vendor windows of `kind` from the readings: [(start, end, highest percentage)], merging reset times closer than 10 minutes."""
    ends = []
    for t, r in reads:
        b = r.get(kind)
        e, pct = (b.get("resets_at"), L._pct(b, "used_percentage")) if isinstance(b, dict) else (None, None)
        if not isinstance(e, (int, float)) or isinstance(e, bool) or pct is None or not e - WINDOWS[kind] <= t < e:
            continue
        w = next((w for w in ends if abs(w[1] - e) <= 600), None)
        if w is None:
            ends.append([e - WINDOWS[kind], e, pct])
        else:
            w[2] = max(w[2], pct)
    return sorted(ends)


def quota_vs_tokens(src):
    """Part 3: per vendor window, the highest percentage, the tokens of the proxy and the transcripts in it and tokens per percentage
    point; the spread over the windows (not partial, at least 1 percent and some tokens) per cache-read weight; the best weight."""
    reads, msgs, reqs = _reads(src["quota"]), _msgs(src["transcripts"]), _reqs(src["proxy"])
    since = min((t for t, _ in msgs), default=None) if src["transcripts"] is not None else None
    out = {}
    for kind in WINDOWS:
        rows = []
        for start, end, pct in _windows(reads, kind):
            c = {k: 0 for k in L.USAGE_FIELDS}
            parts = dict(proxy=0, transcripts=0)
            for name, recs in (("proxy", reqs), ("transcripts", msgs)):
                for t, r in recs:
                    if start <= t < end:
                        n = L._counts(r)
                        parts[name] += n["total"]
                        for k in L.USAGE_FIELDS:
                            c[k] += n[k]
            total = sum(c.values())
            rows.append(dict(start=L._iso(start), end=L._iso(end), pct=pct, tokens=total, **parts, cache_read=c["cache_read_input_tokens"],
                             partial=since is None or start < since, per_point=round(total / pct) if pct else None))
        used = [r for r in rows if not r["partial"] and r["pct"] >= 1 and r["tokens"]]
        weights = {}
        for w in WEIGHTS:
            weights[str(w)] = _spread([(r["tokens"] - (1 - w) * r["cache_read"]) / r["pct"] for r in used])
        fits = [w for w in weights if weights[w] and weights[w]["cv"] is not None and weights[w]["n"] >= 2]
        out[kind] = dict(windows=rows, used=len(used), spread=weights["1.0"], weights=weights,
                         best_weight=min(fits, key=lambda w: weights[w]["cv"]) if fits else None,
                         unattributed=[r for r in rows if r["pct"] >= 1 and not r["tokens"] and not r["partial"]],
                         untracked=[r for r in rows if r["pct"] == 0 and r["tokens"]])
    return out


def tasks_vs_notes(src):
    """Part 4: per task, the proxy's sum against its good-bye note (the Token Usage page's spans: logic.usage_report)."""
    tasks = L.usage_report(src["proxy"], src["plog"], src["notes"])["tasks"] or []
    return [dict(task=t["task"], start=t["start"], worker=t["worker"], overlap=t["overlap"], **_cmp(t["proxy"], t["note"])) for t in tasks]


def day_boundaries(src, tz):
    """Part 5: per day, the proxy's and the transcripts' tokens and messages by UTC day and by local day (`tz`); `moved`: the tokens a
    day gains or loses by the local day."""
    utc = dt.timezone.utc
    rows = {}
    for name, recs in (("proxy", _reqs(src["proxy"])), ("transcripts", _msgs(src["transcripts"]))):
        for t, r in recs:
            n = L._counts(r)["total"]
            for zone, z in (("utc", utc), ("local", tz)):
                e = rows.setdefault(_day(t, z), dict(proxy_utc=0, proxy_local=0, transcripts_utc=0, transcripts_local=0, messages_utc=0, messages_local=0))
                e[f"{name}_{zone}"] += n
                if name == "transcripts":
                    e[f"messages_{zone}"] += 1
    days = [dict(day=d, **v, moved=v["proxy_local"] + v["transcripts_local"] - v["proxy_utc"] - v["transcripts_utc"]) for d, v in sorted(rows.items())]
    off = dt.datetime.now(tz).utcoffset()
    return dict(tz=str(tz), offset_hours=off.total_seconds() / 3600 if off is not None else 0, days=days,
                changed=sum(1 for d in days if d["moved"]), moved=sum(abs(d["moved"]) for d in days) // 2)


def reasons(rep, src):
    """Part 6: the known reasons for a mismatch, each observed here (with the numbers) or possible."""
    msgs = _msgs(src["transcripts"])
    out = []

    def add(key, label, evidence):
        out.append(dict(key=key, label=label, status="observed" if evidence else "possible", evidence=evidence or ""))
    ids = {}
    for t, r in msgs:
        ids.setdefault(r.get("message"), []).append(L._counts(r)["total"])
    dup = {m: v for m, v in ids.items() if len(v) > 1}
    add("double", "a message counted twice (the same message id in two sessions, e.g. a resumed session copying its history)",
        dup and f"{len(dup)} message ids appear more than once in the transcripts, {sum(sum(v[1:]) for v in dup.values()):,} tokens counted again")
    sub = [L._counts(r)["total"] for t, r in msgs if r.get("agent")]
    c = rep["cache_vs_transcripts"]
    add("subagents", "sub-agent sessions (counted with their parent session in the transcripts; the statistics cache may count them apart or not at all)",
        sub and f"{len(sub)} sub-agent messages with {sum(sub):,} tokens in the transcripts")
    q = rep["quota_vs_tokens"]["five_hour"]
    un = q["unattributed"]
    other = un and f"{len(un)} 5-hour windows show quota use with no token counted here (other devices and the web product cannot be told apart)"
    add("devices", "other devices (their use counts in the quota but in no file here)", other)
    add("web", "the web or app product (counts in the quota, not covered by any file here)", other)
    early = c["cache_only"]
    add("pruned", "pruned history (transcripts deleted before the first collection; only the statistics cache remembers those days)",
        early and f"{len(early)} days with statistics-cache data before the first transcript day ({early[0]} to {early[-1]})")
    used = [r for r in q["windows"] if not r["partial"] and r["pct"] >= 1 and r["tokens"]]
    low = min((r["pct"] for r in used), default=None)
    add("rounding", "rounding of the percentage (whole numbers: half a point of error per window)",
        low is not None and low < 10 and f"{sum(r['pct'] < 10 for r in used)} windows below 10 %; at {low:g} % the rounding alone moves tokens per point by up to {50 / low:.0f} %")
    b = rep["day_boundaries"]
    add("day", "day boundaries (the statistics cache uses the local day, the page's tables the UTC day)",
        b["changed"] and f"{b['changed']} days change and {b['moved']:,} tokens move between days when counted by local day ({b['tz']}) instead of UTC")
    add("definition", "what the statistics cache's token total includes (no split is recorded)",
        c["days"] and c["definition"] != "all" and f"its totals fit {c['definition_label']} best ({c['fit'][c['definition']]['agree']} of {c['fit'][c['definition']]['days']} days within 3 %)")
    tk = [t for t in rep["tasks_vs_notes"] if t["diff"]]
    add("note", "the good-bye note is the Worker's own estimate, not a count",
        tk and f"{len(tk)} notes differ from the proxy's sum (median ratio note/proxy {statistics.median(t['b'] / t['a'] for t in tk if t['a']) if any(t['a'] for t in tk) else 0:.2f})")
    ov = [t for t in rep["tasks_vs_notes"] if t["overlap"]]
    add("overlap", "a task's proxy span overlaps another task's (older tasks without a Worker name count all Workers)",
        ov and f"{len(ov)} tasks overlap another")
    return out


def _pctf(x):
    return f"{abs(x - 1) * 100:.0f} %"


def sentences(rep):
    """The three or four most important matches and mismatches in plain sentences."""
    out = []
    c = rep["cache_vs_transcripts"]
    f = c["fit"][c["definition"]]
    if f["days"]:
        s = f"The statistics cache and the transcripts agree within 3 percent on {f['agree']} of {f['days']} days"
        s += f"; on {f['differ']} days they differ by more than 20 percent" if f["differ"] else ""
        out.append(s + f" (cache totals read as {c['definition_label']}; median ratio cache/transcripts {f['median_ratio']:.2f}).")
    else:
        out.append("No day is in both the statistics cache and the transcripts, so they cannot be compared"
                   + (" (the backfill file leaves out days with transcripts; the script with --cache compares them)." if c["transcripts_only"] else "."))
    q = rep["quota_vs_tokens"]["five_hour"]
    if q["spread"]:
        s, w = q["spread"], q["best_weight"]
        t = f"One point of the 5-hour quota was {s['median']:,} tokens on median over {s['n']} windows (from {s['low']:,} to {s['high']:,})"
        if w is not None and w != "1.0" and s["cv"] is not None:
            t += f"; counting cache reads at {float(w):g} of their weight fits best ({q['weights'][w]['median']:,} per point, spread {q['weights'][w]['cv']:.0%} instead of {s['cv']:.0%})"
        out.append(t + ".")
    elif q["windows"]:
        out.append("No 5-hour window has both a percentage of at least 1 and counted tokens over its whole span, so tokens per point cannot be computed.")
    tk = [t for t in rep["tasks_vs_notes"] if t["a"] is not None and t["b"] is not None]
    if tk:
        close = sum(1 for t in tk if t["b"] and abs(t["a"] / t["b"] - 1) <= 0.10)
        out.append(f"{close} of {len(tk)} good-bye notes are within 10 percent of the proxy's sum; the sum of differences (proxy minus notes) is {sum(t['diff'] for t in tk):,} tokens.")
    b = rep["day_boundaries"]
    if b["changed"]:
        out.append(f"Counting by local day ({b['tz']}) instead of UTC moves {b['moved']:,} tokens between days on {b['changed']} days.")
    return out[:4]


def reconcile(src, tz=None):
    """The full report of the sources `src` (load()); `tz`: the local zone (default: the system's)."""
    tz = tz or tzinfo()
    rep = dict(coverage=coverage(src), cache_vs_transcripts=cache_vs_transcripts(src, tz), quota_vs_tokens=quota_vs_tokens(src),
               tasks_vs_notes=tasks_vs_notes(src), day_boundaries=day_boundaries(src, tz))
    rep["reasons"] = reasons(rep, src)
    rep["sentences"] = sentences(rep)
    return rep


def card(rep):
    """The Token Usage page's card: the coverage table, the sentences and the reasons."""
    return dict(coverage=rep["coverage"], sentences=rep["sentences"], reasons=rep["reasons"])


# ---------- text output ----------

def text(rep):
    n = lambda x: "-" if x is None else f"{x:,}" if isinstance(x, int) else f"{x:g}" if isinstance(x, float) else str(x)
    L_ = ["How these numbers fit", ""] + ["  " + s for s in rep["sentences"]] + ["", "1 Coverage"]
    for r in rep["coverage"]:
        L_.append(f"  {r['source']:<12} {'present' if r['present'] else 'missing':<8} {n(r['first'])} .. {n(r['last'])}  days {r['days']}  records {r['records']}")
        L_.append(f"  {'':<12} does not count: {r['not_counted']}")
    c = rep["cache_vs_transcripts"]
    L_ += ["", f"2 Statistics cache against transcripts (local day; cache tokens read as {c['definition_label']})"]
    for k, f in c["fit"].items():
        u = c["fit_utc"][k]
        L_.append(f"  {DEF_LABEL[k]:<30} days {f['days']}  within 3 % {f['agree']}  over 20 % {f['differ']}  median ratio {n(f['median_ratio'])}"
                  f"   (by UTC day: {u['agree']} within 3 %, median {n(u['median_ratio'])})")
    for d in c["days"]:
        L_.append(f"  {d['day']}  messages {n(d['messages']['a'])}/{n(d['messages']['b'])} ({n(d['messages']['ratio'])})"
                  f"  sessions {n(d['sessions']['a'])}/{n(d['sessions']['b'])} (with sub-agents {d['sessions_with_agents']})"
                  f"  tokens {n(d['tokens']['a'])}/{n(d['tokens']['b'])} diff {n(d['tokens']['diff'])} ratio {n(d['tokens']['ratio'])}")
        for m, v in d["models"].items():
            L_.append(f"      {m:<28} {n(v['a'])}/{n(v['b'])} diff {n(v['diff'])} ratio {n(v['ratio'])}")
    L_ += [f"  cache-only days before the transcripts: {len(c['cache_only'])}; transcript days without cache data: {len(c['transcripts_only'])}"]
    L_ += ["", "3 Quota against tokens (proxy + transcripts in the vendor's window)"]
    for kind, q in rep["quota_vs_tokens"].items():
        L_.append(f"  {kind}: {len(q['windows'])} windows, {q['used']} used; best cache-read weight {n(q['best_weight'])}")
        for w, s in q["weights"].items():
            if s:
                L_.append(f"    weight {w:<5} per point median {n(s['median'])}  low {n(s['low'])}  high {n(s['high'])}  cv {n(s['cv'])}")
        for r in q["windows"]:
            L_.append(f"    {r['start']} .. {r['end']}  {n(r['pct'])} %  tokens {n(r['tokens'])} (proxy {n(r['proxy'])}, transcripts {n(r['transcripts'])})"
                      f"  per point {n(r['per_point'])}{'  partial' if r['partial'] else ''}")
    L_ += ["", "4 Per task: proxy sum against good-bye note"]
    for t in rep["tasks_vs_notes"]:
        L_.append(f"  {t['task']:<16} proxy {n(t['a'])}  note {n(t['b'])}  diff {n(t['diff'])}  ratio {n(t['ratio'])}{'  overlap' if t['overlap'] else ''}")
    b = rep["day_boundaries"]
    L_ += ["", f"5 Per UTC day and per local day ({b['tz']}, UTC{b['offset_hours']:+g} now)"]
    for d in b["days"]:
        L_.append(f"  {d['day']}  proxy {n(d['proxy_utc'])}/{n(d['proxy_local'])}  transcripts {n(d['transcripts_utc'])}/{n(d['transcripts_local'])}"
                  f"  messages {n(d['messages_utc'])}/{n(d['messages_local'])}  moved {n(d['moved'])}")
    L_ += ["", "6 Known reasons for mismatch"]
    for r in rep["reasons"]:
        L_.append(f"  [{r['status']}] {r['label']}" + (f": {r['evidence']}" if r["evidence"] else ""))
    return "\n".join(L_)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-dir", required=True, help="the project's data folder (~/.local/share/project-cockpit/<project>)")
    p.add_argument("--cache", help="the tool's statistics cache (stats-cache.json): its days that have transcripts too (numbers only)")
    p.add_argument("--tz", help="the local time zone, an IANA name or +HH:MM (default: the system's)")
    p.add_argument("--json", action="store_true", help="print the report as JSON instead of text")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s token_reconcile %(message)s")
    try:
        tz = tzinfo(a.tz)
    except (ValueError, KeyError) as e:
        p.error(f"--tz: {e}")
    rep = reconcile(load(a.data_dir, a.cache), tz)
    print(json.dumps(rep, indent=1, default=str) if a.json else text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
