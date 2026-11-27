"""API group `state`: the task state (derived from GitHub, with the Dispatcher's live facts laid over it), one task, the open questions and the timeline."""
import logging
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import subprocess
import time

from ckp.core import commits as core_commits, modules as core_modules, queue_view as core_queue_view, rules as core_rules, tasks_model as core_tasks_model
from ckp.api import cache as api_cache, code as api_code, context as api_context, fetch as api_fetch, gh as api_gh

from ckp.api.result import fail, ok
log = logging.getLogger("cockpit")


def gh_user():
    """The login gh acts as ('' when GitHub cannot say and no copy exists); kept on disk for a day, so a restart makes no call."""
    try:
        return api_cache.cached("user", lambda: api_gh.run_gh(["api", "user", "--jq", ".login"]).strip(), 3600, persist=True, disk_fresh=86400)
    except RuntimeError as ex:
        log.warning("gh user unknown: %s", str(ex)[:100])
        return ""


def build_state(p):
    t0 = time.time()
    with ThreadPoolExecutor(4) as ex:
        fi, fp, fd = ex.submit(api_fetch.fetch_issues, p), ex.submit(api_fetch.fetch_project, p), ex.submit(api_fetch.fetch_module_deadlines, p)
        (issues, events), project, deadlines = fi.result(), fp.result(), fd.result()
    S = state_from_raw(p, dict(issues=issues, events=events, project=project), deadlines)
    log.info("state: %d tasks, %d events, %d open proposals in %.1fs", len(S["tasks"]), len(events), len(S["proposals"]), time.time() - t0)
    return S


def state_from_raw(p, raw, deadlines, source=None):
    """The task state derived from the raw copy (no GitHub call). The raw copy is kept in the state (`raw`, never served to the page), so an
    action's known effect and a re-read of single issues can be laid on it and the state derived again (PC-135). `source`: keep this one."""
    issues, events, project = raw["issues"], raw["events"], {int(k): v for k, v in raw["project"].items()}  # JSON keys are strings on disk
    cfgm = api_context.load_modules(p)
    tasks, mods = core_tasks_model.derive(issues, events, project, p["manager"], (cfgm or {}).get("modules", []), grow=not cfgm)
    for t in tasks:
        t["modDl"] = core_modules.module_deadline(deadlines, t["module"])  # (date, module it is set on): the sub-module's, else the module's
    keys = {i["number"]: core_tasks_model.split_title(i["title"], i["number"])[0] for i in issues}
    nums = {t["number"]: t for t in tasks}
    proposals = [dict(p, n=i["number"], key=keys[i["number"]]) for i in issues for p in core_tasks_model.parse_proposals(i["comments"])]
    return dict(
        today=dt.date.today().isoformat(), tasks=tasks, questions=core_tasks_model.parse_questions(issues, p.get("name", "")), modules=mods, modDeadline=deadlines, proposals=proposals,
        events=core_tasks_model.task_events(events, keys, p["manager"])[:400], unauthorised=sum(len(t["flags"]) for t in nums.values()), raw=raw,
        source=source or dict(name=p.get("name", ""), repo=p["repo"], steering=api_context.CFG["steer"], simulate=api_context.CFG["simulate"] and api_context.CFG["steer"], project=p["project"],
                              manager=p["manager"], user=gh_user(), fetched=time.strftime("%H:%M:%S"), fetchedAt=time.time()))


