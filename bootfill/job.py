"""One poll of the boot-filling job (ADR 0014), and the command line that launchd runs every `poll_minutes` (default 10).

    python3 -m bootfill.job --config CONFIG.json --data-dir DIR [--once]

The poll is a pure sequence over injected clients (Ctx), so tests drive it with fakes: no real task is released and no real worker
started unless the config says mode "real" AND real clients are wired in by main()."""
import argparse
import datetime as dt
import logging
import os
import pathlib
import sys
import time

from . import core, quota_history as QH

log = logging.getLogger("bootfill")
plog = logging.getLogger("bootfill.poll")
TRAIL = "Released by the boot-filling job ({mode}), trigger {trigger}, rank {rank}, {when}."


class Ctx:
    """Everything a poll needs. hosting: tasks(project), labels(project, n), release(project, task, comment), comment(project, n, body);
    quota.read(); idle.last_write(); procs() -> [(pid, ppid, cmd)] or None; workers: start(entry, project_cfg), poll(handle); judge(tasks); notify(title, msg)."""

    def __init__(self, cfg, data_dir, hosting, quota, idle, workers, judge, notify, excl=None, procs=None, authority_judge=None, scanner=None):
        self.cfg, self.dir = cfg, pathlib.Path(data_dir)
        self.hosting, self.quota, self.idle, self.workers, self.judge, self.notify = hosting, quota, idle, workers, judge, notify
        self.authority_judge = authority_judge  # (task, signals) -> (verdict, reason); None = no judge: 'maybe' tasks are skipped conservatively
        self.last_authority = dict(needs=[], flagged=0, judged=0)
        self.scanner = scanner  # quota_history.TokenScanner or None (then the history records say tokens were not computed)
        self.qinfo = None  # how this poll's quota was read: dict(source='refreshed', age, advanced) or dict(refresh_error)
        self.raw_quota = None  # the parsed feed of this poll after the refresh call (windows may be None), None when unreadable
        self.assessed = None  # this poll's quota assessment (see assess), computed once per poll
        self.procs = procs  # process lister for the other-agent check; None = not used
        self.excl = excl or {}  # project name -> parsed exclusions
        self.mode = cfg["mode"]
        self.state_path = self.dir / f"state-{self.mode}.json"
        self.decisions = self.dir / "decision-log.jsonl"  # own, richer log (ranking inputs, outcomes)
        self.contract = pathlib.Path(cfg["budget_dir"]).expanduser() / "decisions.jsonl"  # budget-page contract


def _io():
    from . import adapters
    return adapters


def _readings(ctx):
    """Classified readings of both windows (fresh, stale, missing, unavailable; source and age). Uses the feed as read after the refresh call
    when it ran this poll, else the feed as it is (the status line keeps it fresh while sessions run). -> (readings, parsed or None)."""
    info, quota = ctx.qinfo or {}, ctx.raw_quota
    stale_s = core.feed_stale_s(ctx.cfg)
    if quota and info.get("source") == "refreshed":
        age, parsed, ran, adv = info.get("age"), quota, True, info.get("advanced", True)
    else:
        age, parsed, ran, adv = None, None, False, None
        peek = getattr(ctx.quota, "peek", None)
        try:
            if peek is None:
                raise RuntimeError("no feed reader")
            parsed, age = peek()
        except Exception as ex:
            reason = f"{type(ex).__name__}: {str(ex)[:80]}"
            log.warning("quota feed not readable: %s", reason)
            return {k: dict(state="unavailable", used=None, resets_at=None, age=None, source="unavailable", reason=reason) for k in ("five", "week")}, None
    return QH.classify(parsed, age, stale_s, ran, adv, info.get("refresh_error")), parsed


