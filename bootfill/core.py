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
    quota_history="quota-history.jsonl",  # ADR 0017: one record per poll; relative = inside the data folder, null = off
    quota_history_days=400,  # rows older than this are pruned, at most once a day
    usage_report=None,  # path of the budget page's usage.json; null = <budget_dir>/usage.json, "" = do not write it
    workers=2,  # how many workers may run at the same time (configuration key `workers`; 0 starts none)
    feed_stale_minutes=None,  # ADR 0018: a quota feed older than this is stale (null = 3 x poll_minutes); stale windows are estimated, never shown as measured
    worker_stop_left=5,  # stop starting workers when either window has this percent or less left
    summary_hour=20,  # local hour after which the daily summary is written
    tie_judgement=True,
    pause_label="pause",
    budget_dir="~/.local/share/project-cockpit/budget",  # decisions.jsonl for the cockpit's budget page (docs/contracts/budget-panel.md)
    exclude_labels=["type:decision", "question"],
    exclude_title_patterns=[r"\b(send|submit|withdraw|delete|drop|purchase|spend|spending|schema|ddl)\b"],
    # Authority need (ADR 0016): would the task dead-end for lack of an authority the job and its workers do not have?
    authority_mode="skip",  # "skip": do not release, the task stays an embryo; "flag": release but mark it (trail comment, decision log, summary)
    authority_labels=["needs-approval", "needs-owner", "needs-manager", "needs-pa", "owner-decision", "approval", "constitutional", "legal", "security", "policy"],
    authority_strong_patterns=[  # an approval or a choice by the owner is required: a hit alone means needs_authority
        r"\b(needs?|requires?|awaits?|pending)\s+(explicit\s+|prior\s+|the\s+owner'?s?\s+|owner\s+|manager\s+)?(approval|sign-?off|decision|consent)\b",
        r"\b(owner|manager|user)\s+(decides|to\s+decide|must\s+decide|approves|to\s+approve|must\s+approve)\b",
        r"\b(ddl|schema\s+(change|migration)|alter\s+table|drop\s+table|migration\s+of\s+the\s+schema)\b",
        r"\b(credentials?|secrets?|api[ -]?keys?|tokens?|passwords?)\b.{0,40}\b(rotate|issue|create|obtain|provide|share|enter)\b",
        r"\b(rotate|issue|create|obtain|provide|share|enter)\b.{0,40}\b(credentials?|secrets?|api[ -]?keys?|tokens?|passwords?)\b",
        r"\b(make|set)\b.{0,20}\b(public|open[ -]source)\b|\bpublic\s+release\b|\bpublish\b.{0,30}\b(publicly|to\s+the\s+public)\b",
    ],
    authority_weak_patterns=[  # words that often, not always, mean an authority is needed: a hit alone means maybe (model judge)
        r"\b(submit|submission|send|withdraw|delete|deletion|remove\s+data|purge|purchase|spend|spending|budget|payment|billing|paid)\b",
        r"\b(credentials?|secrets?|tokens?|passwords?|api[ -]?keys?)\b",
        r"\b(legal|licen[cs]e|compliance|gdpr|privacy|policy|contract|terms)\b",
        r"\b(public(ly)?|release\s+to|go\s+live|production|deploy)\b",
        r"\b(decide|decision|choose|choice|which\s+option|trade-?off)\b",
        r"\b(migration|migrate|rename|retire|deprecate)\b",
    ],
    protected_paths=["claude.md", "constitution.md", "*/schema.sql", "schema.sql", ".github/workflows/*", "docs/owner-defaults.md"],  # fnmatch on paths named in the task text (lower case)
    authority_weights=dict(label=3, strong=3, question=3, path=3, weak=1),
    authority_skip_score=3,  # score at or above: needs_authority
    authority_maybe_score=1,  # score at or above (and below skip): maybe, sent to the judge
    authority_judgement=True,
    authority_judge_max=10,  # judge calls per poll; further "maybe" tasks wait for a later poll and are skipped meanwhile (conservative)
    authority_excerpt_chars=600,  # cap of the body excerpt sent to the judge
    authority_cache_hours=24,  # a judged verdict is reused for this long while title, labels and excerpt are unchanged
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


