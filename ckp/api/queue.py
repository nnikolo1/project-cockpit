"""API group `queue`: the Task Queue tab (Workers, processing log, poll, all projects) and the action log."""
import logging
import json
import os
import subprocess
import threading
import time

from ckp.core import health as core_health, queue_view as core_queue_view
from ckp.api import cache as api_cache, context as api_context, gh as api_gh, health as api_health, settings as api_settings, state as api_state

from ckp.api.result import ok
log = logging.getLogger("cockpit")


def get_queue(p):
    """The local part of the Task Queue tab (no GitHub): running Workers from the Dispatcher's state file, the processing log's newest lines,
    the Dispatcher settings and the last daily backup's status record (None when there is none). The token and quota files are read for the Token Usage tab (`/api/usage`), also local."""
    f, o = api_context.queue_files(p), api_context.DATA_DIR / (p.get("name") or p["repo"].split("/")[-1])   # the backup and compliance records stay per project
    settings = api_settings.get_settings(p)
    return dict(core_queue_view.queue_local(api_context._read_text(f["state"]), api_context._read_text(f["plog"])), settings=settings, asOf=time.time(),
                projects=core_queue_view.listed_projects(settings["values"]),   # PC-138: empty with one project, so the single-project view stays as it is
                backup=core_health.backup_status(api_context._read_text(o / core_health.BACKUP_STATUS_FILE)), compliance=core_health.compliance_summary(api_context._read_text(o / core_health.COMPLIANCE_REPORT_FILE)))


def get_queue_all(p):
    """PC-138: the one queue's tasks over every project the Dispatcher settings list (more than one; else None): each project's tasks from
    its own repository's cached state (no forced GitHub call), with the live overlay, tagged with the project and ranked as the Dispatcher
    ranks them (logic.queue_all). A project this Cockpit does not serve, or whose state cannot be read, is named with the reason."""
    listed = core_queue_view.listed_projects(api_settings.get_settings(p)["values"])
    if not listed:
        return None
    parts, status = [], []
    for e in listed:
        q, err, at = api_context.served_project(e), None, None
        if q is None:
            err = "not served by this Cockpit"
        else:
            try:
                S = api_state._served(q, api_state.live_state(q, api_state.get_state(p=q)))
                parts.append(dict(short=e["short"], first=e["first"], tasks=S["tasks"]))
                at = S["source"].get("fetchedAt")
            except Exception as ex:
                log.warning("queue of all projects: %s unreadable: %s", e["short"], str(ex)[:120])
                err = str(ex)[:200]
        status.append(dict(e, name=q.get("name") if q else None, fetchedAt=at, error=err))
    return dict(projects=status, tasks=core_queue_view.queue_all(parts))


# ---------- action log (PC-132): <data dir>/<project>/actions.jsonl, appended, mode 0600, rotated by size keeping five files ----------
ACTION_LOG_MAX = 1_000_000  # bytes of one file before it is rotated


ACTION_LOG_KEEP = 5         # files kept: actions.jsonl and actions.jsonl.1 .. .4 (the oldest is dropped)


_action_log_lock = threading.Lock()


_version = []