def assess(ctx, now):
    """Everything the poll knows about the quota, computed once per poll (cached in ctx.assessed): the classified readings, the token counts of
    the windows (the feed's own window when fresh, else the rolling last 5 hours / 7 days: they come from the transcripts and do not depend on
    the feed), the estimates for windows without a fresh reading, and `eff`, the quota the triggers and the stop threshold act on
    ({five, week}: (used, resets_at) or None = unknown; the whole thing None when the feed is unreadable). ADR 0018. Never raises."""
    if ctx.assessed is not None:
        return ctx.assessed
    A = _io()
    readings, parsed = _readings(ctx)
    wins = {}
    if ctx.scanner is None:
        wins["error"] = "no transcript scanner"
    else:
        try:
            ctx.scanner.scan(now - QH.KEEP_DAYS_CACHE * 86400)
            for name, short, length in QH.WINDOWS:
                r = readings[short]
                start, basis = QH.window_bounds(r["resets_at"] if r["state"] == "fresh" else None, length, now)
                wins[name] = dict(start=start, basis=basis, tokens=ctx.scanner.window(start, now))
        except Exception as ex:
            wins = dict(error=f"scan failed: {type(ex).__name__}")
            log.error("token scan failed: %s: %s", type(ex).__name__, str(ex)[:150])
    gpath = ctx.dir / "last-good-quota.json"
    try:
        good = A.read_json(gpath, {})
    except ValueError:
        good = {}
    changed = False
    first_after = getattr(ctx.scanner, "first_event_at_or_after", lambda ts: None)
    for name, short, length in QH.WINDOWS:
        r, w = readings[short], wins.get(name)
        tokens = w["tokens"] if w else None
        r.update(QH.estimate(now, r, tokens, good.get(name), length, first_after))
        if w:
            changed |= QH.remember_good(good, name, now, r, tokens, w["start"], w["basis"])
    if changed:
        try:
            A.write_json(gpath, good)
        except OSError as ex:
            log.error("last good quota not written: %s: %s", type(ex).__name__, ex)
    eff = {k: QH.effective(r) for k, r in readings.items()}
    if all(r["state"] == "unavailable" for r in readings.values()):
        eff = None
    est = {k for k, r in readings.items() if r.get("estimated")}
    ctx.assessed = dict(readings=readings, parsed=parsed, wins=wins, eff=eff, est=est, notes=QH.quota_notes(readings) if eff is not None else [])
    return ctx.assessed


def snapshot(ctx, now, idle, why, text):
    """The quota history record of this poll (also for skipped polls) plus usage.json. Never raises: a failure is logged and the poll goes on.
    -> record or None."""
    A = _io()
    try:
        a = assess(ctx, now)
        rec = QH.build_record(now, ctx.mode, idle, why, text, a["readings"], a["wins"])
        hist = ctx.cfg.get("quota_history")
        if hist:
            hp = ctx.dir / hist
            A.append_jsonl(hp, rec)
            _prune_history(ctx, hp, now)
        if a["wins"].get("five_hour"):
            _write_usage(ctx, now, rec)
        return rec
    except Exception as ex:
        log.error("quota history not written: %s: %s", type(ex).__name__, str(ex)[:200])
        return None


def _write_usage(ctx, now, rec):
    path = ctx.cfg.get("usage_report")
    path = pathlib.Path(ctx.cfg["budget_dir"]).expanduser() / "usage.json" if path is None else path
    if path == "":
        return
    try:
        path = pathlib.Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        _io().write_json(path, QH.usage_report(now, rec))
    except OSError as ex:
        log.error("usage report not written: %s: %s", type(ex).__name__, ex)


def _prune_history(ctx, hp, now):
    """At most once a day: drop history rows older than quota_history_days."""
    marker, today = ctx.dir / "quota-history.pruned", dt.date.fromtimestamp(now).isoformat()
    try:
        if marker.exists() and marker.read_text().strip() == today:
            return
        kept, dropped = QH.prune(hp, now, ctx.cfg["quota_history_days"])
        marker.write_text(today)
        log.info("quota history pruned: %d kept, %d dropped (older than %d days)", kept, dropped, ctx.cfg["quota_history_days"])
    except OSError as ex:
        log.error("quota history prune failed: %s: %s", type(ex).__name__, ex)


