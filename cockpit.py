#!/usr/bin/env python3
"""Project cockpit v1: a JSON API + one web page over GitHub (stdlib + the `gh` CLI; no token stored, acts as the gh login).
Every action is exactly one GitHub operation (labels, sub-issues, dependencies, a repo variable); the only local copy is the cache of the slow answers (--cache-dir)."""
import argparse
import json
import logging
import importlib.util
import os
import pathlib
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
from dispatcher.core import DEFAULTS as SETTING_DEFAULTS, load_settings
from dispatcher.runtime import Config as RuntimeConfig

log = logging.getLogger("cockpit")
HERE = pathlib.Path(__file__).parent
TYPES = ["feature", "bug", "decision", "research", "doc", "devops", "test"]
# Global switches (steer, simulate) live in CFG. A project is a dict of PROJECT_KEYS plus its name; PROJECTS maps name -> project.
# Without any registered project (tests, embedding) CFG itself doubles as the one project.
CFG = dict(repo=None, manager=None, steer=False, simulate=False, project=None, git=None, modules=None, phases=None, code=None, code_collect=None)
PROJECT_KEYS = ("repo", "manager", "project", "git", "modules", "phases", "code", "code_collect")
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
                **{k: a.get(k) for k in PROJECT_KEYS[2:-1]}, code_collect=L.collect_command(a.get("code_collect")))


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
GQL = """query($o:String!,$r:String!,$after:String,$n:Int!){repository(owner:$o,name:$r){issues(first:$n,after:$after,orderBy:{field:CREATED_AT,direction:ASC}){
 pageInfo{hasNextPage endCursor} nodes{number databaseId title url body state stateReason createdAt updatedAt closedAt author{login}
 labels(first:50){nodes{name}} assignees(first:5){nodes{login}} parent{number} blockedBy(first:50){nodes{number}}
 comments(last:10){nodes{body createdAt author{login}}}
 """ + TIMELINE + "}}}}"


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


def _disk_path(key):
    return CACHE_DIR / (re.sub(r"[^A-Za-z0-9_.-]", "_", key) + ".json") if CACHE_DIR else None


def _disk_load(key):
    f = _disk_path(key)
    try:
        return json.loads(f.read_text()) if f else None
    except (OSError, ValueError):
        return None


def _disk_save(key, v):
    f = _disk_path(key)
    if not f or OFFLINE:  # the restored backup is never written to
        return
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(v))
        os.replace(tmp, f)
    except (OSError, TypeError, ValueError) as ex:
        log.warning("cache %s not written to disk: %s", key, ex)


