"""Task Queue view: queue state, capacity, log rows, the live overlay, open-issue counts. Depends on modules, settings, tasks_model and usage."""
import logging
import datetime as dt
import json
import re
from ckp.core.modules import add_tree, short_name, with_parents
from ckp.core.settings import QUEUE_BUDGET_RESERVE
from ckp.core.tasks_model import is_running, stage
from ckp.core.usage import _json_lines

log = logging.getLogger("cockpit")


def queue_interval(setting, remaining, paused_until, now, slow=3600, reserve=QUEUE_BUDGET_RESERVE):
    """The Task Queue pane's task-list refresh interval -> (seconds, reason or None). The setting while the hourly call budget allows it;
    the hourly interval (slow) while a rate-limit pause is on or fewer than `reserve` calls are left of the Cockpit's own cap."""
    if paused_until and now < paused_until:
        return slow, "GitHub rate limit"
    if remaining < reserve:
        return slow, "call budget low"
    return setting, None


# ---------- GitHub rate limit and the local part of the Task Queue ----------
def is_rate_limited(msg):
    """True when a gh error message says the API budget (primary or secondary) is used up."""
    return "rate limit" in (msg or "").lower()


def rate_limit_reset(msg, now):
    """The reset time (epoch seconds, in the future and within 2 hours) when the message carries one as a 10-digit epoch number, else None."""
    for m in re.findall(r"\b(\d{10})\b", msg or ""):
        if now < int(m) <= now + 7200:
            return int(m)
    return None


_TOKEN_MARKS = ("bad credentials", "http 401", "requires authentication", "gh auth login", "not logged in", "token is invalid", "token has expired")
_UNREACHABLE_MARKS = ("error connecting to", "could not resolve", "no such host", "connection refused", "connection reset", "network is unreachable",
                      "timed out", "timeout", "dial tcp", "tls handshake", "http 502", "http 503", "http 504", "eof")


def offline_kind(msg, rate_limited=False):
    """Why GitHub could not be read (owner, offline behaviour): 'rate_limit', 'token' (the login's token was rejected), 'unreachable'
    (network or GitHub down) or 'error' (anything else)."""
    m = (msg or "").lower()
    if rate_limited or is_rate_limited(m):
        return "rate_limit"
    if any(x in m for x in _TOKEN_MARKS):
        return "token"
    if any(x in m for x in _UNREACHABLE_MARKS):
        return "unreachable"
    return "error"


def offline_reason(kind, until=None, clock=None):
    """One sentence for the page: what is wrong, and for a rate limit when GitHub is tried again (clock formats an epoch time)."""
    if kind == "rate_limit":
        return "GitHub rate limit reached" + (f"; next try at {clock(until)}" if until and clock else "")
    return {"token": "GitHub rejected the login's token (run gh auth login)", "unreachable": "GitHub is unreachable"}.get(kind, "GitHub could not be read")


NEW_WORK_MAX_AGE_S = 3600  # owner: an assistant starts no new task from state older than one hour (in-progress work may continue)


def may_start_new(age_s):
    """True when the task state is young enough to start a new task from it (unknown age: no)."""
    return age_s is not None and 0 <= age_s <= NEW_WORK_MAX_AGE_S


def stagger_plan(keys, window, jitter, rng):
    """{key: (offset, jitter)}: the refresh slots of the GitHub resources of all projects, spread evenly over `window` seconds in sorted key
    order, each plus its own random jitter in [0, jitter) from rng (a random.Random), so that no two refreshes fall due together."""
    keys = sorted(set(keys))
    out = {}
    for i, k in enumerate(keys):
        j = rng.uniform(0, jitter) if jitter > 0 else 0.0
        out[k] = (i * window / len(keys) + j, j)
    return out


def refresh_due(fetched_at, ttl, slot, started):
    """When a copy fetched at fetched_at may be refreshed: ttl after the fetch plus the key's jitter, and never before its staggered slot
    after the start of the service (a restart with old copies refreshes them one after another, not in one burst)."""
    offset, jitter = slot
    return max(fetched_at + ttl + jitter, started + offset)


