"""Task model: stage, derive, dispatchable, blocking, ranking, ops, proposals, questions. Depends on common and modules."""
import logging
import datetime as dt
import json
import re
from ckp.core.common import STATES
from ckp.core.modules import add_tree, resolve_module, short_name, task_module, with_parents

log = logging.getLogger("cockpit")


PRIO = ["P1", "P2", "P3"]
PROTECTED = {"released", "cancelled", *PRIO}


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


def issues_needing_timeline(issues, events):
    """Numbers of issues that carry a protected label whose latest labeled/unlabeled event is missing or is not `labeled`: the event list
    is incomplete for them (GitHub thins large GraphQL pages, ADR 0019), so their own timeline must be fetched. A label with a real event
    by someone else is NOT listed: that is a genuine unauthorised change."""
    latest = {}
    for e in sorted((e for e in events if e["kind"] in ("labeled", "unlabeled")), key=lambda e: e["t"]):
        latest[(e["number"], e["name"])] = e
    return [i["number"] for i in issues
            if any(is_protected(n) and (latest.get((i["number"], n)) or {}).get("kind") != "labeled" for n in i["labels"])]


def stage(issue, eff):
    """The six task states (embryo, released, wip, unmerged, failed, merged) plus the separate marker `cancelled`."""
    if "cancelled" in eff or (issue["state"] == "closed" and issue.get("state_reason") == "not_planned"):
        return "cancelled"
    lab = issue.get("labels") or []
    for s in STATES:
        if s in lab and not (s == "wip" and issue["state"] == "closed"):   # a closed issue is never running (PC-98)
            return s
    if issue["state"] == "closed":
        return "merged"
    if issue.get("assignees"):
        return "wip"
    if is_finding(lab):
        return "released"
    return "released" if "released" in eff else "embryo"


def task_lookup(raw):
    """One issue as the REST API returns it -> {n, stage, title}: the answer the page gets when an action of its own got no answer (PC-136).
    The labels count as they stand; the authorship of protected labels is checked on the next full read of the state."""
    i = dict(state=raw.get("state"), state_reason=raw.get("state_reason"), labels=[x["name"] for x in raw.get("labels") or []],
             assignees=[x["login"] for x in raw.get("assignees") or []])
    return dict(n=raw.get("number"), stage=stage(i, set(i["labels"])), title=raw.get("title") or "")


FINDING = "policy-finding"
FILED_BY = re.compile(r"<!--\s*filed-by:\s*(\S+?)\s*-->")


def is_finding(labels):
    """A task filed because a file contradicts a standing policy: released at once (spec 2b), but never a question or a decision."""
    return FINDING in labels and "question" not in labels and "type:decision" not in labels


NOT_DISPATCHABLE = ("question", "type:decision")   # spec 2b: questions and decisions never go to a Worker
INTERACTIVE_DAYS = 10                     # spec 2b: an interactive agent's embryo counts as released after this many days


def _author_releases(lab, created, now):
    """Spec 2b: whether an embryo counts as released by its author label alone."""
    if "author:job" in lab:
        return False
    if "author:interactive" in lab and "author:owner" not in lab:
        return age_days(created, now) >= INTERACTIVE_DAYS
    return True


def dispatchable(t, now):
    """The Dispatcher's release rule (spec 2b), the one copy: a derived task (logic.derive) that is open, unassigned, without a state label,
    not a question or decision, and released (stage, label, or its author label). Blockers are checked by is_blocked."""
    lab = set(t["labels"])
    if t["stage"] not in ("embryo", "released") or lab & set(STATES) or lab & set(NOT_DISPATCHABLE) or t["type"] == "decision":
        return False
    return t["stage"] == "released" or "released" in lab or _author_releases(lab, t["created"], now)


def is_blocked(t, by_key):
    """Blocked while a blocker (a known task) is not merged (owner, 2026-11-25)."""
    return any(by_key[b]["stage"] != "merged" for b in t["blockedBy"] if b in by_key)


def pickable(tasks, now, held=()):
    """The keys of the tasks the Dispatcher would really pick from ("Next up" and the rank column): dispatchable, not blocked and not held
    by a running Worker (`held`: issue numbers)."""
    by_key = {t["key"]: t for t in tasks}
    return {t["key"] for t in tasks if dispatchable(t, now) and not is_blocked(t, by_key) and t["number"] not in held}


def parse_filed_by(body):
    """The agent id in the body's `<!-- filed-by: <agent id> -->` line, or None."""
    m = FILED_BY.search(body or "")
    return m.group(1) if m else None


KEPT = re.compile(r"<details>\s*<summary>\s*Origin.*?</details>|<!--\s*filed-by:.*?-->", re.S)
ANALYSIS = re.compile(r"<!--\s*analysis:\s*(clear|flagged|stale)\b")


def split_text(body):
    """An issue body -> (the editable text, [the provenance block and the filed-by marker, verbatim]): the parts an edit keeps."""
    body = body or ""
    return KEPT.sub("", body).strip(), KEPT.findall(body)


def join_text(text, kept):
    """The edited text with the kept parts re-attached below it (the inverse of split_text)."""
    return "\n\n".join([text.strip()] + list(kept)).strip() + "\n"