def _skip(ctx, now, idle, why, triggers, text):
    """Write the poll: the quota history record, one line to the poll log and one contract line to the budget page's decisions.jsonl."""
    rec = snapshot(ctx, now, idle, why, text)
    a = ctx.assessed
    shown, est = (a["eff"], a["est"]) if a else (None, ())
    plog.info(core.poll_line(now, idle, why, shown, triggers, text, QH.reading_text(rec) if rec else None, QH.token_text(rec) if rec else None, est))
    try:
        ctx.contract.parent.mkdir(parents=True, exist_ok=True)
        _io().append_jsonl(ctx.contract, core.contract_poll(now, bool(idle), why, shown, triggers, text, est))
    except OSError as ex:
        log.error("budget decision log not written: %s: %s", type(ex).__name__, ex)
    return text


def _keep_awake(ctx, state, stopped):
    """Hold the machine awake (see adapters.KeepAwake) while the job is armed (real mode, not paused) or workers are running."""
    ka = getattr(ctx, "keep_awake", None)
    if ka is None or not ctx.cfg.get("keep_awake", True):
        return
    if (ctx.mode == core.REAL and not stopped) or state.get("running"):
        minutes = ctx.cfg.get("keep_awake_minutes") or 2 * ctx.cfg.get("poll_minutes", 10)
        try:
            ka.hold(int(minutes) * 60)
            log.info("keep awake: held for %d minutes", int(minutes))
        except Exception as ex:
            log.warning("keep awake failed: %s: %s", type(ex).__name__, str(ex)[:120])


def poll(ctx, now=None):
    """Run one poll; returns the decision text (also written to the poll log). Never raises for an expected failure."""
    A = _io()
    now = time.time() if now is None else now
    cfg, state = ctx.cfg, A.read_json(ctx.state_path, {})
    ctx.qinfo, ctx.raw_quota, ctx.assessed = None, None, None
    core.roll_day(state, dt.date.fromtimestamp(now).isoformat())
    state.setdefault("dry_run_since", now) if ctx.mode == core.DRY else None
    state.setdefault("queue", []), state.setdefault("running", [])
    track = cfg.get("tracking_issue")
    try:
        labels = ctx.hosting.labels(track["project"], track["number"]) if track else []
        stop = core.kill_switch((ctx.dir / cfg.get("kill_flag", "PAUSE")).exists(), labels, cfg["pause_label"])
    except Exception as ex:
        log.error("kill-switch check failed: %s: %s", type(ex).__name__, str(ex)[:200])
        return _skip(ctx, now, None, "unknown", [], f"skip: cannot read the pause label ({type(ex).__name__}); doing nothing")
    _keep_awake(ctx, state, stop)
    if stop:
        log.warning("%s", stop)
        return _skip(ctx, now, None, "not checked", [], f"skip: {stop}")
    quota, qerr = None, None

    def get_quota():
        nonlocal quota, qerr
        if quota is None and qerr is None:
            try:
                ctx.raw_quota = ctx.quota.read()
                ctx.qinfo = dict(source="refreshed", age=getattr(ctx.quota, "last_age", 0.0), advanced=getattr(ctx.quota, "advanced", True))
                quota = assess(ctx, now)["eff"]
            except Exception as ex:
                qerr = f"{type(ex).__name__}: {str(ex)[:150]}"
                ctx.qinfo = dict(refresh_error=qerr[:80])
                log.error("quota reading failed: %s", qerr)
        return quota

    supervise(ctx, state, now, get_quota)
    idle, why = check_idle(ctx, state, now)
    if not idle:
        text = _skip(ctx, now, idle, why, [], "skip: not idle")
        A.write_json(ctx.state_path, state)
        return text
    q = get_quota()
    if q is None:
        text = _skip(ctx, now, idle, why, [], f"skip: quota feed unreadable ({qerr})")
        A.write_json(ctx.state_path, state)
        return text
    triggers = core.evaluate_triggers(q, now, cfg, state.get("fired", {}))
    dec = core.decide(triggers, cfg, state["day_count"])
    if not dec["n"]:
        text = "skip: " + "; ".join(dec["reasons"] + [f"{t['name']} {t['verdict']}" for t in triggers if t["verdict"] != "fire"][:3]) + _notes(ctx)
        _skip(ctx, now, idle, why, triggers, text)
        daily_summary(ctx, state, now)
        A.write_json(ctx.state_path, state)
        return text
    released = release_batch(ctx, state, now, dec, triggers)
    auth = authority_suffix(ctx)
    text = f"{'would release' if ctx.mode == core.DRY else 'released'} {len(released)} of {dec['n']} ({', '.join(dec['fired'])})" + ("; " + "; ".join(dec["reasons"]) if dec["reasons"] else "")
    if not released:
        text = "skip: no eligible task"
    else:
        core.mark_fired(state, dec, triggers)
        state["day_count"] += len(released)
    text += auth + _notes(ctx)
    _skip(ctx, now, idle, why, triggers, text)
    if q:
        start_workers(ctx, state, now, q)
    daily_summary(ctx, state, now)
    A.write_json(ctx.state_path, state)
    return text


