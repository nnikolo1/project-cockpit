"""Pure logic of the boot-filling job (ADR 0014): no network, no subprocess, no clock, no file access.
Everything here takes its inputs as arguments and is unit-tested; adapters.py and job.py do the I/O."""
import datetime as dt
import json
import re

DEFAULTS = dict(
    mode="dry-run",  # "dry-run" only logs what would be released; the owner changes it to "real"
    idle_minutes=10,
    five_hour=dict(low_left=10, low_payload=5, high_left=50, high_payload=20, max_hours_to_reset=2),
    weekly=dict(min_left=20, max_hours_to_reset=24, payload=50),
    day_cap=60,
    workers=3,
    worker_stop_left=5,  # stop starting workers when either window has this percent or less left
    summary_hour=20,  # local hour after which the daily summary is written
    tie_judgement=True,
    pause_label="pause",
    exclude_labels=["type:decision", "question"],
    exclude_title_patterns=[r"\b(send|submit|withdraw|delete|drop|purchase|spend|spending|schema|ddl)\b"],
)
DRY, REAL = "dry-run", "real"


def merge_config(base, over):
    """Deep merge: `over` (the local config) onto `base` (the defaults)."""
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = merge_config(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


# ---------- quota ----------
def parse_time(v):
    """Epoch seconds (int/float/numeric string) or ISO 8601 -> epoch seconds (float)."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def parse_feed(obj):
    """{five_hour, seven_day: {used_percentage, resets_at}} -> {'five': (used, resets_at), 'week': (used, resets_at)}.
    Raises ValueError on a missing or out-of-range field, so a broken feed is a logged failure, never a guess."""
    out = {}
    for name, key in (("five", "five_hour"), ("week", "seven_day")):
        try:
            w = obj[key]
            used, reset = float(w["used_percentage"]), parse_time(w["resets_at"])
        except (KeyError, TypeError, ValueError) as ex:
            raise ValueError(f"quota feed: bad {key}: {type(ex).__name__}") from None
        if not 0 <= used <= 100:
            raise ValueError(f"quota feed: {key} used_percentage {used} out of range")
        out[name] = (used, reset)
    return out


def window_key(reset):
    """One window = one reset time; rounded to 10 minutes so that a jittering feed does not open a new window."""
    return str(int(round(reset / 600)))


# ---------- idle, kill switch ----------
def idle_verdict(now, last_transcript, workers_running, idle_minutes):
    """-> (idle, reason). last_transcript: epoch seconds of the newest interactive transcript write, or None."""
    if workers_running:
        return False, f"{workers_running} own worker(s) running"
    if last_transcript is not None and now - last_transcript < idle_minutes * 60:
        return False, f"transcript written {int((now - last_transcript) // 60)} min ago"
    return True, "idle" if last_transcript is None else f"no transcript write for {int((now - last_transcript) // 60)} min"


def kill_switch(flag_exists, tracking_labels, pause_label="pause"):
    """-> reason string when the job must do nothing, else None. Flag file AND label are independent switches."""
    if flag_exists:
        return "kill switch: flag file"
    if pause_label in (tracking_labels or []):
        return f"kill switch: '{pause_label}' label"
    return None


# ---------- triggers ----------
def evaluate_triggers(quota, now, cfg, fired):
    """-> list of dict(name, holds, verdict, payload, key). `fired` maps trigger name -> window key it last fired in.
    Verdicts: fire, below_threshold, too_early, already_fired. A window is the quota's resets_at."""
    f5, wk = cfg["five_hour"], cfg["weekly"]
    out = []
    used, reset = quota["five"]
    left, hours = 100 - used, (reset - now) / 3600
    for name, min_left, payload in (("five_low", f5["low_left"], f5["low_payload"]), ("five_high", f5["high_left"], f5["high_payload"])):
        out.append(_trigger(name, left, min_left, hours, f5["max_hours_to_reset"], payload, window_key(reset), fired, strict_hours=True))
    used, reset = quota["week"]
    out.append(_trigger("weekly", 100 - used, wk["min_left"], (reset - now) / 3600, wk["max_hours_to_reset"], wk["payload"], window_key(reset), fired, strict_hours=False))
    return out


def _trigger(name, left, min_left, hours, max_hours, payload, key, fired, strict_hours):
    if left <= min_left:
        verdict = "below_threshold"
    elif hours < 0 or (hours >= max_hours if strict_hours else hours > max_hours):
        verdict = "too_early" if hours >= 0 else "stale_reset"
    elif fired.get(name) == key:
        verdict = "already_fired"
    else:
        verdict = "fire"
    return dict(name=name, verdict=verdict, payload=payload, key=key, holds=verdict in ("fire", "already_fired"))


def decide(triggers, cfg, released_today):
    """-> dict(n, fired, mark, reasons). Five-hour: the larger firing payload wins (no adding); weekly adds; the per-day cap bounds the sum.
    `mark` = triggers to record as fired (the winning five-hour trigger also marks the smaller one, which it covers)."""
    t = {x["name"]: x for x in triggers}
    five = [x for x in (t["five_low"], t["five_high"]) if x["verdict"] == "fire"]
    win = max(five, key=lambda x: x["payload"]) if five else None
    weekly = t["weekly"] if t["weekly"]["verdict"] == "fire" else None
    want = (win["payload"] if win else 0) + (weekly["payload"] if weekly else 0)
    room = max(0, cfg["day_cap"] - released_today)
    n = min(want, room)
    fired = [x["name"] for x in (win, weekly) if x]
    mark = list(fired) + (["five_low"] if win and win["name"] == "five_high" and t["five_low"]["verdict"] == "fire" else [])
    reasons = []
    if not want:
        reasons.append("no trigger fires")
    elif n < want:
        reasons.append(f"day cap {cfg['day_cap']} reached" if n == 0 else f"payload {want} cut to {n} by day cap {cfg['day_cap']}")
    return dict(n=n, fired=fired if n else [], mark=mark if n else [], want=want, reasons=reasons, win=win["name"] if win else None, weekly=bool(weekly))


def batch_triggers(triggers, n):
    """Label each of the n released slots with its trigger: the winning five-hour trigger's payload first, then weekly. -> list of n names."""
    t = {x["name"]: x for x in triggers if x["verdict"] == "fire"}
    five = [x for x in (t.get("five_low"), t.get("five_high")) if x]
    win = max(five, key=lambda x: x["payload"]) if five else None
    slots = ([win["name"]] * win["payload"] if win else []) + (["weekly"] * t["weekly"]["payload"] if "weekly" in t else [])
    return slots[:n]


def mark_fired(state, decision, triggers):
    key = {x["name"]: x["key"] for x in triggers}
    for name in decision["mark"]:
        state.setdefault("fired", {})[name] = key[name]
    return state


def roll_day(state, today):
    """New local date -> the day counter restarts."""
    if state.get("day") != today:
        state["day"], state["day_count"] = today, 0
    return state


# ---------- eligibility ----------
def parse_exclusions(text_or_obj):
    """Per-project exclusion file: {"labels": [...], "numbers": [...], "title_patterns": [...]}; missing file = empty."""
    d = json.loads(text_or_obj) if isinstance(text_or_obj, (str, bytes)) else (text_or_obj or {})
    for k in ("labels", "numbers", "title_patterns"):
        d.setdefault(k, [])
    return d


def eligibility(task, cfg, excl, tracking_number=None):
    """-> (eligible, reason). Opt-out: every open embryo except the exclusion list (decisions, questions, sends and
    withdrawals, schema changes, deletions, spending, plus what the project adds)."""
    if task["stage"] != "embryo":
        return False, f"stage {task['stage']}"
    if task["number"] == tracking_number:
        return False, "tracking issue"
    if task["number"] in excl["numbers"]:
        return False, "excluded by number"
    labels = set(task["labels"])
    hit = (labels & set(cfg["exclude_labels"])) | (labels & set(excl["labels"]))
    if hit or task.get("type") == "decision":
        return False, f"excluded label {sorted(hit)[0] if hit else 'type:decision'}"
    if cfg["pause_label"] in labels:
        return False, "task carries the pause label"
    for pat in list(cfg["exclude_title_patterns"]) + list(excl["title_patterns"]):
        if re.search(pat, task["title"], re.I):
            return False, f"title matches /{pat}/"
    return True, "eligible"


# ---------- ranking ----------
def inherited_deadline(task, by_key):
    """-> (date, source): the task's own deadline, else its parent chain's, else its module's (task['modDl'] = (date, module))."""
    seen, t = set(), task
    while t and t["key"] not in seen:
        seen.add(t["key"])
        if t.get("deadline"):
            return t["deadline"], "own" if t is task else f"parent {t['key']}"
        t = by_key.get(t.get("parent"))
    d, m = task.get("modDl") or (None, None)
    return (d, f"module {m}") if d else (None, "none")


def unblock_counts(tasks):
    """key -> number of open tasks that name it as a blocker."""
    open_ = {t["key"] for t in tasks if t["stage"] in ("embryo", "released", "in_progress")}
    out = {}
    for t in tasks:
        if t["key"] in open_:
            for b in t["blockedBy"]:
                out[b] = out.get(b, 0) + 1
    return out


def rank_inputs(task, by_key, unblocks):
    by = by_key
    deadline, src = inherited_deadline(task, by)
    return dict(priority=f"P{task['prio'] or 2}", deadline=deadline, deadline_source=src,
                blocked=any(by[b]["stage"] != "completed" for b in task["blockedBy"] if b in by),
                unblocks=unblocks.get(task["key"], 0), cls=task["cls"])


def sort_key(inputs):
    """Order: priority (P1 first), due date (none last), unblocked before blocked, unblocks more first, must before nice."""
    return (inputs["priority"], inputs["deadline"] or "9999-99-99", inputs["blocked"], -inputs["unblocks"], inputs["cls"] != "must")


def rank(tasks, by_key, unblocks):
    """-> list of dict(task, inputs, group) sorted best first; equal sort keys share a `group` and are ordered by task number
    (a stable placeholder that the value judgement may overrule)."""
    rows = [dict(task=t, inputs=rank_inputs(t, by_key, unblocks)) for t in tasks]
    return group_rows(rows)


def group_rows(rows):
    """Sort rows (each with `inputs`) best first, ties by project then task number, and number the tie groups."""
    for r in rows:
        r["sk"] = sort_key(r["inputs"])
    rows.sort(key=lambda r: (r["sk"], r.get("project", ""), r["task"]["number"]))
    g = -1
    for i, r in enumerate(rows):
        if i == 0 or r["sk"] != rows[i - 1]["sk"]:
            g += 1
        r["group"] = g
    return rows


def select(rows, n, judge=None):
    """Top n of ranked rows. A tie group that straddles the cutoff goes to `judge(tasks) -> (ordered keys, reasoning)`;
    a judge failure keeps the number order and is recorded as the reasoning. -> list of dict(task, inputs, rank, judgement)."""
    rows = [dict(r, judgement=None) for r in rows]
    if 0 < n < len(rows):
        edge = rows[n - 1]["group"]
        idx = [i for i, r in enumerate(rows) if r["group"] == edge]
        if len(idx) > 1 and rows[n]["group"] == edge and judge:
            grp = [rows[i] for i in idx]
            try:
                order, why = judge([r["task"] for r in grp])
                pos = {k: j for j, k in enumerate(order)}
                grp.sort(key=lambda r: pos.get(r["task"]["key"], len(pos)))
            except Exception as ex:  # a judge failure must not stop the job; the reasoning says so
                why = f"judgement unavailable ({type(ex).__name__}); ordered by task number"
            for i, r in zip(idx, grp):
                rows[i] = dict(r, judgement=why)
    return [dict(r, rank=i + 1) for i, r in enumerate(rows[:n])]


def judge_prompt(tasks):
    """Prompt for the tie-break call: key and title only (no task text)."""
    lines = "\n".join(f"- {t['key']}: {t['title']}" for t in tasks)
    return ("Several backlog tasks are tied on priority, deadline, blockers and class. Order them by relative value to the project "
            "(most valuable first), judging from the titles only. Answer with one JSON object: "
            '{"order": ["KEY", ...], "reasoning": "one or two sentences"}.\n' + lines)


def parse_judgement(text, keys):
    """-> (ordered keys, reasoning). Unknown keys are ignored; missing ones are appended in the given order. Raises ValueError."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the answer")
    d = json.loads(m.group(0))
    order = [k for k in d.get("order", []) if k in keys]
    if not order:
        raise ValueError("no known task key in the answer")
    return order + [k for k in keys if k not in order], str(d.get("reasoning", ""))[:400]


# ---------- records ----------
def release_record(now, project, row, trigger, mode, run):
    """One decision-log line for a released (or, in dry run, would-be released) task: title and number only, never task text."""
    t = row["task"]
    return dict(type="release", time=iso(now), run=run, project=project, key=t["key"], number=t["number"], title=t["title"], rank=row["rank"],
                trigger=trigger, mode=mode, inputs=row["inputs"], judgement=row.get("judgement"),
                state="would_release" if mode == DRY else "queued")


def outcome_record(now, project, key, number, state, note=""):
    """Later outcome of a released task: queued -> running -> finished | unfinished -> resumed (append-only)."""
    return dict(type="outcome", time=iso(now), project=project, key=key, number=number, state=state, note=note[:200])


def iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def release_list(lines):
    """Decision-log lines (dicts, in order) -> one row per released task for the budget view and the summary:
    project, number, key, title, rank, trigger, time, state (latest outcome), mode."""
    rows = {}
    for r in lines:
        k = (r["project"], r["number"])
        if r["type"] == "release":
            rows[k] = {f: r[f] for f in ("project", "number", "key", "title", "rank", "trigger", "time", "mode", "state")}
        elif r["type"] == "outcome" and k in rows:
            rows[k]["state"] = r["state"]
    return list(rows.values())


def counts_per_trigger(rows):
    """-> {trigger: {state: n}} for the budget view."""
    out = {}
    for r in rows:
        d = out.setdefault(r["trigger"], {})
        d[r["state"]] = d.get(r["state"], 0) + 1
    return out


def poll_line(now, idle, idle_reason, quota, triggers, decision_text):
    """The one-line poll log entry: idle verdict and reason, both quota readings, each trigger's verdict, the decision."""
    def q(name):
        if not quota:
            return f"{name}=n/a"
        used, reset = quota[name]
        return f"{name}_used={used:.0f}% {name}_reset_in={(reset - now) / 3600:.1f}h"
    trig = " ".join(f"{t['name']}={t['verdict']}" for t in triggers) or "triggers=not evaluated"
    return f"idle={'yes' if idle else 'no'} ({idle_reason}) | {q('five')} {q('week')} | {trig} | decision: {decision_text}"


def summary_text(rows, day, mode, skipped=0):
    """Daily summary (comment body): every released task of the day, newest last."""
    today = [r for r in rows if r["time"].startswith(day)]
    head = f"Boot-filling summary {day} ({mode}): {len(today)} task(s) {'would be ' if mode == DRY else ''}released"
    body = [f"- {r['project']} #{r['number']} {r['title']} | rank {r['rank']} | {r['trigger']} | {r['time']} | {r['state']}" for r in today]
    return "\n".join([head, *body])


def worker_slots(running, max_workers, quota, stop_left):
    """How many more workers may start: 0 when either window has stop_left percent or less left."""
    if min(100 - quota["five"][0], 100 - quota["week"][0]) <= stop_left:
        return 0
    return max(0, max_workers - running)
