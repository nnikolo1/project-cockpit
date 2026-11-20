"""Pure logic of the cockpit: no network, no subprocess. Everything here is unit-tested."""
import datetime as dt
import json
import logging
import math
import pathlib
import re

log = logging.getLogger("cockpit")

PRIO = ["P1", "P2", "P3"]
PROTECTED = {"released", "cancelled", *PRIO}
STAGES = ["embryo", "released", "in_progress", "completed", "cancelled"]
DUE = re.compile(r"^due:(\d{4}-\d{2}-\d{2})$")
KEY = re.compile(r"^\[([A-Za-z][A-Za-z0-9]*-\d+)\]\s*")
PROPOSAL = re.compile(r"<!--\s*pm:proposal\s*(\{.*?\})\s*-->", re.S)
RESOLVED = re.compile(r"<!--\s*pm:resolved\s+(\S+?)\s*-->")
KIND_TYPE = {"gap": "feature", "check": "research", "bug-risk": "bug", "decision": "decision", "idea": "research"}
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def is_date(s):
    try:
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(s))) and bool(dt.date.fromisoformat(s))
    except ValueError:
        return False


def is_protected(name):
    return name in PROTECTED or bool(DUE.match(name))


def split_title(title, number):
    """'[AB-05] text' -> ('AB-05', 'text'); anything else -> ('#<number>', title)."""
    m = KEY.match(title)
    return (m.group(1).upper(), title[m.end():]) if m else (f"#{number}", title)


def parse_due(labels):
    """The `due:YYYY-MM-DD` labels in a label list -> sorted dates (invalid dates ignored)."""
    return sorted(m.group(1) for l in labels if (m := DUE.match(l)) and is_date(m.group(1)))


def parse_proposals(comments):
    """comments [{body, author, t}] of ONE issue -> open proposals [{id, field, value, reason, author, t}].
    Marker: <!-- pm:proposal {"id":..,"field":"priority|deadline","value":..,"reason":..} -->;
    it is closed by a later comment containing <!-- pm:resolved <id> -->."""
    out, done = {}, set()
    for c in comments:
        body = c.get("body") or ""
        done.update(RESOLVED.findall(body))
        for raw in PROPOSAL.findall(body):
            try:
                p = json.loads(raw)
                pid, field, value = str(p["id"]), p["field"], p["value"]
                value = int(str(value).lstrip("Pp")) if field == "priority" else value
                if field == "priority" and value not in (1, 2, 3) or field == "deadline" and not is_date(value) or field not in ("priority", "deadline"):
                    raise ValueError(f"bad {field} value {value!r}")
            except (ValueError, KeyError, TypeError) as ex:
                log.warning("ignoring malformed proposal marker: %s", ex)
                continue
            out[pid] = dict(id=pid, field=field, value=value, reason=str(p.get("reason", "")), author=c.get("author"), t=c.get("t"))
    return [p for pid, p in out.items() if pid not in done]


def parse_module_deadlines(text):
    """Repository variable MODULE_DEADLINES (JSON {module: 'YYYY-MM-DD'}) -> dict; anything malformed -> {}."""
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return {k: v for k, v in d.items() if isinstance(d, dict) and is_date(v)} if isinstance(d, dict) else {}


# ---------- policy: who may set protected labels ----------
def policy(issues, events, manager):
    """-> {issue#: (effective_labels, flagged_label_names)}. A protected label (released, cancelled, P1-3, due:*)
    counts only if its latest labeled/unlabeled event was made by the manager.
    events: [{number, name, kind: labeled|unlabeled, actor, t}]."""
    latest = {}
    for e in sorted(events, key=lambda e: e["t"]):  # stable: equal timestamps keep their order
        latest[(e["number"], e["name"])] = e
    out = {}
    for i in issues:
        eff, bad = set(), {}
        for name in i["labels"]:
            if not is_protected(name):
                eff.add(name)
                continue
            ev = latest.get((i["number"], name))
            actor = ev["actor"] if ev and ev.get("actor") else "unknown"
            if actor == manager:
                eff.add(name)
            else:
                bad[name] = actor
        out[i["number"]] = (eff, bad)
    return out


def stage(issue, eff):
    if "cancelled" in eff or (issue["state"] == "closed" and issue.get("state_reason") == "not_planned"):
        return "cancelled"
    if issue["state"] == "closed":
        return "completed"
    if issue.get("assignees"):
        return "in_progress"
    return "released" if "released" in eff else "embryo"


def swap_priority(current, new):
    """-> (labels to add, labels to remove) so exactly `new` remains."""
    return ([new] if new not in current else []), [p for p in PRIO if p in current and p != new]


# ---------- modules ----------
def short_name(name):
    return re.split(r" / | \(", name)[0]


SUBID = re.compile(r"[a-z0-9-]+(/[a-z0-9-]+)+")


def parent_id(mid):
    """Module ids are slash paths: 'alpha/one' lives under 'alpha'. -> the parent id, or None for a top-level module."""
    return mid.rpartition("/")[0] or None


def top_id(mid):
    return mid.partition("/")[0]


def chain(mid):
    """'alpha/one' -> ['alpha/one', 'alpha']: the module, then its ancestors."""
    out = [mid]
    while parent_id(out[-1]):
        out.append(parent_id(out[-1]))
    return out


def resolve_module(raw, mods, grow=False):
    """Project-field / label spelling -> module id. Matches an id, or the start of a top-level module name
    ('Alpha' -> 'alpha' for 'Alpha / core'). With grow=True an unknown name becomes a new module."""
    n = (raw or "").strip().lower()
    if not n:
        return "unassigned"
    for m in mods:
        if n == m["id"] or (not parent_id(m["id"]) and m["name"].lower().startswith(n)):
            return m["id"]
    if grow:
        mods.append(dict(id=n if SUBID.fullmatch(n) else re.sub(r"[^a-z0-9]+", "-", n).strip("-"), name=raw.strip()))
        return mods[-1]["id"]
    return "unassigned"