def _when(t):
    """A time for the Task Queue page (PC-114, PC-134): epoch seconds stay a number (the browser shows them in its own zone); an ISO string gets
    an offset: one without (the Dispatcher's processing log before PC-134) is the server machine's local time. Anything unreadable is None."""
    if isinstance(t, bool) or t is None:
        return None
    if isinstance(t, (int, float)):
        try:
            dt.datetime.fromtimestamp(t, dt.timezone.utc)
            return t
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(t, str):
        return None
    try:
        d = dt.datetime.fromisoformat(re.sub(r"Z$", "+00:00", t))
    except ValueError:
        return None
    return (d if d.tzinfo else d.astimezone()).isoformat(timespec="seconds")


def queue_local(state_text, plog_text):
    """The Task Queue facts that need no GitHub: the Workers the Dispatcher's state file lists as running (worker, task id, launched, started)
    and the processing log's newest rows (`log_rows`). Missing or unreadable files give empty lists. Times as `_when` gives them."""
    try:
        st = json.loads(state_text) if state_text else {}
    except ValueError:
        st = {}
    ws = st.get("workers") if isinstance(st, dict) and isinstance(st.get("workers"), dict) else {}
    workers = [dict(worker=u, task=w.get("task_id"), project=w.get("project") if isinstance(w.get("project"), str) else project_key(w.get("task_id")),
                    launched=_when(w.get("launched")), started=bool(w.get("started")))
               for u, w in sorted(ws.items()) if isinstance(w, dict)]
    return dict(workers=workers, log=log_rows(plog_text), capacity=capacity(st.get("capacity") if isinstance(st, dict) else None))


def capacity(c):
    """PC-95: the Dispatcher's last decision on the number of Workers from its state file -> dict(effective, ceiling, held, readings, at)
    (held: the reasons, None when not held; at as `_when` gives it), or None when the state file has none or it is unusable."""
    if not isinstance(c, dict) or not all(isinstance(c.get(k), int) and not isinstance(c.get(k), bool) for k in ("effective", "ceiling")):
        return None
    text = lambda v: v if isinstance(v, str) and v else None   # noqa: E731
    return dict(effective=c["effective"], ceiling=c["ceiling"], held=text(c.get("held")), readings=text(c.get("readings")), at=_when(c.get("at")))


def project_key(task_id):
    """The project key of a task id (spec 2d: `cockpit-44` -> `cockpit`); "" for anything else (PC-67: the queue's project column)."""
    m = re.fullmatch(r"(.+)-\d+", task_id) if isinstance(task_id, str) else None
    return m.group(1) if m else ""


def listed_projects(values):
    """PC-138: the projects the one queue serves, from the Dispatcher settings' `projects` list, the `first` ones leading (in listed order),
    then the others: [{short, repo, first}]. Empty unless more than one usable project is listed (the single-project view stays as it is)."""
    ps = [p for p in (values or {}).get("projects") or [] if isinstance(p, dict) and isinstance(p.get("short"), str) and isinstance(p.get("repo"), str)]
    if len(ps) < 2:
        return []
    out = [dict(short=p["short"], repo=p["repo"], first=p.get("first") is True) for p in ps]
    return [p for p in out if p["first"]] + [p for p in out if not p["first"]]


def _due_chain(t, by_key):
    """The task's own deadline, else its parent chain's (the Dispatcher's rank input, dispatcher.tasks._rank_inputs)."""
    seen = set()
    while t and t["key"] not in seen:
        seen.add(t["key"])
        if t.get("deadline"):
            return t["deadline"]
        t = by_key.get(t.get("parent"))
    return None


def queue_all(parts):
    """PC-138: the tasks of every listed project in one list, each tagged with its `project`, and `rank` (1, 2, ...) on the tasks the
    Dispatcher would pick (`pick`), ranked as it ranks them (dispatcher.core.choose_task): the `first` projects' tasks before all others, then
    priority (none counts as P2), deadline (own, else the parent chain's), what it unblocks, must before nice; ties by listed order, number.
    parts: [{short, first, tasks}] in listed order, each from its own project's state."""
    keyed, out = [], []
    for i, part in enumerate(parts):
        ts = part.get("tasks") or []
        by_key = {t["key"]: t for t in ts}
        unblocks = {}
        for t in ts:
            if t["stage"] in ("embryo", "released", "wip"):
                for b in t.get("blockedBy") or []:
                    unblocks[b] = unblocks.get(b, 0) + 1
        for t in ts:
            row = dict(t, project=part["short"], rank=None)
            out.append(row)
            if t.get("pick"):
                keyed.append(((not part.get("first"), t.get("prio") or 2, _due_chain(t, by_key) or "9999-99-99", -unblocks.get(t["key"], 0),
                               t.get("cls") != "must", i, t.get("number") or 0), row))
    for n, (_, row) in enumerate(sorted(keyed, key=lambda kr: kr[0]), 1):
        row["rank"] = n
    return out


