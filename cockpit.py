#!/usr/bin/env python3
"""Project cockpit v1: a JSON API + one web page over GitHub (stdlib + the `gh` CLI; no token stored, acts as the gh login).
Every action is exactly one GitHub operation (labels, sub-issues, dependencies, a repo variable); nothing is stored locally."""
import argparse
import json
import logging
import os
import pathlib
import re
import secrets
import subprocess
import threading
import time
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import logic as L

log = logging.getLogger("cockpit")
HERE = pathlib.Path(__file__).parent
TYPES = ["feature", "bug", "decision", "research", "doc", "devops", "test"]
CFG = dict(repo=None, manager=None, steer=False, simulate=False, project=None, git=None, modules=None, phases=None, code=None)
SVGS = {"erd": "erd.svg", "dataflow": "dataflow.svg", "deps": "deps.svg", "js": "js.svg"}


# ---------- gh ----------
def run_gh(args):
    """Run `gh <args>` and return stdout; raises RuntimeError with gh's message on failure."""
    log.info("gh %s", " ".join(a if len(a) < 60 else a[:57] + "..." for a in args))
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or f"gh exited {r.returncode}")
    return r.stdout


GQL = """query($o:String!,$r:String!,$after:String){repository(owner:$o,name:$r){issues(first:100,after:$after,orderBy:{field:CREATED_AT,direction:ASC}){
 pageInfo{hasNextPage endCursor} nodes{number databaseId title url body state stateReason createdAt closedAt author{login}
 labels(first:50){nodes{name}} assignees(first:5){nodes{login}} parent{number} blockedBy(first:50){nodes{number}}
 comments(last:10){nodes{body createdAt author{login}}}
 timelineItems(last:100,itemTypes:[LABELED_EVENT,UNLABELED_EVENT,ASSIGNED_EVENT,CLOSED_EVENT]){nodes{__typename
  ... on LabeledEvent{createdAt actor{login} label{name}} ... on UnlabeledEvent{createdAt actor{login} label{name}}
  ... on AssignedEvent{createdAt actor{login} assignee{... on User{login}}} ... on ClosedEvent{createdAt actor{login}}}}}}}}"""


def fetch_issues():
    """All issues of the repo in 100-issue GraphQL pages -> (raw issues, events). One call per 100 issues."""
    owner, name = CFG["repo"].split("/")
    issues, events, after = [], [], None
    while True:
        args = ["api", "graphql", "-f", f"query={GQL}", "-f", f"o={owner}", "-f", f"r={name}"] + (["-f", f"after={after}"] if after else [])
        page = json.loads(run_gh(args))["data"]["repository"]["issues"]
        for n in page["nodes"]:
            issues.append(L.norm_issue(n))
            events += L.norm_events(n)
        if not page["pageInfo"]["hasNextPage"]:
            return issues, events
        after = page["pageInfo"]["endCursor"]


def fetch_project():
    if not CFG["project"]:
        return {}
    owner = CFG["repo"].split("/")[0]
    d = json.loads(run_gh(["project", "item-list", str(CFG["project"]), "--owner", owner, "--format", "json", "--limit", "1000"]))
    return L.parse_project(d["items"], CFG["repo"])


def fetch_module_deadlines():
    try:
        return L.parse_module_deadlines(run_gh(["api", f"repos/{CFG['repo']}/actions/variables/MODULE_DEADLINES", "--jq", ".value"]))
    except RuntimeError as ex:
        log.info("no MODULE_DEADLINES variable (%s)", str(ex)[:80])
        return {}


_cache, _lock = {}, threading.RLock()  # re-entrant: build_state() calls cached() (gh_user) while cached() holds the lock


def cached(key, fn, ttl):
    with _lock:
        t, v = _cache.get(key, (0, None))
        if time.time() - t > ttl:
            v = fn()
            _cache[key] = (time.time(), v)
        return v


def gh_user():
    return cached("user", lambda: run_gh(["api", "user", "--jq", ".login"]).strip(), 3600)


# ---------- state ----------
def load_modules():
    return json.load(open(CFG["modules"])) if CFG.get("modules") else None


