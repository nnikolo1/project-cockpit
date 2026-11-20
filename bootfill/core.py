"""Pure logic of the boot-filling job (ADR 0014): no network, no subprocess, no clock, no file access.
Everything here takes its inputs as arguments and is unit-tested; adapters.py and job.py do the I/O."""
import datetime as dt
import json
import pathlib
import re

DEFAULTS = dict(
    mode="dry-run",  # "dry-run" only logs what would be released; the owner changes it to "real"
    idle_minutes=10,
    poll_minutes=10,  # how often launchd starts a poll (the plist helper renders StartInterval from it; the cockpit's quota stale limit is 3 x this)
    process_check=True,  # also look for headless assistant processes the job did not start (falls back to transcripts only when `ps` fails)
    process_names=["claude"],  # exact executable name of the Claude Code command-line program (case-sensitive: the desktop app's binary is "Claude")
    process_exclude=["*.app/*", "*Chrome*", "*Claude Helper*", "*Claude.app*"],  # fnmatch on the whole command line: desktop app, browser and their helpers never count
    transcript_glob="~/.claude/projects/**/*.jsonl",  # Claude Code transcripts only; Claude Web and the desktop chat write none there
    transcript_exclude=["*-wt-boot-*"],  # fnmatch patterns of transcript paths written by the job's own workers; the headless folder is added automatically
    conflict_window=6,  # how many queued tasks (in rank order) are considered when a worker slot frees up
    conflict_max_skips=3,  # a task passed over this many times is started next regardless of conflicts
    conflict_threshold=3,  # a pair score at or above this counts as "would get in the way"
    conflict_labels=["shared-files", "shared", "constitutional"],  # a label on both tasks means shared files
    conflict_judgement=True,  # ask the model judge about unclear cases (titles and keys only)
    five_hour=dict(low_left=10, low_payload=5, high_left=50, high_payload=20, max_hours_to_reset=2),
    weekly=dict(min_left=20, max_hours_to_reset=24, payload=50),
    day_cap=60,
    workers=2,  # how many workers may run at the same time (configuration key `workers`; 0 starts none)
    worker_stop_left=5,  # stop starting workers when either window has this percent or less left
    summary_hour=20,  # local hour after which the daily summary is written
    tie_judgement=True,
    pause_label="pause",
    budget_dir="~/.local/share/project-cockpit/budget",  # decisions.jsonl for the cockpit's budget page (docs/contracts/budget-panel.md)
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
def idle_verdict(now, last_transcript, idle_minutes, other_procs=(), own_running=0, process_check=True):
    """-> (idle, reason). Idle means: NO CLAUDE CODE AGENT OTHER THAN THE JOB'S OWN is active (Claude Web, the desktop chat app and the browser never count). The job's own agents (quota refresh, judge, workers it
    started) never count: their transcripts are excluded by the caller and their processes are not in `other_procs`.
    last_transcript: epoch seconds of the newest transcript written by anyone else, or None. other_procs: pids of headless assistant
    processes the job did not start. process_check=False: the process list was unavailable, so only the transcript signal was used."""
    suffix = "" if process_check else " (process check unavailable: transcript signal only)"
    if other_procs:
        return False, f"other Claude Code agent active: {len(other_procs)} headless Claude Code process(es) not started by the job{suffix}"
    if last_transcript is not None:
        age = max(0.0, now - last_transcript)  # a write after `now` was taken (clock or timestamp-source offset) counts as "just now", never a negative age
        if age < idle_minutes * 60:
            return False, f"other Claude Code agent active: last write {int(age // 60)} min ago{suffix}"
    own = f" ({own_running} own worker(s) running)" if own_running else ""
    return True, f"only own Claude Code agents{own}{suffix}" if own_running else f"no other Claude Code agent active{suffix}"


def parse_ps(text):
    """`ps -axo pid=,ppid=,command=` output -> list of (pid, ppid, command); malformed lines are skipped."""
    out = []
    for line in (text or "").splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            out.append((int(parts[0]), int(parts[1]), parts[2]))
    return out


def other_agent_processes(procs, own_pids, names=("claude",), exclude=()):
    """Headless Claude Code processes (executable name exactly in `names`, started with -p / --print, command line not matching an `exclude` pattern:
    the desktop app, the browser and their helpers; Claude Web has no local process at all) that are not the job itself, not one of
    `own_pids` and not a descendant of one. An interactive session (no -p) is judged by its transcript only: open but silent is not running.
    -> sorted pids."""
    parent = {pid: pp for pid, pp, _ in procs}
    own = set(own_pids)

    def is_own(pid):
        seen = set()
        while pid in parent and pid not in seen:
            if pid in own:
                return True
            seen.add(pid)
            pid = parent[pid]
        return pid in own

    out = []
    for pid, _, cmd in procs:
        tok = cmd.split()
        if not tok or pathlib.PurePath(tok[0]).name not in names or transcript_excluded(cmd, exclude):
            continue
        if not ({"-p", "--print"} & set(tok[1:])) or is_own(pid):
            continue
        out.append(pid)
    return sorted(out)


def encode_cwd(path):
    """Working directory -> the folder name the assistant CLI uses for its transcripts (every non-alphanumeric character becomes '-')."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def transcript_excluded(path, patterns):
    """True when a transcript path matches one of the fnmatch patterns (transcripts of the job's own calls and workers)."""
    import fnmatch
    return any(fnmatch.fnmatch(str(path), pat) for pat in patterns)


def stale_minutes(poll_minutes):
    """Age after which the quota reading counts as stale: the feed is refreshed once per poll, so three polls without a refresh is a fault."""
    return 3 * max(1, int(poll_minutes))


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
    try:
        cap = max(0, int(max_workers))
    except (TypeError, ValueError):
        cap = DEFAULTS["workers"]  # an unusable value falls back to the default instead of failing the poll
    return max(0, cap - running)


# ---------- conflict-aware choice of the next worker ----------
_PATH = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]*|[\w-]+(?:\.[\w-]+)*\.(?:py|js|ts|tsx|jsx|md|json|html|css|sql|sh|yml|yaml|toml|txt|plist|svg|cfg|ini|rs|go|java|rb))")