def parse_feed(obj, now=None):
    """{five_hour, seven_day: {used_percentage, resets_at}} -> {'five': (used, resets_at) or None, 'week': (used, resets_at)}.
    A missing 5-hour window (the status line omits it after a reset until the next interactive use) is None = unknown (ADR 0018): never a
    synthetic window. Raises ValueError on any other missing or out-of-range field, so a broken feed is a logged failure, never a guess.
    `now` is accepted for compatibility and unused."""
    if isinstance(obj, dict) and isinstance(obj.get("rate_limits"), dict):
        obj = obj["rate_limits"]  # the status line writes {observed_at, rate_limits: {five_hour, seven_day}}; a bare object is accepted too
    out = {}
    for name, key in (("five", "five_hour"), ("week", "seven_day")):
        if key == "five_hour" and isinstance(obj, dict) and obj.get(key) in (None, {}) and isinstance(obj.get("seven_day"), dict):
            out[name] = None
            continue
        try:
            w = obj[key]
            used, reset = float(w["used_percentage"]), parse_time(w["resets_at"])
        except (KeyError, TypeError, ValueError) as ex:
            raise ValueError(f"quota feed: bad {key}: {type(ex).__name__}") from None
        if not 0 <= used <= 100:
            raise ValueError(f"quota feed: {key} used_percentage {used} out of range")
        out[name] = (used, reset)
    return out


def feed_observed_at(obj):
    """The status line's own `observed_at` stamp (epoch seconds) or None."""
    try:
        return parse_time(obj["observed_at"])
    except (KeyError, TypeError, ValueError):
        return None