def build_state():
    t0 = time.time()
    with ThreadPoolExecutor(4) as ex:
        fi, fp, fd = ex.submit(fetch_issues), ex.submit(fetch_project), ex.submit(fetch_module_deadlines)
        (issues, events), project, deadlines = fi.result(), fp.result(), fd.result()
    cfgm = load_modules()
    tasks, mods = L.derive(issues, events, project, CFG["manager"], (cfgm or {}).get("modules", []), grow=not cfgm)
    for m in mods:
        m["gaps"], m["ndocs"] = L.module_facts(L.module_dir(CFG["git"], m)) if CFG["git"] and not m.get("virtual") else (None, None)
    keys = {i["number"]: L.split_title(i["title"], i["number"])[0] for i in issues}
    nums = {t["number"]: t for t in tasks}
    proposals = [dict(p, n=i["number"], key=keys[i["number"]]) for i in issues for p in L.parse_proposals(i["comments"])]
    log.info("state: %d tasks, %d events, %d open proposals in %.1fs", len(tasks), len(events), len(proposals), time.time() - t0)
    return dict(
        today=dt.date.today().isoformat(), tasks=tasks, modules=mods, modDeadline=deadlines, proposals=proposals,
        events=L.task_events(events, keys, CFG["manager"])[:400], unauthorised=sum(len(t["flags"]) for t in nums.values()),
        source=dict(repo=CFG["repo"], steering=CFG["steer"], simulate=CFG["simulate"] and CFG["steer"], project=CFG["project"],
                    manager=CFG["manager"], user=gh_user(), fetched=time.strftime("%H:%M:%S")))


def get_state(fresh=False):
    if fresh:
        _cache.pop("state", None)
    return cached("state", build_state, 5)


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
    openstage = ("embryo", "released", "in_progress")
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
        if t["stage"] not in ("released", "in_progress"):
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
            if t["stage"] != "released" or any(b for b in t["blockedBy"] if next(x for x in S["tasks"] if x["key"] == b)["stage"] != "completed"):
                raise ValueError(f"{t['key']} is not a released, unblocked task")
            return [["issue", "edit", n, "-R", repo, "--add-assignee", "@me"], ["issue", "comment", n, "-R", repo, "--body", "Orchestrator (simulated) takes this task."]], f"{t['key']} in progress"
        if t["stage"] != "in_progress":
            raise ValueError(f"{t['key']} is not in progress")
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


def do_act(form, run=run_gh, state=None):
    """-> (http status, body). 403 when the app is read-only."""
    if not CFG["steer"]:
        log.warning("refused action %s: read-only", form.get("action"))
        return 403, dict(error="read-only: this repo is not steerable (start with --steer)")
    try:
        S = state or get_state(fresh=True)
        ops, msg = plan_action(form, S, CFG["repo"], CFG["simulate"])
        execute(ops, run)
    except PermissionError as ex:
        return 403, dict(error=str(ex))
    except ValueError as ex:
        log.warning("refused %s: %s", form.get("action"), ex)
        return 400, dict(error=str(ex))
    except RuntimeError as ex:
        log.error("action %s failed: %s", form.get("action"), ex)
        return 502, dict(error=f"GitHub: {ex}")
    _cache.clear()
    log.info("action ok: %s -> %s", form.get("action"), msg)
    return 200, dict(ok=True, msg=msg)


# ---------- timeline / code ----------
def load_commits():
    if CFG.get("git"):
        txt = subprocess.run(["git", "-C", CFG["git"], "log", "--name-only", "--date=iso", "--format=@%H|%ad|%s"], capture_output=True, text=True, check=True).stdout
        return L.parse_git_full(txt)
    since = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    raw = json.loads(run_gh(["api", f"repos/{CFG['repo']}/commits?since={since}T00:00:00Z&per_page=100"]))
    return [dict(sha=c["sha"], date=dt.date.fromisoformat(c["commit"]["author"]["date"][:10]), subject=c["commit"]["message"].splitlines()[0], files=[]) for c in raw]