CHECKABLE_ACTIONS = ("release", "cancel", "edit")  # actions whose effect can be read back on the issue (PC-131)


def action_applied(form, issue):
    """Whether the action of `form` is visible on `issue` as GitHub shows it now (`gh issue view --json state,labels,title,body`):
    True = applied (never apply it again), False = not (fully) applied, None = cannot be told from the issue. A partly applied
    release, cancel or edit counts as not applied: running it again is harmless (labels and texts are set, not added up)."""
    labels = {l["name"] if isinstance(l, dict) else l for l in issue.get("labels") or ()}
    a = form.get("action")
    if a == "release":
        return "released" in labels
    if a == "cancel":
        return "cancelled" in labels and str(issue.get("state", "")).upper() == "CLOSED"
    if a == "edit":
        title, text = (form.get("title") or "").strip(), split_text(form.get("body"))[0]
        return split_title(issue.get("title") or "", 0)[1].strip() == title and split_text(issue.get("body"))[0] == text
    return None


def analysis_state(comments):
    """The authority analysis of a task as its latest `<!-- analysis: clear|flagged|stale ... -->` comment records it; None = not recorded."""
    marks = [(c.get("t") or "", m.group(1)) for c in comments or () for m in [ANALYSIS.search(c.get("body") or "")] if m]
    return max(marks)[1] if marks else None


def is_running(stage_):
    """A job runs now: the task is WIP."""
    return stage_ == "wip"


def swap_priority(current, new):
    """-> (labels to add, labels to remove) so exactly `new` remains."""
    return ([new] if new not in current else []), [p for p in PRIO if p in current and p != new]


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
        text, kept = split_text(body)
        tasks.append(dict(
            key=key, number=i["number"], dbid=i.get("id"), url=i["url"], title=title, module=mid,
            type=_prefixed(i["labels"], "type:") or KIND_TYPE.get(kind, "") or "task", cls=cls, prio=prio, deadline=due,
            parent=keys.get(i.get("parent")), blockedBy=[keys[n] for n in i.get("blocked_by", []) if n in keys],
            stage=stage(i, eff), assignee=(i.get("assignees") or [None])[0], hint=hint, est=p.get("est"), used=p.get("used"),
            status=p.get("status", ""), finding=is_finding(i["labels"]), filed_by=parse_filed_by(body), created=i["created_at"], closed=i.get("closed_at"), author=i.get("author"),
            text=text, kept=kept, edited=i.get("edited"), analysis=analysis_state(i.get("comments")),
            desc=body.split("**Evidence**")[0].strip()[:700], evidence=[dict(text=t, url=u) for t, u in LINK.findall(body)][:6],
            labels=i["labels"], running=is_running(stage(i, eff)), flags=[f"unauthorised: {n} set by {a}" for n, a in sorted(bad.items())], bad=sorted(bad)))
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
    if n in STATES:
        return f"state {n}" if verb == "set" else f"state {n} removed"
    if n == "released":
        return "released" if verb == "set" else "release removed"
    if n == "cancelled":
        return "cancelled" if verb == "set" else "cancellation removed"
    if n in PRIO:
        return f"{verb} priority {n}" if verb == "set" else f"removed priority {n}"
    return f"deadline {n[4:]}" if verb == "set" else f"removed deadline {n[4:]}"


def task_events(events, keys, manager):
    """Protected-label / state-label / assignment / close events -> [{t, actor, key, text, kind}] newest first."""
    out = []
    for e in events:
        if e["kind"] in ("labeled", "unlabeled") and not is_protected(e["name"]) and e["name"] not in STATES:
            continue
        text = event_text(e) + ("" if e["kind"] not in ("labeled", "unlabeled") or e["name"] in STATES or e["actor"] == manager else " (unauthorised)")
        out.append(dict(t=e["t"].replace("T", " ")[:16], actor=e["actor"] or "unknown", key=keys.get(e["number"], f"#{e['number']}"), text=text, kind=e["kind"]))
    return sorted(out, key=lambda x: x["t"], reverse=True)


# ---------- GitHub payloads -> raw issues / events ----------
def norm_issue(n):
    """One GraphQL issue node -> the raw issue dict derive() takes."""
    return dict(number=n["number"], id=n.get("databaseId"), title=n["title"], url=n["url"], state=n["state"].lower(),
                state_reason=(n.get("stateReason") or "").lower() or None, created_at=n["createdAt"], updated_at=n.get("updatedAt"), closed_at=n.get("closedAt"), edited=n.get("lastEditedAt"),
                author=(n.get("author") or {}).get("login"), labels=[l["name"] for l in n["labels"]["nodes"]],
                assignees=[a["login"] for a in n["assignees"]["nodes"]], parent=(n.get("parent") or {}).get("number"),
                blocked_by=[b["number"] for b in n["blockedBy"]["nodes"]], body=n.get("body") or "",
                comments=[dict(body=c["body"], author=(c.get("author") or {}).get("login"), t=c["createdAt"]) for c in n["comments"]["nodes"]])