def _notes(ctx):
    """Decision-text addition: which quota windows are estimated or unknown (ADR 0018); empty when the feed is fresh."""
    n = ctx.assessed["notes"] if ctx.assessed else []
    return " | quota: " + "; ".join(n) if n else ""


def check_idle(ctx, state, now):
    """Idle = no agent other than the job's own is active. Own workers never block; other headless assistant processes and any
    transcript written inside the idle window (own transcripts are excluded by the Idle adapter) do."""
    cfg, others, ok = ctx.cfg, [], True
    if ctx.procs is not None and cfg.get("process_check", True):
        try:
            own = [os.getpid()] + [w["handle"].get("pid") for w in state["running"] if w["handle"].get("pid")]
            others = core.other_agent_processes(ctx.procs(), own, cfg["process_names"], cfg["process_exclude"])
        except Exception as ex:
            ok = False
            log.warning("process check unavailable (%s: %s); using the transcript signal only", type(ex).__name__, str(ex)[:150])
    elif ctx.procs is not None:
        ok = False
    return core.idle_verdict(now, ctx.idle.last_write(), cfg["idle_minutes"], others, len(state["running"]), ok)


def release_batch(ctx, state, now, dec, triggers):
    """Gather eligible embryos of every project, rank, select, release (or only log in dry run). -> released rows."""
    A, cfg = _io(), ctx.cfg
    rows, run_id = [], f"{int(now)}"
    cache, logged, open_ = state.setdefault("authority_cache", {}), state.setdefault("authority_logged", {}), state.setdefault("authority_open", {})
    needs, judged, budget = [], 0, [cfg["authority_judge_max"]]
    hist, guarded = A.read_jsonl(ctx.decisions), state.setdefault("guard_logged", {})
    for pr in cfg["projects"]:
        name = pr["name"]
        try:
            tasks = ctx.hosting.tasks(name)
        except Exception as ex:
            log.error("task list of %s failed: %s: %s", name, type(ex).__name__, str(ex)[:200])
            continue
        by_key = {t["key"]: t for t in tasks}
        by_number = {t["number"]: t for t in tasks}
        unb = core.unblock_counts(tasks)
        excl = core.parse_exclusions(ctx.excl.get(name))
        track = (cfg.get("tracking_issue") or {})
        ok, open_items = [], []
        for t in tasks:
            good, reason = core.eligibility(t, cfg, excl, track.get("number") if track.get("project") == name else None)
            if good:
                why = core.release_guard(name, t, hist, state["queue"], state["running"], now, cfg["rerelease_guard_days"])
                if why:  # defence in depth (ADR 0019): never silently, once per change
                    if guarded.get(f"{name}/{t['number']}") != why:
                        guarded[f"{name}/{t['number']}"] = why
                        A.append_jsonl(ctx.decisions, dict(type="release_skip", time=core.iso(now), project=name, key=t["key"], number=t["number"], title=t["title"], reason=why))
                        plog.warning("not released: %s #%s %s: %s", name, t["number"], t["title"], why)
                    continue
                ad = core.authority_decide(t, cfg, excl, by_key, by_number, ctx.authority_judge, cache, now, budget)
                judged += ad["judged"] and not ad["cached"]
                if not ad["release"]:
                    rec = core.authority_skip_record(now, name, t, ad, cfg["authority_mode"])
                    open_items.append(dict(project=name, number=t["number"], title=t["title"], text=rec["text"], action=rec["action"]))
                    if cfg["authority_mode"] == "flag":
                        t["_authority"] = rec
                    else:
                        needs.append(rec)
                        if logged.get(f"{name}/{t['key']}") != rec["text"]:  # one record per change, not one per poll
                            logged[f"{name}/{t['key']}"] = rec["text"]
                            A.append_jsonl(ctx.decisions, rec)
                            _contract(ctx, core.contract_authority(rec))
                            plog.warning("%s", rec["text"])
                            log.warning("authority: %s %s %s", rec["action"], name, t["key"])
                        continue
                ok.append(t)
            elif t["stage"] == "embryo":
                log.info("skip %s %s: %s", name, t["key"], reason)
        open_[name] = open_items
        for r in core.rank(ok, by_key, unb):
            r["project"] = name
            rows.append(r)
    ctx.last_authority = dict(needs=needs, flagged=sum(1 for r in rows if r["task"].get("_authority")), judged=judged)
    rows = core.group_rows(rows)
    judge = ctx.judge if cfg["tie_judgement"] else None
    chosen = core.select(rows, dec["n"], judge)
    slots = core.batch_triggers(triggers, len(chosen))
    out = []
    for r, trig in zip(chosen, slots + ["weekly"] * len(chosen)):
        rec = core.release_record(now, r["project"], r, trig, ctx.mode, run_id)
        try:
            if ctx.mode == core.REAL:
                comment = TRAIL.format(mode="real", trigger=trig, rank=r["rank"], when=rec["time"])
                if r["task"].get("_authority"):
                    comment += " " + r["task"]["_authority"]["text"]
                if r["judgement"]:
                    comment += f" Tie broken by value judgement: {r['judgement']}"
                ctx.hosting.release(r["project"], r["task"], comment)
                state["queue"].append(dict(project=r["project"], key=r["task"]["key"], number=r["task"]["number"], title=r["task"]["title"], attempt=1,
                                           **core.entry_meta(r["task"])))
        except Exception as ex:
            log.error("release of %s %s failed: %s: %s", r["project"], r["task"]["key"], type(ex).__name__, str(ex)[:200])
            _contract(ctx, core.contract_release(rec, failed=True))
            continue
        arec = r["task"].get("_authority")
        if arec:  # authority_mode flag: released, but marked everywhere
            rec["authority"] = dict(status=core.STATUS, text=arec["text"])
            A.append_jsonl(ctx.decisions, arec)
            _contract(ctx, core.contract_authority(arec))
            plog.warning("%s", arec["text"])
        A.append_jsonl(ctx.decisions, rec)
        _contract(ctx, core.contract_release(rec))
        log.info("%s %s %s rank %d trigger %s", rec["state"], r["project"], r["task"]["key"], r["rank"], trig)
        out.append(r)
    return out