def build_timeline(S):
    t0 = time.time()
    cfgm = load_modules()
    assign = cfgm["assign"] if cfgm else []
    mod_of = {t["key"]: t["module"] for t in S["tasks"]}
    url_of = {t["key"]: t["url"] for t in S["tasks"]}
    feed = L.group_commits(cached("commits", load_commits, 60), assign, CFG["repo"]) + L.issue_entries(S["tasks"])
    feed += [dict(d=e["t"], type="task event", mod=mod_of.get(e["key"], "unassigned"), text=f"{e['actor']} {e['key']}: {e['text']}", links=[(e["key"], url_of.get(e["key"], ""))])
             for e in S["events"] if e["kind"] != "closed"]
    if CFG["git"]:
        for m in S["modules"]:
            if not m.get("virtual"):
                feed += L.static_entries(L.module_dir(CFG["git"], m), m["id"], CFG["repo"])
    if CFG["phases"]:
        pj = json.load(open(CFG["phases"]))
        feed += [dict(d=e["date"], type=e["type"], mod=pj.get("module", "unassigned"), text=e["text"], links=[l for l in (L.ref_link(r, CFG["repo"]) for r in e.get("refs", []))]) for e in pj["events"]]
    seen, out = set(), []
    for e in sorted(feed, key=lambda e: e["d"], reverse=True):
        k = (e["d"], e["type"], e["mod"], e["text"])
        if k not in seen:
            seen.add(k)
            out.append(dict(e, links=[list(l) for l in e["links"]]))
    log.info("timeline: %d entries in %.2fs", len(out), time.time() - t0)
    return dict(entries=out[:400], total=len(out))


def code_snapshot():
    try:
        d = pathlib.Path(CFG["code"]).expanduser()
        return dict(wired=True, **L.code_view(json.load(open(d / "metrics.json"))), diagrams=[k for k, f in SVGS.items() if (d / f).exists()])
    except (OSError, ValueError, TypeError) as ex:
        log.info("code snapshot not wired (%s)", ex)
        return dict(wired=False, modules={}, diagrams=[])


# ---------- server ----------
def redact(line):
    """Keep the access token out of the request log."""
    return re.sub(r"([?&]t=)[^ &]+", r"\1<token>", line)


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
            if u.path == "/api/state":
                return self.send(200, get_state(fresh=bool(q.get("fresh"))))
            if u.path == "/api/timeline":
                return self.send(200, build_timeline(get_state()))
            if u.path == "/api/code":
                return self.send(200, code_snapshot())
            name = u.path.removeprefix("/code/").removesuffix(".svg")
            if u.path.startswith("/code/") and name in SVGS:
                return self.send(200, (pathlib.Path(CFG["code"]).expanduser() / SVGS[name]).read_bytes(), "image/svg+xml")
            self.send(404, dict(error="not found"))
        except Exception as ex:
            log.exception("GET %s failed", u.path)
            self.send(502, dict(error=str(ex)))

    def do_POST(self):
        if not self.authed() or urlparse(self.path).path != "/api/act":
            return self.send(403, dict(error="forbidden"))
        try:
            form = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self.send(400, dict(error="bad JSON"))
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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="OWNER/REPO")
    ap.add_argument("--manager", help="the one login whose protected labels count (default: the repo owner)")
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--lan", action="store_true", help="bind 0.0.0.0 (plain HTTP + token)")
    ap.add_argument("--project", type=int, help="GitHub Project number (owner = repo owner): Module, Priority hint, Status, token fields")
    ap.add_argument("--git", help="local clone: commit history for the timeline, docs/project/* for gaps, documents and events")
    ap.add_argument("--modules", help="JSON {modules:[{id,name,docs?}], assign:[{path,module}]}")
    ap.add_argument("--phases", help="extra timeline.json ({module, events:[...]}) merged into the timeline")
    ap.add_argument("--code", help="code snapshot directory: metrics.json and erd|dataflow|deps|js.svg (see README)")
    ap.add_argument("--token-file", help="keep the access token in this file (created 0600 if missing), so the URL survives restarts")
    ap.add_argument("--steer", action="store_true", help="enable actions (default: read-only)")
    ap.add_argument("--simulate-orchestrator", action="store_true", help="demo buttons: 'takes it' and 'completes it'")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    steer = a.steer
    CFG.update(repo=a.repo, manager=a.manager or a.repo.split("/")[0], steer=steer, simulate=a.simulate_orchestrator, project=a.project,
               git=a.git, modules=a.modules, phases=a.phases, code=a.code)
    log.info("repo=%s project=%s mode=%s", a.repo, a.project, "STEERING" if steer else "READ-ONLY")
    if steer and gh_user() != CFG["manager"]:
        log.warning("gh is logged in as %s but the Manager is %s: protected labels set now would be flagged unauthorised", gh_user(), CFG["manager"])
    H.token = load_token(a.token_file)
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    if a.lan:
        log.warning("--lan: plain HTTP, the token travels unencrypted; trusted network only")
    log.info("open http://%s:%d/?t=%s  (acting as the gh user; manager=%s)", host, a.port, "<token file>" if a.token_file else H.token, CFG["manager"])
    ThreadingHTTPServer((host, a.port), H).serve_forever()


if __name__ == "__main__":
    main()