def task_module(field, labels):
    """Module path of a task. field: top-level id from the Project field ('' = none); labels: ids from its `module:` labels.
    A sub-module label wins when its top level equals the field (or there is no field); otherwise the field wins."""
    sub = next((l for l in labels if "/" in l and (not field or top_id(l) == field)), None)
    return sub or field or next(iter(labels), "unassigned")


def with_parents(mods):
    """Appends every missing ancestor of a listed module (in place)."""
    seen = {m["id"] for m in mods}
    for m in list(mods):
        for p in chain(m["id"])[1:]:
            if p not in seen:
                seen.add(p)
                mods.append(dict(id=p, name=p.rpartition("/")[2].capitalize()))


def add_tree(mods, tasks):
    """Adds parent, children (direct), counts (tasks at the node itself) and rollup (node plus all descendants) per stage, in place."""
    for m in mods:
        m["parent"] = parent_id(m["id"])
        m["children"] = [c["id"] for c in mods if parent_id(c["id"]) == m["id"]]
        m["counts"] = {s: sum(1 for t in tasks if t["module"] == m["id"] and t["stage"] == s) for s in STAGES}
    for m in mods:
        under = [x["counts"] for x in mods if x["id"] == m["id"] or x["id"].startswith(m["id"] + "/")]
        m["rollup"] = {s: sum(c[s] for c in under) for s in STAGES}


def module_deadline(deadlines, mid):
    """-> (date, id of the module it is set on): the module's own deadline, else the nearest ancestor's; (None, None) if none."""
    return next(((deadlines[i], i) for i in chain(mid) if deadlines.get(i)), (None, None))


# ---------- derivation ----------
def _prefixed(labels, p):
    return next((l[len(p):] for l in labels if l.startswith(p)), "")


def derive(issues, events, project, manager, mods, grow):
    """Raw issues -> (tasks, modules). issue: number,id,title,url,state,state_reason,created_at,closed_at,labels,assignees,
    parent,blocked_by,body,author. project: {number: {module,hint,status,est,used}}."""
    pol = policy(issues, events, manager)
    latest_t = {(e["number"], e["name"]): e["t"] for e in sorted(events, key=lambda e: e["t"])}
    keys = {i["number"]: split_title(i["title"], i["number"])[0] for i in issues}
    mods = [dict(m) for m in mods]
    pnames = {}
    tasks = []
    for i in issues:
        eff, bad = pol[i["number"]]
        key, title = split_title(i["title"], i["number"])
        p = project.get(i["number"], {})
        field = resolve_module(p["module"], mods, grow) if p.get("module") else ""
        mid = task_module(field, [resolve_module(l[7:], mods, grow) for l in i["labels"] if l.startswith("module:")])
        if field and field != "unassigned":
            pnames.setdefault(field, p["module"])
        prio = next((int(x[1]) for x in PRIO if x in eff), None)
        dues = [d for d in parse_due(eff)]
        due = max(dues, key=lambda d: latest_t.get((i["number"], f"due:{d}"), "")) if dues else None
        hint = p.get("hint") or _prefixed(i["labels"], "prio:").capitalize()
        kind = _prefixed(i["labels"], "kind:")
        cls = "must" if "must" in eff else "nice" if "nice" in eff else ("must" if hint.lower() == "must" else "nice")
        body = i.get("body") or ""
        tasks.append(dict(
            key=key, number=i["number"], dbid=i.get("id"), url=i["url"], title=title, module=mid,
            type=_prefixed(i["labels"], "type:") or KIND_TYPE.get(kind, "") or "task", cls=cls, prio=prio, deadline=due,
            parent=keys.get(i.get("parent")), blockedBy=[keys[n] for n in i.get("blocked_by", []) if n in keys],
            stage=stage(i, eff), assignee=(i.get("assignees") or [None])[0], hint=hint, est=p.get("est"), used=p.get("used"),
            status=p.get("status", ""), created=i["created_at"], closed=i.get("closed_at"), author=i.get("author"),
            desc=body.split("**Evidence**")[0].strip()[:700], evidence=[dict(text=t, url=u) for t, u in LINK.findall(body)][:6],
            labels=i["labels"], flags=[f"unauthorised: {n} set by {a}" for n, a in sorted(bad.items())], bad=sorted(bad)))
    with_parents(mods)
    for m in mods:
        m["short"] = pnames.get(m["id"]) or short_name(m["name"])
    if any(t["module"] == "unassigned" for t in tasks) and not any(m["id"] == "unassigned" for m in mods):
        mods.append(dict(id="unassigned", name="Not mapped to a module", short="Unassigned", virtual=True))
    add_tree(mods, tasks)
    return tasks, mods


def event_text(e):
    n = e["name"]
    if e["kind"] == "assigned":
        return f"assigned to {e['name']}"
    if e["kind"] == "closed":
        return "closed as " + ("not planned" if e["name"] == "not_planned" else "completed")
    verb = "removed" if e["kind"] == "unlabeled" else "set"
    if n == "released":
        return "released" if verb == "set" else "release removed"
    if n == "cancelled":
        return "cancelled" if verb == "set" else "cancellation removed"
    if n in PRIO:
        return f"{verb} priority {n}" if verb == "set" else f"removed priority {n}"
    return f"deadline {n[4:]}" if verb == "set" else f"removed deadline {n[4:]}"