def feed_stale_s(cfg):
    """Feed age (seconds) above which a reading counts as stale: `feed_stale_minutes`, default 3 x poll_minutes."""
    m = cfg.get("feed_stale_minutes")
    return (stale_minutes(cfg.get("poll_minutes", 10)) if m is None else float(m)) * 60


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
    Verdicts: fire, below_threshold, too_early, already_fired, unknown. A window is the quota's resets_at. A window that is None or has no
    reset time (feed stale and no cap known, ADR 0018) gives 'unknown': it never fires."""
    f5, wk = cfg["five_hour"], cfg["weekly"]
    out = []
    w = quota["five"]
    for name, min_left, payload in (("five_low", f5["low_left"], f5["low_payload"]), ("five_high", f5["high_left"], f5["high_payload"])):
        if w is None or w[1] is None:
            out.append(dict(name=name, verdict="unknown", payload=payload, key="", holds=False))
        else:
            out.append(_trigger(name, 100 - w[0], min_left, (w[1] - now) / 3600, f5["max_hours_to_reset"], payload, window_key(w[1]), fired, strict_hours=True))
    w = quota["week"]
    if w is None or w[1] is None:
        out.append(dict(name="weekly", verdict="unknown", payload=wk["payload"], key="", holds=False))
    else:
        out.append(_trigger("weekly", 100 - w[0], wk["min_left"], (w[1] - now) / 3600, wk["max_hours_to_reset"], wk["payload"], window_key(w[1]), fired, strict_hours=False))
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
    """Per-project exclusion file: {"labels": [...], "numbers": [...], "title_patterns": [...]}; missing file = empty. Optional authority keys
    (ADR 0016): "authority_labels", "authority_patterns", "protected_paths" add to the configured lists."""
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


# ---------- authority need (ADR 0016) ----------
NEEDS, MAYBE, NONE_ = "needs_authority", "maybe", "none"
_SECRET = re.compile(r"(?i)(gh[pousr]_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9_-]{10,}|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{12,}|[A-Za-z0-9+/=_-]{32,}"
                     r"|(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+|[\w.+-]+@[\w-]+\.[\w.-]+|/(?:Users|home)/[\w.-]+)")


def redact(text):
    """Remove secret-looking strings, e-mail addresses and home-directory paths from a text before it leaves the job (marked [removed])."""
    return _SECRET.sub("[removed]", text or "")


def excerpt(text, limit):
    """Redacted, whitespace-collapsed body excerpt capped at `limit` characters."""
    return re.sub(r"\s+", " ", redact(text)).strip()[:max(0, int(limit))]


def question_links(task, by_key, by_number):
    """Open question or decision issues the task depends on: its blockers, its parent and '#N' references in its text.
    -> sorted list of 'T-n' keys / numbers (strings)."""
    def is_q(t):
        return t and t["stage"] != "completed" and ("question" in (t.get("labels") or []) or t.get("type") == "decision" or "type:decision" in (t.get("labels") or []))
    out = set()
    for k in list(task.get("blockedBy") or []) + [task.get("parent")]:
        if is_q(by_key.get(k)):
            out.add(str(k))
    for n in re.findall(r"(?<![\w&])#(\d+)\b", (task.get("title") or "") + " " + (task.get("desc") or "")):
        t = by_number.get(int(n))
        if t is not None and t is not task and is_q(t):
            out.add(f"#{n}")
    return sorted(out)


def authority_signals(task, cfg, excl, by_key, by_number):
    """Cheap deterministic signals -> list of dict(kind, detail, weight). kinds: label, strong, weak, question, path."""
    w, sig = cfg["authority_weights"], []
    labels = set(task.get("labels") or [])
    for lab in sorted(labels & (set(cfg["authority_labels"]) | set(excl.get("authority_labels", [])))):
        sig.append(dict(kind="label", detail=lab, weight=w["label"]))
    text = (task.get("title") or "") + "\n" + (task.get("desc") or "")
    for kind, pats in (("strong", list(cfg["authority_strong_patterns"]) + list(excl.get("authority_patterns", []))), ("weak", cfg["authority_weak_patterns"])):
        for pat in pats:
            m = re.search(pat, text, re.I | re.S)
            if m:
                sig.append(dict(kind=kind, detail=re.sub(r"\s+", " ", m.group(0))[:60], weight=w[kind]))
    for q in question_links(task, by_key, by_number):
        sig.append(dict(kind="question", detail=f"open question/decision {q}", weight=w["question"]))
    import fnmatch
    prot = list(cfg["protected_paths"]) + list(excl.get("protected_paths", []))
    for p in task_paths(text):
        if any(fnmatch.fnmatch(p, pat.lower()) for pat in prot):
            sig.append(dict(kind="path", detail=p, weight=w["path"]))
    return sig


def authority_assess(task, cfg, excl, by_key, by_number):
    """-> dict(verdict, score, signals). needs_authority at score >= authority_skip_score, maybe at >= authority_maybe_score, else none."""
    sig = authority_signals(task, cfg, excl, by_key, by_number)
    score = sum(s["weight"] for s in sig)
    verdict = NEEDS if score >= cfg["authority_skip_score"] else MAYBE if score >= cfg["authority_maybe_score"] else NONE_
    return dict(verdict=verdict, score=score, signals=[f"{s['kind']}: {s['detail']}" for s in sig])


def authority_prompt(task, signals, limit):
    """Prompt for the authority judgement: title, labels, the cheap signals and a short redacted excerpt; nothing else."""
    return ("A background worker (a headless coding assistant) may pick up the task below on its own. It can edit and test code in the repository and open a draft "
            "pull request. It has NO authority to: decide between options on the owner's behalf, approve or change a database schema, handle credentials, spend "
            "money, send or submit anything outside the repository, delete data, publish publicly, or settle legal or policy questions. "
            "Would this task dead-end without such an authority? Answer with one JSON object: "
            '{"verdict": "needs_authority" or "none", "reason": "one short sentence"}.\n'
            f"Title: {redact(task.get('title', ''))}\nLabels: {', '.join(task.get('labels') or []) or '(none)'}\n"
            f"Signals: {'; '.join(signals) or '(none)'}\nExcerpt: {excerpt(task.get('desc'), limit)}")


def parse_authority(text):
    """-> (verdict, one-line reason). Raises ValueError when no usable verdict is in the answer."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON object in the answer")
    d = json.loads(m.group(0))
    v = str(d.get("verdict", "")).strip().lower()
    if v not in (NEEDS, NONE_):
        raise ValueError(f"unknown verdict {v!r}")
    return v, re.sub(r"\s+", " ", str(d.get("reason", ""))).strip()[:200]