def task_paths(text):
    """File or folder paths named in a text (path-like tokens only), normalised: lower case, no trailing punctuation or slash. -> sorted list."""
    out = set()
    for m in _PATH.finditer(text or ""):
        p = m.group(1).strip(".,;:)").rstrip("/").lower()
        if len(p) >= 3 and re.search(r"[a-z]", p) and not re.fullmatch(r"\d+(\.\d+)+", p) and "://" not in p:
            out.add(p)
    return sorted(out)


def entry_meta(task):
    """What the queue remembers about a released task for the conflict choice: module, labels and the paths its text names (no text)."""
    return dict(module=task.get("module") or "", labels=list(task.get("labels") or []),
                paths=task_paths(task.get("title", "") + "\n" + (task.get("desc") or "")))


def _paths_overlap(a, b):
    """First pair of paths of the two sets that are equal or one is a folder prefix of the other, else None."""
    for x in a:
        for y in b:
            if x == y or x.startswith(y + "/") or y.startswith(x + "/"):
                return x if len(x) <= len(y) else y
    return None


def _module_known(m):
    return bool(m) and m != "unassigned"


def pair_conflict(a, b, cfg):
    """How likely two tasks (queue/running entries: project, module, labels, paths) are to get in each other's way.
    -> dict(score, signals, unclear). Cheap deterministic signals: different project (never conflicts); a shared path; a shared
    'shared files' label; the same module (+3) or the same top-level module (+2); same project (+1). `unclear` = same project but
    neither the module map nor paths nor labels say anything for at least one of the two, so only a judgement could tell."""
    if a.get("project") != b.get("project"):
        return dict(score=0, signals=["different project"], unclear=False)
    score, sig = 1, ["same project"]
    hit = _paths_overlap(a.get("paths") or [], b.get("paths") or [])
    if hit:
        score += 10
        sig.append(f"shared path {hit}")
    lab = sorted((set(a.get("labels") or []) & set(b.get("labels") or [])) & set(cfg["conflict_labels"]))
    if lab:
        score += 6
        sig.append(f"shared-files label {lab[0]}")
    ma, mb = a.get("module") or "", b.get("module") or ""
    if _module_known(ma) and _module_known(mb):
        if ma == mb:
            score += 3
            sig.append(f"same module {ma}")
        elif ma.split("/")[0] == mb.split("/")[0]:
            score += 2
            sig.append(f"same top-level module {ma.split('/')[0]}")
        else:
            sig.append("different modules")
    informative = lambda e: _module_known(e.get("module")) or e.get("paths") or (set(e.get("labels") or []) & set(cfg["conflict_labels"]))
    unclear = not hit and not lab and not (informative(a) and informative(b))
    if unclear:
        sig.append("module or paths unknown")
    return dict(score=score, signals=sig, unclear=unclear)


def entry_conflict(cand, active, cfg):
    """A candidate against everything running or already chosen -> dict(score, signals, unclear). The worst pair decides; unclear only when
    no signal already reaches the threshold."""
    if not active:
        return dict(score=0, signals=["nothing running"], unclear=False)
    pairs = [pair_conflict(cand, x, cfg) for x in active]
    score = max(p["score"] for p in pairs)  # the worst pair decides (a sum would make many same-project workers conflict by baseline alone)
    sig = [f"vs {x['key']}: " + ", ".join(p["signals"]) for x, p in zip(active, pairs)]
    return dict(score=score, signals=sig, unclear=any(p["unclear"] for p in pairs) and score < cfg["conflict_threshold"])


def conflict_prompt(running, candidates):
    """Prompt for the conflict judgement: keys and titles only."""
    r = "\n".join(f"- {t['key']}: {t['title']}" for t in running) or "- (nothing)"
    c = "\n".join(f"- {t['key']}: {t['title']}" for t in candidates)
    return ("These tasks are being worked on right now, in the same code base, by parallel workers:\n" + r +
            "\nOrder the candidate tasks below by how unlikely they are to edit the same files or otherwise get in the way of the running ones "
            "(least likely first), judging from the titles only. Answer with one JSON object: "
            '{"order": ["KEY", ...], "reasoning": "one or two sentences"}.\n' + c)


