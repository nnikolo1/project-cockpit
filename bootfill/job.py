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

from . import core

log = logging.getLogger("bootfill")
plog = logging.getLogger("bootfill.poll")
TRAIL = "Released by the boot-filling job ({mode}), trigger {trigger}, rank {rank}, {when}."


class Ctx:
    """Everything a poll needs. hosting: tasks(project), labels(project, n), release(project, task, comment), comment(project, n, body);
    quota.read(); idle.last_write(); procs() -> [(pid, ppid, cmd)] or None; workers: start(entry, project_cfg), poll(handle); judge(tasks); notify(title, msg)."""

    def __init__(self, cfg, data_dir, hosting, quota, idle, workers, judge, notify, excl=None, procs=None):
        self.cfg, self.dir = cfg, pathlib.Path(data_dir)
        self.hosting, self.quota, self.idle, self.workers, self.judge, self.notify = hosting, quota, idle, workers, judge, notify
        self.procs = procs  # process lister for the other-agent check; None = not used
        self.excl = excl or {}  # project name -> parsed exclusions
        self.mode = cfg["mode"]
        self.state_path = self.dir / f"state-{self.mode}.json"
        self.decisions = self.dir / "decision-log.jsonl"  # own, richer log (ranking inputs, outcomes)
        self.contract = pathlib.Path(cfg["budget_dir"]).expanduser() / "decisions.jsonl"  # budget-page contract


def _io():
    from . import adapters
    return adapters


def _skip(ctx, now, idle, why, quota, triggers, text):
    """Write the poll: one line to the poll log and one contract line to the budget page's decisions.jsonl."""
    plog.info(core.poll_line(now, idle, why, quota, triggers, text))
    try:
        ctx.contract.parent.mkdir(parents=True, exist_ok=True)
        _io().append_jsonl(ctx.contract, core.contract_poll(now, bool(idle), why, quota, triggers, text))
    except OSError as ex:
        log.error("budget decision log not written: %s: %s", type(ex).__name__, ex)
    return text


def poll(ctx, now=None):
    """Run one poll; returns the decision text (also written to the poll log). Never raises for an expected failure."""
    A = _io()
    now = time.time() if now is None else now
    cfg, state = ctx.cfg, A.read_json(ctx.state_path, {})
    core.roll_day(state, dt.date.fromtimestamp(now).isoformat())
    state.setdefault("dry_run_since", now) if ctx.mode == core.DRY else None
    state.setdefault("queue", []), state.setdefault("running", [])
    track = cfg.get("tracking_issue")
    try:
        labels = ctx.hosting.labels(track["project"], track["number"]) if track else []
        stop = core.kill_switch((ctx.dir / cfg.get("kill_flag", "PAUSE")).exists(), labels, cfg["pause_label"])
    except Exception as ex:
        log.error("kill-switch check failed: %s: %s", type(ex).__name__, str(ex)[:200])
        return _skip(ctx, now, None, "unknown", None, [], f"skip: cannot read the pause label ({type(ex).__name__}); doing nothing")
    if stop:
        log.warning("%s", stop)
        return _skip(ctx, now, None, "not checked", None, [], f"skip: {stop}")
    quota, qerr = None, None

    def get_quota():
        nonlocal quota, qerr
        if quota is None and qerr is None:
            try:
                quota = ctx.quota.read()
            except Exception as ex:
                qerr = f"{type(ex).__name__}: {str(ex)[:150]}"
                log.error("quota reading failed: %s", qerr)
        return quota

    supervise(ctx, state, now, get_quota)
    idle, why = check_idle(ctx, state, now)
    if not idle:
        text = _skip(ctx, now, idle, why, quota, [], "skip: not idle")
        A.write_json(ctx.state_path, state)
        return text
    q = get_quota()
    if q is None:
        text = _skip(ctx, now, idle, why, None, [], f"skip: quota feed unreadable ({qerr})")
        A.write_json(ctx.state_path, state)
        return text
    triggers = core.evaluate_triggers(q, now, cfg, state.get("fired", {}))
    dec = core.decide(triggers, cfg, state["day_count"])
    if not dec["n"]:
        text = "skip: " + "; ".join(dec["reasons"] + [f"{t['name']} {t['verdict']}" for t in triggers if t["verdict"] != "fire"][:3])
        _skip(ctx, now, idle, why, q, triggers, text)
        daily_summary(ctx, state, now)
        A.write_json(ctx.state_path, state)
        return text
    released = release_batch(ctx, state, now, dec, triggers)
    text = f"{'would release' if ctx.mode == core.DRY else 'released'} {len(released)} of {dec['n']} ({', '.join(dec['fired'])})" + ("; " + "; ".join(dec["reasons"]) if dec["reasons"] else "")
    if not released:
        text = "skip: no eligible task"
    else:
        core.mark_fired(state, dec, triggers)
        state["day_count"] += len(released)
    _skip(ctx, now, idle, why, q, triggers, text)
    if q:
        start_workers(ctx, state, now, q)
    daily_summary(ctx, state, now)
    A.write_json(ctx.state_path, state)
    return text


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
    for pr in cfg["projects"]:
        name = pr["name"]
        try:
            tasks = ctx.hosting.tasks(name)
        except Exception as ex:
            log.error("task list of %s failed: %s: %s", name, type(ex).__name__, str(ex)[:200])
            continue
        by_key = {t["key"]: t for t in tasks}
        unb = core.unblock_counts(tasks)
        excl = core.parse_exclusions(ctx.excl.get(name))
        track = (cfg.get("tracking_issue") or {})
        ok = []
        for t in tasks:
            good, reason = core.eligibility(t, cfg, excl, track.get("number") if track.get("project") == name else None)
            if good:
                ok.append(t)
            elif t["stage"] == "embryo":
                log.info("skip %s %s: %s", name, t["key"], reason)
        for r in core.rank(ok, by_key, unb):
            r["project"] = name
            rows.append(r)
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
                if r["judgement"]:
                    comment += f" Tie broken by value judgement: {r['judgement']}"
                ctx.hosting.release(r["project"], r["task"], comment)
                state["queue"].append(dict(project=r["project"], key=r["task"]["key"], number=r["task"]["number"], title=r["task"]["title"], attempt=1,
                                           **core.entry_meta(r["task"])))
        except Exception as ex:
            log.error("release of %s %s failed: %s: %s", r["project"], r["task"]["key"], type(ex).__name__, str(ex)[:200])
            _contract(ctx, core.contract_release(rec, failed=True))
            continue
        A.append_jsonl(ctx.decisions, rec)
        _contract(ctx, core.contract_release(rec))
        log.info("%s %s %s rank %d trigger %s", rec["state"], r["project"], r["task"]["key"], r["rank"], trig)
        out.append(r)
    return out


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
        log.info("no worker started: %d running, stop threshold %s%%", len(state["running"]), ctx.cfg["worker_stop_left"])
        return
    active = [w["entry"] for w in state["running"]]
    counted = set()  # skips are counted once per task and poll
    judge = ctx.judge if ctx.cfg.get("conflict_judgement", True) else None
    for _ in range(min(free, len(state["queue"]))):
        idx, rec = core.choose_next(state["queue"], active, ctx.cfg, state.get("skips", {}), judge)
        e = state["queue"].pop(idx)
        try:
            h = ctx.workers.start(e, project_cfg(ctx.cfg, e["project"]))
        except Exception as ex:
            log.error("worker start for %s failed: %s: %s", e["key"], type(ex).__name__, str(ex)[:200])
            state["queue"].append(e)
            break
        state["skips"] = core.update_skips(state.get("skips", {}), rec, counted)
        counted.update(rec["skipped"])
        state["running"].append(dict(entry=e, handle=h))
        active.append(e)
        if active[:-1] or rec["skipped"]:
            A.append_jsonl(ctx.decisions, dict(rec, type="conflict_choice", time=core.iso(now), project=e["project"]))
            log.info("worker choice %s: %s (skipped %s)", rec["chosen"], rec["reason"], ", ".join(rec["skipped"]) or "none")
        A.append_jsonl(ctx.decisions, core.outcome_record(now, e["project"], e["key"], e["number"], "resumed" if e["attempt"] > 1 else "running"))