def _flag(op, name):
    """The values of `name` options in one gh argument list (`--add-label x` or `-f title=x` style)."""
    return [op[k + 1] for k in range(len(op) - 1) if op[k] == name]


def _fields(op):
    return dict(f.split("=", 1) for f in _flag(op, "-f") + _flag(op, "-F") if "=" in f)


def apply_ops(raw, deadlines, ops, outs, actor, now):
    """PC-135: the known effect of gh calls that succeeded, laid on the raw copy (issues, events) and the module deadlines, with no GitHub call.
    ops: the argument lists run, outs: each one's JSON answer ({} when none). Labels, assignees, closing, comments, title and body, blockers
    and new issues change as GitHub changes them; label events carry `actor` (the gh login), so the policy sees who set them.
    -> (raw, deadlines, numbers of the issues touched). The inputs are not changed."""
    issues = {i["number"]: dict(i, labels=list(i["labels"]), assignees=list(i.get("assignees") or []), blocked_by=list(i.get("blocked_by") or []),
                                comments=list(i.get("comments") or [])) for i in raw["issues"]}
    by_db = {i.get("id"): n for n, i in issues.items()}
    events, touched, created = list(raw["events"]), [], None
    ev = lambda n, kind, name: events.append(dict(number=n, kind=kind, name=name, actor=actor, t=now))
    for op, out in zip(ops, outs):
        if op[0] == "variable" and op[1:3] == ["set", "MODULE_DEADLINES"]:
            deadlines = parse_module_deadlines(_flag(op, "--body")[0])
            continue
        if op[0] == "issue" and len(op) > 2 and op[2].isdigit() and int(op[2]) in issues:
            i = issues[int(op[2])]
            if op[1] == "edit":
                for l in _flag(op, "--remove-label"):
                    if l in i["labels"]:
                        i["labels"].remove(l)
                    ev(i["number"], "unlabeled", l)
                for l in _flag(op, "--add-label"):
                    i["labels"] += [] if l in i["labels"] else [l]
                    ev(i["number"], "labeled", l)
                for a in _flag(op, "--add-assignee"):
                    a = actor if a == "@me" else a
                    i["assignees"] += [] if a in i["assignees"] else [a]
                    ev(i["number"], "assigned", a)
            elif op[1] == "close":
                reason = "not_planned" if _flag(op, "--reason") == ["not planned"] else "completed"
                i.update(state="closed", state_reason=reason, closed_at=now)
                ev(i["number"], "closed", reason)
            elif op[1] == "comment":
                i["comments"] = (i["comments"] + [dict(body=_flag(op, "--body")[0], author=actor, t=now)])[-10:]
            else:
                continue
            i["updated_at"] = now
            touched.append(i["number"])
            continue
        if op[0] != "api":
            continue
        path, f = next((x for x in op[1:] if x.startswith("repos/")), ""), _fields(op)
        m = re.fullmatch(r"repos/[^/]+/[^/]+/issues(?:/(\d+)(/.*)?)?", path)
        if not m:
            continue
        n, rest = int(m.group(1)) if m.group(1) else None, m.group(2) or ""
        if n is None and isinstance(out, dict) and out.get("number"):   # a new issue
            created = int(out["number"])
            issues[created] = dict(number=created, id=out.get("id"), title=f.get("title", ""), url=out.get("html_url", ""), state="open", state_reason=None,
                                   created_at=out.get("created_at") or now, updated_at=now, closed_at=None, edited=None, author=actor,
                                   labels=[x.split("=", 1)[1] for x in _flag(op, "-f") if x.startswith("labels[]=")],
                                   assignees=[], parent=None, blocked_by=[], body=f.get("body", ""), comments=[])
            by_db[out.get("id")] = created
            for l in issues[created]["labels"]:
                ev(created, "labeled", l)
            touched.append(created)
        elif n in issues and rest == "" and "-X" in op and op[op.index("-X") + 1] == "PATCH":
            i = issues[n]
            i.update({k: f[k] for k in ("title", "body") if k in f}, edited=now, updated_at=now)
            touched.append(n)
        elif n in issues and rest == "/sub_issues" and created:
            issues[created]["parent"] = n
            touched.append(created)
        elif n in issues and rest.startswith("/dependencies/blocked_by"):
            b = by_db.get(int(f["issue_id"])) if "issue_id" in f else by_db.get(int(rest.rsplit("/", 1)[1])) if rest.count("/") == 3 else None
            if b is not None:
                bl = [x for x in issues[n]["blocked_by"] if x != b]
                issues[n].update(blocked_by=bl + ([b] if "issue_id" in f else []), updated_at=now)
                touched.append(n)
    return dict(raw, issues=list(issues.values()), events=events), deadlines, list(dict.fromkeys(touched))


def replace_issues(raw, issues, events):
    """PC-135: the raw copy with the given issues (and all their events) replaced by a newer reading of them; new ones are added."""
    got = {i["number"]: i for i in issues}
    old = [got.pop(i["number"], i) for i in raw["issues"]]
    nums = {i["number"] for i in issues}
    return dict(raw, issues=old + list(got.values()), events=[e for e in raw["events"] if e["number"] not in nums] + list(events))


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