def choose_next(queue, active, cfg, skips=None, judge=None):
    """Pick which queued entry starts next. Rank (queue order) stays primary: only the first `conflict_window` entries are considered
    and the head wins unless it would get in the way of `active` (running or already chosen) and a later one would not.
    A candidate already passed over `conflict_max_skips` times is taken at once. `judge(candidates, active) -> (ordered ids, reasoning)`
    is asked only when the cheap signals leave the head and every alternative unclear.
    -> (index in queue, record dict(chosen, skipped, signals, judgement, reason))."""
    skips = skips or {}
    win = list(queue[:max(1, int(cfg["conflict_window"]))])
    ident = lambda e: f"{e['project']}/{e['key']}"
    info = [entry_conflict(e, active, cfg) for e in win]
    thr = cfg["conflict_threshold"]
    ok = lambda i: info[i]["score"] < thr and not info[i]["unclear"]
    judgement, reason, pick = None, "", 0
    starved = next((i for i, e in enumerate(win) if skips.get(ident(e), 0) >= cfg["conflict_max_skips"]), None)
    if starved is not None:
        pick, reason = starved, f"passed over {skips[ident(win[starved])]} times: starvation limit {cfg['conflict_max_skips']}"
    elif not active:
        reason = "nothing running; rank order"
    elif ok(0):
        reason = "head does not conflict"
    else:
        alt = next((i for i in range(1, len(win)) if ok(i)), None)
        if alt is not None:
            pick, reason = alt, "skipped ahead to the first task without a conflict signal"
        else:
            unc = [i for i in range(len(win)) if info[i]["unclear"] and info[i]["score"] < thr]
            if unc and judge is not None and cfg.get("conflict_judgement", True):
                try:
                    order, why = judge([dict(key=ident(win[i]), title=win[i]["title"]) for i in unc], active)
                    pos = {k: j for j, k in enumerate(order)}
                    pick = min(unc, key=lambda i: (pos.get(ident(win[i]), len(pos)), i))
                    judgement, reason = why, "unclear signals; model judgement"
                except Exception as ex:  # a failing judge never stops the job
                    pick = min(range(len(win)), key=lambda i: (info[i]["score"], i))
                    reason = f"judgement unavailable ({type(ex).__name__}); least conflicting by score"
            else:
                pick = min(range(len(win)), key=lambda i: (info[i]["score"], i))
                reason = "every candidate conflicts or is unclear; least conflicting by score"
    chosen = win[pick]
    rec = dict(chosen=ident(chosen), number=chosen["number"], title=chosen["title"], queue_index=pick, skipped=[ident(e) for e in win[:pick]],
               signals={ident(win[i]): info[i]["signals"] + [f"score {info[i]['score']}"] for i in range(len(win)) if i <= pick},
               judgement=judgement, reason=reason, active=[x["key"] for x in active])
    return pick, rec


def update_skips(skips, rec, already=()):
    """Count one skip for each task passed over by `rec` (once per poll: ids in `already` are not counted again); the chosen task's counter resets."""
    skips = dict(skips)
    skips.pop(rec["chosen"], None)
    for k in rec["skipped"]:
        if k not in already:
            skips[k] = skips.get(k, 0) + 1
    return skips


# ---------- budget-panel contract (docs/contracts/budget-panel.md): decisions.jsonl lines for the cockpit ----------
def contract_release(rec, failed=False):
    """Own release record -> the contract's release line (kind, time, trigger, rank, project, issue, title, reason, state, dry_run)."""
    i = rec["inputs"]
    reason = f"{i['priority']}, due {i['deadline'] or 'none'} ({i['deadline_source']}), {'blocked' if i['blocked'] else 'unblocked'}, unblocks {i['unblocks']}, {i['cls']}"
    if rec.get("judgement"):
        reason += f"; judgement: {rec['judgement']}"
    return dict(kind="release", time=rec["time"], trigger=rec["trigger"], rank=rec["rank"], project=rec["project"], issue=rec["number"],
                title=rec["title"], reason=reason[:300], state="failed" if failed else ("would_release" if rec["mode"] == DRY else "released"),
                dry_run=rec["mode"] == DRY)


def contract_poll(now, idle, idle_reason, quota, triggers, decision):
    """The contract's poll line. Unreadable quota -> the used/resets fields are left out (the page shows them as missing)."""
    d = dict(kind="poll", time=iso(now), idle=bool(idle), idle_reason=idle_reason,
             triggers=[dict(name=t["name"], verdict="fired" if t["verdict"] == "fire" else t["verdict"]) for t in triggers], decision=decision)
    if quota:
        d.update(five_hour_used=quota["five"][0], five_hour_resets_at=iso(quota["five"][1]),
                 weekly_used=quota["week"][0], weekly_resets_at=iso(quota["week"][1]))
    return d
