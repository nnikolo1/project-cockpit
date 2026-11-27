"""Usage report, session transcripts, quota readings and estimates. Depends on modules and settings."""
import bisect
import datetime as dt
import json
import re
from ckp.core.modules import USAGE_FIELDS, _num
from ckp.core.settings import validate_quota_settings


WINDOW_SECONDS = 5 * 3600


def _json_lines(text):
    """The JSON objects of a JSON-lines text; an unreadable line is skipped. None for no text (missing file)."""
    if text is None:
        return None
    out = []
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _epoch(s):
    """Epoch seconds of an ISO time (a trailing Z is UTC); a time without a zone is local time. None if unreadable."""
    try:
        return dt.datetime.fromisoformat(re.sub(r"Z$", "+00:00", s)).astimezone().timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def _counts(rec):
    c = {k: rec.get(k) if isinstance(rec.get(k), int) and not isinstance(rec.get(k), bool) else 0 for k in USAGE_FIELDS}
    return dict(c, total=sum(c.values()))


def _add(into, c):
    for k in (*USAGE_FIELDS, "total"):
        into[k] = into.get(k, 0) + c[k]
    return into


HISTORY_DAYS = 90


def _pct(block, key):
    v = block.get(key) if isinstance(block, dict) else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _iso(epoch):
    """An epoch-seconds number as an ISO UTC time to the minute, or None."""
    if not isinstance(epoch, (int, float)) or isinstance(epoch, bool):
        return None
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat(timespec="minutes")


MONTHLY_MONTHS = 2      # whole months before the current one that are shown per month