def drop_cache(prefix):
    """Forget the memory and disk copies of every key starting with `prefix` (after an action: nothing older may be served)."""
    with _lock:
        for k in [k for k in _cache if k.startswith(prefix)]:
            del _cache[k]
    if CACHE_DIR and CACHE_DIR.is_dir():
        for f in CACHE_DIR.glob(re.sub(r"[^A-Za-z0-9_.-]", "_", prefix) + "*.json"):
            f.unlink(missing_ok=True)


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
    cfgm = load_modules(p)
    tasks, mods = L.derive(issues, events, project, p["manager"], (cfgm or {}).get("modules", []), grow=not cfgm)
    for t in tasks:
        t["modDl"] = L.module_deadline(deadlines, t["module"])  # (date, module it is set on): the sub-module's, else the module's
    keys = {i["number"]: L.split_title(i["title"], i["number"])[0] for i in issues}
    nums = {t["number"]: t for t in tasks}
    proposals = [dict(p, n=i["number"], key=keys[i["number"]]) for i in issues for p in L.parse_proposals(i["comments"])]
    log.info("state: %d tasks, %d events, %d open proposals in %.1fs", len(tasks), len(events), len(proposals), time.time() - t0)
    return dict(
        today=dt.date.today().isoformat(), tasks=tasks, questions=L.parse_questions(issues, p.get("name", "")), modules=mods, modDeadline=deadlines, proposals=proposals,
        events=L.task_events(events, keys, p["manager"])[:400], unauthorised=sum(len(t["flags"]) for t in nums.values()),
        source=dict(name=p.get("name", ""), repo=p["repo"], steering=CFG["steer"], simulate=CFG["simulate"] and CFG["steer"], project=p["project"],
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
    if a == "release":
        if t["stage"] != "embryo":
            raise ValueError(f"{t['key']} is {t['stage']}; only embryos can be released")
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
    """Run the planned gh calls in order; '{id}' is filled from the previous call's JSON; `label create` may fail (exists)."""
    last = {}
    for op in ops:
        op = [x.replace("{id}", str(last.get("id", ""))) for x in op]
        try:
            out = run(op)
        except RuntimeError:
            if op[0] == "label":
                continue
            raise
        try:
            last = json.loads(out) if out.strip().startswith("{") else {}
        except ValueError:
            last = {}


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
                backup=L.backup_status(_read_text(d / L.BACKUP_STATUS_FILE)))


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
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(values, fh, indent=2)
    os.replace(tmp, f)
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
    """The selected project's report plus `monthly`: every project the Cockpit serves per month (L.monthly_by_project), from each one's
    proxy file and processing log and from the transcript and statistics-cache files of all their data folders."""
    rep = _usage_one(p, data_dir)
    d = (data_dir or DATA_DIR) / (p.get("name") or p["repo"].split("/")[-1])
    rep["sessions"] = L.transcript_report(_read_text(d / TRANSCRIPT_FILE), rep["quota"], _read_text(d / AGGREGATE_FILE))
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
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(values, fh, indent=2)
    os.replace(tmp, f)
    log.info("dispatcher settings saved for %s: %s", p.get("name", ""), values)
    return "settings saved"


def do_act(form, run=run_gh, state=None, p=None):
    """-> (http status, body). 403 when the app is read-only; acts on the project named in form["project"] (default project otherwise)."""
    if not CFG["steer"]:
        log.warning("refused action %s: read-only", form.get("action"))
        return 403, dict(error="read-only: this is an offline copy from a backup" if OFFLINE else "read-only: this instance was started with --read-only")
    try:
        p = p or proj(form.get("project"))
        if form.get("action") == "settings":  # a local file, not a GitHub operation; same guard, same errors
            return 200, dict(ok=True, msg=save_settings(p, form))
        if form.get("action") == "cardsettings":  # same: a local file, same guard
            return 200, dict(ok=True, msg=save_card_settings(p, form))
        S = state or get_state(p=p)  # the cached state: an action does not bypass the hour (owner F-701)
        ops, msg = plan_action(form, S, p["repo"], CFG["simulate"])
        execute(ops, run)
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
    return 200, dict(ok=True, msg=msg)


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
    k, at = pk(p, "timeline"), S["source"]["fetchedAt"]
    build = lambda: dict(build_timeline(S, p, fresh), stateAt=at)
    if fresh:
        _expire(k)
        log.info("owner Refresh of the timeline of %s", p.get("name", ""))
    tl = cached(k, build, 30, swr=not fresh, persist=True, disk_fresh=DISK_FRESH_S)
    return tl if tl.get("stateAt") == at else cached(k, build, -1, persist=True)


def stale_note(p, now=None):
    """None, or why the state served for p is older than it should be: dict(age_s, error, until, rate_limited) of the failed refresh (until = when GitHub is tried again)."""
    f, now = _failures.get(pk(p, "state")), now or time.time()
    v = _cache.get(pk(p, "state"), (0, None))[1]
    if not f or not v:
        return None
    return dict(age_s=max(0, round(now - v["source"]["fetchedAt"])), error=f["error"], until=f["until"], rate_limited=f["rate_limited"])


def get_state_served(p, fresh=False):
    """The state for the page: `stale` is true while a newer copy is being fetched in the background, so the page asks again shortly;
    `staleNote` is set when the last refresh failed and an older copy is shown (its age, the reason and when GitHub is tried again)."""
    S = get_state(fresh=fresh, p=p)
    if OFFLINE:  # the copy was taken from a steering instance: steering is off here whatever the cached state says
        S = dict(S, source=dict(S.get("source", {}), steering=False, simulate=False))
    if "modules" in S:
        S = dict(S, modules=ordered(p, S["modules"]))
    return dict(S, stale=refreshing(pk(p, "state")), staleNote=stale_note(p), refreshAfter=S.get("source", {}).get("fetchedAt", 0) + GH_REFRESH_S)


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
            if u.path == "/api/usage":
                return self.send(200, usage_served(p))
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
        if not self.authed() or u.path != "/api/act":
            return self.send(403, dict(error="forbidden"))
        try:
            form = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self.send(400, dict(error="bad JSON"))
        if not form.get("project") and parse_qs(u.query).get("project"):
            form["project"] = parse_qs(u.query)["project"][0]
        self.send(*do_act(form))


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
    ap.add_argument("--projects", help="JSON {default, projects:[{name, repo, project?, manager?, modules?, git?, code?, code_collect?, phases?, meta?}]}: serve several projects (replaces the single-project flags)")
    ap.add_argument("--repo", help="OWNER/REPO")
    ap.add_argument("--manager", help="the one login whose protected labels count (default: the repo owner)")
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--lan", action="store_true", help="bind 0.0.0.0 (plain HTTP + token)")
    ap.add_argument("--project", type=int, help="GitHub Project number (owner = repo owner): Module, Priority hint, Status, token fields")
    ap.add_argument("--git", help="local clone: commit history for the timeline, docs/project/* for gaps, documents and events")
    ap.add_argument("--modules", help="JSON {modules:[{id,name,docs?}], assign:[{path,module}]}")
    ap.add_argument("--phases", help="extra timeline.json ({module, events:[...]}) merged into the timeline")
    ap.add_argument("--code", help="code snapshot directory: metrics.json and erd|dataflow|deps|js.svg (see README)")
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
        if steer and gh_user() and gh_user() != p["manager"]:
            log.warning("%s: gh is logged in as %s but the Manager is %s: protected labels set now would be flagged unauthorised", p["name"], gh_user(), p["manager"])
    global CACHE_DIR
    if not a.offline:
        CACHE_DIR = None if a.cache_dir == "none" else pathlib.Path(a.cache_dir or DATA_DIR / "cache").expanduser()
    H.token = load_token(a.token_file)
    threading.Thread(target=warm, daemon=True).start()
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    if a.lan:
        log.warning("--lan: plain HTTP, the token travels unencrypted; trusted network only")
    log.info("open http://%s:%d/?t=%s  (acting as the gh user; manager=%s)", host, a.port, "<token file>" if a.token_file else H.token, ", ".join(f"{p['name']}={p['manager']}" for p in projects))
    Server((host, a.port), H).serve_forever()


if __name__ == "__main__":
    main()