def build_questions(fresh=False, now=None):
    """Open questions of every configured project (from each project's cached state) -> one oldest-first list plus a status per project.
    A project that cannot be read is reported with its error; a stale copy is served with its age (age_s)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    per, status = {}, []
    for name, p in (api_context.PROJECTS or {api_context.CFG.get("name", ""): api_context.CFG}).items():
        try:
            S = get_state(fresh=fresh, p=p)
            per[name] = S["questions"]
            status.append(dict(name=name, repo=p["repo"], age_s=max(0, round(time.time() - S["source"]["fetchedAt"])), error=None, note=stale_note(p)))
        except Exception as ex:
            log.warning("questions: project %s unreadable: %s", name, str(ex)[:120])
            status.append(dict(name=name, repo=p["repo"], age_s=None, error=str(ex)[:200]))
    rows = core_tasks_model.merge_questions(per, now)
    log.info("questions: %d open across %d projects", len(rows), len(per))
    return dict(questions=rows, counts=core_tasks_model.group_questions(rows), projects=status)


def get_state(fresh=False, p=None):
    """The task state. Only fresh=True (the owner's Refresh click on a pane) bypasses the hour; timers, page loads, restarts and actions never do."""
    p = p or api_context.proj()
    k = api_context.pk(p, "state")
    if fresh:
        api_cache._expire(k)
    if fresh:
        log.info("owner Refresh of %s: fetching from GitHub now (counted in the hourly total)", p.get("name", ""))
    return api_cache.cached(k, lambda: build_state(p), api_gh.GH_REFRESH_S, swr=not fresh, persist=True, disk_fresh=api_gh.DISK_FRESH_S, queue=True)


def get_task(p, n, run=api_gh.run_gh):
    """The current state of one task, read from GitHub now (one call, bypassing the cached state): the page asks for it when an action of
    its own got no answer, so that it can say what happened (PC-136)."""
    if not str(n).isdigit():
        raise ValueError(f"bad task number: {n!r}")
    return core_tasks_model.task_lookup(json.loads(run(["api", f"repos/{p['repo']}/issues/{n}"])))


# ---------- timeline / code ----------
def load_commits(p):
    if p.get("git"):
        txt = subprocess.run(["git", "-C", p["git"], "log", "--name-only", "--date=iso", "--format=@%H|%ad|%s"], capture_output=True, text=True, check=True).stdout
        return core_commits.parse_git_full(txt)
    since = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    raw = json.loads(api_gh.run_gh(["api", f"repos/{p['repo']}/commits?since={since}T00:00:00Z&per_page=100"]))
    return [dict(sha=c["sha"], date=dt.date.fromisoformat(c["commit"]["author"]["date"][:10]), subject=c["commit"]["message"].splitlines()[0], files=[]) for c in raw]


def build_timeline(S, p=None, fresh=False):
    p = p or api_context.proj()
    t0 = time.time()
    cfgm = api_context.load_modules(p)
    assign = cfgm["assign"] if cfgm else []
    mod_of = {t["key"]: t["module"] for t in S["tasks"]}
    url_of = {t["key"]: t["url"] for t in S["tasks"]}
    ck = api_context.pk(p, "commits")
    if fresh:
        api_cache._expire(ck)
    feed = core_commits.group_commits(api_cache.cached(ck, lambda: load_commits(p), api_gh.GH_REFRESH_S if not p.get("git") else 60, swr=not fresh, persist=True, disk_fresh=api_gh.DISK_FRESH_S, queue=not p.get("git")), assign, p["repo"]) + core_commits.issue_entries(S["tasks"])
    feed += [dict(d=e["t"], type="task event", mod=mod_of.get(e["key"], "unassigned"), text=f"{e['actor']} {e['key']}: {e['text']}", links=[(e["key"], url_of.get(e["key"], ""))])
             for e in S["events"] if e["kind"] != "closed"]
    if p["git"]:
        for m in S["modules"]:
            if not m.get("virtual"):
                feed += core_commits.static_entries(core_commits.module_dir(p["git"], m), m["id"], p["repo"])
    try:
        feed += core_rules.rule_feed(api_code.get_rules(p).get("changes", []), p["repo"])
    except Exception as ex:  # the timeline must not break because the rule view could not be built
        log.warning("timeline: rule changes unavailable (%s)", str(ex)[:80])
    if p["phases"]:
        pj = json.load(open(p["phases"]))
        feed += [dict(d=e["date"], type=e["type"], mod=pj.get("module", "unassigned"), text=e["text"], links=[l for l in (core_commits.ref_link(r, p["repo"]) for r in e.get("refs", []))]) for e in pj["events"]]
    seen, out = set(), []
    for e in sorted(feed, key=lambda e: e["d"], reverse=True):
        k = (e["d"], e["type"], e["mod"], e["text"])
        if k not in seen:
            seen.add(k)
            out.append(dict(e, links=[list(l) for l in e["links"]]))
    log.info("timeline: %d entries in %.2fs", len(out), time.time() - t0)
    return dict(entries=out[:400], total=len(out), activity=core_commits.activity_days(S["tasks"], S["events"], mod_of))


def get_timeline(p, fresh=False):
    """The timeline built from the state that is served right now (a refreshed state gets a rebuilt timeline at once, never one from older data)."""
    S = get_state(p=p)
    k, at = api_context.pk(p, "timeline"), state_version(S)
    build = lambda: dict(build_timeline(S, p, fresh), stateAt=at)
    if fresh:
        api_cache._expire(k)
        log.info("owner Refresh of the timeline of %s", p.get("name", ""))
    tl = api_cache.cached(k, build, 30, swr=not fresh, persist=True, disk_fresh=api_gh.DISK_FRESH_S)
    return tl if tl.get("stateAt") == at else api_cache.cached(k, build, -1, persist=True)


def state_version(S):
    """When the state last changed: its full load, an action's applied effect or a re-read of single issues (PC-135)."""
    src = S.get("source", {})
    return max(src.get("fetchedAt") or 0, src.get("patchedAt") or 0, src.get("rereadAt") or 0)


def stale_note(p, now=None):
    """None, or why the state served for p is older than it should be: dict(age_s, error, until, rate_limited) of the failed refresh (until = when GitHub is tried again)."""
    f, now = api_cache._failures.get(api_context.pk(p, "state")), now or time.time()
    v = api_cache._cache.get(api_context.pk(p, "state"), (0, None))[1]
    if not f or not v:
        return None
    kind = core_queue_view.offline_kind(f["error"], f["rate_limited"])
    return dict(age_s=max(0, round(now - v["source"]["fetchedAt"])), error=f["error"], until=f["until"], rate_limited=f["rate_limited"],
                kind=kind, reason=core_queue_view.offline_reason(kind, f["until"], api_context._clock))


def steer_block(p, now=None):
    """None, or why the steering actions on GitHub are off now (owner: the write side refuses while GitHub cannot be read and never queues)."""
    now = time.time() if now is None else now
    if api_context.OFFLINE:
        return "offline copy from a backup"
    if now < api_gh._gh_pause["until"]:
        return core_queue_view.offline_reason("rate_limit", api_gh._gh_pause["until"], api_context._clock)
    n = stale_note(p, now)
    return n["reason"] if n else None


_live_try = {}     # state key -> epoch of the last refresh asked for by a newer processing-log event (PC-119)


def live_state(p, S, fresh=False):
    """PC-119: the cached state with the Dispatcher's newer facts laid over it (logic.overlay_live), `pick` set by the Dispatcher's own rule,
    and, when the processing log has a state change newer than the cache, one background refresh at most every LIVE_REFRESH_S seconds."""
    if "tasks" not in S:
        return S
    f = api_context.queue_files(p)
    shorts = {s.lower() for s in (p.get("short"), p.get("name"), p["repo"].split("/")[-1]) if s}
    events, k, now = core_queue_view.live_events(api_context._read_text(f["plog"]), shorts), api_context.pk(p, "state"), time.time()
    at = S.get("source", {}).get("fetchedAt", 0)
    if not fresh and core_queue_view.live_refresh_due(events, at, _live_try.get(k, 0), now, api_gh.gh_room(now)):
        _live_try[k] = now
        log.info("%s: the processing log is newer than the task list (data from %s): refreshing it", p.get("name", ""), api_context._clock(at))
        api_cache._expire(k)
        S = get_state(p=p)   # the old copy at once, the new one fetched in the background
        at = S.get("source", {}).get("fetchedAt", 0)
    state_text = api_context._read_text(f["state"])
    held = {n for w in core_queue_view.queue_local(state_text, None)["workers"] if (n := core_queue_view.task_number(w["task"], shorts)) is not None}
    tasks = core_queue_view.overlay_live(S["tasks"], at, events, held, state_text is not None)
    pick = core_tasks_model.pickable(tasks, dt.datetime.now(dt.timezone.utc), held)
    return dict(S, tasks=[dict(t, pick=t["key"] in pick) for t in tasks], openCounts=core_queue_view.open_counts(tasks, S.get("modules", [])))


def get_state_served(p, fresh=False):
    """The state for the page: `stale` is true while a newer copy is being fetched in the background, so the page asks again shortly;
    `staleNote` is set when the last refresh failed and an older copy is shown (its age, the reason and when GitHub is tried again)."""
    return _served(p, live_state(p, get_state(fresh=fresh, p=p), fresh))


def _served(p, S):
    """A cached state as the page gets it (modules in size order, steering off in an offline copy, its refresh flags)."""
    if api_context.OFFLINE:  # the copy was taken from a steering instance: steering is off here whatever the cached state says
        S = dict(S, source=dict(S.get("source", {}), steering=False, simulate=False))
    if "modules" in S:
        S = dict(S, modules=api_code.ordered(p, S["modules"]))
    S = {k: v for k, v in S.items() if k != "raw"}   # the raw copy stays on the server (PC-135)
    at = S.get("source", {}).get("fetchedAt")
    new_work = core_queue_view.may_start_new(time.time() - at if at else None)
    if not new_work and "tasks" in S:  # owner: no new task is started from state older than an hour; work in progress may continue
        S = dict(S, tasks=[dict(t, pick=False) for t in S["tasks"]])
    return dict(S, stale=api_cache.refreshing(api_context.pk(p, "state")) or api_context.pk(p, "state") in api_cache._reread or api_context.pk(p, "state") in api_cache._deferred, staleNote=stale_note(p), refreshAfter=(at or 0) + api_gh.GH_REFRESH_S,
                steerBlock=steer_block(p) if api_context.CFG["steer"] else None, newWork=new_work, warming=api_context.WARM["on"])


# ---------- API entry points (Result) ----------
def state(ctx, fresh=False):
    """GET /api/state: the task state for the page."""
    return ok(get_state_served(ctx.p, fresh=fresh))


def task(ctx, n=""):
    """GET /api/task: one task read from GitHub now."""
    try:
        return ok(get_task(ctx.p, n, **({"run": ctx.run} if ctx.run else {})))
    except ValueError as ex:
        return fail(ex, 400)


def questions(ctx, fresh=False):
    """GET /api/questions: the open questions of every project."""
    return ok(build_questions(fresh=fresh))


def timeline(ctx, fresh=False):
    """GET /api/timeline."""
    return ok(get_timeline(ctx.p, fresh=fresh))