def _period(t, anchor):
    """Where a reading at epoch t falls, seen from the newest reading `anchor`: (level, key, label) or None when older than shown.
    Per hour for the current day; per day for the earlier days of the current (Monday-first) week; per week for the earlier
    weeks of the current month (the first one cut at the month's start); per month for the MONTHLY_MONTHS months before the current one."""
    utc = dt.timezone.utc
    a, x = dt.datetime.fromtimestamp(anchor, utc), dt.datetime.fromtimestamp(t, utc)
    hour0 = a.replace(hour=0, minute=0, second=0, microsecond=0)
    week0 = (a - dt.timedelta(days=a.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    month0 = a.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    m = month0.year * 12 + month0.month - 1 - MONTHLY_MONTHS
    older = dt.datetime(m // 12, m % 12 + 1, 1, tzinfo=utc)
    if x >= hour0:
        return "hour", x.strftime("%Y-%m-%dT%H:00"), x.strftime("%Y-%m-%d %H:00")
    if x >= week0:
        return "day", x.strftime("%Y-%m-%d"), x.strftime("%Y-%m-%d")
    if x >= month0:
        mon = max((x - dt.timedelta(days=x.weekday())).replace(hour=0, minute=0, second=0, microsecond=0), month0)
        return "week", mon.strftime("%Y-%m-%d"), "week of " + mon.strftime("%Y-%m-%d")
    if x >= older:
        return "month", x.strftime("%Y-%m"), x.strftime("%Y-%m")
    return None


def _periods(reads):
    """Readings (oldest first) aggregated in the graded periods of _period(): the count and, per window, first, last, lowest and highest
    percentage, and the windows' reset times (of the last reading). Only periods with readings get a row: an hour without a recorded
    change has none, and nothing is carried forward into it (owner, 2026-11-24, reversing F-680)."""
    out = {}
    anchor = reads[-1]["t"] if reads else 0
    for r in reads:
        p = _period(r["t"], anchor)
        if p is None:
            continue
        level, key, label = p
        h = out.setdefault((level, key), dict(level=level, key=key, label=label, readings=0, five_hour={}, seven_day={}, reset5=None, reset7=None))
        h["readings"] += 1
        for k in ("five_hour", "seven_day"):
            v = r[k]
            if v is not None:
                s = h[k]
                s.update(first=s.get("first", v), last=v, low=min(s.get("low", v), v), high=max(s.get("high", v), v))
        h["reset5"], h["reset7"] = r["reset5"] or h["reset5"], r["reset7"] or h["reset7"]
    return list(out.values())


def _held_peaks(periods):
    """{(level, key): {window: highest}} of the quota periods (oldest first, as _periods() lists them), with an hour of the current day that
    has no reading holding the last value before it (from the previous hour with readings, else the newest earlier period); the quota rows
    themselves list only the hours with readings."""
    w = ("five_hour", "seven_day")
    out, carry, hours = {}, {}, {}
    for p in periods:
        out[(p["level"], p["key"])] = {k: p[k].get("high") for k in w}
        if p["level"] == "hour":
            hours[p["key"]] = p
        else:
            carry = {k: p[k].get("last", carry.get(k)) for k in w}
    if hours:
        day, last = min(hours)[:10], int(max(hours)[11:13])
        for h in range(last + 1):
            key = "%sT%02d:00" % (day, h)
            if key in hours:
                carry = {k: hours[key][k].get("last", carry.get(k)) for k in w}
            else:
                out[("hour", key)] = dict(carry)
    return out


def _quota(quota_text, end_day):
    """The vendor's readings -> (vendor window ends as epoch seconds, report dict or None for no file). end_day: the last day of the 90."""
    recs = _json_lines(quota_text)
    if recs is None:
        return [], None
    reads, resets = [], []
    for rec in recs:
        t = _epoch(rec.get("ts"))
        if t is None:
            continue
        f, w = rec.get("five_hour"), rec.get("seven_day")
        reads.append(dict(t=t, ts=dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds"),
                          five_hour=_pct(f, "used_percentage"), seven_day=_pct(w, "used_percentage"),
                          reset5=_iso(f.get("resets_at") if isinstance(f, dict) else None),
                          reset7=_iso(w.get("resets_at") if isinstance(w, dict) else None)))
        r = f.get("resets_at") if isinstance(f, dict) else None
        if isinstance(r, (int, float)) and not isinstance(r, bool):
            resets.append(r)
    reads.sort(key=lambda r: r["t"])
    ends = []
    for r in sorted(resets):  # the same window's reset time can wobble by seconds: merge ends closer than 10 minutes
        if not ends or r - ends[-1] > 600:
            ends.append(r)
    peaks = {}  # vendor window end -> the highest 5-hour percentage read in it; a reading without a reset time belongs to no window
    for rec in recs:
        f = rec.get("five_hour")
        r, v = (f.get("resets_at"), _pct(f, "used_percentage")) if isinstance(f, dict) else (None, None)
        if _epoch(rec.get("ts")) is not None and v is not None and isinstance(r, (int, float)) and not isinstance(r, bool):
            e = ends[bisect.bisect_right(ends, r) - 1]
            peaks[e] = max(peaks.get(e, v), v)
    if end_day is None and reads:
        end_day = dt.datetime.fromtimestamp(reads[-1]["t"], dt.timezone.utc).date()
    days = {}
    if end_day is not None:
        for i in range(HISTORY_DAYS):
            days[(end_day - dt.timedelta(days=HISTORY_DAYS - 1 - i)).isoformat()] = dict(five_hour=None, seven_day=None)
    for r in reads:
        d = days.get(dt.datetime.fromtimestamp(r["t"], dt.timezone.utc).date().isoformat())
        if d is not None:
            for k in ("five_hour", "seven_day"):
                if r[k] is not None and (d[k] is None or r[k] > d[k]):
                    d[k] = r[k]
    return ends, dict(days=[dict(key=k, **v) for k, v in days.items()], periods=_periods(reads), anchor=reads[-1]["t"] if reads else None,
                      windows=[dict(end=e, peak=v) for e, v in sorted(peaks.items())])


SESSIONS_SHOWN = 20


def _message_counts(rec):
    return dict(_counts(rec), messages=1)


def _period_skeleton(anchor):
    """Every graded period of _period() up to `anchor`, oldest first: (level, key, label). Hours still to come are not listed."""
    out, seen = [], set()
    t = anchor - anchor % 3600 - 3 * 31 * 86400
    while t <= anchor:
        p = _period(t, anchor)
        if p is not None and p[:2] not in seen:
            seen.add(p[:2])
            out.append(p)
        t += 3600
    return out


def record_kind(rec):
    """A transcript-usage record's `kind`; a record without one (earlier runs) is headless when its source label starts with `headless`."""
    k = rec.get("kind")
    return k if k in ("interactive", "headless") else "headless" if str(rec.get("source") or "").startswith("headless") else "interactive"


def transcript_report(text, quota=None, aggregate=None):
    """The counts of interactive sessions (contract transcript-usage.jsonl) -> dict(periods, sources, sessions, aggregate_days, headless), or None when
    neither file exists. periods: the graded rows of _period(), anchored on the newest quota reading (else the newest message or aggregate
    day), each with the message count, the four counts, their sum and the highest 5-hour and weekly percentage of the same period from
    `quota` (the report of _quota()); on a day, week or month row the 5-hour value is instead the mean of the peaks of the vendor 5-hour
    windows ending in the period (five_hour_windows: their number, five_hour_high: the highest; a window without readings is not counted).
    sources, sessions: the last HISTORY_DAYS days; sessions are the SESSIONS_SHOWN with the most tokens.
    aggregate: the text of stats-cache-usage.jsonl (its contract): its token totals per day count in a period's `total` and `aggregate`
    only (no split, no messages), never in sources or sessions; a day with per-message records ignores them (the exact data wins).
    aggregate_days: {day: tokens} of the aggregates used. Only records of kind interactive (record_kind) count in periods, sources and
    sessions; headless: the graded rows of the headless records alike (no aggregates), [] when there are none.
    transcript_days: {day: tokens} of the interactive records of every day (the sums of the history's day rows, not cut at its range);
    transcript_span: [first, last] day of any record, the days the transcripts cover (None without records)."""
    recs, agg = _json_lines(text), _json_lines(aggregate)
    if recs is None and agg is None:
        return None
    msgs = []
    for rec in recs or []:
        t = _epoch(rec.get("ts"))
        if t is not None:
            msgs.append((t, str(rec.get("source") or "?"), str(rec.get("session") or "?"), _message_counts(rec), record_kind(rec) == "headless"))
    day = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()
    exact, tdays = {day(m[0]) for m in msgs}, {}
    for m in msgs:
        if not m[4]:
            tdays[day(m[0])] = tdays.get(day(m[0]), 0) + m[3]["total"]
    days = {}
    for rec in agg or []:
        d, n = rec.get("day"), rec.get("tokens")
        if rec.get("source") == "stats-cache" and isinstance(n, int) and not isinstance(n, bool) and _epoch(f"{d}T00:00:00+00:00") is not None and d not in exact:
            days[d] = days.get(d, 0) + n
    anchor = (quota or {}).get("anchor") or max([m[0] for m in msgs] + [_epoch(f"{d}T00:00:00+00:00") for d in days], default=None)
    empty = lambda: {k: 0 for k in ("messages", *USAGE_FIELDS, "total")}
    peaks = _held_peaks((quota or {}).get("periods", []))
    skeleton = _period_skeleton(anchor) if anchor is not None else []
    rows, hrows = ({p[:2]: dict(level=p[0], key=p[1], label=p[2], aggregate=0, **empty()) for p in skeleton} for _ in range(2))
    sources, sessions = {}, {}
    since = anchor - HISTORY_DAYS * 86400 if anchor is not None else None
    for t, src, ses, c, headless in msgs:
        p = _period(t, anchor)
        if p is not None and p[:2] in rows:
            _add_messages((hrows if headless else rows)[p[:2]], c)
        if t >= since and not headless:
            _add_messages(sources.setdefault(src, empty()), c)
            row = sessions.setdefault((src, ses), dict(source=src, **empty()))
            _add_messages(row, c)
    for d, n in days.items():
        p = _period(_epoch(f"{d}T00:00:00+00:00"), anchor)
        if p is not None and p[:2] in rows:
            rows[p[:2]]["aggregate"] += n
            rows[p[:2]]["total"] += n
    wins = {}
    for w in (quota or {}).get("windows", []) if anchor is not None else []:
        p = _period(w["end"], anchor)
        if p is not None and p[0] != "hour":
            wins.setdefault(p[:2], []).append(w["peak"])
    for k, row in [(k, r) for k in rows for r in (rows[k], hrows[k])]:  # the interactive rows and the headless ones alike (PC-77)
        q = peaks.get(k, {})
        row["five_hour"], row["seven_day"] = q.get("five_hour"), q.get("seven_day")
        if row["level"] != "hour":  # the mean of the peaks of the vendor 5-hour windows ending in the period (owner, 2026-11-25, PC-110)
            ws = wins.get(k, [])
            row["five_hour"] = round(sum(ws) / len(ws), 1) if ws else None
            row["five_hour_windows"], row["five_hour_high"] = len(ws), max(ws, default=None)
    top = sorted(sessions.items(), key=lambda kv: (-kv[1]["total"], kv[0]))[:SESSIONS_SHOWN]
    return dict(periods=_history(rows.values(), [m[0] for m in msgs if not m[4]] + [_epoch(f"{d}T00:00:00+00:00") for d in days]),
                headless=_history(hrows.values(), [m[0] for m in msgs if m[4]]), sources=[dict(key=k, **v) for k, v in sorted(sources.items(), key=lambda kv: (-kv[1]["total"], kv[0]))],
                sessions=[dict(v, key=k[1]) for k, v in top], aggregate_days=dict(sorted(days.items())),
                transcript_days=dict(sorted(tdays.items())), transcript_span=[min(exact), max(exact)] if exact else None)


def heat_days(proxy_days, sessions):
    """The values of the heatmap "Tokens per day" (PC-106): {day: dict(total, sources={source: tokens})} from the proxy file's day rows
    (`days` of usage_report), the interactive transcripts' day totals and the statistics cache's daily aggregates (transcript_report),
    added up. Every day of the transcripts' span counts as covered (zero when no interactive record); a day no source covers is absent:
    the page shows it as "no data", not as zero."""
    s, out = sessions or {}, {}

    def add(d, src, n):
        e = out.setdefault(d, dict(total=0, sources={}))
        e["sources"][src] = e["sources"].get(src, 0) + n
        e["total"] += n
    for r in proxy_days or []:
        add(r["key"], "proxy", r["total"])
    span = s.get("transcript_span")
    if span:
        d, last = dt.date.fromisoformat(span[0]), dt.date.fromisoformat(span[1])
        while d <= last:
            add(d.isoformat(), "transcripts", 0)
            d += dt.timedelta(days=1)
    for d, n in (s.get("transcript_days") or {}).items():
        add(d, "transcripts", n)
    for d, n in (s.get("aggregate_days") or {}).items():
        add(d, "statistics cache", n)
    return dict(sorted(out.items()))


def _history(rows, times):
    """The rows of the Interactive sessions history: none before the first month with data (all keys start with YYYY-MM), and no hour
    without messages (as in Today's readings)."""
    if not times:
        return []
    first = dt.datetime.fromtimestamp(min(times), dt.timezone.utc).strftime("%Y-%m")
    return [r for r in rows if r["key"][:7] >= first and not (r["level"] == "hour" and not r["messages"] and not r["total"])]


def _add_messages(into, c):
    for k in ("messages", *USAGE_FIELDS, "total"):
        into[k] += c[k]


MONTHS_SHOWN = 3                          # owner, 2026-11-25 (PC-113): the current month and the two before it
UNATTENDED_KINDS = ("headless", "unattended")  # a transcript record whose `kind` (PC-77) is one of these is unattended, not interactive
UNASSIGNED = "unassigned"                  # the row group of transcript sources and aggregate days no project claims


def _months(today):
    y, m = today.year, today.month
    out = []
    for _ in range(MONTHS_SHOWN):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def source_project(label, projects):
    """The project a transcript source (its label, the transcript folder's name) belongs to, or UNASSIGNED. projects: [dict(name, dir,
    sources)]. An explicit `sources` list wins; otherwise the default folder rule: the label is the project's directory name or the tool's
    encoded path of it (ends with "-DIR") or of a worktree inside it ("-DIR--..."); the longest matching directory wins."""
    for p in projects:
        if label in (p.get("sources") or ()):
            return p["name"]
    hits = [p for p in projects if p.get("dir") and (label == p["dir"] or label.endswith("-" + p["dir"]) or f"-{p['dir']}--" in label
                                                    or label.startswith(p["dir"] + "--"))]
    return max(hits, key=lambda p: len(p["dir"]))["name"] if hits else UNASSIGNED


def monthly_by_project(projects, transcripts, aggregates, today):
    """The Token Usage card "Per project, by month". projects: [dict(name, dir, sources, start (YYYY-MM or None), usage (proxy file text or
    None), plog (processing log text or None))]; transcripts, aggregates: the texts of every transcript-usage and stats-cache-usage file
    (None for a missing one; a message or day seen in several files counts once); today: a date (UTC).
    -> dict(months, projects=[dict(name, rows)]), a row per month newest first: dict(month, state) with state "before_start" (the project's
    start month is later), "no_data" (no source exists at all) or "data" with the four counts, `total`, `interactive` (transcripts),
    `unattended` (proxy and transcript records of an unattended kind), `aggregate` (stats cache, no split: in `total` only), `tasks`
    (launched that month) and `last_day`. total = interactive + unattended + aggregate. An aggregate day goes to the only project started
    by its month, else to UNASSIGNED; so do transcript sources no project claims. The UNASSIGNED group is listed only when it has tokens."""
    months = _months(today)
    names = [p["name"] for p in projects]
    blank = lambda: dict({k: 0 for k in (*USAGE_FIELDS, "total", "interactive", "unattended", "aggregate", "tasks")}, last_day=None)
    acc = {n: {m: blank() for m in months} for n in names + [UNASSIGNED]}

    def put(name, day, c, kind):
        r = acc[name].get(day[:7])
        if r is None:
            return
        if kind != "aggregate":
            _add(r, c)
        else:
            r["total"] += c["total"]
        r[kind] += c["total"]
        if c["total"] and (r["last_day"] is None or day > r["last_day"]):
            r["last_day"] = day
    day_of = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()
    seen, exact = set(), set()
    for text in transcripts:
        for rec in _json_lines(text) or []:
            t, key = _epoch(rec.get("ts")), (rec.get("session"), rec.get("agent"), rec.get("message"))
            if t is None or key in seen:
                continue
            seen.add(key)
            exact.add(day_of(t))
            put(source_project(str(rec.get("source") or "?"), projects), day_of(t), _counts(rec),
                "unattended" if rec.get("kind") in UNATTENDED_KINDS else "interactive")
    agg_seen = set()
    for text in aggregates:
        for rec in _json_lines(text) or []:
            d, n = rec.get("day"), rec.get("tokens")
            if (rec.get("source") != "stats-cache" or not isinstance(n, int) or isinstance(n, bool) or _epoch(f"{d}T00:00:00+00:00") is None
                    or d in exact or (d, rec.get("model")) in agg_seen):
                continue
            agg_seen.add((d, rec.get("model")))
            live = [p["name"] for p in projects if not p.get("start") or p["start"] <= d[:7]]
            put(live[0] if len(live) == 1 else UNASSIGNED, d, dict(total=n), "aggregate")
    for p in projects:
        for rec in _json_lines(p.get("usage")) or []:
            t = _epoch(rec.get("ts"))
            if t is not None:
                put(p["name"], day_of(t), _counts(rec), "unattended")
        for rec in _json_lines(p.get("plog")) or []:
            t = _epoch(rec.get("time"))
            if t is not None and rec.get("state") == "launched" and day_of(t)[:7] in acc[p["name"]]:
                acc[p["name"]][day_of(t)[:7]]["tasks"] += 1
    shared = any(x is not None for x in (*transcripts, *aggregates))
    out = []
    for p in projects:
        some = shared or p.get("usage") is not None or p.get("plog") is not None
        out.append(dict(name=p["name"], rows=[dict(month=m, state="before_start") if p.get("start") and m < p["start"] else
                                               dict(acc[p["name"]][m], month=m, state="data" if some else "no_data") for m in months]))
    if any(acc[UNASSIGNED][m]["total"] for m in months):
        out.append(dict(name=UNASSIGNED, rows=[dict(acc[UNASSIGNED][m], month=m, state="data") for m in months]))
    return dict(months=months, projects=out)


def usage_report(usage_text, plog_text, goodbye_tokens, quota_text=None):
    """The Usage tab. usage_text / plog_text / quota_text: the file contents or None when missing; goodbye_tokens(folder) -> the `tokens` of the
    task's good-bye note or None. -> dict(days, windows (each a list of {key, counts}), tasks, quota) or None parts where there is no data.
    days: the days with usage (a sum above zero) among the last 90 UTC days counted back from the latest day of use, newest first; windows: the
    5-hour windows with usage, newest first. Windows are the vendor's 5-hour windows (from
    the quota readings' `resets_at`, kind "vendor") and, for requests outside them or without readings, fixed 5-hour blocks counted from the
    epoch (kind "fixed"). A task launched with a Worker name counts only that Worker's requests between its launch and its last log entry; an
    older task counts all Workers' requests in that span (`overlap` marks spans that overlap another task's)."""
    reqs = []
    for rec in _json_lines(usage_text) or []:
        t = _epoch(rec.get("ts"))
        if t is not None:
            reqs.append((t, rec.get("worker") or "?", _counts(rec)))
    end_day = max((dt.datetime.fromtimestamp(t, dt.timezone.utc).date() for t, _, _ in reqs), default=None)
    ends, quota = _quota(quota_text, end_day)
    groups = {"days": {}, "windows": {}}
    if end_day is not None:
        for i in range(HISTORY_DAYS):
            groups["days"][(end_day - dt.timedelta(days=HISTORY_DAYS - 1 - i)).isoformat()] = {k: 0 for k in (*USAGE_FIELDS, "total")}
    for t, _, c in reqs:
        u = dt.datetime.fromtimestamp(t, dt.timezone.utc)
        if u.date().isoformat() in groups["days"]:
            _add(groups["days"][u.date().isoformat()], c)
        win = next((e for e in ends if e - WINDOW_SECONDS <= t < e), None)
        if win is not None:
            key = dict(key=dt.datetime.fromtimestamp(win - WINDOW_SECONDS, dt.timezone.utc).strftime("%Y-%m-%d %H:%M"), kind="vendor")
        else:
            key = dict(key=dt.datetime.fromtimestamp(t - t % WINDOW_SECONDS, dt.timezone.utc).strftime("%Y-%m-%d %H:%M"), kind="fixed")
        _add(groups["windows"].setdefault((key["key"], key["kind"]), {}), c)
    rep = {"days": None if usage_text is None else [dict(key=key, **v) for key, v in sorted(groups["days"].items(), reverse=True) if v["total"]]}
    rep["windows"] = None if usage_text is None else [dict(key=k[0], kind=k[1], **v) for k, v in sorted(groups["windows"].items(), reverse=True)
                                                      if v["total"]]
    rep["quota"] = quota
    spans = {}
    for rec in _json_lines(plog_text) or []:
        t, task = _epoch(rec.get("time")), rec.get("task")
        if t is None or not task:
            continue
        s = spans.setdefault(task, dict(task=task, folder=rec.get("folder"), start=None, end=t, worker=None))
        if rec.get("state") == "launched":
            s["start"], s["folder"], s["worker"] = t, rec.get("folder"), rec.get("worker") or None
        s["end"] = max(s["end"], t)
    tasks = []
    for s in sorted((s for s in spans.values() if s["start"] is not None), key=lambda s: s["start"]):
        proxy = None if usage_text is None else sum(c["total"] for t, w, c in reqs
                                                    if s["start"] <= t <= s["end"] and (s["worker"] is None or w == s["worker"]))
        note = goodbye_tokens(s["folder"]) if s["folder"] else None
        tasks.append(dict(task=s["task"], start=dt.datetime.fromtimestamp(s["start"], dt.timezone.utc).isoformat(timespec="seconds"),
                          worker=s["worker"], proxy=proxy, note=note, diff=None if proxy is None or note is None else proxy - note,
                          overlap=s["worker"] is None and any(o is not s and o["start"] is not None and o["start"] <= s["end"] and s["start"] <= o["end"]
                                                              for o in spans.values())))
    rep["tasks"] = None if plog_text is None else tasks
    return rep


# -- Quota estimates: the percentage of a window without readings, from its token counts (owner, 2026-11-25, PC-111) --
WEEK_SECONDS = 7 * 86400
QUOTA_KINDS = (("seven_day", "weekly", WEEK_SECONDS, True), ("five_hour", "5-hour", WINDOW_SECONDS, False))  # key, name, length, extrapolated


def _utc(t, fmt="%Y-%m-%d %H:%M"):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime(fmt)


def vendor_windows(ends, span, lo, hi, extrapolate=True):
    """The vendor's windows of length `span` from the known window ends (reset times, sorted, merged), oldest first: (start, end, basis), basis
    None for a window whose end is a known reset, else the known reset its boundaries are extrapolated from by whole steps of `span`. With
    `extrapolate` the windows cover lo..hi: before the first known reset back from it; between two, back from the later one (a shorter rest
    is one window, a rest under an hour widens the first); after the last, forward from it. A known window never reaches back over the previous known end."""
    out, prev = [], None
    if extrapolate and ends:
        e = ends[0] - span
        while e > lo:
            out.insert(0, (e - span, e, ends[0]))
            e -= span
    for r in ends:
        if extrapolate and prev is not None:
            seg, e = [], r - span
            while e - span >= prev:
                seg.insert(0, (e - span, e, r))
                e -= span
            if seg and e - prev < 3600:  # a wobble of the reset time, not a shift: the first window absorbs it
                seg[0] = (prev, seg[0][1], r)
            elif e > prev:
                seg.insert(0, (prev, e, r))
            out += seg
        out.append((r - span if prev is None else max(r - span, prev), r, None))
        prev = r
    while extrapolate and prev is not None and prev <= hi:
        out.append((prev, prev + span, ends[-1]))
        prev += span
    return out


def estimate_percent(tokens, calibration):
    """The quota percentage `tokens` stand for under a calibration of quota_estimates() (one decimal), None without one."""
    return round(tokens / calibration["factor"], 1) if calibration else None


def _quota_kind(key, name, span, extrapolate, reads, events, plan, weak, lo, hi):
    """The rows and the calibration of one window kind (see quota_estimates)."""
    ends = []
    for t, rec in reads:
        r = _num(rec[key].get("resets_at")) if isinstance(rec.get(key), dict) else None
        if r is not None:
            ends.append(r)
    merged = []
    for r in sorted(ends):  # the same window's reset time wobbles by seconds to minutes
        if not merged or r - merged[-1] > 600:
            merged.append(r)
    new = lambda s, e, kind, basis: dict(start=s, end=e, kind=kind, basis=basis, tokens=0, cal_tokens=0, readings=0, peak=None, peak_t=None)
    vend = [new(s, e, "vendor" if b is None else "extrapolated", b) for s, e, b in vendor_windows(merged, span, lo, hi, extrapolate)]
    starts, fixed = [w["start"] for w in vend], {}

    def window(t):
        i = bisect.bisect_right(starts, t) - 1
        if i >= 0 and t < vend[i]["end"]:
            return vend[i]
        b = t - t % span
        return fixed.setdefault(b, new(b, b + span, "fixed", None))
    for t, rec in reads:  # the readings first: the peak's time decides which tokens calibrate
        p = _pct(rec.get(key), "used_percentage")
        if p is not None:
            w = window(t)
            w["readings"] += 1
            if w["peak"] is None or p >= w["peak"]:
                w["peak"], w["peak_t"] = p, t
    for t, n in events:
        w = window(t)
        w["tokens"] += n
        if w["peak_t"] is not None and t <= w["peak_t"]:
            w["cal_tokens"] += n
    wins = sorted([w for w in vend if w["end"] > lo] + list(fixed.values()), key=lambda w: (w["start"], w["end"]))
    base = [w for w in wins if w["start"] >= plan and w["peak"] and w["cal_tokens"] > 0]
    cal = None
    if base:
        cal = dict(factor=sum(w["cal_tokens"] for w in base) / sum(w["peak"] for w in base), readings=sum(w["readings"] for w in base),
                   windows=len(base), first=_utc(min(w["start"] for w in base), "%Y-%m-%d"), last=_utc(max(w["end"] for w in base), "%Y-%m-%d"))
        cal["weak"] = cal["readings"] < weak
    nf = lambda x: f"{round(x):,}"
    rows = []
    for w in wins:
        if not (w["tokens"] or w["readings"] or (extrapolate and w["kind"] != "fixed")):
            continue
        row = dict(start=_iso(w["start"]), end=_iso(w["end"]), kind=w["kind"], tokens=w["tokens"], readings=w["readings"], percent=None, weak=False)
        if w["peak"] is not None:
            row.update(state="measured", percent=w["peak"], note=f"Measured: the highest of {w['readings']} readings in this window.")
        elif w["start"] < plan:
            row.update(state="other plan", note=f"No readings, other plan: before the plan period that starts on {_utc(plan, '%Y-%m-%d')} the quota "
                                                 "was different and the calibration does not carry over.")
        elif cal is None:
            row.update(state="none", note=f"No calibration yet: no {name} window of the plan period has a reading and token counts.")
        else:
            row.update(state="estimate", percent=estimate_percent(w["tokens"], cal), weak=cal["weak"],
                       note=f"Estimate: {nf(w['tokens'])} tokens at {nf(cal['factor'])} tokens per percent, calibrated on {cal['readings']} readings in "
                            f"{cal['windows']} {name} windows with readings, {cal['first']} to {cal['last']} (UTC)."
                            + (f" Weak: fewer than {weak} readings." if cal["weak"] else ""))
        if w["kind"] == "extrapolated":
            row["note"] += (f" No reset is known for this window: its boundaries are extrapolated by whole {span // 86400}-day steps from the known "
                            f"reset of {_utc(w['basis'])} UTC.")
        elif w["kind"] == "fixed":
            row["note"] += f" No vendor window is known here: a fixed {name} block counted from the epoch (UTC)."
        rows.append(row)
    return dict(calibration=cal, rows=rows)


def quota_estimates(quota_text, usage_text=None, transcript_text=None, settings=None):
    """The quota percentage per vendor window, measured or estimated (owner, 2026-11-25, PC-111), for the weekly and the 5-hour windows alike.
    quota_text: quota-history.jsonl; usage_text / transcript_text: the token counts of the Workers (proxy) and of the interactive sessions;
    settings: quota_plan_start and quota_weak_readings (QUOTA_SETTING_DEFAULTS). -> dict(plan_start, weak_readings, weekly, five_hour) or None
    when there is neither a reading nor a token count; each kind: dict(calibration, rows oldest first, the last HISTORY_DAYS days).
    Windows: the vendor's, from the readings' reset times (the weekly ones extrapolated by whole 7-day steps where no reset is known, kind
    "extrapolated"; a 5-hour period outside every known window is a fixed block counted from the epoch, kind "fixed"). A window's tokens are
    the sum of the four counts of every request and message in it. Calibration: tokens per percent = the tokens up to the highest reading of
    each window of the plan period with a reading above zero and token counts, summed, over those readings summed; `readings` counts them all.
    A row: measured (its highest reading; always wins), estimate (tokens / factor, `weak` below quota_weak_readings readings), none (no
    calibration yet: no number) or other plan (the window starts before the plan period: never estimated); `note` is the tooltip."""
    s = validate_quota_settings(settings or {}, strict=False)
    plan = dt.datetime.fromisoformat(s["quota_plan_start"]).replace(tzinfo=dt.timezone.utc).timestamp()
    reads = [(t, rec) for rec in _json_lines(quota_text) or [] if (t := _epoch(rec.get("ts"))) is not None]
    events = [(t, _counts(rec)["total"]) for text in (usage_text, transcript_text) for rec in _json_lines(text) or [] if (t := _epoch(rec.get("ts"))) is not None]
    times = [t for t, _ in reads] + [t for t, _ in events]
    if not times:
        return None
    hi = max(times)
    lo = max(min(times), hi - HISTORY_DAYS * 86400)
    reads = sorted((r for r in reads if r[0] >= lo), key=lambda r: r[0])
    events = [e for e in events if e[0] >= lo]
    out = dict(plan_start=s["quota_plan_start"], weak_readings=s["quota_weak_readings"])
    for key, name, span, extrapolate in QUOTA_KINDS:
        out[key] = _quota_kind(key, name, span, extrapolate, reads, events, plan, s["quota_weak_readings"], lo, hi)
    return out