def authority_suffix(ctx):
    """Poll-line addition: how many tasks NEED YOU (skipped, not released) or were released flagged, by number; empty when none."""
    a = ctx.last_authority
    parts = []
    if a["needs"]:
        parts.append(f"{core.STATUS}: {len(a['needs'])} task(s) NOT released, authority needed ({', '.join('#' + str(r['number']) for r in a['needs'])})")
    if a["flagged"]:
        parts.append(f"{core.STATUS}: {a['flagged']} flagged task(s) up for release (authority_mode flag)")
    return "; " + "; ".join(parts) if parts else ""


def _contract(ctx, line):
    try:
        ctx.contract.parent.mkdir(parents=True, exist_ok=True)
        _io().append_jsonl(ctx.contract, line)
    except OSError as ex:
        log.error("budget decision log not written: %s: %s", type(ex).__name__, ex)


def project_cfg(cfg, name):
    return next(p for p in cfg["projects"] if p["name"] == name)


def start_workers(ctx, state, now, q):
    """Start queued tasks up to the free slots unless a window is nearly used up (real mode only)."""
    A = _io()
    if ctx.mode != core.REAL or not state["queue"]:
        return
    free = core.worker_slots(len(state["running"]), ctx.cfg["workers"], q, ctx.cfg["worker_stop_left"])
    if not free:
        why = "a window is unknown (feed stale, no cap yet)" if q["five"] is None or q["week"] is None else f"stop threshold {ctx.cfg['worker_stop_left']}%"
        log.info("no worker started: %d running, %s", len(state["running"]), why)
        return
    lines = A.read_jsonl(ctx.decisions)
    state["queue"], dropped = core.worker_candidates(state["queue"], state["running"], lines)
    for e, why in dropped:  # never twice: a worker ran for this issue already (or it repeats an entry); recorded, never silent
        log.warning("no worker for %s %s: %s", e["project"], e["key"], why)
        A.append_jsonl(ctx.decisions, core.outcome_record(now, e["project"], e["key"], e["number"], "skipped_already_worked", why))
    if not state["queue"]:
        return
    active = [w["entry"] for w in state["running"]]
    counted = set()  # skips are counted once per task and poll
    judge = ctx.judge if ctx.cfg.get("conflict_judgement", True) else None
    for _ in range(min(free, len(state["queue"]))):
        idx, rec = core.choose_next(state["queue"], active, ctx.cfg, state.get("skips", {}), judge)
        e = state["queue"].pop(idx)
        try:
            h = ctx.workers.start(e, project_cfg(ctx.cfg, e["project"]))
        except Exception as ex:  # nothing was assigned, nothing is undone; no retry: the Orchestrator or the Manager decides (requeue record)
            why = f"{type(ex).__name__}: {str(ex)[:150]}"
            log.error("worker start for %s failed: %s", e["key"], why)
            plog.warning("%s: worker for %s %s could not be started (%s); the task stays released, no retry", core.STATUS, e["project"], e["key"], why)
            A.append_jsonl(ctx.decisions, core.outcome_record(now, e["project"], e["key"], e["number"], "start_failed", why))
            _contract(ctx, core.contract_worker(now, e["project"], e["number"], e["title"], "start_failed", note=why))
            continue
        state["skips"] = core.update_skips(state.get("skips", {}), rec, counted)
        counted.update(rec["skipped"])
        state["running"].append(dict(entry=e, handle=h))
        active.append(e)
        if active[:-1] or rec["skipped"]:
            A.append_jsonl(ctx.decisions, dict(rec, type="conflict_choice", time=core.iso(now), project=e["project"]))
            log.info("worker choice %s: %s (skipped %s)", rec["chosen"], rec["reason"], ", ".join(rec["skipped"]) or "none")
        A.append_jsonl(ctx.decisions, core.outcome_record(now, e["project"], e["key"], e["number"], "resumed" if e.get("requeue") else "running"))