def fingerprint(task, limit):
    import hashlib
    raw = "\n".join([task.get("title") or "", ",".join(sorted(task.get("labels") or [])), excerpt(task.get("desc"), limit)])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def authority_decide(task, cfg, excl, by_key, by_number, judge=None, cache=None, now=0.0, budget=None):
    """Full assessment of one eligible task. -> dict(verdict, release, score, signals, reason, judged, cached).
    none: release. needs_authority: do not release. maybe: ask `judge(task, signals) -> (verdict, reason)` (cached by fingerprint for
    authority_cache_hours; `budget` is a one-item list [calls left]); a missing judge, an exhausted budget or a failing judge leaves the
    task unreleased (a maybe always carries a cheap signal, so the cheap signals were not clean). `release` is the pre-mode verdict:
    True only for none or a judge's none."""
    a = authority_assess(task, cfg, excl, by_key, by_number)
    out = dict(verdict=a["verdict"], release=a["verdict"] == NONE_, score=a["score"], signals=a["signals"], reason="no authority signal" if a["verdict"] == NONE_ else "", judged=False, cached=False, failure="")
    if a["verdict"] == NEEDS:
        out["reason"] = "cheap signals: " + "; ".join(a["signals"])
    elif a["verdict"] == MAYBE:
        fp, hit = fingerprint(task, cfg["authority_excerpt_chars"]), (cache or {}).get(f"{task.get('key')}")
        if hit and hit.get("fp") == fp and now - hit.get("at", 0) < cfg["authority_cache_hours"] * 3600:
            out.update(verdict=hit["verdict"], release=hit["verdict"] == NONE_, reason=hit["reason"], judged=True, cached=True)
        elif not (judge and cfg.get("authority_judgement", True)):
            out["failure"] = "no authority judge available"
            out["reason"] = "weak signals (" + "; ".join(a["signals"]) + "); no judge available: conservative skip"
        elif budget is not None and budget[0] <= 0:
            out["failure"] = "judge budget for this poll used up (retried next poll)"
            out["reason"] = "weak signals; judge budget for this poll used up: conservative skip, retried next poll"
        else:
            if budget is not None:
                budget[0] -= 1
            try:
                v, why = judge(task, a["signals"])
                out.update(verdict=v, release=v == NONE_, reason=why or "judge gave no reason", judged=True)
                if cache is not None:
                    cache[f"{task.get('key')}"] = dict(fp=fp, at=now, verdict=v, reason=out["reason"])
            except Exception as ex:  # conservative: the cheap signals were not clean, so a failed judgement never releases
                out["failure"] = f"the authority judgement FAILED ({type(ex).__name__})"
                out["reason"] = f"weak signals ({'; '.join(a['signals'])}); judgement unavailable ({type(ex).__name__}): conservative skip"
    return out


STATUS = "NEEDS YOU"  # fixed status word: cannot be mistaken for progress; used in the decision log, the poll log and the summary


def needs_you_sentences(dec):
    """-> (why, todo): one plain sentence each. why: why the job did not release the task. todo: what exactly the owner must decide or do."""
    sig = dec.get("signals") or []
    first = lambda kind: next((x.split(": ", 1)[1] for x in sig if x.startswith(kind + ": ")), None)
    if dec.get("failure"):
        why = f"The job could not tell whether this task needs an authority: it has weak signals ({'; '.join(sig)}) and {dec['failure']}, so it stayed unreleased rather than guess."
        todo = "Read the task and release it by hand if no owner decision is needed; fix the judge if this repeats."
    elif dec.get("judged") and dec["verdict"] == NEEDS:
        why = f"The model judge found that the task would dead-end without an authority: {dec['reason'] or 'no reason given'}."
        todo = "Make the decision or give the approval the judge names, then release the task by hand."
    elif first("question"):
        why = f"The task depends on an open question or decision ({first('question')})."
        todo = "Answer that question and close it, then release the task by hand."
    elif first("path"):
        why = f"The task names a protected file ({first('path')}) that a worker may not change on its own."
        todo = f"Approve the change to {first('path')} (or take it off the protected list), then release the task by hand."
    elif first("label"):
        why = f"The task carries the label '{first('label')}', which marks it as needing the owner."
        todo = f"Decide or approve what '{first('label')}' stands for, then release the task by hand."
    elif first("strong"):
        why = f"The task text asks for an approval or a choice by the owner ('{first('strong')}')."
        todo = "Give that approval or make that choice in the task, then release it by hand."
    else:
        why = f"The task has weak authority signals ({'; '.join(sig) or 'none'}) and was not cleared."
        todo = "Read the task and release it by hand if no owner decision is needed."
    return why, todo


def needs_you_text(project, number, title, dec, action="skipped"):
    """THE one wording of a dead-end task; the decision-log record, the poll-log line and the daily summary all embed exactly this text."""
    why, todo = needs_you_sentences(dec)
    head = f"{STATUS}: {project} #{number} {title}" + (" (released anyway: authority_mode is flag)" if action == "flagged" else " (NOT released)")
    return f"{head}. Why: {why} To release it: {todo} Signals: {'; '.join(dec.get('signals') or []) or 'none'}."