def task_events(events, keys, manager):
    """Protected-label / assignment / close events -> [{t, actor, key, text, kind}] newest first."""
    out = []
    for e in events:
        if e["kind"] in ("labeled", "unlabeled") and not is_protected(e["name"]):
            continue
        text = event_text(e) + ("" if e["kind"] not in ("labeled", "unlabeled") or e["actor"] == manager else " (unauthorised)")
        out.append(dict(t=e["t"].replace("T", " ")[:16], actor=e["actor"] or "unknown", key=keys.get(e["number"], f"#{e['number']}"), text=text, kind=e["kind"]))
    return sorted(out, key=lambda x: x["t"], reverse=True)


# ---------- timeline feed ----------
def map_path(path, assign):
    """Longest-prefix module for a file path; tests/<x>/test_y.py follows <x>/y.py; no match or null -> 'unassigned'."""
    p = path
    if p.startswith("tests/"):
        parts = p[6:].split("/")
        parts[-1] = parts[-1].removeprefix("test_")
        p = "/".join(parts)
    best, mod = -1, None
    for a in assign:
        ap = a["path"]
        if (p == ap or p.startswith(ap if ap.endswith("/") else ap + "/")) and len(ap) > best:
            best, mod = len(ap), a["module"]
    return mod or "unassigned"


def commit_lanes(files, assign):
    return {map_path(f, assign) for f in files} or {"unassigned"} if assign else {"all"}


def parse_git_full(text):
    """`git log --name-only --format=@%H|%ad|%s --date=iso` -> [{sha,date,subject,files}] (newest first, as git prints)."""
    out = []
    for line in text.splitlines():
        if line.startswith("@"):
            sha, d, subj = line[1:].split("|", 2)
            out.append(dict(sha=sha, date=dt.date.fromisoformat(d[:10]), subject=subj, files=[]))
        elif line.strip() and out:
            out[-1]["files"].append(line.strip())
    return out


def group_commits(commits, assign, repo):
    """Same-day commits of one module -> one feed entry with several commit links (shown as short shas)."""
    g = {}
    for c in commits:
        for lane in sorted(commit_lanes(c["files"], assign)):
            g.setdefault((c["date"], lane), []).append(c)
    out = []
    for (d, lane), cs in g.items():
        links = [(c["sha"][:7], f"https://github.com/{repo}/commit/{c['sha']}") for c in cs[:5]]
        if len(cs) > 5:
            links.append((f"+{len(cs) - 5} more", f"https://github.com/{repo}/commits?since={d}&until={d + dt.timedelta(days=1)}"))
        out.append(dict(d=str(d), type="commit", mod=lane, links=links,
                        text=cs[0]["subject"] + (f" (+{len(cs) - 1} more commits)" if len(cs) > 1 else "")))
    return out


def issue_entries(tasks):
    """Issues opened/closed; same day+module+kind collapse to one entry listing the first few."""
    g = {}
    for t in tasks:
        for kind, key in (("opened", "created"), ("closed", "closed")):
            if t.get(key):
                g.setdefault((t[key][:10], t["module"], kind), []).append(t)
    out = []
    for (d, m, kind), xs in g.items():
        text = f"{xs[0]['key']} {kind}: {xs[0]['title']}" if len(xs) == 1 else f"{len(xs)} issues {kind}"
        links = [(x["key"], x["url"]) for x in xs[:8]] + ([(f"+{len(xs) - 8} more", xs[0]["url"].rsplit("/", 1)[0])] if len(xs) > 8 else [])
        out.append(dict(d=d, type="issue", mod=m, text=text, links=links))
    return out


def ref_link(ref, repo):
    """A timeline.json ref -> (text, url or None)."""
    base = f"https://github.com/{repo}"
    if ref.get("sha"):
        return ref["sha"][:7], f"{base}/commit/{ref['sha']}"
    if ref.get("url"):
        return ref.get("text", "link"), ref["url"]
    p = ref.get("path")
    if p:
        m = re.match(r"^(.*):(\d+)$", p)
        return ref.get("text") or p, f"{base}/blob/main/{m.group(1)}#L{m.group(2)}" if m else f"{base}/blob/main/{p}"
    return ref.get("text", ""), None


def module_dir(root, mod):
    """docs/project/<module>/ of a module (its modules.json `docs` file's folder when given)."""
    docs = mod.get("docs")
    return pathlib.Path(root) / (pathlib.Path(docs).parent if docs else pathlib.Path("docs/project") / mod["id"])


def static_entries(d, mod_id, repo):
    """<d>/{timeline,backlog}.json -> feed entries; [] when the module has no data yet (read-only)."""
    out = []
    try:
        for e in json.load(open(d / "timeline.json"))["events"]:
            out.append(dict(d=e["date"], type=e["type"], mod=mod_id, text=e["text"], links=[l for l in (ref_link(r, repo) for r in e.get("refs", [])) if l[1] or l[0]]))
    except FileNotFoundError:
        pass
    try:
        b = json.load(open(d / "backlog.json"))
        out.append(dict(d=b["read_on"], type="gap", mod=mod_id, text=f"Backlog built from the stock-taking: {len(b['items'])} items", links=[]))
    except FileNotFoundError:
        pass
    return out


def module_facts(d):
    """(open gap items, document sources) from the module's backlog/documents json; None = no data."""
    gaps = docs = None
    try:
        gaps = sum(1 for i in json.load(open(d / "backlog.json"))["items"] if i.get("kind") == "gap" and i.get("status") == "open")
    except FileNotFoundError:
        pass
    try:
        docs = len(json.load(open(d / "documents.json"))["sources"])
    except FileNotFoundError:
        pass
    return gaps, docs


def filter_feed(feed, module=None, typ=None):
    return [e for e in feed if (not module or e["mod"] == module) and (not typ or e["type"] == typ)]


