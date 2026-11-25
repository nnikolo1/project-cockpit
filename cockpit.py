#!/usr/bin/env python3
"""Project cockpit v1: a JSON API + one web page over GitHub (stdlib + the `gh` CLI; no token stored, acts as the gh login).
Every action is exactly one GitHub operation (labels, sub-issues, dependencies, a repo variable); the only local copy is the cache of the slow answers (--cache-dir)."""
import argparse
import json
import logging
import importlib.util
import os
import pathlib
import random
import re
import secrets
import shutil
import tarfile
import tempfile
import collections
import subprocess
import sys
import threading
import time
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import logic as L
from ckp.store import io as store_io, layout as store_layout
from ckp.store.cache import DiskCache
from dispatcher.core import DEFAULTS as SETTING_DEFAULTS, load_settings
from dispatcher.runtime import Config as RuntimeConfig

log = logging.getLogger("cockpit")
HERE = pathlib.Path(__file__).parent
TYPES = ["feature", "bug", "decision", "research", "doc", "devops", "test"]
# Global switches (steer, simulate) live in CFG. A project is a dict of PROJECT_KEYS plus its name; PROJECTS maps name -> project.
# Without any registered project (tests, embedding) CFG itself doubles as the one project.
CFG = dict(repo=None, manager=None, steer=False, simulate=False, project=None, git=None, modules=None, phases=None, code=None, code_collect=None, compliance=None)
PROJECT_KEYS = ("repo", "manager", "project", "git", "modules", "phases", "code", "code_collect", "compliance")
PROJECTS, DEFAULT = {}, None


class UnknownProject(KeyError):
    pass


def proj(name=None):
    """The settings of project `name` (default: the configured default)."""
    if not PROJECTS:
        return CFG
    n = name or DEFAULT
    if n not in PROJECTS:
        raise UnknownProject(f"unknown project {n!r}; configured: {', '.join(PROJECTS)}")
    return PROJECTS[n]


def pk(p, what):
    """Cache key of `what` for project p: projects never share a cached value."""
    return f"{p.get('name', '')}:{what}"


def project_from_flags(a):
    """The implicit project of the single-project flags, named after the repo."""
    return dict(name=a["repo"].split("/")[-1], repo=a["repo"], manager=a.get("manager") or a["repo"].split("/")[0],
                **{k: a.get(k) for k in PROJECT_KEYS[2:-2]}, code_collect=L.collect_command(a.get("code_collect")),
                compliance=L.collect_command(a.get("compliance"), "compliance"))