def apply_requeues(ctx, state, now):
    """An explicit requeue record (Orchestrator or Manager) puts the task back into the queue ONCE; nothing else ever does (ADR 0019)."""
    A = _io()
    lines = A.read_jsonl(ctx.decisions)
    rel = {(r["project"], r["number"]): r for r in lines if r.get("type") == "release"}
    for project, number, rq in core.pending_requeues(lines):
        if any(e["project"] == project and e["number"] == number for e in [w["entry"] for w in state["running"]] + state["queue"]):
            continue
        r = rel.get((project, number), {})
        state["queue"].append(dict(project=project, key=r.get("key", f"#{number}"), number=number, title=r.get("title", ""), attempt=1, requeue=True,
                                   module="", labels=[], paths=[]))
        log.info("requeue of %s #%s by %s: %s", project, number, rq["by"], rq["reason"])


def check_marked(ctx, w, now):
    """The worker's FIRST action is to assign the issue to the Manager login (stage in progress, the signal the cockpit reads). The job only checks and records:
    marked -> one `in_progress` line; not marked -> ONE NEEDS YOU record 'worker did not mark in progress'. It never assigns, never retries."""
    A, e = _io(), w["entry"]
    if w.get("marked"):
        return
    try:
        marked = bool(ctx.hosting.assignees(e["project"], e["number"]))
    except Exception as ex:
        log.error("in-progress check of %s failed: %s: %s", e["key"], type(ex).__name__, str(ex)[:150])
        return
    if marked:
        w["marked"] = True
        A.append_jsonl(ctx.decisions, core.outcome_record(now, e["project"], e["key"], e["number"], "in_progress", "the worker marked the issue in progress"))
        _contract(ctx, core.contract_worker(now, e["project"], e["number"], e["title"], "in_progress"))
    elif not w.get("flagged"):
        w["flagged"] = True
        note = "worker did not mark in progress"
        A.append_jsonl(ctx.decisions, core.outcome_record(now, e["project"], e["key"], e["number"], "not_in_progress", note))
        _contract(ctx, core.contract_worker(now, e["project"], e["number"], e["title"], "needs_you", note=note))
        plog.warning("%s: %s %s: %s", core.STATUS, e["project"], e["key"], note)