# ---------- the processing log as a table (PC-120) ----------
LOG_KINDS = ("launched", "wip", "unmerged", "failed", "merged")   # the task states a log record names; besides them: sleep, heartbeat, other
LOG_LIMITS = (15, 50, 200)                 # the page's row limits; the server sends up to the largest of each class (events, heartbeats)
GROUP_LINES = 50                           # the original lines a condensed heartbeat row keeps (the newest)
QUIET = ("skipped: interval", "skipped: switched off in Settings")   # heartbeats that tell nothing new; other skips are events
READINGS = ("memory pressure", "free disk", "load", "power", "proxy")   # the host readings a heartbeat row shows, in this order
_GIB, _MIB = 1 << 30, 1 << 20


def _size(n):
    """Bytes as "1.2 GiB" (a whole number without decimals: "4 GiB"), below a GiB as "512 MiB"."""
    if n >= _GIB:
        return f"{n / _GIB:.1f}".removesuffix(".0") + " GiB"
    return f"{n / _MIB:.0f} MiB"


def container_reading(text):
    """The container's stats (raw JSON, possibly cut short by the Dispatcher) as "1.2 GiB of 4 GiB, 37 processes"; never the raw text."""
    if not isinstance(text, str) or "{" not in text:
        return text.strip() if isinstance(text, str) and text.strip() else "not reported"
    num = lambda k: (m := re.search(r'"%s"\s*:\s*(\d+)' % k, text)) and int(m.group(1))   # noqa: E731
    use, lim, procs = num("memoryUsageBytes"), num("memoryLimitBytes"), num("numProcesses")
    out = ", ".join(x for x in ((_size(use) + (f" of {_size(lim)}" if lim else "")) if use is not None else "",
                                f"{procs} process{'' if procs == 1 else 'es'}" if procs is not None else "") if x)
    return out or "readings unreadable"


def parse_heartbeat(line):
    """A Dispatcher heartbeat line (`<time> | running: ... | <what it did> | memory pressure ... | ... | container <JSON>`) ->
    dict(running=[(worker, task)], what, readings): `what` is what the firing did (with "slept N minutes" when given), `readings` one
    short text of the host and container readings. A line of another shape keeps its text as `what`."""
    parts = line.split(" | ") if isinstance(line, str) else [str(line)]
    i = next((k for k, p in enumerate(parts) if p.startswith("container ") and not p.startswith("container data ")), len(parts))
    head, cont = parts[:i], " | ".join(parts[i:])[len("container "):] if i < len(parts) else None
    running, what, read = [], [], {}
    for p in head[1:] if head and re.match(r"\d{4}-\d\d-\d\d", head[0]) else head:
        key = next((k for k in READINGS if p.startswith(k + " ")), None)
        if p.startswith("running: "):
            running = [tuple(x.split("=", 1)) if "=" in x else (x, "") for x in p[9:].split(", ") if x and x != "none"]
        elif key:
            read[key] = p
        elif not p.startswith("container data "):
            what.append(p)
    readings = [f"running {', '.join(w for w, _ in running) or 'none'}"] + [read[k] for k in READINGS if k in read]
    if cont is not None:
        readings.append("container " + container_reading(cont))
    return dict(running=running, what=" | ".join(what), readings=" · ".join(readings))