# ---------- code snapshot (metrics.json) ----------
def _g(d, *path):
    for p in path:
        d = d.get(p) if isinstance(d, dict) else None
    return d


def code_view(metrics):
    """metrics.json -> {generated_at, commit, modules: {id: flat dict the Code tab shows}}."""
    mods = {}
    for mid, m in (metrics.get("modules") or {}).items():
        x = m.get("metrics") or {}
        loc = x.get("loc") or {}
        top = (_g(x, "lint", "top_codes") or {}).items()
        worst = (_g(x, "complexity", "worst") or [{}])[0]
        mods[mid] = dict(
            name=m.get("name"), code=sum(v.get("code", 0) for v in loc.values()),
            files=f"{x.get('files_total')} files ({_g(loc, 'python_source', 'files') or 0} source, {_g(loc, 'python_test', 'files') or 0} test)",
            cc=_g(x, "complexity", "max"), ccAt=f"{worst['path']}:{worst['line']} {worst['name']}" if worst.get("path") else None,
            mean=_g(x, "complexity", "mean"), dfe=_g(x, "complexity", "rated_DEF"), mi=_g(x, "maintainability", "mean_mi"),
            lint=_g(x, "lint", "total"), lintNote=f"{_g(x, 'lint', 'per_kloc')} per 1,000 lines" + ("; " + ", ".join(f"{k} {v}" for k, v in list(top)[:2]) if top else ""),
            dead=_g(x, "dead_code", "candidates"), cyc=_g(x, "structure", "files_in_cycles"), dup=_g(x, "duplication", "pct_of_code_lines"),
            doc=_g(x, "docstring_coverage", "pct_documented"), cov=_g(x, "test_coverage", "pct_statements_covered"),
            churn=_g(x, "churn", "commits_touching_files"), churnNote=f"{_g(x, 'churn', 'commits_touching_files')} commits touching {_g(x, 'churn', 'files_touched')} files in {_g(x, 'churn', 'window_days')} days",
            testRatio=_g(x, "tests", "test_to_code_ratio_python"), testsNote=f"{_g(x, 'tests', 'source_files_with_mirror_test')} source files have a mirror test",
            hot=[[h["path"], h["churn"], h["cc_max"]] for h in (_g(x, "hotspots", "top") or [])[:5]])
    return dict(generated_at=metrics.get("generated_at"), commit=metrics.get("git_commit_short"), modules=mods)


# ---------- GitHub payloads -> raw issues / events ----------
def norm_issue(n):
    """One GraphQL issue node -> the raw issue dict derive() takes."""
    return dict(number=n["number"], id=n.get("databaseId"), title=n["title"], url=n["url"], state=n["state"].lower(),
                state_reason=(n.get("stateReason") or "").lower() or None, created_at=n["createdAt"], closed_at=n.get("closedAt"),
                author=(n.get("author") or {}).get("login"), labels=[l["name"] for l in n["labels"]["nodes"]],
                assignees=[a["login"] for a in n["assignees"]["nodes"]], parent=(n.get("parent") or {}).get("number"),
                blocked_by=[b["number"] for b in n["blockedBy"]["nodes"]], body=n.get("body") or "",
                comments=[dict(body=c["body"], author=(c.get("author") or {}).get("login"), t=c["createdAt"]) for c in n["comments"]["nodes"]])


def norm_events(n):
    """timelineItems nodes of one GraphQL issue -> flat events (labeled/unlabeled/assigned/closed)."""
    out = []
    for e in n["timelineItems"]["nodes"]:
        k, actor = e.get("__typename"), (e.get("actor") or {}).get("login")
        base = dict(number=n["number"], actor=actor, t=e["createdAt"])
        if k in ("LabeledEvent", "UnlabeledEvent"):
            out.append(dict(base, kind="labeled" if k == "LabeledEvent" else "unlabeled", name=e["label"]["name"]))
        elif k == "AssignedEvent":
            out.append(dict(base, kind="assigned", name=(e.get("assignee") or {}).get("login", "?")))
        elif k == "ClosedEvent":
            out.append(dict(base, kind="closed", name=(n.get("stateReason") or "COMPLETED").lower()))
    return out


# ---------- open questions: issues labelled `question` ----------
RECOMMENDATION = re.compile(r"\*\*Recommendation\.?\*\*\.?[ \t]*(.*?)(?:\n[ \t]*\n|\Z)", re.S)
RECO_CAP = 400


def parse_recommendation(body):
    """The text of the `**Recommendation.**` paragraph of an issue body (whitespace collapsed, capped), or None."""
    m = RECOMMENDATION.search(body or "")
    text = " ".join(m.group(1).split()) if m else ""
    return (text[:RECO_CAP - 1] + "\u2026" if len(text) > RECO_CAP else text) or None


def age_days(created, now):
    """Whole days between an ISO timestamp (`...Z`) and `now` (aware datetime); never negative."""
    t = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
    return max(0, (now - t).days)


def parse_questions(issues, project):
    """Raw issues -> the open ones labelled `question`, as panel entries (title series split off, recommendation extracted)."""
    out = []
    for i in issues:
        if i["state"] != "open" or "question" not in i["labels"]:
            continue
        key, title = split_title(i["title"], i["number"])
        out.append(dict(project=project, number=i["number"], key=key, title=title, url=i["url"], created=i["created_at"],
                        recommendation=parse_recommendation(i.get("body"))))
    return out


def merge_questions(per_project, now):
    """{project: [entries]} -> one list, oldest first (ties by project then number), each entry with `age` in days."""
    rows = [dict(q, age=age_days(q["created"], now)) for qs in per_project.values() for q in qs]
    return sorted(rows, key=lambda q: (q["created"], q["project"], q["number"]))