def supervise(ctx, state, now, get_quota):
    """Reap finished workers (outcome records). An unfinished one goes back to the queue once per attempt and resumes after a reset."""
    A = _io()
    still = []
    for w in state["running"]:
        rc = ctx.workers.poll(w["handle"])
        e = w["entry"]
        if rc is None:
            still.append(w)
        elif rc == 0:
            A.append_jsonl(ctx.decisions, core.outcome_record(now, e["project"], e["key"], e["number"], "finished"))
        else:
            A.append_jsonl(ctx.decisions, core.outcome_record(now, e["project"], e["key"], e["number"], "unfinished", f"exit code {rc}"))
            if e["attempt"] < ctx.cfg.get("max_attempts", 2):
                state["queue"].append(dict(e, attempt=e["attempt"] + 1))
    state["running"] = still
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
    text = core.summary_text(rows, today, ctx.mode)
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
    excl = {}
    for p in cfg["projects"]:
        excl[p["name"]] = A.read_json(os.path.expanduser(p["exclusions"]), {}) if p.get("exclusions") else {}
    own_cwd = pathlib.Path(cfg.get("headless_dir") or pathlib.Path(a.data_dir) / "headless").expanduser().resolve()
    own_cwd.mkdir(parents=True, exist_ok=True)  # the job's own headless calls run here, so their transcripts land in one recognisable, excluded folder
    exclude = list(cfg["transcript_exclude"]) + [f"*{core.encode_cwd(own_cwd)}*"]
    ctx = Ctx(cfg, a.data_dir, A.Gh(cfg["projects"]), A.Quota(cfg["quota_feed"], cfg.get("quota_refresh_cmd"), cwd=own_cwd), A.Idle(cfg["transcript_glob"], exclude),
              A.Workers(cfg["worker"], a.data_dir), A.Judge(cfg["judge_cmd"], cwd=own_cwd), A.Notifier(cfg.get("notify_cmd")), excl, A.Processes())
    if ctx.mode == core.REAL:
        since = A.read_json(f"{a.data_dir}/state-{core.DRY}.json", {}).get("dry_run_since")
        if since is None or time.time() - since < 7 * 86400:
            log.warning("mode is real but the dry run has not run for a week (since=%s)", since)
    log.info("poll start, mode %s", ctx.mode)
    print(poll(ctx))


if __name__ == "__main__":
    main()