def code_version():
    """The Cockpit's own commit (short hash), read once; '' when it is not a git checkout."""
    if not _version:
        try:
            _version.append(subprocess.run(["git", "-C", str(api_context.ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip())
        except (OSError, subprocess.SubprocessError):
            _version.append("")
    return _version[0]


def action_log_path(p):
    """<data dir>/<project>/actions.jsonl; None for a project without a name or repo (nothing to file it under)."""
    name = p.get("name") or (p.get("repo") or "").split("/")[-1]
    return api_context.DATA_DIR / name / "actions.jsonl" if name else None


def log_action(p, rec):
    """Append one event to the project's action log; rotate first when the file is full. Never fails the action: a write error is a warning.
    An offline copy writes nothing (its data folder is the backup)."""
    f = action_log_path(p) if p and not api_context.OFFLINE else None
    if not f:
        return
    try:
        with _action_log_lock:
            f.parent.mkdir(parents=True, exist_ok=True)
            if f.exists() and f.stat().st_size >= ACTION_LOG_MAX:
                for i in range(ACTION_LOG_KEEP - 1, 0, -1):
                    src = f.with_name(f"{f.name}.{i - 1}" if i > 1 else f.name)
                    if src.exists():
                        os.replace(src, f.with_name(f"{f.name}.{i}"))
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "a") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as ex:
        log.warning("action log of %s not written: %s", p.get("name", ""), ex)


def read_action_log(p, action="", outcome="", task=""):
    """The last events of the project's action log (newest first, at most 200) that match the filters; the rotated files are read as needed."""
    f = action_log_path(p)

    def lines():
        if not f:
            return
        for x in [f] + [f.with_name(f"{f.name}.{i}") for i in range(1, ACTION_LOG_KEEP)]:
            try:
                yield from reversed(x.read_text(errors="replace").splitlines())
            except OSError:
                continue
    events = core_health.filter_events(lines(), action, outcome, task)
    return dict(events=events, limit=core_health.ACTION_LOG_LIMIT, path=str(f or ""))


def get_queue_poll(p, since=0, now=None):
    """PC-130: the Task Queue pane's light poll (the page asks every queue_refresh_s while the tab is visible). Always the local part
    (Workers, Dispatcher line, processing log) and the CI line (its own hourly cache); the task state is refreshed from GitHub when older
    than the pane's interval, which falls back to the hourly one while a rate-limit pause is on or the call budget is low (`slowed`).
    The state itself is sent only when it differs from the copy the page has (`since` = its fetchedAt); a failed refresh keeps the old copy."""
    now = time.time() if now is None else now
    every = api_settings.get_card_settings(p)["values"]["queue_refresh_s"]
    interval, slowed = core_queue_view.queue_interval(every, api_gh.gh_calls_left(now), api_gh._gh_pause["until"], now, api_gh.GH_REFRESH_S)
    k, S, err = api_context.pk(p, "state"), None, None
    t = api_cache._cache.get(k, (None,))[0]
    if t is not None and now - t > interval and not api_context.OFFLINE:
        api_cache._expire(k)  # due: fetch now, in this request (the page waits for it; one request in flight at a time)
    try:
        S = api_cache.cached(k, lambda: api_state.build_state(p), interval, persist=True, disk_fresh=api_gh.DISK_FRESH_S, queue=True)
    except Exception as ex:
        log.warning("queue poll of %s: no state (%s)", p.get("name", ""), str(ex)[:120])
        err = str(ex)[:200]
    at = S["source"].get("fetchedAt", 0) if S else 0
    out = dict(local=get_queue(p), ci=api_health.get_ci(p), every=every, interval=interval, slowed=slowed, fetchedAt=at or None,
               nextAt=max(at + interval, now) if at else None, staleNote=api_state.stale_note(p), error=err, all=get_queue_all(p))
    if S and at != since:
        out["state"] = api_state._served(p, api_state.live_state(p, S))
    return out


# ---------- API entry points (Result) ----------
def queue(ctx):
    """GET /api/queue: the local part of the Task Queue tab."""
    return ok(get_queue(ctx.p))


def queue_all(ctx):
    """GET /api/queueall: the one queue's tasks over every project it lists."""
    return ok(get_queue_all(ctx.p) or dict(projects=[], tasks=[]))


def queue_poll(ctx, since=0):
    """GET /api/queuepoll: the Task Queue pane's light poll."""
    return ok(get_queue_poll(ctx.p, since=since))


def action_log(ctx, action="", outcome="", task=""):
    """GET /api/actionlog: the project's action log, filtered."""
    return ok(read_action_log(ctx.p, action, outcome, task))