def projects_from_file(data):
    """Parsed --projects JSON -> (list of project dicts, default name). Raises ValueError with a clear message."""
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list) or not data["projects"]:
        raise ValueError('--projects: expected {"default": NAME, "projects": [{"name", "repo", ...}]}')
    out = []
    for e in data["projects"]:
        name, repo = (e.get("name") or "").strip() if isinstance(e, dict) else "", e.get("repo") if isinstance(e, dict) else None
        if not name or not isinstance(repo, str) or repo.count("/") != 1:
            raise ValueError(f"--projects: every project needs a name and a repo OWNER/REPO (got {e!r})")
        if any(name == p["name"] for p in out):
            raise ValueError(f"--projects: duplicate project name {name!r}")
        p = dict(name=name, repo=repo, manager=e.get("manager") or repo.split("/")[0], project=e.get("project"))
        p.update({k: os.path.expanduser(e[k]) if e.get(k) else None for k in ("git", "modules", "phases", "code")})
        if e.get("meta"):
            p["meta"] = os.path.expanduser(e["meta"])  # optional: absent means the default folder, see meta_dir()
        if e.get("stats_cache"):
            p["stats_cache"] = os.path.expanduser(e["stats_cache"])  # optional: the tool's statistics cache, for "How these numbers fit"
        if e.get("start_month") is not None:  # optional: the month the project started; Token Usage shows nothing of it before (PC-113)
            if not (isinstance(e["start_month"], str) and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", e["start_month"])):
                raise ValueError(f"--projects: project {name!r}: start_month must be YYYY-MM (got {e['start_month']!r})")
            p["start_month"] = e["start_month"]
        if e.get("transcript_sources") is not None:  # optional: the project's transcript folders (source labels); default: the folder rule
            if not (isinstance(e["transcript_sources"], list) and all(isinstance(x, str) and x for x in e["transcript_sources"])):
                raise ValueError(f"--projects: project {name!r}: transcript_sources must be a list of folder names")
            p["transcript_sources"] = list(e["transcript_sources"])
        try:
            p["code_collect"] = L.collect_command(e.get("code_collect"))  # the snapshot's collector command, run by the daily refresh
            p["compliance"] = L.collect_command(e.get("compliance"), "compliance")  # the compliance check, the same command as the terminal and the daily job
        except ValueError as ex:
            raise ValueError(f"--projects: project {name!r}: {ex}") from None
        out.append(p)
    default = data.get("default") or out[0]["name"]
    if default not in [p["name"] for p in out]:
        raise ValueError(f"--projects: default {default!r} is not one of the projects")
    return out, default


def register(projects, default):
    PROJECTS.clear()
    PROJECTS.update({p["name"]: p for p in projects})
    global DEFAULT
    DEFAULT = default
SVGS = {"erd": "erd.svg", "dataflow": "dataflow.svg", "deps": "deps.svg", "js": "js.svg"}
DATA_DIR = pathlib.Path("~/.local/share/project-cockpit").expanduser()  # where deployment.md keeps each project's token folder


def meta_dir(p):
    """Folder of the project's card metadata files: its `meta` setting, else <data dir>/<project name>/cardmeta."""
    return pathlib.Path(p.get("meta") or DATA_DIR / (p.get("name") or p["repo"].split("/")[-1]) / "cardmeta")


# ---------- gh ----------
GH_CALLS_PER_HOUR = 600       # cap of gh calls this Cockpit makes per rolling hour (a state load is about 12 calls; the 5,000-point budget is shared with every other tool of the login, so the Cockpit keeps well below it)
RATE_LIMIT_PAUSE_S = 900      # after a rate-limit error with no reset time in it: no gh call for this long (the next hourly reset is at most 3600 s away; 15 min bounds the damage of a wrong guess)
_gh_queue = threading.RLock()  # the hourly queue: GitHub refreshes run one at a time (projects one after another, never in parallel bursts); re-entrant for a refresh that needs another queued key
GH_SUMMARY_EVERY = 25         # one summary line "gh calls in the last hour: n of cap" per this many calls
GH_REFRESH_S = 3600           # lazy refresh (owner F-699): GitHub data is fetched at most once per this many seconds per project; within it the cache is served with no call, an older cache is served at once and one refresh is queued
DISK_FRESH_S = GH_REFRESH_S   # a disk copy younger than this is used as is after a restart (nothing is fetched at startup)


class GitHubUnavailable(RuntimeError):
    """No gh call was made: the budget is exhausted or paused. `until` is the epoch time of the next call allowed."""
    def __init__(self, msg, until):
        super().__init__(msg)
        self.until = until


_gh_calls, _gh_lock, _gh_pause = collections.deque(), threading.Lock(), dict(until=0.0, rate_limited=False)
OFFLINE = {}  # set by --offline: dict(root, created, gh_attempts). An offline copy makes no GitHub call and serves every cached copy as is.


def _clock(t):
    return time.strftime("%H:%M", time.localtime(t))


def _gh_gate(now=None):
    """Count one gh call, or raise GitHubUnavailable when the Cockpit is paused after a rate-limit error or over its hourly cap."""
    now = time.time() if now is None else now
    with _gh_lock:
        if OFFLINE:
            OFFLINE["gh_attempts"] = OFFLINE.get("gh_attempts", 0) + 1
            log.warning("offline copy: a GitHub call was refused (the backup lacks the data that needed it)")
            raise GitHubUnavailable("offline copy: the Cockpit makes no GitHub call; this data is not in the backup", 0)
        if now < _gh_pause["until"]:
            raise GitHubUnavailable(f"GitHub rate limit reached; the Cockpit makes no GitHub call until {_clock(_gh_pause['until'])}", _gh_pause["until"])
        while _gh_calls and _gh_calls[0] <= now - 3600:
            _gh_calls.popleft()
        if len(_gh_calls) >= GH_CALLS_PER_HOUR:
            until = _gh_calls[0] + 3600
            log.warning("gh call cap reached: %d calls in the last hour, next call allowed at %s", len(_gh_calls), _clock(until))
            raise GitHubUnavailable(f"the Cockpit's own limit of {GH_CALLS_PER_HOUR} GitHub calls per hour is reached; next call at {_clock(until)}", until)
        _gh_calls.append(now)
        if len(_gh_calls) % GH_SUMMARY_EVERY == 0:
            log.info("gh calls in the last hour: %d of %d", len(_gh_calls), GH_CALLS_PER_HOUR)


def _gh_failed(msg, now=None):
    """After a failed call: a rate-limit error pauses every further call until the reset time (the message's, else RATE_LIMIT_PAUSE_S from now)."""
    now = time.time() if now is None else now
    if L.is_rate_limited(msg):
        with _gh_lock:
            _gh_pause["until"] = L.rate_limit_reset(msg, now) or now + RATE_LIMIT_PAUSE_S
            log.warning("GitHub rate limit hit (%s): no gh call until %s", msg[:100], _clock(_gh_pause["until"]))


def run_gh(args):
    """Run `gh <args>` and return stdout; raises RuntimeError with gh's message on failure, GitHubUnavailable (no call made) while paused or over the hourly cap."""
    _gh_gate()
    log.info("gh %s", " ".join(a if len(a) < 60 else a[:57] + "..." for a in args))
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError("gh timed out")
    if r.returncode:
        msg = r.stderr.strip() or f"gh exited {r.returncode}"
        _gh_failed(msg)
        raise RuntimeError(msg)
    return r.stdout


PAGE = 20  # issues per GraphQL page. GitHub silently thins the nested timelineItems of a big page (ADR 0019): 100 issues -> 5 events each, 50 -> 7, 30 -> complete
TL_BATCH = 10  # issues per on-demand timeline query
TIMELINE = """timelineItems(last:100,itemTypes:[LABELED_EVENT,UNLABELED_EVENT,ASSIGNED_EVENT,CLOSED_EVENT]){nodes{__typename
  ... on LabeledEvent{createdAt actor{login} label{name}} ... on UnlabeledEvent{createdAt actor{login} label{name}}
  ... on AssignedEvent{createdAt actor{login} assignee{... on User{login}}} ... on ClosedEvent{createdAt actor{login}}}}"""
NODE = """number databaseId title url body state stateReason createdAt updatedAt lastEditedAt closedAt author{login}
 labels(first:50){nodes{name}} assignees(first:5){nodes{login}} parent{number} blockedBy(first:50){nodes{number}}
 comments(last:10){nodes{body createdAt author{login}}}
 """ + TIMELINE  # the fields of one issue: the full load and the re-read of single issues (PC-135) read the same
GQL = """query($o:String!,$r:String!,$after:String,$n:Int!){repository(owner:$o,name:$r){issues(first:$n,after:$after,orderBy:{field:CREATED_AT,direction:ASC}){
 pageInfo{hasNextPage endCursor} nodes{""" + NODE + "}}}}"


def fetch_issues(p):
    """All issues of the repo in PAGE-issue GraphQL pages -> (raw issues, events). The events are then checked and, where a protected
    label has no determinable event, completed per issue (repair_events)."""
    owner, name = p["repo"].split("/")
    issues, events, after = [], [], None
    while True:
        args = ["api", "graphql", "-f", f"query={GQL}", "-f", f"o={owner}", "-f", f"r={name}", "-F", f"n={PAGE}"] + (["-f", f"after={after}"] if after else [])
        page = json.loads(run_gh(args))["data"]["repository"]["issues"]
        for n in page["nodes"]:
            issues.append(L.norm_issue(n))
            events += L.norm_events(n)
        if not page["pageInfo"]["hasNextPage"]:
            return issues, repair_events(p, issues, events)
        after = page["pageInfo"]["endCursor"]


def fetch_issue_nodes(p, numbers):
    """PC-135: a fresh reading of the given issues only, TL_BATCH per GraphQL call (one call for an action's one or two issues)
    -> (raw issues, events). An issue GitHub no longer returns is left out."""
    owner, name = p["repo"].split("/")
    issues, events = [], []
    for k in range(0, len(numbers), TL_BATCH):
        chunk = numbers[k:k + TL_BATCH]
        body = " ".join(f"i{n}:issue(number:{n}){{{NODE}}}" for n in chunk)
        data = json.loads(run_gh(["api", "graphql", "-f", f"query=query($o:String!,$r:String!){{repository(owner:$o,name:$r){{{body}}}}}", "-f", f"o={owner}", "-f", f"r={name}"]))["data"]["repository"]
        for n in chunk:
            if data.get(f"i{n}"):
                issues.append(L.norm_issue(data[f"i{n}"]))
                events += L.norm_events(data[f"i{n}"])
    return issues, events


def fetch_timelines(p, numbers):
    """Label/assign/close events of the given issues, TL_BATCH issues per query (small enough that GitHub does not thin the timeline) -> {number: [events]}."""
    owner, name = p["repo"].split("/")
    out = {}
    for k in range(0, len(numbers), TL_BATCH):
        chunk = numbers[k:k + TL_BATCH]
        body = " ".join(f"i{n}:issue(number:{n}){{number stateReason {TIMELINE}}}" for n in chunk)
        data = json.loads(run_gh(["api", "graphql", "-f", f"query=query($o:String!,$r:String!){{repository(owner:$o,name:$r){{{body}}}}}", "-f", f"o={owner}", "-f", f"r={name}"]))["data"]["repository"]
        for n in chunk:
            if data.get(f"i{n}"):
                out[n] = L.norm_events(data[f"i{n}"])
    return out


_TL = {}  # (repo, issue, updatedAt, protected labels) -> events fetched on demand; a changed issue has a new updatedAt, so it is fetched again


def repair_events(p, issues, events, fetch=None, cache=None):
    """GitHub silently returns a thinned timeline for large GraphQL pages (ADR 0019), so a protected label may have no event in `events`
    although its actor is on record. For each issue whose protected label has no `labeled` event as its latest one, fetch that issue's own
    timeline (bounded: only those issues, TL_BATCH per call, cached per issue and updatedAt). A label that STILL has no event stays without actor,
    so policy() keeps flagging it. A failed fetch leaves the events as they are (conservative: flagged), never raises."""
    cache = _TL if cache is None else cache
    need = L.issues_needing_timeline(issues, events)
    if not need:
        return events
    by = {i["number"]: i for i in issues}
    key = lambda n: (p["repo"], n, by[n].get("updated_at"), tuple(sorted(l for l in by[n]["labels"] if L.is_protected(l))))
    todo = [n for n in need if key(n) not in cache]
    if todo:
        try:
            got = (fetch or (lambda nums: fetch_timelines(p, nums)))(todo)
        except Exception as ex:
            log.warning("timeline repair of %d issue(s) failed: %s: %s; their protected labels stay unverified", len(todo), type(ex).__name__, str(ex)[:150])
            got = {}
        if len(cache) > 5000:
            cache.clear()
        for n, evs in got.items():
            cache[key(n)] = evs
        log.info("timeline repair: %d of %d issue(s) with a protected label lacking an event fetched (%d got events)", len(todo), len(need), len(got))
    fixed = {n: cache[key(n)] for n in need if key(n) in cache}
    return [e for e in events if e["number"] not in fixed] + [e for evs in fixed.values() for e in evs]


def fetch_project(p):
    if not p["project"]:
        return {}
    owner = p["repo"].split("/")[0]
    d = json.loads(run_gh(["project", "item-list", str(p["project"]), "--owner", owner, "--format", "json", "--limit", "1000"]))
    return L.parse_project(d["items"], p["repo"])


def fetch_module_deadlines(p):
    try:
        return L.parse_module_deadlines(run_gh(["api", f"repos/{p['repo']}/actions/variables/MODULE_DEADLINES", "--jq", ".value"]))
    except RuntimeError as ex:
        log.info("no MODULE_DEADLINES variable (%s)", str(ex)[:80])
        return {}


_cache, _lock = {}, threading.RLock()  # _lock only guards the bookkeeping dicts; every key computes under its own lock, so a slow key never blocks the others
_locks, _refreshing = {}, set()
CACHE_DIR = None  # folder of the on-disk copies of the slow answers (set by main); None = memory only. Lets a restarted service answer at once.


CACHE_SCHEMA = 1  # the shape of what is cached: bump it when a cached payload changes its shape; a new folder (cache/s<N>) is then used and no old shape is read


def _dc():
    return DiskCache(CACHE_DIR)


def _disk_path(key):
    return _dc().path(key)


def _disk_load(key):
    return _dc().load(key)


def _jsonable(o):
    """Dates (the commits' days) are kept on disk as ISO strings; logic.group_commits reads both forms."""
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _disk_save(key, v):
    if not CACHE_DIR or OFFLINE:  # the restored backup is never written to
        return
    try:
        _dc().save(key, v, default=_jsonable)
    except (OSError, TypeError, ValueError) as ex:
        log.warning("cache %s not written to disk: %s", key, ex)


def drop_cache(prefix):
    """Forget the memory and disk copies of every key starting with `prefix` (after an action: nothing older may be served)."""
    with _lock:
        for k in [k for k in _cache if k.startswith(prefix)]:
            del _cache[k]
    _dc().drop(prefix)


STAGGER_WINDOW_S = 900   # the GitHub refreshes of all projects and resources are spread over this window (owner: staggered offsets, no burst)
STAGGER_JITTER_S = 120   # plus a random jitter per key, so that slots do not line up again
GH_RESOURCES = ("state", "ci", "commits", "rulesprs")  # the queued GitHub resources of a project (cached(..., queue=True))
_started, _slots = time.time(), {}


def _slot(key):
    """(offset, jitter) of `key` in the staggered plan of every configured project's resources; a key outside the plan gets a slot of its own."""
    with _lock:
        if key not in _slots:
            keys = {pk(p, r) for p in (PROJECTS.values() or [CFG]) for r in GH_RESOURCES} | set(_slots) | {key}
            _slots.clear()
            _slots.update(L.stagger_plan(keys, STAGGER_WINDOW_S, STAGGER_JITTER_S, random.Random()))
        return _slots[key]


_failures = {}  # key -> dict(at, error, until, rate_limited) of the last failed refresh that was answered with an older copy; removed by the next success


def cached(key, fn, ttl, swr=False, persist=False, disk_fresh=0, queue=False):
    """fn()'s value for `key`, recomputed when older than ttl seconds. swr (stale-while-revalidate): an expired copy is served at once and
    refreshed in a background thread. persist: the value (JSON) is also kept on disk; a copy from an earlier run counts as expired, unless it is
    younger than disk_fresh seconds (then a restart costs no refresh). queue: the refresh costs GitHub requests and runs inside the one-at-a-time queue."""
    t, v = _cache.get(key, (0, None))
    if v is None and persist:
        v = _disk_load(key)
        if v is not None:
            t = _disk_time(key, disk_fresh)
            _cache.setdefault(key, (t, v))
    if v is not None and (OFFLINE or time.time() - t <= ttl):
        return v
    if v is not None and swr and queue and time.time() < L.refresh_due(t, ttl, _slot(key), _started):
        return v  # staggered: this key's refresh slot has not come yet
    if v is not None and swr:
        with _lock:
            if key in _refreshing:
                return v
            _refreshing.add(key)

        def go():
            try:
                _refresh(key, fn, ttl, persist, queue)
            except Exception as ex:
                log.warning("%s: background refresh failed: %s", key, str(ex)[:150])
            finally:
                _refreshing.discard(key)
        threading.Thread(target=go, daemon=True).start()
        return v
    return _refresh(key, fn, ttl, persist, queue)


def _disk_time(key, disk_fresh):
    """The time a disk copy counts as fetched: its file time when younger than disk_fresh, else 0 (expired)."""
    try:
        m = _disk_path(key).stat().st_mtime
    except (OSError, AttributeError):
        return 0
    return m if disk_fresh and time.time() - m <= disk_fresh else 0


def _refresh(key, fn, ttl, persist, queue=False):
    with _lock:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        t, v = _cache.get(key, (0, None))
        if v is not None and time.time() - t <= ttl:
            return v  # another thread refreshed it while this one waited
        try:
            if queue:
                with _gh_queue:
                    v = fn()
            else:
                v = fn()
        except Exception as ex:
            if v is None:
                raise
            until = getattr(ex, "until", None)
            _failures[key] = dict(at=time.time(), error=str(ex)[:300], until=until, rate_limited=bool(until) or L.is_rate_limited(str(ex)))
            log.warning("%s: refresh failed, serving the last good copy (%.0f s old): %s", key, time.time() - t, str(ex)[:120])
            _cache[key] = ((until or time.time() + 15) - ttl, v)  # try again when GitHub is allowed again, else in about 15 s, not on every request
            return v
        _failures.pop(key, None)
        _cache[key] = (time.time(), v)
        if persist:
            _disk_save(key, v)
        return v


def refreshing(prefix):
    """True while a background refresh of a key starting with `prefix` runs: what is served may be older than its ttl."""
    return any(k.startswith(prefix) for k in list(_refreshing))


def gh_user():
    """The login gh acts as ('' when GitHub cannot say and no copy exists); kept on disk for a day, so a restart makes no call."""
    try:
        return cached("user", lambda: run_gh(["api", "user", "--jq", ".login"]).strip(), 3600, persist=True, disk_fresh=86400)
    except RuntimeError as ex:
        log.warning("gh user unknown: %s", str(ex)[:100])
        return ""


# ---------- state ----------
def load_modules(p):
    return json.load(open(p["modules"])) if p.get("modules") else None


def build_state(p):
    t0 = time.time()
    with ThreadPoolExecutor(4) as ex:
        fi, fp, fd = ex.submit(fetch_issues, p), ex.submit(fetch_project, p), ex.submit(fetch_module_deadlines, p)
        (issues, events), project, deadlines = fi.result(), fp.result(), fd.result()
    S = state_from_raw(p, dict(issues=issues, events=events, project=project), deadlines)
    log.info("state: %d tasks, %d events, %d open proposals in %.1fs", len(S["tasks"]), len(events), len(S["proposals"]), time.time() - t0)
    return S


def state_from_raw(p, raw, deadlines, source=None):
    """The task state derived from the raw copy (no GitHub call). The raw copy is kept in the state (`raw`, never served to the page), so an
    action's known effect and a re-read of single issues can be laid on it and the state derived again (PC-135). `source`: keep this one."""
    issues, events, project = raw["issues"], raw["events"], {int(k): v for k, v in raw["project"].items()}  # JSON keys are strings on disk
    cfgm = load_modules(p)
    tasks, mods = L.derive(issues, events, project, p["manager"], (cfgm or {}).get("modules", []), grow=not cfgm)
    for t in tasks:
        t["modDl"] = L.module_deadline(deadlines, t["module"])  # (date, module it is set on): the sub-module's, else the module's
    keys = {i["number"]: L.split_title(i["title"], i["number"])[0] for i in issues}
    nums = {t["number"]: t for t in tasks}
    proposals = [dict(p, n=i["number"], key=keys[i["number"]]) for i in issues for p in L.parse_proposals(i["comments"])]
    return dict(
        today=dt.date.today().isoformat(), tasks=tasks, questions=L.parse_questions(issues, p.get("name", "")), modules=mods, modDeadline=deadlines, proposals=proposals,
        events=L.task_events(events, keys, p["manager"])[:400], unauthorised=sum(len(t["flags"]) for t in nums.values()), raw=raw,
        source=source or dict(name=p.get("name", ""), repo=p["repo"], steering=CFG["steer"], simulate=CFG["simulate"] and CFG["steer"], project=p["project"],
                              manager=p["manager"], user=gh_user(), fetched=time.strftime("%H:%M:%S"), fetchedAt=time.time()))


def build_questions(fresh=False, now=None):
    """Open questions of every configured project (from each project's cached state) -> one oldest-first list plus a status per project.
    A project that cannot be read is reported with its error; a stale copy is served with its age (age_s)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    per, status = {}, []
    for name, p in (PROJECTS or {CFG.get("name", ""): CFG}).items():
        try:
            S = get_state(fresh=fresh, p=p)
            per[name] = S["questions"]
            status.append(dict(name=name, repo=p["repo"], age_s=max(0, round(time.time() - S["source"]["fetchedAt"])), error=None, note=stale_note(p)))
        except Exception as ex:
            log.warning("questions: project %s unreadable: %s", name, str(ex)[:120])
            status.append(dict(name=name, repo=p["repo"], age_s=None, error=str(ex)[:200]))
    rows = L.merge_questions(per, now)
    log.info("questions: %d open across %d projects", len(rows), len(per))
    return dict(questions=rows, counts=L.group_questions(rows), projects=status)


def _expire(key):
    """Make the copy of `key` (memory, else disk) count as expired but keep it for a failed refresh."""
    v = _cache[key][1] if key in _cache else _disk_load(key)
    if v is not None:
        _cache[key] = (0, v)


def get_state(fresh=False, p=None):
    """The task state. Only fresh=True (the owner's Refresh click on a pane) bypasses the hour; timers, page loads, restarts and actions never do."""
    p = p or proj()
    k = pk(p, "state")
    if fresh:
        _expire(k)
    if fresh:
        log.info("owner Refresh of %s: fetching from GitHub now (counted in the hourly total)", p.get("name", ""))
    return cached(k, lambda: build_state(p), GH_REFRESH_S, swr=not fresh, persist=True, disk_fresh=DISK_FRESH_S, queue=True)


# ---------- actions: plan (pure) -> execute ----------
def _date(v, what="date"):
    if not L.is_date(v):
        raise ValueError(f"bad {what}: {v!r}")
    return v


def _prio(v):
    p = f"P{str(v).lstrip('Pp')}"
    if p not in L.PRIO:
        raise ValueError(f"bad priority: {v!r}")
    return p


def _task(S, n):
    t = next((t for t in S["tasks"] if t["number"] == _int(n)), None)
    if not t:
        raise ValueError(f"no such task: {n!r}")
    return t


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ValueError(f"bad number: {v!r}")


def _edit(repo, t, add, remove):
    """gh commands that leave `add` effective and `remove` gone. A label that sits on the issue but was set by someone
    else (unauthorised) is removed first, so that re-adding it records the Manager as the actor."""
    eff = set(t["labels"]) - set(t["bad"])
    ops = []
    redo = [l for l in add if l in t["bad"]]
    if redo:
        ops.append(["issue", "edit", str(t["number"]), "-R", repo] + [x for l in redo for x in ("--remove-label", l)])
    add = [l for l in add if l not in eff]
    remove = [l for l in remove if l in t["labels"]]
    if add or remove:
        ops.append(["issue", "edit", str(t["number"]), "-R", repo] + [x for l in add for x in ("--add-label", l)] + [x for l in remove for x in ("--remove-label", l)])
    return ops


def _prio_due(t, prio, due, set_due):
    """(labels to add, labels to remove) giving task t priority `prio` (None = leave) and deadline `due` ('' = clear) when set_due."""
    eff = set(t["labels"]) - set(t["bad"])
    add, remove = [], []
    if prio:
        add, remove = L.swap_priority(eff, prio)
        remove += [p for p in L.PRIO if p in t["bad"] and p != prio]
    if set_due:
        dues = [l for l in t["labels"] if L.DUE.match(l)]
        if due:
            add.append(f"due:{due}")
            remove += [l for l in dues if l != f"due:{due}"]
        else:
            remove += dues
    return add, remove


EDITABLE = ("embryo", "released")   # the pre-WIP states: the only ones whose text may be edited (owner, 2026-11-25)
STALE_NOTE = "<!-- analysis: stale -->\nThe task text was edited in the Cockpit; its authority analysis is stale until the task is analysed again."


def _edit_text(form, t, repo):
    """Title and body of the issue from the editor. The provenance block and the filed-by marker are taken from the issue, never from the
    form (read-only there), and re-attached; labels are untouched. A saved edit records the authority analysis as stale (a comment)."""
    if t["stage"] not in EDITABLE or t.get("closed"):
        raise ValueError(f"{t['key']} is {'closed' if t.get('closed') else t['stage']}; only embryo and released tasks can be edited")
    title, text = (form.get("title") or "").strip(), L.split_text(form.get("body"))[0]
    if not title:
        raise ValueError("a title is required")
    if title == t["title"] and text == t["text"]:
        return [], f"{t['key']}: nothing changed"
    n = str(t["number"])
    full = title if t["key"].startswith("#") else f"[{t['key']}] {title}"
    ops = [["api", "-X", "PATCH", f"repos/{repo}/issues/{n}", "-f", f"title={full}", "-f", f"body={L.join_text(text, t['kept'])}"],
           ["issue", "comment", n, "-R", repo, "--body", STALE_NOTE]]
    return ops, f"{t['key']} text saved; its authority analysis is now stale"


def plan_action(form, S, repo, simulate=False):
    ops, msg = _plan(form, S, repo, simulate)
    # new due:<date> labels must exist before they can be added
    ops = [["label", "create", x, "-R", repo] for x in sorted({x for op in ops for x in op if x.startswith("due:")})] + ops
    return ops, msg


def _plan(form, S, repo, simulate):
    """-> (ops, message). Each op is the argument list of one `gh` call; '{id}' in an op is the database id returned by the
    previous call. Raises ValueError for anything the framework rules forbid (checked here as well as in the page)."""
    a = form.get("action")
    openstage = ("embryo", "released", "wip")
    if a == "new":
        title = (form.get("title") or "").strip()
        mod, typ, cls = form.get("module") or "", form.get("type") or "task", form.get("cls") or "nice"
        if not title or typ not in TYPES or cls not in ("must", "nice"):
            raise ValueError("a title, a known type and must/nice are required")
        if mod and not any(m["id"] == mod and not m.get("virtual") for m in S["modules"]):
            raise ValueError(f"unknown module {mod!r}")
        labels = [f"type:{typ}", cls] + ([f"module:{mod}"] if mod else [])
        ops = [["label", "create", l, "-R", repo] for l in labels]  # tolerated if they exist
        ops.append(["api", "-X", "POST", f"repos/{repo}/issues", "-f", f"title={title}", "-f", f"body={form.get('desc') or ''}"] + [x for l in labels for x in ("-f", f"labels[]={l}")])
        if form.get("parent"):
            ops.append(["api", "-X", "POST", f"repos/{repo}/issues/{_task(S, form['parent'])['number']}/sub_issues", "-F", "sub_issue_id={id}"])
        return ops, f"task filed as an embryo: {title}"
    if a == "moddl":
        m, d = form.get("module"), form.get("d") or ""
        if not any(x["id"] == m and not x.get("virtual") for x in S["modules"]):
            raise ValueError(f"unknown module {m!r}")
        new = dict(S["modDeadline"])
        new.pop(m, None)
        if d:
            new[m] = _date(d)
        return [["variable", "set", "MODULE_DEADLINES", "--body", json.dumps(new, sort_keys=True), "-R", repo]], f"module deadline of {m} {'set to ' + d if d else 'cleared'}"
    t = _task(S, form.get("n"))
    n = str(t["number"])
    if a == "edit":
        return _edit_text(form, t, repo)
    if a == "release":
        if t["stage"] != "embryo":
            raise ValueError(f"{t['key']} is {t['stage']}; only embryos can be released")
        if t.get("analysis") == "stale":
            raise ValueError(f"{t['key']} was edited; its authority analysis is stale and must be redone before release")
        p, d = _prio(form.get("p")), form.get("d") or ""
        add, remove = _prio_due(t, p, d and _date(d), bool(d))
        return _edit(repo, t, ["released"] + add, remove), f"{t['key']} released as {p}" + (f", deadline {d}" if d else "")
    if a == "change":
        if t["stage"] not in ("released", "wip"):
            raise ValueError(f"{t['key']} is {t['stage']}; priority and deadline change only on released tasks")
        p = _prio(form["p"]) if form.get("p") else None
        d = form.get("d") if "d" in form else None
        return _edit(repo, t, *_prio_due(t, p, d and _date(d), d is not None)), f"{t['key']} updated"
    if a == "cancel":
        if t["stage"] not in openstage:
            raise ValueError(f"{t['key']} is {t['stage']}")
        return _edit(repo, t, ["cancelled"], []) + [["issue", "close", n, "-R", repo, "--reason", "not planned"]], f"{t['key']} cancelled"
    if a in ("block", "unblock"):
        b = _task(S, form.get("b"))
        if t["stage"] not in openstage or b["number"] == t["number"]:
            raise ValueError("cannot change blockers of this task")
        if (b["key"] in t["blockedBy"]) == (a == "block"):
            raise ValueError(f"{b['key']} is {'already' if a == 'block' else 'not'} a blocker of {t['key']}")
        path = f"repos/{repo}/issues/{n}/dependencies/blocked_by"
        if a == "block":
            return [["api", "-X", "POST", path, "-F", f"issue_id={b['dbid']}"]], f"{t['key']} blocked by {b['key']}"
        return [["api", "-X", "DELETE", f"{path}/{b['dbid']}"]], f"{t['key']} no longer blocked by {b['key']}"
    if a in ("accept", "dismiss"):
        p = next((p for p in S["proposals"] if p["n"] == t["number"] and p["id"] == str(form.get("id"))), None)
        if not p:
            raise ValueError("no such open proposal")
        ops = []
        if a == "accept":
            ops = _edit(repo, t, *_prio_due(t, f"P{p['value']}" if p["field"] == "priority" else None, p["value"] if p["field"] == "deadline" else None, p["field"] == "deadline"))
        verb = "Accepted" if a == "accept" else "Dismissed"
        ops.append(["issue", "comment", n, "-R", repo, "--body", f"<!-- pm:resolved {p['id']} -->\n{verb} by the Manager: {p['field']} -> {p['value']}."])
        return ops, f"proposal {verb.lower()} on {t['key']}"
    if a in ("take", "complete"):
        if not simulate:
            raise PermissionError("simulation is off (start with --simulate-orchestrator)")
        if a == "take":
            if t["stage"] != "released" or any(b for b in t["blockedBy"] if next(x for x in S["tasks"] if x["key"] == b)["stage"] != "merged"):
                raise ValueError(f"{t['key']} is not a released, unblocked task")
            return [["issue", "edit", n, "-R", repo, "--add-assignee", "@me"], ["issue", "comment", n, "-R", repo, "--body", "Orchestrator (simulated) takes this task."]], f"{t['key']} WIP"
        if t["stage"] != "wip":
            raise ValueError(f"{t['key']} is not WIP")
        return [["issue", "close", n, "-R", repo, "--reason", "completed"], ["issue", "comment", n, "-R", repo, "--body", "Orchestrator (simulated) completed this task."]], f"{t['key']} completed"
    raise ValueError(f"unknown action {a!r}")


def execute(ops, run=run_gh):
    """Run the planned gh calls in order; '{id}' is filled from the previous call's JSON; `label create` may fail (exists).
    -> each call's JSON answer ({} when none), for the known effect of the action (PC-135)."""
    last, outs = {}, []
    for op in ops:
        op = [x.replace("{id}", str(last.get("id", ""))) for x in op]
        try:
            out = run(op)
        except RuntimeError:
            if op[0] == "label":
                outs.append({})
                continue
            raise
        try:
            last = json.loads(out) if (out or "").strip().startswith("{") else {}
        except ValueError:
            last = {}
        outs.append(last)
    return outs


def settings_path(p):
    """The Dispatcher's settings file of the project: <data dir>/<project name>/dispatcher-settings.json (spec 6a)."""
    return DATA_DIR / (p.get("name") or p["repo"].split("/")[-1]) / "dispatcher-settings.json"


def get_settings(p):
    """Current values (the defaults when the file is missing) with the cap on workers and the page's minimums."""
    f = settings_path(p)
    return dict(values=load_settings(f) if f.exists() else dict(SETTING_DEFAULTS), defaults=SETTING_DEFAULTS, minimums=L.SETTING_MINIMUMS,
                workers_cap=RuntimeConfig.image_workers, steering=CFG["steer"])


def get_queue(p):
    """The local part of the Task Queue tab (no GitHub): running Workers from the Dispatcher's state file, the processing log's newest lines,
    the Dispatcher settings and the last daily backup's status record (None when there is none). The token and quota files are read for the Token Usage tab (`/api/usage`), also local."""
    d = DATA_DIR / (p.get("name") or p["repo"].split("/")[-1])
    return dict(L.queue_local(_read_text(d / "dispatcher-state.json"), _read_text(d / PLOG_FILE)), settings=get_settings(p), asOf=time.time(),
                backup=L.backup_status(_read_text(d / L.BACKUP_STATUS_FILE)), compliance=L.compliance_summary(_read_text(d / L.COMPLIANCE_REPORT_FILE)))


def card_settings_path(p):
    """The module-card options of the project: <data dir>/<project name>/cockpit-settings.json."""
    return DATA_DIR / (p.get("name") or p["repo"].split("/")[-1]) / "cockpit-settings.json"


def get_card_settings(p):
    """Current module-card options; the defaults when the file is missing or unreadable."""
    values = dict(L.CARD_SETTING_DEFAULTS)
    try:
        values = L.validate_card_settings(json.loads(card_settings_path(p).read_text()), strict=False)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, AttributeError) as ex:
        log.warning("card settings of %s unreadable, using defaults: %s", p.get("name", ""), ex)
    return dict(values=values, defaults=L.CARD_SETTING_DEFAULTS, choices=dict(churn_colour=list(L.CARD_CHURN_COLOURS)), steering=CFG["steer"])


def save_card_settings(p, form):
    """Validate and write the module-card options atomically, mode 0600. ValueError on a refused value."""
    values = L.validate_card_settings(form)
    f = card_settings_path(p)
    store_io.atomic_write_json(f, values, mode=0o600, indent=2)
    log.info("card settings saved for %s: %s", p.get("name", ""), values)
    return "card settings saved"


# The Token Usage tab's files, under DATA_DIR/<project name>/ (the folder the Settings page uses)
USAGE_FILE, PLOG_FILE, AREA_DIR, QUOTA_FILE = "proxy-usage.jsonl", "processing/processing.jsonl", "area", "quota-history.jsonl"
TRANSCRIPT_FILE, AGGREGATE_FILE = "transcript-usage.jsonl", "stats-cache-usage.jsonl"
SNAPSHOT_REFRESH_FILE = "code-snapshot-refresh.json"  # the last result of tools/code_snapshot_refresh.py (card-metadata contract)


def _read_text(path):
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def _usage_one(p, data_dir=None):
    d = (data_dir or DATA_DIR) / (p.get("name") or p["repo"].split("/")[-1])

    def goodbye_tokens(folder):  # only the task folder's name is taken from the log; the file is read inside the area
        try:
            t = json.loads(_read_text(d / AREA_DIR / pathlib.PurePath(folder).name / "outbox" / "goodbye.json")).get("tokens")
        except (TypeError, ValueError, AttributeError):
            return None
        return t if isinstance(t, int) and not isinstance(t, bool) else None
    return L.usage_report(_read_text(d / USAGE_FILE), _read_text(d / PLOG_FILE), goodbye_tokens, _read_text(d / QUOTA_FILE))


def usage_served(p):
    return cached(pk(p, "usage"), lambda: get_usage(p), 15, swr=True)


def get_usage(p, data_dir=None, projects=None, today=None):
    """The selected project's report plus `estimates` (the quota per vendor window, measured or estimated) and `monthly`: every project the Cockpit serves per month (L.monthly_by_project), from each one's
    proxy file and processing log and from the transcript and statistics-cache files of all their data folders."""
    rep = _usage_one(p, data_dir)
    d = (data_dir or DATA_DIR) / (p.get("name") or p["repo"].split("/")[-1])
    rep["sessions"] = L.transcript_report(_read_text(d / TRANSCRIPT_FILE), rep["quota"], _read_text(d / AGGREGATE_FILE))
    try:  # the plan start and the weak threshold live with the card settings (Settings > Module cards > Quota estimates)
        qs = json.loads(_read_text(d / "cockpit-settings.json") or "{}")
    except ValueError:
        qs = {}
    rep["estimates"] = L.quota_estimates(_read_text(d / QUOTA_FILE), _read_text(d / USAGE_FILE), _read_text(d / TRANSCRIPT_FILE),
                                         qs if isinstance(qs, dict) else {})
    ps = list((projects or PROJECTS or {p.get("name", ""): p}).values())
    folders = [(data_dir or DATA_DIR) / (q.get("name") or q["repo"].split("/")[-1]) for q in ps]
    rep["monthly"] = L.monthly_by_project(
        [dict(name=q.get("name") or q["repo"].split("/")[-1], dir=pathlib.Path(q["git"]).name if q.get("git") else q.get("name") or q["repo"].split("/")[-1],
              sources=q.get("transcript_sources"), start=q.get("start_month"), usage=_read_text(f / USAGE_FILE), plog=_read_text(f / PLOG_FILE))
         for q, f in zip(ps, folders)],
        [_read_text(f / TRANSCRIPT_FILE) for f in folders], [_read_text(f / AGGREGATE_FILE) for f in folders],
        today or dt.datetime.now(dt.timezone.utc).date())
    rep["reconcile"] = _reconcile_card(d, p.get("stats_cache"))
    return rep


def _reconcile_card(d, cache=None):
    """The card "How these numbers fit": tools/token_reconcile.py on the same files (local day: the server's zone); None if it fails."""
    try:
        R = _tool("token_reconcile")
        return R.card(R.reconcile(R.load(d, cache)))
    except Exception:  # a defect of the analysis must not take the Token Usage page down
        log.exception("token reconciliation failed")
        return None


def _tool(name):
    """A module of tools/ (not a package), loaded once."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).resolve().parent / "tools" / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


def save_settings(p, form):
    """Validate and write the settings file atomically, mode 0600. ValueError on a refused value."""
    values = L.validate_settings(form, SETTING_DEFAULTS, RuntimeConfig.image_workers)
    f = settings_path(p)
    store_io.atomic_write_json(f, values, mode=0o600, indent=2)
    log.info("dispatcher settings saved for %s: %s", p.get("name", ""), values)
    return "settings saved"


def get_task(p, n, run=run_gh):
    """The current state of one task, read from GitHub now (one call, bypassing the cached state): the page asks for it when an action of
    its own got no answer, so that it can say what happened (PC-136)."""
    if not str(n).isdigit():
        raise ValueError(f"bad task number: {n!r}")
    return L.task_lookup(json.loads(run(["api", f"repos/{p['repo']}/issues/{n}"])))


# ---------- after an action (PC-135): the known effect at once, then a re-read of only the touched issues ----------
REREAD_DELAY_S = 3   # the actions of a burst within this many seconds are re-read together: one call per TL_BATCH issues, not one per action
_reread, _reread_lock = {}, threading.Lock()   # state key -> issue numbers waiting for their re-read


def _key_lock(k):
    with _lock:
        return _locks.setdefault(k, threading.Lock())


def _store(k, t, S):
    """Replace the cached state, keeping the time of its full load (a patch never resets the hour)."""
    _cache[k] = (t, S)
    _disk_save(k, S)


APPLY_WAIT_S = 1.0     # an action's answer waits at most this long for a full load that holds the state (a warm-up, a Refresh): actions never wait for it (PC-131)
_deferred = set()      # state keys with a known effect still waiting for a full load to finish


def after_action(p, ops, outs, now=None, schedule=True, wait=APPLY_WAIT_S):
    """Lay the known effect of the gh calls that succeeded on the cached state (logic.apply_ops, no GitHub call) and queue a re-read of the
    touched issues. -> their numbers, or None when the cache holds no raw copy (an older copy: it counts as expired, the next look reloads it)
    or a full load holds the state longer than `wait` (the effect is then laid on it in the background once the load is stored)."""
    k, now = pk(p, "state"), now or time.time()
    lock = _key_lock(k)   # never between a full load's fetch and its store
    if not lock.acquire(timeout=-1 if wait is None else wait):
        log.info("%s: a full load holds the state; the effect of the action is applied when it is stored", p.get("name", ""))
        _deferred.add(k)

        def later():
            try:
                after_action(p, ops, outs, now, schedule, wait=None)
            except Exception:
                log.exception("the deferred known effect of an action could not be applied")
            finally:
                _deferred.discard(k)
        threading.Thread(target=later, daemon=True).start()
        return None
    try:
        t, S = _cache.get(k, (0, None))
        if S is None and (S := _disk_load(k)) is not None:
            t = _disk_time(k, DISK_FRESH_S)
        if S is None:
            return None
        if "raw" not in S:
            _cache[k] = (0, S)
            return None
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        raw, dl, nums = L.apply_ops(S["raw"], S["modDeadline"], ops, outs, S["source"].get("user") or p["manager"], stamp)
        _store(k, t, state_from_raw(p, raw, dl, dict(S["source"], patchedAt=now, rereadNote=None)))
    finally:
        lock.release()
    if nums:
        with _reread_lock:
            first = k not in _reread
            _reread.setdefault(k, set()).update(nums)
        if first and schedule:
            threading.Timer(REREAD_DELAY_S, _reread_safe, (p,)).start()
    log.info("%s: action applied to the cached state; issue(s) %s re-read from GitHub in %d s", p.get("name", ""), nums, REREAD_DELAY_S)
    return nums


def _reread_safe(p):
    try:
        reread(p)
    except Exception:
        log.exception("re-read after an action failed")


def reread(p, fetch=None):
    """Read the issues touched by the last actions afresh (one light call per TL_BATCH issues, counted in the hourly cap) and put them in the cached
    state. When GitHub cannot be asked (rate-limit pause, cap, error) the applied change stays and `source.rereadNote` says so."""
    k = pk(p, "state")
    with _reread_lock:
        nums = sorted(_reread.pop(k, ()))
    if not nums:
        return
    try:
        with _gh_queue:
            issues, events = (fetch or (lambda ns: fetch_issue_nodes(p, ns)))(nums)
        note = None
    except Exception as ex:
        issues, note = None, f"GitHub could not be asked about {', '.join(f'#{n}' for n in nums)} ({str(ex)[:150]}); the change is shown as applied here"
        log.warning("%s: re-read after an action failed: %s", p.get("name", ""), str(ex)[:150])
    with _key_lock(k):
        t, S = _cache.get(k, (0, None))
        if not S or "raw" not in S:
            return
        if issues is None:
            S = dict(S, source=dict(S["source"], rereadNote=note))
        else:
            with _reread_lock:   # an issue acted on again meanwhile keeps its newer applied change until its own re-read
                again = _reread.get(k, set())
            keep = [i for i in issues if i["number"] not in again]
            S = state_from_raw(p, L.replace_issues(S["raw"], keep, [e for e in events if e["number"] not in again]), S["modDeadline"],
                               dict(S["source"], rereadAt=time.time(), rereadNote=None))
            log.info("%s: issue(s) %s re-read from GitHub after an action", p.get("name", ""), [i["number"] for i in keep])
        _store(k, t, S)


# ---------- action ids: the page retries an action after a network failure; a repeat is never applied twice (PC-131) ----------
ACTS_KEEP, ACT_WAIT_S = 200, 120
_acts_lock, _acts_running = threading.Lock(), {}  # aid -> Event of an action that runs in this process right now


def acts_path():
    """The last ACTS_KEEP action ids with their state (started, or done with the answer): <data dir>/actions-seen.json. On disk, so that
    an action interrupted by a restart is recognised when the page sends it again."""
    return DATA_DIR / "actions-seen.json"


def _acts():
    try:
        return json.loads(acts_path().read_text())
    except (OSError, ValueError):
        return {}


def _acts_put(aid, rec):
    with _acts_lock:
        a = _acts()
        a[aid] = dict(rec, at=time.time())
        for k in sorted(a, key=lambda k: a[k]["at"])[:-ACTS_KEEP]:
            del a[k]
        try:
            _write_json(acts_path(), a)
        except OSError as ex:
            log.warning("action ids not written to disk: %s", ex)


def applied_now(form, run, p=None):
    """True/False: the effect of the action can be read on the issue as it is on GitHub now; None: it cannot be told."""
    if form.get("action") not in L.CHECKABLE_ACTIONS:
        return None
    try:
        p = p or proj(form.get("project"))
        issue = json.loads(run(["issue", "view", str(_int(form.get("n"))), "-R", p["repo"], "--json", "state,labels,title,body"]))
    except (RuntimeError, ValueError, UnknownProject) as ex:
        log.warning("cannot read back the task of an interrupted %s: %s", form.get("action"), str(ex)[:100])
        return None
    return L.action_applied(form, issue)


def do_act(form, run=None, state=None, p=None):
    """-> (http status, body). With an action id (`aid`, sent by the page) the action is applied at most once: a repeat of a finished
    action gets the first answer (repeat=True); a repeat of one that is still running waits for it; a repeat of one interrupted by a
    restart first reads the task's current state on GitHub and is only run again when its effect is not there."""
    run = run or run_gh
    aid = str(form.get("aid") or "")[:64]
    if not aid or not CFG["steer"]:
        return _logged_act(form, run, state, p)
    with _acts_lock:
        ev = _acts_running.get(aid)
    if ev:
        ev.wait(ACT_WAIT_S)
    rec = _acts().get(aid)
    if rec and rec.get("state") == "done":
        log.info("action %s repeated (id %s): answered as the first time, not applied again", form.get("action"), aid)
        return rec["code"], dict(rec["body"], repeat=True)
    if rec:
        if aid in _acts_running:
            return 409, dict(error="this action is still running; look at the task in a minute")
        done = applied_now(form, run, p)
        log.info("action %s (id %s) was interrupted by a restart; its effect on GitHub: %s", form.get("action"), aid, done)
        if done:
            body = dict(ok=True, msg=f"{form.get('action')} had already been applied before the Cockpit restarted; not applied again", repeat=True)
            _acts_put(aid, dict(state="done", code=200, body=body))
            return 200, body
        if done is None:
            return 409, dict(error="this action was interrupted by a restart of the Cockpit and its effect cannot be read back; look at the task before trying again")
    with _acts_lock:
        if aid in _acts_running:  # another repeat claimed it in the meantime
            return 409, dict(error="this action is still running; look at the task in a minute")
        ev = _acts_running[aid] = threading.Event()
    try:
        _acts_put(aid, dict(state="started", action=form.get("action")))
        code, body = _logged_act(form, run, state, p)
        _acts_put(aid, dict(state="done", code=code, body={k: v for k, v in body.items() if k != "state"}))  # the page's copy of the state (PC-135) is not kept for repeats
        return code, body
    finally:
        with _acts_lock:
            _acts_running.pop(aid, None)
        ev.set()


def _logged_act(form, run, state=None, p=None):
    """-> (http status, body). 403 when the app is read-only; acts on the project named in form["project"] (default project otherwise).
    Every action, whatever its outcome, leaves one line in the project's action log (PC-132)."""
    ctx, t0 = dict(p=p), time.monotonic()
    try:
        code, body = _do_act(form, run, state, ctx)
    except Exception as ex:  # an unexpected error is a failed action too: logged, and answered rather than dropping the connection
        log.exception("action %s failed", form.get("action"))
        code, body = 500, dict(error=f"{type(ex).__name__}: {ex}")
    outcome = "ok" if code == 200 else "failed" if code >= 500 else "refused"
    log_action(ctx.get("p"), L.action_record(form, outcome, body.get("msg") or body.get("error"), (time.monotonic() - t0) * 1000,
                                             task=ctx.get("task"), version=code_version(), secrets=(H.token,)))
    return code, body


def _do_act(form, run, state, ctx):
    if not CFG["steer"]:
        log.warning("refused action %s: read-only", form.get("action"))
        try:
            ctx["p"] = ctx["p"] or proj(form.get("project"))
        except UnknownProject:
            pass
        return 403, dict(error="read-only: this is an offline copy from a backup" if OFFLINE else "read-only: this instance was started with --read-only")
    try:
        p = ctx["p"] = ctx["p"] or proj(form.get("project"))
        if form.get("action") == "settings":  # a local file, not a GitHub operation; same guard, same errors
            return 200, dict(ok=True, msg=save_settings(p, form))
        if form.get("action") == "cardsettings":  # same: a local file, same guard
            return 200, dict(ok=True, msg=save_card_settings(p, form))
        if form.get("action") == "compliance":  # starts the project's compliance check; same guard (the UI is the owner's alone)
            return 200, dict(ok=True, msg=start_compliance(p))
        why = steer_block(p)
        if why:  # owner: the write side refuses while GitHub cannot be read; nothing is queued for later
            log.warning("refused action %s on %s: %s", form.get("action"), p.get("name", ""), why)
            return 503, dict(error=f"not done, and not queued: {why}")
        S = state or get_state(p=p)  # the cached state: an action does not bypass the hour (owner F-701)
        ctx["task"] = next((t["key"] for t in S["tasks"] if str(t["number"]) == str(form.get("n", "")).lstrip("#")), None)
        ops, msg = plan_action(form, S, p["repo"], CFG["simulate"])
        outs = execute(ops, run)
    except UnknownProject as ex:
        return 404, dict(error=ex.args[0])
    except PermissionError as ex:
        return 403, dict(error=str(ex))
    except ValueError as ex:
        log.warning("refused %s: %s", form.get("action"), ex)
        return 400, dict(error=str(ex))
    except RuntimeError as ex:
        log.error("action %s failed: %s", form.get("action"), ex)
        return 502, dict(error=f"GitHub: {ex}")
    log.info("action ok on %s: %s -> %s", p.get("name", ""), form.get("action"), msg)
    try:
        nums = after_action(p, ops, outs) if ops else []
    except Exception:  # the action itself succeeded: its answer must not fail; the next look reloads the state
        log.exception("the known effect of %s could not be applied to the cached state", form.get("action"))
        if pk(p, "state") in _cache:
            _expire(pk(p, "state"))
        nums = None
    # the page's copy at once (PC-135): the served state with the known effect, when the cache holds one (no GitHub call)
    return 200, dict(ok=True, msg=msg, issues=nums or [], **({"state": get_state_served(p)} if nums is not None and ops and state is None else {}))


# ---------- action log (PC-132): <data dir>/<project>/actions.jsonl, appended, mode 0600, rotated by size keeping five files ----------
ACTION_LOG_MAX = 1_000_000  # bytes of one file before it is rotated
ACTION_LOG_KEEP = 5         # files kept: actions.jsonl and actions.jsonl.1 .. .4 (the oldest is dropped)
_action_log_lock = threading.Lock()
_version = []


def code_version():
    """The Cockpit's own commit (short hash), read once; '' when it is not a git checkout."""
    if not _version:
        try:
            _version.append(subprocess.run(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip())
        except (OSError, subprocess.SubprocessError):
            _version.append("")
    return _version[0]


def action_log_path(p):
    """<data dir>/<project>/actions.jsonl; None for a project without a name or repo (nothing to file it under)."""
    name = p.get("name") or (p.get("repo") or "").split("/")[-1]
    return DATA_DIR / name / "actions.jsonl" if name else None


def log_action(p, rec):
    """Append one event to the project's action log; rotate first when the file is full. Never fails the action: a write error is a warning.
    An offline copy writes nothing (its data folder is the backup)."""
    f = action_log_path(p) if p and not OFFLINE else None
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
    events = L.filter_events(lines(), action, outcome, task)
    return dict(events=events, limit=L.ACTION_LOG_LIMIT, path=str(f or ""))


def page_events(form):
    """POST /api/event {project, events: [...]}: the failures a page reports (each with its page id; the ones it kept while the server could not be
    reached come in one batch) are appended to the project's action log. -> (http status, body); the invalid ones are counted, not logged."""
    try:
        p = proj(form.get("project") or None)
    except UnknownProject as ex:
        return 404, dict(error=ex.args[0])
    evs = form.get("events")
    if not isinstance(evs, list) or not evs or len(evs) > 50:
        return 400, dict(error="events: a list of 1 to 50 events")
    bad = 0
    for ev in evs:
        try:
            rec = L.page_record(ev if isinstance(ev, dict) else {}, p.get("name", ""), version=code_version(), secrets=(H.token,))
        except ValueError:
            bad += 1
            continue
        log.warning("page %s reports %s on %s: %s", rec["page"], rec["kind"], rec["action"] or "-", rec["msg"])
        log_action(p, rec)
    return 200, dict(ok=True, logged=len(evs) - bad, refused=bad)


# ---------- compliance check (Settings > Policies): the report is the check's (PC-108); the Cockpit shows it and starts the check ----------
_compliance_lock = threading.Lock()


def compliance_status_path(p):
    """The run status of the project's compliance check: <data dir>/<project name>/compliance-run.json (the page polls it)."""
    return DATA_DIR / (p.get("name") or p["repo"].split("/")[-1]) / "compliance-run.json"


def _write_json(f, data):
    store_io.atomic_write_json(f, data)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def compliance_of(p):
    """One project's compliance view: configured, the report (date, violations; None = never run), the run state."""
    try:
        status = json.loads(compliance_status_path(p).read_text())
    except (OSError, ValueError):
        status = None
    out = dict(name=p.get("name", ""), configured=bool(p.get("compliance") and p.get("git")), report=None, path=L.COMPLIANCE_REPORT,
               run=dict(status or {}, state=L.compliance_run_state(status, status and _alive(status.get("pid")))))
    f = compliance_report_file(p)
    if f:
        try:
            r = L.parse_compliance(f.read_text())
            out["report"] = dict(r, checked=r["checked"] or dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="minutes"))
        except (OSError, UnicodeDecodeError) as ex:
            out["error"] = f"report unreadable: {ex}"
    return out


def build_compliance():
    """The compliance view of every configured project (local files only, no GitHub)."""
    return dict(steering=CFG["steer"], projects=[compliance_of(p) for p in (PROJECTS or {CFG.get("name", ""): CFG}).values()])


def start_compliance(p):
    """Start the project's compliance check detached, in its clone; one run at a time per project. The status file records the run.
    ValueError when not configured or already running. The check itself writes the report; the Cockpit writes nothing into the project."""
    if not (p.get("compliance") and p.get("git")):
        raise ValueError("compliance check not configured: set the project's `compliance` command and `git` clone")
    name = p.get("name", "")
    with _compliance_lock:
        if compliance_of(p)["run"]["state"] == "running":
            raise ValueError(f"a compliance check of {name} is already running")
        f = compliance_status_path(p)
        f.parent.mkdir(parents=True, exist_ok=True)
        logf = open(f.with_suffix(".log"), "w")
        proc = subprocess.Popen(p["compliance"], cwd=pathlib.Path(p["git"]).expanduser(), stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True, env=dict(os.environ, COMPLIANCE_REPORT=L.COMPLIANCE_REPORT))
        rec = dict(state="running", pid=proc.pid, started=time.time())
        _write_json(f, rec)

    def wait():
        code = proc.wait()
        logf.close()
        _write_json(f, dict(rec, state="ended", exit=code, ended=time.time()))
        log.info("compliance check of %s ended with exit %s", name, code)

    threading.Thread(target=wait, daemon=True).start()
    log.info("compliance check of %s started (pid %d): %s", name, proc.pid, " ".join(p["compliance"]))
    return f"compliance check of {name} started"


def compliance_report_file(p):
    """The project's current report file, or None."""
    f = pathlib.Path(p["git"]).expanduser() / L.COMPLIANCE_REPORT if p.get("git") else None
    return f if f and f.is_file() else None


# ---------- rules ----------
def run_git(p, args, timeout=30):
    """Run `git -C <clone> <args>` (list-based, bounded) and return stdout."""
    return subprocess.run(["git", "-C", p["git"], *args], capture_output=True, text=True, check=True, timeout=timeout).stdout


def build_rules(p=None):
    p = p or proj()
    rc = L.rules_config(load_modules(p))
    if not rc or not p["git"]:
        return dict(configured=False)
    t0, gs, spec = time.time(), rc["groups"], L.rule_pathspecs(rc["groups"])
    fmt = ["log", "--no-renames", f"--format={L.LOG_FMT}"]
    changes = L.parse_rule_changes(run_git(p, fmt + ["--since=90.days.ago", "--numstat", "--", *spec]), run_git(p, fmt + ["--since=90.days.ago", "-p", "-U0", "--", *spec]), gs, rc["managers"], rc["bots"])
    last = L.parse_last_changes(run_git(p, fmt + ["--name-only", "--", *spec]), gs)
    files = L.rule_files([p for p in run_git(p, ["ls-files", "-z", "--", *spec]).split("\0") if p], last, gs)
    log.info("rules: %d files, %d changes in %.1fs", len(files), len(changes), time.time() - t0)
    return dict(configured=True, groups=[g["name"] for g in gs], files=files, changes=changes, repo=p["repo"])


def get_rules(p=None):
    """The rule view from local files and local git only (no GitHub): Settings > Policies renders from it whatever GitHub does."""
    p = p or proj()
    return cached(pk(p, "rules"), lambda: build_rules(p), 120, swr=True, persist=True)


def get_rule_prs(p, fresh=False):
    """The open pull requests that touch rule files: the one GitHub-sourced part of Policies, served separately so that it can never block or fail
    the rest. Hourly cache; fresh (the owner's Refresh click on the pane) bypasses it. -> dict(configured, available, pending, at, error)."""
    rc = L.rules_config(load_modules(p))
    if not rc or not (p.get("git") or OFFLINE):  # an offline copy may hold the pull requests without the clone
        return dict(configured=False, available=False, pending=[])
    k = pk(p, "rulesprs")
    if fresh:
        _expire(k)
        log.info("owner Refresh of the rule pull requests of %s", p.get("name", ""))
    fetch = lambda: dict(at=time.time(), prs=json.loads(run_gh(["pr", "list", "-R", p["repo"], "--state", "open", "--json", "number,title,author,files,url,createdAt"])))
    try:
        v = cached(k, fetch, GH_REFRESH_S, swr=not fresh, persist=True, disk_fresh=DISK_FRESH_S, queue=True)
    except (RuntimeError, ValueError) as ex:
        log.warning("rules: open pull requests unavailable (%s)", str(ex)[:80])
        return dict(configured=True, available=False, pending=[], error=str(ex)[:200])
    return dict(configured=True, available=True, pending=L.pending_rule_prs(v["prs"], rc["groups"]), at=v["at"])


def local_gate(p):
    """The last plain merge gate run on main in the project's clone (logic.gate_result), or None."""
    if not p.get("git"):
        return None
    try:
        common = pathlib.Path(p["git"]) / run_git(p, ["rev-parse", "--git-common-dir"], timeout=5).strip()
        return L.gate_result((common / L.GATE_LOG).read_text())
    except (OSError, subprocess.SubprocessError):
        return None


def get_ci(p):
    """The state of main for the Task Queue page (until the daily job reports a red main, PC-91): the newer of the last hosted run of
    the test workflow (hourly cache, served on its own so that it can never block the page) and the local merge gate's last run on
    main (PC-129). -> logic.ci_run(...) plus at, the local result, or dict(state="unknown", error)."""
    fetch = lambda: dict(L.ci_run(run_gh(["run", "list", "-R", p["repo"], "--workflow", L.CI_WORKFLOW, "--branch", "main", "--limit", "1", "--json", L.CI_FIELDS])), at=time.time())
    try:
        hosted = cached(pk(p, "ci"), fetch, GH_REFRESH_S, swr=True, persist=True, disk_fresh=DISK_FRESH_S, queue=True)
    except (RuntimeError, ValueError) as ex:
        log.warning("ci: last run of main unavailable (%s)", str(ex)[:80])
        hosted = dict(state="unknown", error=str(ex)[:200])
    return L.ci_latest(hosted, local_gate(p))


# ---------- timeline / code ----------
def load_commits(p):
    if p.get("git"):
        txt = subprocess.run(["git", "-C", p["git"], "log", "--name-only", "--date=iso", "--format=@%H|%ad|%s"], capture_output=True, text=True, check=True).stdout
        return L.parse_git_full(txt)
    since = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    raw = json.loads(run_gh(["api", f"repos/{p['repo']}/commits?since={since}T00:00:00Z&per_page=100"]))
    return [dict(sha=c["sha"], date=dt.date.fromisoformat(c["commit"]["author"]["date"][:10]), subject=c["commit"]["message"].splitlines()[0], files=[]) for c in raw]


def build_timeline(S, p=None, fresh=False):
    p = p or proj()
    t0 = time.time()
    cfgm = load_modules(p)
    assign = cfgm["assign"] if cfgm else []
    mod_of = {t["key"]: t["module"] for t in S["tasks"]}
    url_of = {t["key"]: t["url"] for t in S["tasks"]}
    ck = pk(p, "commits")
    if fresh:
        _expire(ck)
    feed = L.group_commits(cached(ck, lambda: load_commits(p), GH_REFRESH_S if not p.get("git") else 60, swr=not fresh, persist=True, disk_fresh=DISK_FRESH_S, queue=not p.get("git")), assign, p["repo"]) + L.issue_entries(S["tasks"])
    feed += [dict(d=e["t"], type="task event", mod=mod_of.get(e["key"], "unassigned"), text=f"{e['actor']} {e['key']}: {e['text']}", links=[(e["key"], url_of.get(e["key"], ""))])
             for e in S["events"] if e["kind"] != "closed"]
    if p["git"]:
        for m in S["modules"]:
            if not m.get("virtual"):
                feed += L.static_entries(L.module_dir(p["git"], m), m["id"], p["repo"])
    try:
        feed += L.rule_feed(get_rules(p).get("changes", []), p["repo"])
    except Exception as ex:  # the timeline must not break because the rule view could not be built
        log.warning("timeline: rule changes unavailable (%s)", str(ex)[:80])
    if p["phases"]:
        pj = json.load(open(p["phases"]))
        feed += [dict(d=e["date"], type=e["type"], mod=pj.get("module", "unassigned"), text=e["text"], links=[l for l in (L.ref_link(r, p["repo"]) for r in e.get("refs", []))]) for e in pj["events"]]
    seen, out = set(), []
    for e in sorted(feed, key=lambda e: e["d"], reverse=True):
        k = (e["d"], e["type"], e["mod"], e["text"])
        if k not in seen:
            seen.add(k)
            out.append(dict(e, links=[list(l) for l in e["links"]]))
    log.info("timeline: %d entries in %.2fs", len(out), time.time() - t0)
    return dict(entries=out[:400], total=len(out), activity=L.activity_days(S["tasks"], S["events"], mod_of))


def get_timeline(p, fresh=False):
    """The timeline built from the state that is served right now (a refreshed state gets a rebuilt timeline at once, never one from older data)."""
    S = get_state(p=p)
    k, at = pk(p, "timeline"), state_version(S)
    build = lambda: dict(build_timeline(S, p, fresh), stateAt=at)
    if fresh:
        _expire(k)
        log.info("owner Refresh of the timeline of %s", p.get("name", ""))
    tl = cached(k, build, 30, swr=not fresh, persist=True, disk_fresh=DISK_FRESH_S)
    return tl if tl.get("stateAt") == at else cached(k, build, -1, persist=True)


def state_version(S):
    """When the state last changed: its full load, an action's applied effect or a re-read of single issues (PC-135)."""
    src = S.get("source", {})
    return max(src.get("fetchedAt") or 0, src.get("patchedAt") or 0, src.get("rereadAt") or 0)


def stale_note(p, now=None):
    """None, or why the state served for p is older than it should be: dict(age_s, error, until, rate_limited) of the failed refresh (until = when GitHub is tried again)."""
    f, now = _failures.get(pk(p, "state")), now or time.time()
    v = _cache.get(pk(p, "state"), (0, None))[1]
    if not f or not v:
        return None
    kind = L.offline_kind(f["error"], f["rate_limited"])
    return dict(age_s=max(0, round(now - v["source"]["fetchedAt"])), error=f["error"], until=f["until"], rate_limited=f["rate_limited"],
                kind=kind, reason=L.offline_reason(kind, f["until"], _clock))


def steer_block(p, now=None):
    """None, or why the steering actions on GitHub are off now (owner: the write side refuses while GitHub cannot be read and never queues)."""
    now = time.time() if now is None else now
    if OFFLINE:
        return "offline copy from a backup"
    if now < _gh_pause["until"]:
        return L.offline_reason("rate_limit", _gh_pause["until"], _clock)
    n = stale_note(p, now)
    return n["reason"] if n else None


STATE_CALLS = 12   # gh calls of one state load (see GH_CALLS_PER_HOUR)
_live_try = {}     # state key -> epoch of the last refresh asked for by a newer processing-log event (PC-119)


def gh_room(now=None):
    """GitHub may be called for one state load now: no rate-limit pause, room in the hourly cap, not an offline copy."""
    now = time.time() if now is None else now
    with _gh_lock:
        return not OFFLINE and now >= _gh_pause["until"] and sum(c > now - 3600 for c in _gh_calls) + STATE_CALLS <= GH_CALLS_PER_HOUR


def live_state(p, S, fresh=False):
    """PC-119: the cached state with the Dispatcher's newer facts laid over it (logic.overlay_live), `pick` set by the Dispatcher's own rule,
    and, when the processing log has a state change newer than the cache, one background refresh at most every LIVE_REFRESH_S seconds."""
    if "tasks" not in S:
        return S
    d = DATA_DIR / (p.get("name") or p["repo"].split("/")[-1])
    shorts = {s.lower() for s in (p.get("short"), p.get("name"), p["repo"].split("/")[-1]) if s}
    events, k, now = L.live_events(_read_text(d / PLOG_FILE), shorts), pk(p, "state"), time.time()
    at = S.get("source", {}).get("fetchedAt", 0)
    if not fresh and L.live_refresh_due(events, at, _live_try.get(k, 0), now, gh_room(now)):
        _live_try[k] = now
        log.info("%s: the processing log is newer than the task list (data from %s): refreshing it", p.get("name", ""), _clock(at))
        _expire(k)
        S = get_state(p=p)   # the old copy at once, the new one fetched in the background
        at = S.get("source", {}).get("fetchedAt", 0)
    state_text = _read_text(d / "dispatcher-state.json")
    held = {n for w in L.queue_local(state_text, None)["workers"] if (n := L.task_number(w["task"], shorts)) is not None}
    tasks = L.overlay_live(S["tasks"], at, events, held, state_text is not None)
    pick = L.pickable(tasks, dt.datetime.now(dt.timezone.utc), held)
    return dict(S, tasks=[dict(t, pick=t["key"] in pick) for t in tasks], openCounts=L.open_counts(tasks, S.get("modules", [])))


def get_state_served(p, fresh=False):
    """The state for the page: `stale` is true while a newer copy is being fetched in the background, so the page asks again shortly;
    `staleNote` is set when the last refresh failed and an older copy is shown (its age, the reason and when GitHub is tried again)."""
    return _served(p, live_state(p, get_state(fresh=fresh, p=p), fresh))


def gh_calls_left(now=None):
    """How many gh calls the Cockpit's own hourly cap still allows now."""
    now = time.time() if now is None else now
    with _gh_lock:
        return GH_CALLS_PER_HOUR - sum(1 for t in _gh_calls if t > now - 3600)


def get_queue_poll(p, since=0, now=None):
    """PC-130: the Task Queue pane's light poll (the page asks every queue_refresh_s while the tab is visible). Always the local part
    (Workers, Dispatcher line, processing log) and the CI line (its own hourly cache); the task state is refreshed from GitHub when older
    than the pane's interval, which falls back to the hourly one while a rate-limit pause is on or the call budget is low (`slowed`).
    The state itself is sent only when it differs from the copy the page has (`since` = its fetchedAt); a failed refresh keeps the old copy."""
    now = time.time() if now is None else now
    every = get_card_settings(p)["values"]["queue_refresh_s"]
    interval, slowed = L.queue_interval(every, gh_calls_left(now), _gh_pause["until"], now, GH_REFRESH_S)
    k, S, err = pk(p, "state"), None, None
    t = _cache.get(k, (None,))[0]
    if t is not None and now - t > interval and not OFFLINE:
        _expire(k)  # due: fetch now, in this request (the page waits for it; one request in flight at a time)
    try:
        S = cached(k, lambda: build_state(p), interval, persist=True, disk_fresh=DISK_FRESH_S, queue=True)
    except Exception as ex:
        log.warning("queue poll of %s: no state (%s)", p.get("name", ""), str(ex)[:120])
        err = str(ex)[:200]
    at = S["source"].get("fetchedAt", 0) if S else 0
    out = dict(local=get_queue(p), ci=get_ci(p), every=every, interval=interval, slowed=slowed, fetchedAt=at or None,
               nextAt=max(at + interval, now) if at else None, staleNote=stale_note(p), error=err)
    if S and at != since:
        out["state"] = _served(p, live_state(p, S))
    return out


def _served(p, S):
    """A cached state as the page gets it (modules in size order, steering off in an offline copy, its refresh flags)."""
    if OFFLINE:  # the copy was taken from a steering instance: steering is off here whatever the cached state says
        S = dict(S, source=dict(S.get("source", {}), steering=False, simulate=False))
    if "modules" in S:
        S = dict(S, modules=ordered(p, S["modules"]))
    S = {k: v for k, v in S.items() if k != "raw"}   # the raw copy stays on the server (PC-135)
    at = S.get("source", {}).get("fetchedAt")
    new_work = L.may_start_new(time.time() - at if at else None)
    if not new_work and "tasks" in S:  # owner: no new task is started from state older than an hour; work in progress may continue
        S = dict(S, tasks=[dict(t, pick=False) for t in S["tasks"]])
    return dict(S, stale=refreshing(pk(p, "state")) or pk(p, "state") in _reread or pk(p, "state") in _deferred, staleNote=stale_note(p), refreshAfter=(at or 0) + GH_REFRESH_S,
                steerBlock=steer_block(p) if CFG["steer"] else None, newWork=new_work, warming=WARM["on"])


WARM = dict(on=False, since=0.0)  # on from the moment the server listens until the startup warm-up is done (PC-131): the page shows "warming up, data from <time>"


def warm_module_pages(p):
    """The shared inputs of the module pages (file list, document heads, import edges), so the first module page opens fast."""
    cfgm = load_modules(p)
    if p.get("git") and cfgm:
        tracked, _ = tracked_and_heads(p)
        cached(pk(p, "importedges"), lambda: import_edges_of(p, tracked, cfgm["assign"]), 120, swr=True)


def warm(projects=None):
    """Fill the caches of every project in the background (at startup): with a disk copy this only starts the refreshes, without it it computes."""
    def one(p):
        for name, fn in (("state", get_state), ("rules", get_rules), ("timeline", get_timeline), ("code", code_snapshot), ("usage", usage_served), ("module pages", warm_module_pages)):
            try:
                fn(p)
            except Exception as ex:
                log.warning("warm-up %s of %s failed: %s", name, p.get("name", ""), str(ex)[:120])
    with ThreadPoolExecutor(1) as ex:  # one project after another: the first fetches of projects without a disk copy never run as a parallel burst
        list(ex.map(one, projects or list(PROJECTS.values()) or [CFG]))
    log.info("warm-up done")


def line_counts(p, tracked):
    """{path: physical lines} of the tracked text files of the clone (binary files and unreadable ones are left out)."""
    root, out = pathlib.Path(p["git"]).expanduser(), {}
    for x in tracked[:5000]:
        try:
            b = (root / x).read_bytes()
        except OSError:
            continue
        if b"\0" not in b[:8192]:
            out[x] = b.count(b"\n") + (1 if b and not b.endswith(b"\n") else 0)
    return out


def clone_sizes(p):
    """(tracked files, {path: physical lines}) of the clone, read once per two minutes for the Size tile and the module order."""
    def build():
        tracked = [x for x in run_git(p, ["ls-files", "-z"]).split("\0") if x]
        return tracked, line_counts(p, tracked)
    return cached(pk(p, "sizes"), build, 120, swr=True)


def module_lines(p, ids):
    """{module id: code lines} that orders the modules: from the clone with the module map, else from the code snapshot, else {}."""
    try:
        cfgm = load_modules(p)
        if p.get("git") and cfgm:
            return L.code_lines(clone_sizes(p)[1], cfgm.get("assign", []), ids)
        if p.get("code"):
            return L.snapshot_lines(json.load(open(pathlib.Path(p["code"]).expanduser() / "metrics.json")))
    except Exception as ex:  # the module lists must not break because the sizes could not be read; they keep the map order
        log.info("module sizes of %s unavailable (%s)", p.get("name", ""), str(ex)[:100])
    return {}


def ordered(p, mods):
    """The module list in the one order of every list (logic.order_modules), each module with the `lines` it was ordered by."""
    return L.order_modules(mods, module_lines(p, [m["id"] for m in mods if not m.get("virtual")]))


def biggest_by_module(p, ids):
    """{module id: its biggest files (logic.biggest_files)} from the clone and the module map; {} without --git or a map."""
    cfgm = load_modules(p)
    if not (p.get("git") and cfgm):
        return {}
    tracked, sizes = clone_sizes(p)
    assign = cfgm.get("assign", [])
    base = f"https://github.com/{p['repo']}/blob/main/"
    return {mid: [dict(r, url=base + quote(r["path"])) for r in L.biggest_files({x: sizes[x] for x in L.module_files(tracked, assign, mid) if x in sizes})]
            for mid in ids}


def code_snapshot(p=None):
    """The Code tab's payload. `configured`: the project names a snapshot folder (without one the cards hide the snapshot lines);
    `age_h`, `stale`, `max_age_h`: the snapshot's age against the 24 hours of the standing policy."""
    try:
        p = p or proj()
        d = pathlib.Path(p["code"]).expanduser()
        view = L.code_view(json.load(open(d / "metrics.json")), get_card_settings(p)["values"]["quality_thresholds"])
        big = cached(pk(p, "biggest"), lambda: biggest_by_module(p, list(view["modules"])), 120, swr=True, persist=True)
        for mid, m in view["modules"].items():
            m["biggest"] = big.get(mid, [])
        age = L.snapshot_age(view["generated_at"], dt.datetime.now(dt.timezone.utc))
        return dict(wired=True, configured=True, **view, **age, diagrams=[k for k, f in SVGS.items() if (d / f).exists()], mods=local_modules_of(p))
    except (OSError, ValueError, TypeError, KeyError) as ex:
        log.info("code snapshot not wired (%s)", ex)
        return dict(wired=False, configured=bool(p and p.get("code")), modules={}, diagrams=[], mods=local_modules_of(p) if p else [])


def local_modules_of(p):
    """The Code tab's module list from the local module map file (and the local clone for the gap and document counts); no GitHub."""
    try:
        cfgm = load_modules(p)
    except (OSError, ValueError) as ex:
        log.warning("module map of %s unreadable: %s", p.get("name", ""), ex)
        cfgm = None
    return ordered(p, L.local_modules((cfgm or {}).get("modules", [])))


def card_metadata(p=None, now=None):
    """Read footprint/churn/ambiguity.json of the project (read-only). Anything missing or broken is reported as 'no data', never raised."""
    p = p or proj()
    d, texts = meta_dir(p), {}
    for kind in L.CARD_KINDS:
        try:
            texts[kind] = (d / f"{kind}.json").read_text()
        except FileNotFoundError:
            texts[kind] = None
        except (OSError, UnicodeDecodeError) as ex:
            log.warning("card metadata %s unreadable: %s", d / f"{kind}.json", ex)
            texts[kind] = "\0unreadable"  # parse_card reports it as invalid
    out = L.card_meta(texts, p.get("name") or None, now)
    log.info("card metadata of %s from %s: %s", p.get("name", ""), d, {k: v["status"] for k, v in out.items()})
    return dict(out, dir=str(d))


def import_edges_of(p, tracked, assign):
    """Python imports between files of different modules (logic.import_edges over the tracked .py files of the clone)."""
    src = {}
    for x in [x for x in tracked if x.endswith(".py")][:3000]:
        try:
            src[x] = (pathlib.Path(p["git"]).expanduser() / x).read_text(errors="replace")
        except OSError:
            continue
    return L.import_edges(src, assign)


def tracked_and_heads(p):
    """(tracked files of the clone, {docs/*.md path: its first 1500 characters}); shared by all module pages, so read once per minute, not per page."""
    def build():
        tracked = [x for x in run_git(p, ["ls-files", "-z"]).split("\0") if x]
        heads = {}
        for x in [x for x in tracked if x.startswith("docs/") and x.endswith(".md")][:2000]:
            try:
                heads[x] = (pathlib.Path(p["git"]).expanduser() / x).read_text(errors="replace")[:1500]
            except OSError:
                continue
        return tracked, heads
    return cached(pk(p, "tracked"), build, 60, swr=True)


def build_moduledoc(p, mid):
    """The content of a module page: description paragraphs (docs/modules/<id>.txt next to the module map, else the map's `desc`), the sanitised
    drawing (.svg), the Workflow elements to highlight (.hl), the key files grouped and the documents naming the module. Anything missing is None / []."""
    stem = L.module_stem(mid)
    if not stem:
        raise ValueError(f"unusable module id {mid!r}")
    cfgm = load_modules(p)
    folder = pathlib.Path(p["modules"]).expanduser().parent / "modules" if p.get("modules") else None

    def read(ext):
        try:
            return (folder / f"{stem}.{ext}").read_text() if folder else None
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as ex:
            log.warning("module page %s.%s unreadable: %s", stem, ext, ex)
            return None
    txt, svg_raw, hl = read("txt"), read("svg"), read("hl")
    desc = next((m.get("desc") for m in (cfgm or {}).get("modules", []) if m.get("id") == mid), None)
    svg = L.sanitise_svg(svg_raw) if svg_raw else None
    if svg_raw and svg is None:
        log.warning("module page %s.svg refused by the sanitiser", stem)
    files = docs = rel = None
    if p.get("git"):
        tracked, heads = tracked_and_heads(p)
        files = L.group_files(L.module_files(tracked, (cfgm or {}).get("assign", []), mid)) if cfgm else None
        docs = L.module_docs(heads, mid, f"https://github.com/{p['repo']}")
        if cfgm:
            rel = L.module_relations(cached(pk(p, "importedges"), lambda: import_edges_of(p, tracked, cfgm["assign"]), 120, swr=True), mid)
    log.info("module page %s: text %s, svg %s, hl %s", mid, bool(txt), bool(svg), bool(hl))
    return dict(id=mid, paragraphs=L.module_paragraphs(txt, desc), svg=svg, hl=L.hl_ids(hl), files=files, docs=docs, relations=rel)


# ---------- offline copy from a backup (PC-63; layout: docs/contracts/backup.md) ----------
MANIFEST = "backup.json"
BACKUP_PATHS = ("modules", "git", "code", "phases")  # project settings that name a file or folder inside the backup


def unpack_backup(src, dest):
    """Unpack the backup archive `src` (tar, any compression) into the folder `dest`, or copy it when `src` is a folder -> the backup root
    (the folder holding backup.json: dest itself or its one subfolder). Members outside dest, links and devices are refused (ValueError)."""
    src, dest = pathlib.Path(src).expanduser(), pathlib.Path(dest)
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
    else:
        with tarfile.open(src) as tf:
            for m in tf.getmembers():
                if not (m.isfile() or m.isdir()) or m.name.startswith("/") or ".." in pathlib.PurePosixPath(m.name).parts:
                    raise ValueError(f"{src}: refused archive member {m.name!r}")
            tf.extractall(dest, filter="data")
    if (dest / MANIFEST).is_file():
        return dest
    subs = [d for d in dest.iterdir() if (d / MANIFEST).is_file()]
    if len(subs) != 1:
        raise ValueError(f"{src}: no {MANIFEST} at the top of the backup")
    return subs[0]


def open_backup(root):
    """The backup folder -> (project dict with its paths inside the backup, created epoch). ValueError when the manifest is unusable."""
    root = pathlib.Path(root).resolve()
    try:
        m = json.loads((root / MANIFEST).read_text())
        e = dict(m["project"])
    except (OSError, ValueError, KeyError, TypeError) as ex:
        raise ValueError(f"{root / MANIFEST}: unusable manifest: {ex}")
    (p,), _ = projects_from_file(dict(projects=[{k: v for k, v in e.items() if k not in BACKUP_PATHS + ("meta", "stats_cache")}]))
    for k in BACKUP_PATHS:
        if e.get(k):
            f = (root / e[k]).resolve()
            if not f.is_relative_to(root):
                raise ValueError(f"{MANIFEST}: {k} {e[k]!r} lies outside the backup")
            p[k] = str(f) if f.exists() else None
    created = m.get("created")
    return p, created if isinstance(created, (int, float)) else (root / MANIFEST).stat().st_mtime


def backup_inventory(p):
    """The items logic.OFFLINE_PANES asks for that are present for project p (cache folder, data folder, snapshot, clone, module map)."""
    have = set()
    pre = re.sub(r"[^A-Za-z0-9_.-]", "_", pk(p, ""))
    if CACHE_DIR and CACHE_DIR.is_dir():
        have |= {"cache:" + f.stem[len(pre):] for f in CACHE_DIR.glob(pre + "*.json")}
    d = DATA_DIR / p["name"]
    if d.is_dir():
        have |= {"data:" + f.relative_to(d).as_posix() for f in d.rglob("*") if f.is_file()}
    if p.get("code") and (pathlib.Path(p["code"]) / "metrics.json").is_file():
        have.add("code")
    if p.get("git") and pathlib.Path(p["git"]).is_dir():
        have.add("git")
    try:
        if not L.rules_config(load_modules(p)) and "cache:rules" not in have:
            have.add("norules")
    except (OSError, ValueError):
        pass
    return have


def offline_info():
    """What the page's banner and the restore test need in an offline copy: the time of the data, the refused GitHub calls and the gaps."""
    return dict(created=OFFLINE["created"], ghAttempts=OFFLINE.get("gh_attempts", 0), gaps=L.backup_gaps(backup_inventory(proj())))


def start_offline(src):
    """--offline: unpack the backup (an archive into a temporary folder; a folder is used in place, never written to) and point the data and cache
    folders into it -> the project. No GitHub call is made from here on."""
    global DATA_DIR, CACHE_DIR
    root = pathlib.Path(src).expanduser()
    if not root.is_dir():
        root = unpack_backup(root, tempfile.mkdtemp(prefix="cockpit-offline-"))
    elif not (root / MANIFEST).is_file():
        raise ValueError(f"{root}: no {MANIFEST} in this folder")
    p, created = open_backup(root)
    DATA_DIR, CACHE_DIR = root.resolve() / "data", root.resolve() / "cache"
    OFFLINE.update(root=str(root), created=created, gh_attempts=0)
    gaps = L.backup_gaps(backup_inventory(p))
    log.info("offline copy of %s from %s (data from %s); %s", p["name"], root, time.strftime("%Y-%m-%d %H:%M", time.localtime(created)),
             "every pane has its data" if not gaps else "gaps: " + "; ".join(f"{g['pane']} lacks {', '.join(g['missing'])}" for g in gaps))
    return p


# ---------- server ----------
def redact(line):
    """Keep the access token out of the request log."""
    return re.sub(r"([?&]t=)[^ &]+", r"\1<token>", line)


class Server(ThreadingHTTPServer):
    request_queue_size = 128  # the page opens about ten connections at once; the default backlog of 5 dropped some of them
    daemon_threads = True


class H(BaseHTTPRequestHandler):
    token = ""

    def log_message(self, fmt, *a):
        log.info("%s %s", self.address_string(), redact(fmt % a))

    def send(self, code, body=b"", ctype="application/json", headers=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return "t" in c and secrets.compare_digest(c["t"].value, self.token)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":   # no data, no token: only that the server answers and whether it is still warming up (the deploy's health check, PC-84)
            return self.send(200, dict(ok=True, warming=bool(WARM["on"])))
        if q.get("t", [""])[0] and secrets.compare_digest(q["t"][0], self.token):
            return self.send(303, headers=[("Set-Cookie", f"t={self.token}; HttpOnly; SameSite=Strict; Path=/"), ("Location", u.path)])
        if not self.authed():
            return self.send(403, dict(error="forbidden"))
        try:
            if u.path in ("/", "/index.html"):
                return self.send(200, (HERE / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/projects":
                return self.send(200, dict(default=DEFAULT, projects=[dict(name=n, repo=p["repo"]) for n, p in PROJECTS.items()], **({"offline": offline_info()} if OFFLINE else {})))
            name_q = q.get("project", [""])[0]
            if u.path.startswith("/report/") and not name_q:  # files a report page loads carry no query: take the page's own
                name_q = parse_qs(urlparse(self.headers.get("Referer", "")).query).get("project", [""])[0]
            p = proj(name_q or None)
            if u.path == "/api/state":
                return self.send(200, get_state_served(p, fresh=bool(q.get("fresh"))))
            if u.path == "/api/task":
                try:
                    return self.send(200, get_task(p, q.get("n", [""])[0]))
                except ValueError as ex:
                    return self.send(400, dict(error=str(ex)))
            if u.path == "/api/questions":
                return self.send(200, build_questions(fresh=bool(q.get("fresh"))))
            if u.path == "/api/timeline":
                return self.send(200, get_timeline(p, fresh=bool(q.get("fresh"))))
            if u.path == "/api/settings":
                return self.send(200, get_settings(p))
            if u.path == "/api/cardsettings":
                return self.send(200, get_card_settings(p))
            if u.path == "/api/queue":
                return self.send(200, get_queue(p))
            if u.path == "/api/queuepoll":
                return self.send(200, get_queue_poll(p, since=_since(q)))
            if u.path == "/api/ci":
                return self.send(200, get_ci(p))
            if u.path == "/api/usage":
                return self.send(200, usage_served(p))
            if u.path == "/api/compliance":
                return self.send(200, build_compliance())
            if u.path == "/api/actionlog":
                return self.send(200, read_action_log(p, *(q.get(k, [""])[0] for k in ("action", "outcome", "task"))))
            if u.path == "/compliance-report":
                f = compliance_report_file(p)
                return self.send(200, f.read_bytes(), "text/plain; charset=utf-8") if f else self.send(404, dict(error="no compliance report yet"))
            if u.path == "/api/rules":
                return self.send(200, get_rules(p))
            if u.path == "/api/rulesprs":
                return self.send(200, get_rule_prs(p, fresh=bool(q.get("fresh"))))
            if u.path == "/api/code":
                return self.send(200, code_snapshot(p))
            if u.path == "/api/moduledoc":
                mid = q.get("id", [""])[0]
                if not L.module_stem(mid):
                    return self.send(400, dict(error="bad module id"))
                return self.send(200, cached(pk(p, "moduledoc:" + mid), lambda: build_moduledoc(p, mid), 60, swr=True, persist=True))
            if u.path == "/api/cardmeta":
                return self.send(200, cached(pk(p, "cardmeta"), lambda: card_metadata(p), 60, swr=True, persist=True))
            name = u.path.removeprefix("/code/").removesuffix(".svg")
            if u.path.startswith("/code/") and name in SVGS and p.get("code"):
                return self.send(200, (pathlib.Path(p["code"]).expanduser() / SVGS[name]).read_bytes(), "image/svg+xml")
            if u.path.startswith("/report/") and p.get("code"):
                f = L.report_file(p["code"], u.path.removeprefix("/report/"))
                if f:
                    kind = {".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".css": "text/css"}[f.suffix]
                    return self.send(200, f.read_bytes(), kind)
            self.send(404, dict(error="not found"))
        except UnknownProject as ex:
            self.send(404, dict(error=ex.args[0]))
        except Exception as ex:
            log.exception("GET %s failed", u.path)
            self.send(502, dict(error=str(ex) + ("" if isinstance(ex, GitHubUnavailable) else " (no earlier copy to show; Refresh tries again now)")))

    def do_POST(self):
        u = urlparse(self.path)
        if not self.authed() or u.path not in ("/api/act", "/api/event"):
            return self.send(403, dict(error="forbidden"))
        try:
            form = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self.send(400, dict(error="bad JSON"))
        if not form.get("project") and parse_qs(u.query).get("project"):
            form["project"] = parse_qs(u.query)["project"][0]
        if not isinstance(form, dict):
            return self.send(400, dict(error="bad JSON"))
        self.send(*(page_events(form) if u.path == "/api/event" else do_act(form)))


def check_manager(projects, steer):
    """Warn when gh acts as someone else than a project's Manager (a gh call: run after the server listens)."""
    for p in projects:
        if steer and gh_user() and gh_user() != p["manager"]:
            log.warning("%s: gh is logged in as %s but the Manager is %s: protected labels set now would be flagged unauthorised", p["name"], gh_user(), p["manager"])


def serve(host, port, warm_up):
    """Listen first, then warm up in the background (PC-131): a restarted service answers the page at once from its disk copies, marked
    `warming` until warm_up() returns, instead of refusing connections for the whole warm-up. -> the server (the caller runs serve_forever)."""
    WARM.update(on=True, since=time.time())
    srv = Server((host, port), H)

    def go():
        try:
            warm_up()
        except Exception:
            log.exception("warm-up failed")
        finally:
            WARM["on"] = False
    threading.Thread(target=go, daemon=True).start()
    return srv


def _since(q):
    """The `since` query value (the fetchedAt of the page's copy) as a number; 0 when missing or not a number."""
    try:
        return float(q.get("since", ["0"])[0])
    except ValueError:
        return 0


def load_token(path=None):
    """A fresh random token, or the one kept in `path` (created mode 0600 if missing)."""
    if not path:
        return secrets.token_urlsafe(16)
    p = pathlib.Path(path).expanduser()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(16))
        log.info("created token file %s", p)
    return p.read_text().strip()


def resolve(a):
    """Parsed command line (dict) -> (projects, default name). Single-project flags and --projects exclude each other."""
    if a.get("projects"):
        mixed = [f"--{k}" for k in PROJECT_KEYS if a.get(k) is not None]
        if mixed:
            raise ValueError(f"--projects replaces {', '.join(mixed)}: put them into the projects file (only --port, --lan, --read-only, --token-file, --simulate-orchestrator stay on the command line)")
        try:
            data = json.load(open(a["projects"]))
        except (OSError, ValueError) as ex:
            raise ValueError(f"--projects: cannot read {a['projects']}: {ex}")
        return projects_from_file(data)
    if not a.get("repo"):
        raise ValueError("give --repo OWNER/REPO or --projects FILE")
    p = project_from_flags(a)
    return [p], p["name"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--projects", help="JSON {default, projects:[{name, repo, project?, manager?, modules?, git?, code?, code_collect?, compliance?, phases?, meta?}]}: serve several projects (replaces the single-project flags)")
    ap.add_argument("--repo", help="OWNER/REPO")
    ap.add_argument("--manager", help="the one login whose protected labels count (default: the repo owner)")
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--lan", action="store_true", help="bind 0.0.0.0 (plain HTTP + token)")
    ap.add_argument("--project", type=int, help="GitHub Project number (owner = repo owner): Module, Priority hint, Status, token fields")
    ap.add_argument("--git", help="local clone: commit history for the timeline, docs/project/* for gaps, documents and events")
    ap.add_argument("--modules", help="JSON {modules:[{id,name,docs?}], assign:[{path,module}]}")
    ap.add_argument("--phases", help="extra timeline.json ({module, events:[...]}) merged into the timeline")
    ap.add_argument("--code", help="code snapshot directory: metrics.json and erd|dataflow|deps|js.svg (see README)")
    ap.add_argument("--compliance", help="the compliance check command (PC-108), run in the --git clone; it writes docs/compliance/compliance-report.md")
    ap.add_argument("--code-collect", help="the code snapshot's collector command, run by tools/code_snapshot_refresh.py when the snapshot is stale")
    ap.add_argument("--token-file", help="keep the access token in this file (created 0600 if missing), so the URL survives restarts")
    ap.add_argument("--cache-dir", help="keep the slow answers on disk here (default: <data dir>/cache) so that a restarted service shows data at once; 'none' = memory only")
    ap.add_argument("--read-only", action="store_true", help="refuse every action (default: actions are enabled)")
    ap.add_argument("--steer", action="store_true", help=argparse.SUPPRESS)  # actions are the default now; kept so that existing service files still start
    ap.add_argument("--simulate-orchestrator", action="store_true", help="demo buttons: 'takes it' and 'completes it'")
    ap.add_argument("--offline", metavar="BACKUP", help="run a read-only copy of one project from its backup (archive or folder, see docs/contracts/backup.md): no GitHub call at all")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if a.offline:
            mixed = [k for k in ("projects", "cache_dir", "simulate_orchestrator", *PROJECT_KEYS) if getattr(a, k) not in (None, False)]
            if mixed:
                raise ValueError(f"--offline takes the project from the backup: drop {', '.join('--' + x.replace('_', '-') for x in mixed)}")
            projects = [start_offline(a.offline)]
            default = projects[0]["name"]
        else:
            projects, default = resolve(vars(a))
    except ValueError as ex:
        ap.error(str(ex))
    register(projects, default)
    steer = not (a.read_only or a.offline)
    CFG.update(steer=steer, simulate=a.simulate_orchestrator)
    for p in projects:
        log.info("project %s: repo=%s project=%s manager=%s mode=%s", p["name"], p["repo"], p["project"], p["manager"], "STEERING" if steer else "READ-ONLY")
    global CACHE_DIR
    if not a.offline:
        CACHE_DIR = None if a.cache_dir == "none" else pathlib.Path(a.cache_dir).expanduser() if a.cache_dir else store_layout.cache_dir(CACHE_SCHEMA, DATA_DIR)
    H.token = load_token(a.token_file)
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    if a.lan:
        log.warning("--lan: plain HTTP, the token travels unencrypted; trusted network only")
    srv = serve(host, a.port, lambda: (check_manager(projects, steer), warm()))
    log.info("open http://%s:%d/?t=%s  (acting as the gh user; manager=%s)", host, a.port, "<token file>" if a.token_file else H.token, ", ".join(f"{p['name']}={p['manager']}" for p in projects))
    srv.serve_forever()


if __name__ == "__main__":
    main()