def supervise(ctx, state, now, get_quota):
    """Reap finished workers. Exit 0 and a pull request: AWAITING ACCEPTANCE (the Orchestrator or the Manager merges or rejects it). Anything else:
    NEEDS YOU, the task stays in progress. Never a retry, never back to the queue (ADR 0019)."""
    A = _io()
    still = []
    for w in state["running"]:
        rc = ctx.workers.poll(w["handle"])
        e = w["entry"]
        check_marked(ctx, w, now)
        if rc is None:
            still.append(w)
            continue
        P, K, N, T = e["project"], e["key"], e["number"], e["title"]
        if rc == 0:
            A.append_jsonl(ctx.decisions, core.outcome_record(now, P, K, N, "finished", "exit code 0"))
            try:
                pr = ctx.hosting.find_pr(P, f"{ctx.cfg.get('worker', {}).get('branch_prefix', 'boot/')}{N}")
            except Exception as ex:
                pr = None
                log.error("pull request lookup for %s failed: %s: %s", K, type(ex).__name__, str(ex)[:150])
            if pr is not None:
                note = f"draft pull request #{pr} is open; the Orchestrator or the Manager reviews and merges it (the issue then closes) or rejects it"
                A.append_jsonl(ctx.decisions, core.outcome_record(now, P, K, N, "awaiting_acceptance", note, pr=pr))
                _contract(ctx, core.contract_worker(now, P, N, T, "awaiting_acceptance", pr=pr, note=note))
                continue
            why = "the worker exited 0 but no pull request was found"
        else:
            A.append_jsonl(ctx.decisions, core.outcome_record(now, P, K, N, "unfinished", f"exit code {rc}"))
            why = f"the worker failed (exit code {rc})"
        note = f"{why}; the task stays in progress, no retry"
        A.append_jsonl(ctx.decisions, core.outcome_record(now, P, K, N, "needs_you", note))
        _contract(ctx, core.contract_worker(now, P, N, T, "needs_you", note=note))
        plog.warning("%s: %s %s: %s", core.STATUS, P, K, note)
        try:
            ctx.notify(core.STATUS, f"{P} {K}: {note}")
        except Exception as ex:
            log.warning("notification failed: %s", ex)
    state["running"] = still
    apply_requeues(ctx, state, now)
    if state["queue"] and ctx.mode == core.REAL:
        q = get_quota()
        if q:
            start_workers(ctx, state, now, q)