def _row(rec, raw):
    """One processing-log record (a dict, or None for an unreadable line) -> a table row."""
    base = dict(time=_when(rec.get("time")) if rec else None, kind="other", task="", project="", worker="", detail="", what="", count=1, first=None,
                lines=[raw], event=True, alert=False, idle=False)
    if rec is None:
        return dict(base, detail=raw)
    if "heartbeat" in rec:
        h = parse_heartbeat(rec["heartbeat"])
        idle = not h["running"] and h["what"].startswith("nothing to do") and "slept" not in h["what"]
        skip = h["what"].startswith("skipped:") and not h["what"].startswith(QUIET)
        return dict(base, kind="heartbeat", task=", ".join(t for _, t in h["running"] if t),
                    project=", ".join(dict.fromkeys(k for _, t in h["running"] if (k := project_key(t)))), worker=", ".join(w for w, _ in h["running"]),
                    what=h["what"], detail=h["readings"], event=skip, alert=skip, idle=idle, first=base["time"])
    task = rec.get("task") if isinstance(rec.get("task"), str) and rec.get("task") != "-" else ""
    worker = rec.get("worker") if isinstance(rec.get("worker"), str) else ""
    base["project"] = rec["project"] if isinstance(rec.get("project"), str) else project_key(task)   # an old line has no field: from the id
    if rec.get("event") == "sleep":
        return dict(base, kind="sleep", sleep_start=rec.get("start"), first=base["time"],
                    detail=f"the host slept or the Dispatcher was down for {rec.get('minutes', '?')} minutes")
    st = rec.get("state") if isinstance(rec.get("state"), str) else ""
    m = RECONCILED.search(st)
    if st in LOG_KINDS or m:
        reason = rec.get("reason") if isinstance(rec.get("reason"), str) else ""
        return dict(base, kind=m.group(1) if m else st, task=task, worker=worker, detail=st if m else reason,
                    alert=bool(m) or st == "failed")
    return dict(base, task=task, worker=worker, detail=st or raw)


def log_rows(plog_text, limit=max(LOG_LIMITS)):
    """The processing log as table rows, newest first (PC-120). Every line gives a row: a task event (kind launched, wip, unmerged, failed,
    merged; a failure or a stale-WIP reconciliation is `alert`), a sleep, a heartbeat, or `other` (an unknown record, an unreadable line
    with its text); never an exception. Consecutive idle heartbeats (no Worker running, "nothing to do") collapse into one row with the
    newest readings, `count` and the oldest time `first`; a heartbeat that carries a Worker or an event is its own row. `event` marks the
    rows of the default view (all but heartbeats, and heartbeats with a skip worth seeing). Up to `limit` rows of each class are kept,
    so that the page can filter and choose among 15, 50 and 200. Each row keeps its original lines (a condensed row its newest
    GROUP_LINES)."""
    rows, kept, prev, sleeps = [], {True: 0, False: 0}, None, {}
    for raw in reversed((plog_text or "").splitlines()):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            rec = None
        r = _row(rec if isinstance(rec, dict) else None, raw)
        if r["idle"] and prev is not None and prev["idle"]:   # the line before (newer) was idle too: one row
            prev["count"] += 1
            prev["first"] = r["time"]
            if len(prev["lines"]) < GROUP_LINES:
                prev["lines"].append(raw)
            continue
        if r["kind"] == "sleep" and isinstance(r.get("sleep_start"), (int, float)):   # the same gap reported again (issue 143): one row
            same = sleeps.get(r["sleep_start"])
            if same is not None:
                same["count"] += 1
                same["first"] = r["time"]
                if len(same["lines"]) < GROUP_LINES:
                    same["lines"].append(raw)
                continue
            sleeps[r["sleep_start"]] = r
        prev = r
        if kept[r["event"]] >= limit:
            if all(v >= limit for v in kept.values()):
                break
            continue
        kept[r["event"]] += 1
        rows.append(r)
    for r in rows:
        if r["idle"] and r["count"] > 1:
            r["what"] = "nothing to do"
    return rows


# ---------- the live overlay on the cached task table (PC-119) ----------
LIVE_STAGE = {"launched": "wip", "wip": "wip", "unmerged": "unmerged", "failed": "failed"}   # processing-log state -> task stage
RECONCILED = re.compile(r"-> (unmerged|failed)$")   # the Dispatcher's PC-98 reconciliation entries ("wip with no Worker -> failed")
LIVE_REFRESH_S = 120                                # a newer log event refreshes the task list at most this often


def task_number(task_id, shorts):
    """The issue number of a Dispatcher task id `<short>-<number>` whose short name is one of `shorts` (lower case), else None."""
    m = re.fullmatch(r"(.+)-(\d+)", task_id) if isinstance(task_id, str) else None
    return int(m.group(2)) if m and m.group(1).lower() in shorts else None