def group_questions(rows):
    """Entries -> {project: count} in first-seen order (the project filter chips)."""
    out = {}
    for q in rows:
        out[q["project"]] = out.get(q["project"], 0) + 1
    return out


def parse_project(items, repo):
    """`gh project item-list --format json` items -> {issue#: {module, hint, status, est, used}} (this repo's issues only).
    Field keys are the lower-cased field names; the number fields are matched by their first word."""
    out = {}
    for it in items:
        c = it.get("content") or {}
        if c.get("type") != "Issue" or c.get("repository") != repo:
            continue
        num = lambda prefix: next((v for k, v in it.items() if k.lower().startswith(prefix) and isinstance(v, (int, float))), None)
        out[int(c["number"])] = dict(module=it.get("module", ""), hint=it.get("priority", ""), status=it.get("status", ""),
                                     est=num("estimate"), used=num("tokens"))
    return out


# ---------- rule files: which files steer the agents, and what changed in them ----------
RS, US, GS = "\x1e", "\x1f", "\x1d"  # record, field and body separators of the git formats below
LOG_FMT = f"{RS}%H{US}%aI{US}%an{US}%ae{US}%s{US}%b{GS}"  # then --numstat or -p output follows
BOT = ("[bot]", "noreply@anthropic")
TRAILER = re.compile(r"^co-authored-by:\s*([^<\n]*?)\s*(?:<[^>\n]*>)?\s*$", re.I | re.M)
DIFF_CAP, LINE_CAP = 30, 200