def daily_summary(ctx, state, now):
    """After summary_hour, once per day: a comment on the tracking issue plus a desktop notification."""
    A = _io()
    today = dt.date.fromtimestamp(now).isoformat()
    if state.get("summary_date") == today or dt.datetime.fromtimestamp(now).hour < ctx.cfg["summary_hour"]:
        return
    rows = core.release_list(A.read_jsonl(ctx.decisions))
    needs = [i for items in state.get("authority_open", {}).values() for i in items]
    text = core.summary_text(rows, today, ctx.mode, needs)
    track = ctx.cfg.get("tracking_issue")
    try:
        if track:
            ctx.hosting.comment(track["project"], track["number"], text)
        ctx.notify("Boot-filling", text.splitlines()[0])
    except Exception as ex:
        log.error("daily summary failed: %s: %s", type(ex).__name__, str(ex)[:200])
        return
    state["summary_date"] = today
    log.info("daily summary written (%d line(s))", len(text.splitlines()))


def main(argv=None):
    A = _io()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True, help="local config JSON (see bootfill/config.example.json)")
    ap.add_argument("--data-dir", required=True, help="local folder for logs, state and the decision log (derived data, never committed)")
    ap.add_argument("--once", action="store_true", help="one poll, then exit (what launchd runs)")
    a = ap.parse_args(argv)
    A.setup_logging(a.data_dir)
    cfg = core.merge_config(core.DEFAULTS, A.read_json(a.config, None))
    if cfg["mode"] not in (core.DRY, core.REAL):
        sys.exit(f"mode must be {core.DRY!r} or {core.REAL!r}")
    for p in cfg["projects"]:  # a leading ~ in the project paths is expanded here, once (the hosting adapter opens them as they are)
        for k in ("dir", "modules", "exclusions"):
            if isinstance(p.get(k), str):
                p[k] = os.path.expanduser(p[k])
    excl = {}
    for p in cfg["projects"]:
        excl[p["name"]] = A.read_json(p["exclusions"], {}) if p.get("exclusions") else {}
    own_cwd = pathlib.Path(cfg.get("headless_dir") or pathlib.Path(a.data_dir) / "headless").expanduser().resolve()
    own_cwd.mkdir(parents=True, exist_ok=True)  # the job's own headless calls run here, so their transcripts land in one recognisable, excluded folder
    exclude = list(cfg["transcript_exclude"]) + [f"*{core.encode_cwd(own_cwd)}*"]
    ctx = Ctx(cfg, a.data_dir, A.Gh(cfg["projects"]), A.Quota(cfg["quota_feed"], cfg.get("quota_refresh_cmd"), cwd=own_cwd), A.Idle(cfg["transcript_glob"], exclude),
              A.Workers(cfg["worker"], a.data_dir, env=cfg.get("worker_env"), rules=cfg.get("worker_rules")), A.Judge(cfg["judge_cmd"], cwd=own_cwd), A.Notifier(cfg.get("notify_cmd")), excl, A.Processes(),
              A.AuthorityJudge(cfg["judge_cmd"], cfg["authority_excerpt_chars"], cwd=own_cwd),
              QH.TokenScanner(cfg["transcript_glob"], exclude, pathlib.Path(a.data_dir) / "token-cache.json"))
    ctx.keep_awake = A.KeepAwake()
    if ctx.mode == core.REAL:
        since = A.read_json(f"{a.data_dir}/state-{core.DRY}.json", {}).get("dry_run_since")
        if since is None or time.time() - since < 7 * 86400:
            log.warning("mode is real but the dry run has not run for a week (since=%s)", since)
    log.info("poll start, mode %s", ctx.mode)
    print(poll(ctx))


if __name__ == "__main__":
    main()