def live_events(plog_text, shorts):
    """Issue number -> (epoch, stage) of the newest state change of each task of this project in the processing log."""
    out = {}
    for r in _json_lines(plog_text) or []:
        st = r.get("state") if isinstance(r.get("state"), str) else ""
        m = RECONCILED.search(st)
        stage, num = LIVE_STAGE.get(st) or (m and m.group(1)), task_number(r.get("task"), shorts)
        try:
            at = dt.datetime.fromisoformat(r.get("time")).timestamp()   # with an offset, or (before PC-134) the server's local time
        except (TypeError, ValueError):
            continue
        if stage and num is not None and (num not in out or at >= out[num][0]):
            out[num] = (at, stage)
    return out


def overlay_live(tasks, fetched_at, events, held, state_known):
    """The cached tasks with the newer facts of the processing log: a state change newer than `fetched_at` replaces the cached stage and is
    marked `live` (at, was); a WIP task that no running Worker (issue numbers in `held`) holds gets `noWorker`, when the Dispatcher's
    state file could be read (`state_known`). Merged and cancelled are final and stay. The input is not changed."""
    out = []
    for t in tasks:
        e = events.get(t["number"])
        if e and e[0] > fetched_at and e[1] != t["stage"] and t["stage"] not in ("merged", "cancelled"):
            t = dict(t, stage=e[1], running=is_running(e[1]), live=dict(at=e[0], was=t["stage"]))
        if state_known and t["stage"] == "wip" and t["number"] not in held:
            t = dict(t, noWorker=True)
        out.append(t)
    return out


def live_refresh_due(events, fetched_at, last_try, now, gh_ok):
    """True when the processing log has a state change newer than the cached task list, the last such refresh was LIVE_REFRESH_S or more
    ago and GitHub may be called (`gh_ok`: no rate-limit pause, room in the hourly cap, not an offline copy)."""
    newest = max((at for at, _ in events.values()), default=None)
    return bool(gh_ok) and newest is not None and newest > fetched_at and now - last_try >= LIVE_REFRESH_S


def local_modules(mods):
    """The module list the Code tab needs, from the module map file alone (no issues): the listed modules plus missing ancestors, each with
    its short name, parent and children; task counts are zero (they come from GitHub and the Code tab does not show them)."""
    mods = [dict(m) for m in mods]
    with_parents(mods)
    for m in mods:
        m["short"] = short_name(m["name"])
    if not any(m["id"] == "unassigned" for m in mods):
        mods.append(dict(id="unassigned", name="Not mapped to a module", short="Unassigned", virtual=True))
    add_tree(mods, [])
    return mods


# ---------- open issues per module (PC-128): the Task Queue's module chips ----------
OPEN_KINDS = ("embryo", "released", "wip", "unmerged", "failed", "question")   # the order of the chip's tooltip


def open_kind(t):
    """The kind a task counts as on a module chip: None when its issue is no longer open (merged, cancelled), `question` for an open
    question (label `question`), else its stage."""
    if t["stage"] in ("merged", "cancelled"):
        return None
    return "question" if "question" in (t.get("labels") or []) else t["stage"]


def open_tip(counts):
    """{kind: n} -> the chip's tooltip, e.g. '3 embryo, 1 WIP, 4 question'; 'no open issue' when there is none."""
    parts = [f"{counts[k]} {'WIP' if k == 'wip' else k}" for k in OPEN_KINDS if counts.get(k)]
    return ", ".join(parts) or "no open issue"


def open_counts(tasks, mods):
    """-> {module id: {n, tip}} for every module (its sub-modules rolled up) and `all` (every open issue). A task without a module
    carries the module `unassigned`, so that chip counts it."""
    kinds = [(t.get("module") or "unassigned", k) for t in tasks if (k := open_kind(t))]

    def one(keep):
        c = {}
        for mid, k in kinds:
            if keep(mid):
                c[k] = c.get(k, 0) + 1
        return dict(n=sum(c.values()), tip=open_tip(c))
    out = {m["id"]: one(lambda mid, m=m: mid == m["id"] or mid.startswith(m["id"] + "/")) for m in mods}
    out["all"] = one(lambda mid: True)
    return out