def glob_re(g):
    """Glob -> regex over repo-relative '/' paths. `**/` = any directories (or none), `**` = anything, `*` and `?` stay inside one segment."""
    out, i = "", 0
    while i < len(g):
        if g.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif g.startswith("**", i):
            out, i = out + ".*", i + 2
        elif g[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif g[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(g[i]), i + 1
    return re.compile(out + r"\Z")


def rules_config(cfg):
    """The `rules` key of the --modules JSON -> {groups: [{name, res, paths}], managers: [lower-case]}, or None when absent/malformed."""
    r = (cfg or {}).get("rules")
    if not isinstance(r, dict) or not isinstance(r.get("groups"), list):
        return None
    gs = [dict(name=str(g["name"]), paths=[str(p) for p in g["paths"]], res=[glob_re(str(p)) for p in g["paths"]])
          for g in r["groups"] if isinstance(g, dict) and g.get("name") and isinstance(g.get("paths"), list)]
    return dict(groups=gs, managers=[str(m).lower() for m in r.get("managers", [])]) if gs else None


def rule_group(path, groups):
    """Name of the first group whose globs match `path`, else None."""
    return next((g["name"] for g in groups if any(r.match(path) for r in g["res"])), None)


def rule_pathspecs(groups):
    """git pathspecs covering all globs (`:(glob)` magic); the Python matching stays authoritative."""
    return [":(glob)" + p for g in groups for p in g["paths"]]


def is_agent_assisted(body, name, email):
    """A Co-Authored-By trailer in the message, or a bot-looking author."""
    who = f"{name} {email}".lower()
    return bool(TRAILER.search(body or "")) or any(b in who for b in BOT)


def is_manager(name, email, managers):
    return name.lower() in managers or email.lower() in managers


def _records(text):
    """git output in LOG_FMT -> [(header fields, text after the body)]."""
    out = []
    for rec in text.split(RS)[1:]:
        head, _, rest = rec.partition(GS)
        out.append((head.split(US), rest))
    return out


def parse_numstat(rest, groups):
    """numstat lines -> [{path, group, add, del}] for the files that match a group (binary files count 0)."""
    out = []
    for ln in rest.splitlines():
        p = ln.split("\t")
        if len(p) == 3 and (g := rule_group(p[2], groups)):
            out.append({"path": p[2], "group": g, "add": int(p[0]) if p[0].isdigit() else 0, "del": int(p[1]) if p[1].isdigit() else 0})
    return out


def diff_excerpt(rest, groups, cap=DIFF_CAP):
    """`git show -U0` text -> [[sign, text]]: the first `cap` changed lines of the matching files; [' @', path] marks a file."""
    out, n, on = [], 0, False
    for ln in rest.splitlines():
        if ln.startswith("diff --git "):
            path = ln.rsplit(" b/", 1)[-1]
            on = rule_group(path, groups) is not None
            if on and n < cap:
                out.append(["@", path])
        elif on and n < cap and ln[:1] in ("+", "-") and not ln.startswith(("+++", "---")):
            out.append([ln[0], ln[1:][:LINE_CAP]])
            n += 1
    return out


def parse_rule_changes(numstat_txt, diff_txt, groups, managers):
    """Outputs of two `git log` runs (LOG_FMT + --numstat / -p -U0) -> rule changes, newest first; commits without matching files are dropped."""
    diffs = {h[0]: rest for h, rest in _records(diff_txt)}
    out = []
    for (sha, date, name, email, subj, body), rest in _records(numstat_txt):
        files = parse_numstat(rest, groups)
        if not files:
            continue
        agent, mgr = is_agent_assisted(body, name, email), is_manager(name, email, managers)
        out.append(dict(sha=sha[:7], date=date[:10], author=name, email=email, subject=subj, files=files, diff=diff_excerpt(diffs.get(sha, ""), groups),
                        co_authors=[c for c in TRAILER.findall(body or "") if c], agent_assisted=agent, by_manager=mgr, needs_attention=agent or not mgr))
    return out


def parse_last_changes(text, groups):
    """`git log --name-only` in LOG_FMT (newest first) -> {path: {date, sha, author, subject}}: the first sight of a path is its last change."""
    out = {}
    for (sha, date, name, _e, subj, _b), rest in _records(text):
        for p in rest.split("\n"):
            if p.strip() and p not in out and rule_group(p, groups):
                out[p] = dict(date=date[:10], sha=sha[:7], author=name, subject=subj)
    return out


def rule_files(paths, last, groups):
    """Tracked paths -> [{path, group, last: {...}|None}] for those matching a group, sorted by group order then path."""
    order = {g["name"]: i for i, g in enumerate(groups)}
    xs = [dict(path=p, group=rule_group(p, groups), last=last.get(p)) for p in paths if rule_group(p, groups)]
    return sorted(xs, key=lambda x: (order[x["group"]], x["path"]))


def pending_rule_prs(prs, groups):
    """`gh pr list --json number,title,author,files,url,createdAt` -> open PRs touching rule files, with those files."""
    out = []
    for p in prs:
        fs = [dict(path=f["path"], group=g) for f in p.get("files") or [] if (g := rule_group(f.get("path", ""), groups))]
        if fs:
            out.append(dict(number=p["number"], title=p["title"], author=(p.get("author") or {}).get("login", ""), url=p["url"], created=(p.get("createdAt") or "")[:10], files=fs))
    return out


def rule_feed(changes, repo):
    """Rule changes -> timeline entries (type 'rule change', lane 'rules')."""
    return [dict(d=c["date"], type="rule change", mod="rules", text=f"{c['subject']} ({', '.join(f['path'] for f in c['files'][:3])}{', ...' if len(c['files']) > 3 else ''})",
                 links=[(c["sha"], f"https://github.com/{repo}/commit/{c['sha']}")]) for c in changes]


REPORT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(html|svg|css)$")


def report_file(code_dir, name):
    """Path of one file of the snapshot's report/ folder, or None. Plain file names only (no folders, no '..')."""
    if not REPORT_NAME.match(name or ""):
        return None
    p = pathlib.Path(code_dir).expanduser() / "report" / name
    return p if p.is_file() else None


# ---------- card metadata: footprint, churn, ambiguity (contract: docs/contracts/card-metadata.md) ----------
CARD_KINDS = ("footprint", "churn", "ambiguity")
STALE_HOURS = 48       # a file older than this is shown with a stale warning
UNSORTED_WARN = 0.30   # unsorted files above this share of a module's files raise the marker
HOT_SHARE = 0.2        # top fifth of the churn ranking is hot, bottom fifth cold
GAP_WORDS = ("", "undocumented", "unknown", "none", "n/a")


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def parse_card(text, kind, project=None, now=None):
    """Raw text of one card file (None = file missing) -> {status: ok|missing|invalid, data, generated_at, age_h, stale, reason}.
    Never raises: a missing, unreadable, foreign (wrong kind, project or schema) file just means 'no data'."""
    if text is None:
        return dict(status="missing", data=None, generated_at=None, age_h=None, stale=False, reason="file not found")
    bad = lambda why: dict(status="invalid", data=None, generated_at=None, age_h=None, stale=False, reason=why)
    try:
        d = json.loads(text)
    except ValueError as ex:
        return bad(f"not JSON ({str(ex)[:60]})")
    if not isinstance(d, dict):
        return bad("not a JSON object")
    if d.get("schema") != 1:
        return bad(f"unsupported schema {d.get('schema')!r} (this cockpit reads 1)")
    if d.get("kind") != kind:
        return bad(f"kind is {d.get('kind')!r}, expected {kind!r}")
    if project and d.get("project") != project:
        return bad(f"belongs to project {d.get('project')!r}, not {project!r}")
    gen, age = d.get("generated_at"), None
    try:
        t = dt.datetime.fromisoformat(str(gen).replace("Z", "+00:00"))
        t = t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
        age = ((now or dt.datetime.now(dt.timezone.utc)) - t).total_seconds() / 3600
    except ValueError:
        gen = None
    return dict(status="ok", data=d, generated_at=gen, age_h=None if age is None else round(max(age, 0), 1), stale=age is None or age > STALE_HOURS, reason="")


def rank_bands(values, share=HOT_SHARE):
    """{id: number|None} -> {id: 'hot'|'mid'|'cold'|'dormant'|None}. Zero is dormant and not ranked; among the others the top
    fifth (ties kept together) is hot, the bottom fifth cold; fewer than 3 ranked values, or all equal, stay mid."""
    live = sorted((v for v in values.values() if v), reverse=True)
    k = max(1, math.ceil(len(live) * share)) if live else 0
    hot, cold = (live[k - 1], live[-k]) if len(live) >= 3 else (None, None)
    split = hot is not None and hot > cold
    return {i: None if v is None else "dormant" if v == 0 else "hot" if split and v >= hot else "cold" if split and v <= cold else "mid"
            for i, v in values.items()}


def churn_view(data):
    """churn.json -> {windows, modules: {id: {windows: {'30': {..entry, rel, band_abs, band_rel}}, size, lines}}, unassigned, excluded, mapping_check}.
    Cards show `total` (the module with its sub-modules; `own` when there is no total). Bands are ranked per level (top-level
    modules among themselves, sub-modules among themselves), by churn (abs) and by churn per current line (rel)."""
    wins = [str(w) for w in (data.get("windows") or [30, 90])]
    mods = {}
    for mid, m in (data.get("modules") or {}).items():
        if not isinstance(m, dict):
            continue
        size = m.get("size") if isinstance(m.get("size"), dict) else {}
        lines = (_num(size.get("source_lines")) or 0) + (_num(size.get("test_lines")) or 0)
        t = m.get("total") or m.get("own") or {}
        w = {}
        for k in wins:
            e = t.get(k)
            if isinstance(e, dict):
                c = _num(e.get("churn"))
                w[k] = dict(e, rel=round(c / lines, 4) if c is not None and lines else None)
        mods[mid] = dict(windows=w, size=size, lines=lines)
    for level in (False, True):
        ids = [i for i in mods if ("/" in i) == level]
        for k in wins:
            for mode, f in (("abs", lambda e: _num(e.get("churn"))), ("rel", lambda e: e.get("rel"))):
                bands = rank_bands({i: f(mods[i]["windows"][k]) for i in ids if k in mods[i]["windows"]})
                for i, b in bands.items():
                    mods[i]["windows"][k]["band_" + mode] = b
    return dict(windows=wins, modules=mods, unassigned=data.get("unassigned"), excluded=data.get("excluded"), mapping_check=data.get("mapping_check"))


def file_location(path):
    """'inside' when the path is relative to the repository, else 'outside' (absolute, '~', or climbing out with '..')."""
    p = str(path or "")
    return "outside" if p.startswith(("/", "~", "..")) or re.match(r"^[A-Za-z]:[\\/]", p) else "inside"


def policy_text(v):
    """Backup/retention policy -> (text, gap). Missing, empty or 'undocumented' is a gap, shown as such."""
    t = str(v).strip() if v is not None else ""
    return (t or "undocumented", t.lower() in GAP_WORDS)


def footprint_view(data):
    """footprint.json -> {modules: {id: {tables, files (+location), logging, volume, backup, retention as (text, gap)}}}."""
    out = {}
    for mid, m in (data.get("modules") or {}).items():
        if not isinstance(m, dict):
            continue
        pol = m.get("policy") if isinstance(m.get("policy"), dict) else {}
        lst = lambda k: [x for x in (m.get(k) or []) if isinstance(x, dict)]
        out[mid] = dict(tables=lst("tables"), logging=lst("logging"), files=[dict(f, location=file_location(f.get("path"))) for f in lst("files")],
                        volume=m.get("volume") if isinstance(m.get("volume"), dict) else {}, backup=policy_text(pol.get("backup")), retention=policy_text(pol.get("retention")))
    return dict(modules=out)


def ambiguity_view(data, warn=UNSORTED_WARN):
    """ambiguity.json -> {modules: {id: {own, subs, unsorted, ambiguous, total, share, high}}, module_level, unassigned_nondoc}.
    total = own_files + all sub_files + unsorted (unsorted files are counted in neither of the others); high = share > warn."""
    mods = {}
    for mid, m in (data.get("modules") or {}).items():
        if not isinstance(m, dict):
            continue
        subs = {k: v for k, v in (m.get("sub_files") or {}).items() if _num(v) is not None}
        uns = [u for u in (m.get("unsorted") or []) if isinstance(u, dict)]
        total = (_num(m.get("own_files")) or 0) + sum(subs.values()) + len(uns)
        share = len(uns) / total if total else 0
        amb = [u for u in uns if u.get("candidates")]   # unsorted because ambiguous between sub-modules: shown as notes on the card
        mods[mid] = dict(own=_num(m.get("own_files")) or 0, subs=subs, unsorted=uns, ambiguous=amb, total=total, share=round(share, 3), high=share > warn)
    ml = [u for u in (data.get("module_level") or []) if isinstance(u, dict)]
    return dict(modules=mods, module_level=ml, unassigned_nondoc=_num(data.get("unassigned_nondoc")))


CARD_VIEWS = dict(footprint=footprint_view, churn=churn_view, ambiguity=ambiguity_view)


def card_meta(texts, project=None, now=None):
    """{kind: raw text or None} -> {kind: parse_card result plus `view` (the page's data) when ok}. A view that cannot be built makes it 'invalid'."""
    out = {}
    for kind in CARD_KINDS:
        c = parse_card(texts.get(kind), kind, project, now)
        if c["status"] == "ok":
            try:
                c["view"] = CARD_VIEWS[kind](c["data"])
            except (AttributeError, TypeError, ValueError) as ex:
                log.warning("card metadata %s: unusable (%s)", kind, ex)
                c.update(status="invalid", reason=f"unusable content ({str(ex)[:60]})")
        c.pop("data")
        out[kind] = c
    return out


# ---------- budget panel: quota feed, usage report, boot-filling decision log (contract: docs/contracts/budget-panel.md) ----------
QUOTA_STALE_MIN = 30   # default: a quota reading older than this is shown as stale; the cockpit passes 3 x the poll interval (stale_min)
USAGE_STALE_H = 24     # a usage report older than this is shown as stale
QUOTA_WINDOWS = (("five_hour", "5-hour window"), ("seven_day", "Weekly window"))
LOG_LIMIT = 50         # most recent entries kept per table
TEXT_CAP = 300         # longest free text taken from the decision log


def parse_ts(v):
    """Epoch seconds (number) or ISO 8601 text -> aware UTC datetime, else None."""
    try:
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return dt.datetime.fromtimestamp(v, dt.timezone.utc)
        if isinstance(v, str) and v.strip():
            t = dt.datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
            return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass
    return None


def _json_obj(text):
    """-> (dict, None) or (None, reason). `text` None means the file does not exist."""
    if text is None:
        return None, "file not found"
    try:
        d = json.loads(text)
    except ValueError as ex:
        return None, f"not JSON ({str(ex)[:60]})"
    return (d, None) if isinstance(d, dict) else (None, "not a JSON object")


def _age_s(t, now):
    return None if t is None else max(0, round((now - t).total_seconds()))


def quota_view(text, mtime, now, stale_min=QUOTA_STALE_MIN):
    """Raw quota feed {five_hour, seven_day: {used_percentage, resets_at}} plus the file's modification time (datetime) ->
    {status: ok|missing|invalid, reason, age_s, stale, windows: [...]}. Each window: key, label, used_pct, left_pct, resets_at,
    resets_in_s, expired (the reset time has passed, so the reading belongs to a window that is over). `stale_min`: age limit in minutes. Never raises."""
    d, why = _json_obj(text)
    if d is None:
        return dict(status="missing" if text is None else "invalid", reason=why, age_s=None, stale=False, windows=[])
    age = _age_s(mtime, now)
    wins = []
    for key, label in QUOTA_WINDOWS:
        w = d.get(key)
        if not isinstance(w, dict) or _num(w.get("used_percentage")) is None:
            continue
        used = min(max(float(w["used_percentage"]), 0.0), 100.0)
        r = parse_ts(w.get("resets_at"))
        left = None if r is None else round((r - now).total_seconds())
        wins.append(dict(key=key, label=label, used_pct=round(used, 1), left_pct=round(100 - used, 1),
                         resets_at=r and r.strftime("%Y-%m-%dT%H:%M:%SZ"), resets_in_s=None if left is None else max(left, 0), expired=left is not None and left < 0))
    if not wins:
        return dict(status="invalid", reason="no window with used_percentage", age_s=age, stale=False, windows=[])
    return dict(status="ok", reason="", age_s=age, stale=age is None or age > stale_min * 60, windows=wins)


def usage_view(text, mtime, now, quota=None):
    """Raw usage report {schema: 1, generated_at, windows: {five_hour|seven_day: {tokens_used, tokens_cap_estimate?}}} -> per window
    tokens_used, tokens_cap (an ESTIMATE, always), cap_source ('reported' or 'derived' from the quota share when >= 1 percent) and tokens_left."""
    d, why = _json_obj(text)
    if d is None:
        return dict(status="missing" if text is None else "invalid", reason=why, age_s=None, stale=False, windows={})
    if d.get("schema") != 1 or not isinstance(d.get("windows"), dict):
        return dict(status="invalid", reason="needs schema 1 and a windows object", age_s=None, stale=False, windows={})
    t = parse_ts(d.get("generated_at")) or mtime
    age = _age_s(t, now)
    pct = {w["key"]: w["used_pct"] for w in (quota or {}).get("windows", [])}
    out = {}
    for key, _ in QUOTA_WINDOWS:
        w = d["windows"].get(key)
        used = _num(w.get("tokens_used")) if isinstance(w, dict) else None
        if used is None:
            continue
        cap, src = _num(w.get("tokens_cap_estimate")), "reported"
        if cap is None and pct.get(key, 0) >= 1:
            cap, src = round(used / (pct[key] / 100)), "derived"
        if cap is None:
            src = None
        out[key] = dict(tokens_used=used, tokens_cap=cap, cap_source=src, tokens_left=None if cap is None else max(round(cap - used), 0))
    return dict(status="ok", reason="", age_s=age, stale=age is None or age > USAGE_STALE_H * 3600, windows=out)


def _txt(v):
    return None if v is None else str(v).strip()[:TEXT_CAP]


def _release_row(d):
    return dict(time=_txt(d.get("time")), trigger=_txt(d.get("trigger")), rank=_num(d.get("rank")), project=_txt(d.get("project")), issue=_num(d.get("issue")),
                title=_txt(d.get("title")), reason=_txt(d.get("reason")), state=_txt(d.get("state")), dry_run=d.get("dry_run") is True)


def _poll_row(d):
    trig = [dict(name=_txt(x.get("name")), verdict=_txt(x.get("verdict"))) for x in (d.get("triggers") if isinstance(d.get("triggers"), list) else []) if isinstance(x, dict)]
    return dict(time=_txt(d.get("time")), idle=d.get("idle") if isinstance(d.get("idle"), bool) else None, idle_reason=_txt(d.get("idle_reason")),
                five_hour_used=_num(d.get("five_hour_used")), five_hour_resets_at=_txt(d.get("five_hour_resets_at")),
                weekly_used=_num(d.get("weekly_used")), weekly_resets_at=_txt(d.get("weekly_resets_at")), triggers=trig, decision=_txt(d.get("decision")))


IGNORED_DECISION_KINDS = ("authority_skip",)


def decision_view(text, limit=LOG_LIMIT):
    """JSON-lines decision log (one object per line, `kind` is "release" or "poll") -> {status, releases, polls, skipped}, newest first,
    at most `limit` each. A line that is not JSON, has no known kind or no readable `time` is counted in `skipped`, never raised."""
    if text is None:
        return dict(status="missing", releases=[], polls=[], skipped=0)
    rel, polls, skipped = [], [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        t = parse_ts(d.get("time")) if isinstance(d, dict) else None
        kind = d.get("kind") if isinstance(d, dict) else None
        if kind in IGNORED_DECISION_KINDS:  # extension kinds of the contract (authority_skip): known, not shown here, not "unreadable"
            continue
        if t is None or kind not in ("release", "poll"):
            skipped += 1
            continue
        (rel if kind == "release" else polls).append((t, _release_row(d) if kind == "release" else _poll_row(d)))
    newest = lambda rows: [r for _, r in sorted(rows, key=lambda x: x[0], reverse=True)[:limit]]
    return dict(status="ok", releases=newest(rel), polls=newest(polls), skipped=skipped)


def budget_view(quota_text, quota_mtime, usage_text, usage_mtime, log_text, now, stale_min=QUOTA_STALE_MIN):
    q = quota_view(quota_text, quota_mtime, now, stale_min)
    return dict(now=now.strftime("%Y-%m-%dT%H:%M:%SZ"), quota=q, usage=usage_view(usage_text, usage_mtime, now, q), log=decision_view(log_text))