def authority_skip_record(now, project, task, dec, mode):
    """Decision-log line for a task not released (mode skip) or released with a mark (mode flag): task, project, verdict, signals, reason, time, and the shared text."""
    action = "skipped" if mode == "skip" else "flagged"
    why, todo = needs_you_sentences(dec)
    return dict(type="authority_skip", time=iso(now), project=project, key=task["key"], number=task["number"], title=task["title"],
                status=STATUS, verdict=dec["verdict"], signals=dec["signals"], reason=dec["reason"], why=why, todo=todo, judged=dec["judged"],
                failure=dec.get("failure", ""), mode=mode, action=action, text=needs_you_text(project, task["number"], task["title"], dec, action))


def contract_authority(rec):
    """Own authority_skip record -> the budget contract's `authority_skip` line (extension kind, ignored by older readers)."""
    return dict(kind="authority_skip", time=rec["time"], project=rec["project"], issue=rec["number"], title=rec["title"], status=STATUS,
                verdict=rec["verdict"], reason=rec["text"][:300], action=rec["action"])


def authority_needs_text(items):
    """Summary section. items: authority_skip records (or their state copies) with `text`. Same wording as the records."""
    if not items:
        return ""
    return f"{STATUS}: {len(items)} task(s) wait for an owner decision or approval (they stay embryos; release by hand if you want them):\n" + "\n".join("- " + i["text"] for i in items)


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


def poll_line(now, idle, idle_reason, quota, triggers, decision_text, sources=None, tokens=None, estimated=()):
    """The one-line poll log entry: idle verdict and reason, both quota readings (an estimate is marked 'est.'), each trigger's verdict, the decision."""
    def q(name):
        if not quota:
            return f"{name}=n/a"
        w = quota[name]
        if w is None:
            return f"{name}=unknown"
        used, reset = w
        est = name in estimated
        out = f"{name}_used={'~' if est else ''}{used:.0f}%{' est.' if est else ''}"
        return out + ("" if reset is None else f" {name}_reset_in={(reset - now) / 3600:.1f}h")
    trig = " ".join(f"{t['name']}={t['verdict']}" for t in triggers) or "triggers=not evaluated"
    extra = (f" [{sources}]" if sources else "") + (f" | {tokens}" if tokens else "")
    return f"idle={'yes' if idle else 'no'} ({idle_reason}) | {q('five')} {q('week')}{extra} | {trig} | decision: {decision_text}"


def summary_text(rows, day, mode, needs=()):
    """Daily summary (comment body): every released task of the day, newest last."""
    today = [r for r in rows if r["time"].startswith(day)]
    head = f"Boot-filling summary {day} ({mode}): {len(today)} task(s) {'would be ' if mode == DRY else ''}released"
    body = [f"- {r['project']} #{r['number']} {r['title']} | rank {r['rank']} | {r['trigger']} | {r['time']} | {r['state']}" for r in today]
    extra = authority_needs_text(list(needs))
    return "\n".join([head, *body] + ([extra] if extra else []))


def worker_slots(running, max_workers, quota, stop_left):
    """How many more workers may start: 0 when either window has stop_left percent or less left."""
    if quota["five"] is None or quota["week"] is None:
        return 0  # a window with no reading and no estimate (ADR 0018): conservative, nothing new starts
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


def contract_poll(now, idle, idle_reason, quota, triggers, decision, estimated=()):
    """The contract's poll line. Unreadable or unknown quota -> the used/resets fields are left out (the page shows them as missing).
    An estimated window is NOT written as `*_used` (that field means measured): it goes to the extension field `*_est_used`."""
    d = dict(kind="poll", time=iso(now), idle=bool(idle), idle_reason=idle_reason,
             triggers=[dict(name=t["name"], verdict="fired" if t["verdict"] == "fire" else t["verdict"]) for t in triggers], decision=decision)
    for name, key in (("five", "five_hour"), ("week", "weekly")):
        w = quota.get(name) if quota else None
        if w is None:
            continue
        d[f"{key}_est_used" if name in estimated else f"{key}_used"] = w[0]
        if w[1] is not None:
            d[f"{key}_resets_at"] = iso(w[1])
    return d
