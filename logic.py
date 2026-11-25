"""Pure logic of the cockpit: no network, no subprocess. Everything here is unit-tested."""
import bisect
import datetime as dt
import json
import logging
import math
import pathlib
import re
import shlex

log = logging.getLogger("cockpit")

PRIO = ["P1", "P2", "P3"]
PROTECTED = {"released", "cancelled", *PRIO}
STATES = ["wip", "unmerged", "failed", "merged"]  # set by the Dispatcher as issue labels; an issue carries at most one
STAGES = ["embryo", "released", *STATES, "cancelled"]
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


FINDING = "policy-finding"
FILED_BY = re.compile(r"<!--\s*filed-by:\s*(\S+?)\s*-->")


def is_finding(labels):
    """A task filed because a file contradicts a standing policy: released at once (spec 2b), but never a question or a decision."""
    return FINDING in labels and "question" not in labels and "type:decision" not in labels


NOT_DISPATCHABLE = ("question", "type:decision", "interactive-only")   # spec 2b: questions, decisions and the Task Queue's own tasks never go to a Worker
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
    not a question, decision or `interactive-only`, and released (stage, label, or its author label). Blockers are checked by is_blocked."""
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


# ---------- module pages ----------
MODID = re.compile(r"[a-z0-9-]+(/[a-z0-9-]+)*")
HLID = re.compile(r"wf-[a-z0-9-]+")
SVG_TAGS = {"svg", "defs", "marker", "path", "rect", "polygon", "text", "tspan", "g"}  # what the drawing helpers produce
SVG_ATTRS = {"class", "x", "y", "width", "height", "rx", "d", "points", "viewBox", "role", "aria-label", "id", "refX", "refY",
             "markerWidth", "markerHeight", "orient", "text-anchor", "dy", "marker-end", "marker-start", "style", "preserveAspectRatio"}
SVG_MAX = 200_000
_SAFE_VALUE = re.compile(r"[A-Za-z0-9 .,#:;()%_/<>'\u2019-]*")
_SAFE_STYLE = re.compile(r"(?:[a-z-]+:[A-Za-z0-9 .,#()-]+;?)+")


def module_stem(mid):
    """Module id -> the base name of its content files in docs/modules/ ('a/b' -> 'a-b'); None for an id that is not a plain slash path."""
    return mid.replace("/", "-") if isinstance(mid, str) and MODID.fullmatch(mid) else None


def module_paragraphs(text, desc=None):
    """The module's .txt (paragraphs separated by blank lines; the first is the short description), else its map `desc`, else []."""
    paras = [re.sub(r"\s*\n\s*", " ", p.strip()) for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return paras or ([desc] if desc else [])


def hl_ids(text):
    """The .hl file: one Workflow element id per line (blank lines and lines starting with # are skipped; anything else not an id is dropped)."""
    return [l for l in (x.strip() for x in (text or "").splitlines()) if HLID.fullmatch(l)]


def sanitise_svg(text):
    """A static drawing -> the same drawing rebuilt from allowed elements and attributes only, or None when it is unusable.
    No scripts, no external references (the only url() allowed is a marker in the same file), no DOCTYPE/entities."""
    import xml.etree.ElementTree as ET
    from xml.sax.saxutils import escape, quoteattr
    if not isinstance(text, str) or len(text) > SVG_MAX or re.search(r"<!(DOCTYPE|ENTITY)", text, re.I):
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    local = lambda t: t.rpartition("}")[2]
    if local(root.tag) != "svg":
        return None

    def attr_ok(k, v):
        if k not in SVG_ATTRS:
            return False
        if k in ("marker-end", "marker-start"):
            return re.fullmatch(r"url\(#[A-Za-z0-9_-]+\)", v) is not None
        if k == "style":
            return _SAFE_STYLE.fullmatch(v) is not None and "url" not in v
        return _SAFE_VALUE.fullmatch(v) is not None and "javascript" not in v.lower()

    def out(e):
        tag = local(e.tag)
        if tag not in SVG_TAGS:
            return ""
        at = "".join(f" {k}={quoteattr(v)}" for k, v in ((local(k), v) for k, v in e.attrib.items()) if attr_ok(k, v))
        if tag == "svg":
            at = re.sub(r' class="[^"]*"', "", at) + ' class="wf"'
        body = escape(e.text or "") if tag in ("text", "tspan") else ""
        for c in e:
            body += out(c) + (escape(c.tail or "") if tag in ("text", "tspan") else "")
        return f"<{tag}{at}>{body}</{tag}>"

    return out(root)


def group_files(paths, limit=8):
    """File paths -> [{dir, count, names}] one entry per folder (the repository root is '.'), sorted by folder; names are the first `limit`."""
    g = {}
    for p in sorted(paths):
        d, _, n = p.rpartition("/")
        g.setdefault(d or ".", []).append(n)
    return [dict(dir=d, count=len(n), names=n[:limit]) for d, n in sorted(g.items())]


CODE_EXT = (".html", ".css", ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".c", ".h", ".cc", ".cpp", ".rb", ".sh", ".swift", ".sql")


def file_kind(path):
    """'test' for files under a tests folder or named test_* / *_test.*, 'code' for other source files by extension, else 'other'."""
    parts = path.split("/")
    name = parts[-1]
    stem = name.rpartition(".")[0]
    if any(x in ("tests", "test", "__tests__") for x in parts[:-1]) or stem.startswith("test_") or stem.endswith("_test"):
        return "test"
    return "code" if name.endswith(CODE_EXT) else "other"


def biggest_files(sizes, n=3):
    """{path: lines} -> up to n of [{path, lines, kind}]: the biggest code files first; when there are fewer than n code files, the
    biggest test files fill up, then the biggest other files (each group by lines, largest first)."""
    rank = {"code": 0, "test": 1, "other": 2}
    rows = [dict(path=p, lines=v, kind=file_kind(p)) for p, v in sizes.items()]
    rows.sort(key=lambda r: (rank[r["kind"]], -r["lines"], r["path"]))
    return rows[:n]


def module_files(paths, assign, mid):
    """The tracked files the module map assigns to the module or one of its sub-modules."""
    return [p for p in paths if (m := map_path(p, assign)) == mid or m.startswith(mid + "/")]


def code_lines(sizes, assign, ids):
    """{path: physical lines} of the clone -> {module id: lines of its code files, sub-modules included (tests and documents not counted)}."""
    code = {p: v for p, v in sizes.items() if file_kind(p) == "code"}
    return {mid: sum(code[p] for p in module_files(list(code), assign, mid)) for mid in ids}


def snapshot_lines(metrics):
    """metrics.json of the code snapshot -> {module id: code lines}, test kinds left out (the fallback of code_lines without a clone)."""
    return {mid: sum(v.get("code", 0) for k, v in ((m.get("metrics") or {}).get("loc") or {}).items() if "test" not in k)
            for mid, m in (metrics.get("modules") or {}).items()}


def order_modules(mods, lines):
    """The one module order of every list (cards, chips, module navigation): modules by size (`lines` {id: code lines}), biggest
    first, equal sizes by name; unsized ones after them in their map order; virtual entries ("Unassigned") last. Top-level modules are
    ordered among themselves, the sub-modules of one module among their siblings only (PC-90: same rule, a sub-module never leaves its
    module); each top-level module is followed by its sub-modules, and its `children` list has the same order. Sets `lines` (None when
    unknown) on every module; returns a new list."""
    mods = [dict(m, lines=lines.get(m["id"])) for m in mods]
    pos = {m["id"]: i for i, m in enumerate(mods)}

    def by_size(ms):
        return sorted(ms, key=lambda m: (bool(m.get("virtual")), m["lines"] is None, -(m["lines"] or 0),
                                         m["name"].lower() if m["lines"] is not None else "", pos[m["id"]]))
    tops = by_size([m for m in mods if not parent_id(m["id"])])
    out = []
    for t in tops:
        subs = by_size([m for m in mods if m["id"].startswith(t["id"] + "/")])
        if "children" in t:
            t["children"] = [m["id"] for m in subs if m["id"] in t["children"]] + [c for c in t["children"] if c not in {m["id"] for m in subs}]
        out += [t] + subs
    seen = {m["id"] for m in out}
    return out + [m for m in mods if m["id"] not in seen]  # a sub-module without its parent listed is never dropped


def doc_modules(text):
    """The ids named by the `Modules:` line near the top of a document ([] when there is none)."""
    for line in (text or "").splitlines()[:15]:
        m = re.match(r"\s*\**Modules\**:\s*(.*)$", line, re.I)
        if m:
            return [x.strip().strip("`") for x in m.group(1).split(",") if x.strip()]
    return []


def doc_title(text, fallback=""):
    for line in (text or "").splitlines()[:30]:
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def module_docs(heads, mid, base):
    """{path: first text of the file} -> [{path, title, url}] of the documents whose Modules line names the module or a sub-module."""
    return [dict(path=p, title=doc_title(t, p.rpartition("/")[2]), url=f"{base}/blob/main/{p}")
            for p, t in sorted(heads.items()) if any(x == mid or x.startswith(mid + "/") for x in doc_modules(t))]


def _py_targets(path, node):
    """One entry per imported name: the dotted module paths (lists of parts) it may refer to, most specific first; the first that is a file wins."""
    import ast
    out = []
    if isinstance(node, ast.Import):
        out += [[a.name.split(".")[:n] for n in range(len(a.name.split(".")), 0, -1)] for a in node.names]
    elif isinstance(node, ast.ImportFrom):
        base = node.module.split(".") if node.module else []
        if node.level:
            pkg = path.split("/")[:-1]
            pkg = pkg[:len(pkg) - (node.level - 1)] if node.level > 1 else pkg
            base = pkg + base
        out += [[base + [a.name], base] for a in node.names]
    return out


def import_edges(sources, assign):
    """{path: Python source} -> [{src, dst, src_mod, dst_mod}]: one entry per (importing file, imported file) pair between two files of the
    given set that the module map assigns to different modules. Files that do not parse are skipped. Standard-library and third-party imports are not files here."""
    import ast
    known = set(sources)
    seen, out = set(), []
    for path, text in sorted(sources.items()):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for options in _py_targets(path, node):
                dst = next((f for parts in options if parts for b in ["/".join(parts)] for f in (b + ".py", b + "/__init__.py") if f in known), None)
                if not dst or dst == path or (path, dst) in seen:
                    continue
                seen.add((path, dst))
                a, b = map_path(path, assign), map_path(dst, assign)
                if a != b:
                    out.append(dict(src=path, dst=dst, src_mod=a, dst_mod=b))
    return out


def module_relations(edges, mid, examples=3):
    """-> {uses: [...], used_by: [...]}: the other modules this module's files import, and those that import its files. A module counts with its
    sub-modules (imports inside that tree are not shown), so a sub-module shows its parent's and its siblings' links. One entry per neighbour:
    {module, count (file pairs), examples (up to `examples` 'importer -> imported' pairs)}, most links first."""
    inside = lambda m: m == mid or m.startswith(mid + "/")
    res = {"uses": {}, "used_by": {}}
    for e in edges:
        a, b = inside(e["src_mod"]), inside(e["dst_mod"])
        if a == b:
            continue
        key, other = ("uses", e["dst_mod"]) if a else ("used_by", e["src_mod"])
        res[key].setdefault(other, []).append(f"{e['src']} -> {e['dst']}")
    return {k: [dict(module=m, count=len(v), examples=v[:examples]) for m, v in sorted(d.items(), key=lambda kv: (-len(kv[1]), kv[0]))] for k, d in res.items()}


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


HEAT_LEVELS = 4  # shades 1..4 above the empty shade 0


def shade_level(n, mx):
    """Heatmap shade 0..4 of a day's value n against the largest day mx (0 = none; mirrored by `shade` in web/index.html)."""
    if not n or n <= 0 or not mx or mx <= 0:
        return 0
    return min(HEAT_LEVELS, -(-HEAT_LEVELS * n // mx))


def activity_days(tasks, events, mod_of):
    """Task activity per day and module for the Timeline heatmap: one count per task created or closed (from the tasks) and per
    label/assign event (closed events are the tasks' `closed`). -> [{d, mod, n}]; an unreadable timestamp is skipped."""
    g = {}

    def add(ts, mod):
        try:
            d = dt.date.fromisoformat(str(ts)[:10]).isoformat()
        except ValueError:
            return
        g[(d, mod)] = g.get((d, mod), 0) + 1
    for t in tasks:
        add(t.get("created"), t["module"])
        if t.get("closed"):
            add(t["closed"], t["module"])
    for e in events:
        if e["kind"] != "closed":
            add(e["t"], mod_of.get(e["key"], "unassigned"))
    return [dict(d=d, mod=m, n=n) for (d, m), n in sorted(g.items())]


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


def filter_feed(feed, module=None, typ=None):
    return [e for e in feed if (not module or e["mod"] == module) and (not typ or e["type"] == typ)]


# ---------- code snapshot (metrics.json) ----------
def _g(d, *path):
    for p in path:
        d = d.get(p) if isinstance(d, dict) else None
    return d


def code_view(metrics, thresholds=None):
    """metrics.json -> {generated_at, commit, modules: {id: flat dict the Code tab shows, with its `bands`}}."""
    mods = {}
    for mid, m in (metrics.get("modules") or {}).items():
        x = m.get("metrics") or {}
        loc = x.get("loc") or {}
        top = (_g(x, "lint", "top_codes") or {}).items()
        worst = (_g(x, "complexity", "worst") or [{}])[0]
        mods[mid] = dict(
            name=m.get("name"), code=sum(v.get("code", 0) for v in loc.values()), codeFiles=sum(v.get("files", 0) for v in loc.values()),
            files=f"{x.get('files_total')} files ({_g(loc, 'python_source', 'files') or 0} source, {_g(loc, 'python_test', 'files') or 0} test)",
            cc=_g(x, "complexity", "max"), ccAt=f"{worst['path']}:{worst['line']} {worst['name']}" if worst.get("path") else None,
            mean=_g(x, "complexity", "mean"), dfe=_g(x, "complexity", "rated_DEF"), mi=_g(x, "maintainability", "mean_mi"),
            lint=_g(x, "lint", "total"), lintNote=f"{_g(x, 'lint', 'per_kloc')} per 1,000 lines" + ("; " + ", ".join(f"{k} {v}" for k, v in list(top)[:2]) if top else ""),
            dead=_g(x, "dead_code", "candidates"), cyc=_g(x, "structure", "files_in_cycles"), dup=_g(x, "duplication", "pct_of_code_lines"),
            doc=_g(x, "docstring_coverage", "pct_documented"), cov=_g(x, "test_coverage", "pct_statements_covered"), covN=_g(x, "test_coverage", "statements"),
            pyN=_g(loc, "python_source", "files") or 0, filesN=x.get("files_total") or 0,
            churn=_g(x, "churn", "commits_touching_files"), churnNote=f"{_g(x, 'churn', 'commits_touching_files')} commits touching {_g(x, 'churn', 'files_touched')} files in {_g(x, 'churn', 'window_days')} days",
            testRatio=_g(x, "tests", "test_to_code_ratio_python"), testsNote=f"{_g(x, 'tests', 'source_files_with_mirror_test')} source files have a mirror test",
            hot=[[h["path"], h["churn"], h["cc_max"]] for h in (_g(x, "hotspots", "top") or [])[:5]])
        mods[mid]["bands"] = module_bands(mods[mid], thresholds)
    return dict(generated_at=metrics.get("generated_at"), commit=metrics.get("git_commit_short"), modules=mods)


def snapshot_age(generated_at, now, max_h=None):
    """The snapshot's `generated_at` -> dict(age_h, stale, max_age_h): stale when older than max_h (default: the 24 hours of the standing
    policy) or when its time is missing or unreadable."""
    max_h = GAPS_MAX_AGE_H if max_h is None else max_h
    age = _age_h(generated_at, now) if generated_at else None
    return dict(age_h=None if age is None else round(age, 1), stale=age is None or age > max_h, max_age_h=max_h)


def collect_command(value, what="code_collect"):
    """A command project setting (`code_collect`, `compliance`: a list of arguments, or one string split like a shell would) -> argv list,
    or None when unset. Raises ValueError for anything else."""
    if value in (None, "", []):
        return None
    if isinstance(value, str):
        return shlex.split(value)
    if isinstance(value, list) and all(isinstance(x, str) and x for x in value):
        return list(value)
    raise ValueError(f"{what}: expected a command string or a list of strings (got {value!r})")


def refresh_outcome(exit_code, metrics_after, now, max_h=None):
    """Result of one run of the collector command -> (ok, reason). Ok only when the command succeeded and left a readable, fresh snapshot."""
    if exit_code is None:
        return False, "collector command could not run or timed out"
    if exit_code != 0:
        return False, f"collector command exited with {exit_code}"
    if not isinstance(metrics_after, dict):
        return False, "collector command succeeded but metrics.json is missing or unreadable"
    a = snapshot_age(metrics_after.get("generated_at"), now, max_h)
    if a["stale"]:
        return False, "collector command succeeded but the snapshot is still stale (generated_at " + str(metrics_after.get("generated_at")) + ")"
    return True, ""


# ---------- GitHub payloads -> raw issues / events ----------
def norm_issue(n):
    """One GraphQL issue node -> the raw issue dict derive() takes."""
    return dict(number=n["number"], id=n.get("databaseId"), title=n["title"], url=n["url"], state=n["state"].lower(),
                state_reason=(n.get("stateReason") or "").lower() or None, created_at=n["createdAt"], updated_at=n.get("updatedAt"), closed_at=n.get("closedAt"), edited=n.get("lastEditedAt"),
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
BOT = ("[bot]", "noreply@anthropic")  # default of rules.bots
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
    """The `rules` key of the --modules JSON -> {groups: [{name, res, paths}], managers: [lower-case], bots: [lower-case]}, or None when absent/malformed."""
    r = (cfg or {}).get("rules")
    if not isinstance(r, dict) or not isinstance(r.get("groups"), list):
        return None
    gs = [dict(name=str(g["name"]), paths=[str(p) for p in g["paths"]], res=[glob_re(str(p)) for p in g["paths"]])
          for g in r["groups"] if isinstance(g, dict) and g.get("name") and isinstance(g.get("paths"), list)]
    bots = [str(b).lower() for b in r["bots"]] if isinstance(r.get("bots"), list) else list(BOT)
    return dict(groups=gs, managers=[str(m).lower() for m in r.get("managers", [])], bots=bots) if gs else None


def rule_group(path, groups):
    """Name of the first group whose globs match `path`, else None."""
    return next((g["name"] for g in groups if any(r.match(path) for r in g["res"])), None)


def rule_pathspecs(groups):
    """git pathspecs covering all globs (`:(glob)` magic); the Python matching stays authoritative."""
    return [":(glob)" + p for g in groups for p in g["paths"]]


def is_agent_assisted(body, name, email, bots=BOT):
    """A Co-Authored-By trailer in the message, or an author matching one of `bots` (substrings, lower-case)."""
    who = f"{name} {email}".lower()
    return bool(TRAILER.search(body or "")) or any(b in who for b in bots)


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


def parse_rule_changes(numstat_txt, diff_txt, groups, managers, bots=BOT):
    """Outputs of two `git log` runs (LOG_FMT + --numstat / -p -U0) -> rule changes, newest first; commits without matching files are dropped."""
    diffs = {h[0]: rest for h, rest in _records(diff_txt)}
    out = []
    for (sha, date, name, email, subj, body), rest in _records(numstat_txt):
        files = parse_numstat(rest, groups)
        if not files:
            continue
        agent, mgr = is_agent_assisted(body, name, email, bots), is_manager(name, email, managers)
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
CARD_CHURN_DAYS = 10   # the churn window the module cards show, and the collector's first window (owner, 2026-11-24)
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
    """churn.json -> {windows, card, modules: {id: {windows: {'10': {..entry, rel, band_abs, band_rel}}, size, lines}}, unassigned, excluded, mapping_check}.
    `card` is the window the cards show (CARD_CHURN_DAYS as a string). Cards show `total` (the module with its sub-modules; `own` when there is no total). Bands are ranked per level (top-level
    modules among themselves, sub-modules among themselves), by churn (abs) and by churn per current line (rel)."""
    wins = [str(w) for w in (data.get("windows") or [CARD_CHURN_DAYS, 30, 90])]
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
    return dict(windows=wins, card=str(CARD_CHURN_DAYS), modules=mods, unassigned=data.get("unassigned"), excluded=data.get("excluded"), mapping_check=data.get("mapping_check"),
                changes=changes_view(data.get("modules")))


def _weeks(rows):
    """One series list from churn.json -> up to 52 clean weeks {w, commits, added, removed, tests, docs, src}; None when it is not a list.
    src = added + removed - tests - docs (never below 0)."""
    if not isinstance(rows, list):
        return None
    out = []
    for r in rows[-52:]:
        if not isinstance(r, dict):
            continue
        n = {k: _num(r.get(k)) or 0 for k in ("commits", "added", "removed", "tests", "docs")}
        out.append(dict(n, w=str(r.get("w", "")), src=max(n["added"] + n["removed"] - n["tests"] - n["docs"], 0)))
    return out


def changes_view(modules):
    """Panels of the Code > Changes tab from the modules of churn.json (the optional `series.weekly` key).
    Top-level modules show `total`, sub-modules `own`. Order: top-level by churn of the last 30 days (descending, id breaks ties), each
    followed by its sub-modules in the same order. A module without usable series is flagged missing. -> [{id, level, weeks|None, churn30, churn52, commits90}]."""
    def panel(mid, m):
        sub = "/" in mid
        wk = ((m.get("series") or {}).get("weekly") or {}) if isinstance(m.get("series"), dict) else {}
        weeks = _weeks(wk.get("own" if sub else "total"))
        e = (m.get("own" if sub else "total") or m.get("own") or {}).get("30")
        c30 = _num(e.get("churn")) if isinstance(e, dict) else None
        if c30 is None and weeks:
            c30 = sum(x["added"] + x["removed"] for x in weeks[-4:])
        return dict(id=mid, level=int(sub), weeks=weeks or None, churn30=c30 or 0,
                    churn52=sum(x["added"] + x["removed"] for x in weeks) if weeks else None,
                    commits90=sum(x["commits"] for x in weeks[-13:]) if weeks else None)
    ps = {mid: panel(mid, m) for mid, m in (modules or {}).items() if isinstance(m, dict)}
    key = lambda i: (-ps[i]["churn30"], i)
    out = []
    for t in sorted((i for i in ps if "/" not in i), key=key):
        out.append(ps[t])
        out += [ps[i] for i in sorted((i for i in ps if i.startswith(t + "/")), key=key)]
    out += [ps[i] for i in sorted((i for i in ps if "/" in i and i.split("/")[0] not in ps), key=key)]  # orphan sub-modules: still shown
    return out


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


# ---------- Dispatcher settings (spec 3c, 3f, 3i, 3j, 4a, 6a) ----------
SETTING_MINIMUMS = {"runaway_minutes": 30, "idle_minutes": 10, "start_minutes": 1}  # a rule of the page only; the Dispatcher does not enforce them
SETTING_LABELS = {"enabled": "enabled", "interval_minutes": "interval", "retries": "retries", "workers": "workers",
                  "runaway_minutes": "runaway limit", "idle_minutes": "no-activity limit", "start_minutes": "no-start limit"}


def validate_settings(form, defaults, workers_cap):
    """The seven Dispatcher settings from a form -> a dict of the same keys, or ValueError with a clear message.
    `defaults` gives the keys and which are switches; whole numbers only, retries from 0, the rest from 1, workers up to the image's cap."""
    out = {}
    for k, default in defaults.items():
        v, name = form.get(k), SETTING_LABELS.get(k, k)
        if isinstance(default, bool):
            if not isinstance(v, bool):
                raise ValueError(f"{name} must be on or off")
            out[k] = v
            continue
        if isinstance(v, str) and re.fullmatch(r"\s*\d+\s*", v):
            v = int(v)
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{name} must be a whole number")
        low = SETTING_MINIMUMS.get(k, 0 if k == "retries" else 1)
        if v < low:
            raise ValueError(f"{name} must be at least {low}" + (" minutes" if k.endswith("_minutes") else ""))
        if k == "workers" and v > workers_cap:
            raise ValueError(f"workers must be at most {workers_cap} (the worker users defined in the image)")
        out[k] = v
    return out


# ---------- Module-card settings (Settings tab, section "Module cards") ----------
CARD_CHURN_COLOURS = ("size", "absolute")  # size = changed lines / current lines; absolute = lines added + removed
# Quality bands (docs/contracts/quality-bands.md): metric -> (direction, ok, warn); "low" = lower is better
QUALITY_DEFAULTS = {
    "complexity": {"direction": "low", "ok": 10, "warn": 20},
    "maintainability": {"direction": "high", "ok": 20, "warn": 10},
    "lint": {"direction": "low", "ok": 10, "warn": 30},
    "dead": {"direction": "low", "ok": 2, "warn": 5},
    "cycles": {"direction": "low", "ok": 0, "warn": 0},
    "duplication": {"direction": "low", "ok": 3, "warn": 5},
    "docstrings": {"direction": "high", "ok": 80, "warn": 50},
    "coverage": {"direction": "high", "ok": 80, "warn": 60},
    "tests": {"direction": "high", "ok": 0.5, "warn": 0.2},
}
QUOTA_SETTING_DEFAULTS = {"quota_plan_start": "2026-11-01",  # the first day of the current plan (and so of the same quota; owner, 2026-11-25, PC-111)
                          "quota_weak_readings": 20}          # an estimate resting on fewer readings is marked weak
CARD_SETTING_DEFAULTS = {"churn_colour": "size", "quality_thresholds": QUALITY_DEFAULTS, **QUOTA_SETTING_DEFAULTS}  # churn: what the overview showed before the option moved to Settings


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def validate_thresholds(raw, strict=True):
    """quality_thresholds from a form/file -> a full dict (a missing entry gets its default). strict: ValueError on an unknown metric
    or an invalid entry; otherwise an invalid entry falls back to its default."""
    raw = {} if raw is None else raw
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("quality thresholds must be an object")
        raw = {}
    if strict and (bad := [k for k in raw if k not in QUALITY_DEFAULTS]):
        raise ValueError("unknown quality metric: " + ", ".join(map(str, bad)))
    out = {}
    for k, d in QUALITY_DEFAULTS.items():
        e = raw.get(k)
        if e is None:
            out[k] = dict(d)
            continue
        ok = (isinstance(e, dict) and e.get("direction") == d["direction"] and _is_num(e.get("ok")) and _is_num(e.get("warn"))
              and (e["ok"] <= e["warn"] if d["direction"] == "low" else e["ok"] >= e["warn"]))
        if ok:
            out[k] = dict(direction=d["direction"], ok=e["ok"], warn=e["warn"])
        elif strict:
            raise ValueError(f"quality threshold {k}: direction must be {d['direction']}, ok and warn numbers with ok "
                             + ("<= warn" if d["direction"] == "low" else ">= warn"))
        else:
            out[k] = dict(d)
    return out


def validate_card_settings(form, strict=True):
    """The module-card options from a form -> a dict with every key of CARD_SETTING_DEFAULTS, or ValueError with a clear message."""
    v = form.get("churn_colour")
    if v not in CARD_CHURN_COLOURS:
        raise ValueError("churn colour must be one of: " + ", ".join(CARD_CHURN_COLOURS))
    return {"churn_colour": v, "quality_thresholds": validate_thresholds(form.get("quality_thresholds"), strict), **validate_quota_settings(form, strict)}


def validate_quota_settings(form, strict=True):
    """quota_plan_start (YYYY-MM-DD) and quota_weak_readings (a whole number, at least 1) from a form/file; a missing one gets its default.
    strict: ValueError on an invalid value; otherwise it falls back to its default."""
    out = dict(QUOTA_SETTING_DEFAULTS)
    d, n = form.get("quota_plan_start"), form.get("quota_weak_readings")
    if d not in (None, ""):
        try:
            out["quota_plan_start"] = dt.date.fromisoformat(str(d)).isoformat()
        except ValueError:
            if strict:
                raise ValueError("plan start must be a date YYYY-MM-DD") from None
    if n not in (None, ""):
        try:
            k = int(n) if not isinstance(n, bool) and float(n) == int(float(n)) else 0
        except (TypeError, ValueError):
            k = 0
        if k >= 1:
            out["quota_weak_readings"] = k
        elif strict:
            raise ValueError("readings for a firm estimate must be a whole number of at least 1")
    return out


BAND_WORDS = {"ok": "normal", "warn": "borderline", "bad": "out of range", "none": "no figure"}


def band_of(value, t):
    """(band, word) of a figure against a threshold entry {direction, ok, warn}; no figure (None, not a number) -> none."""
    if not _is_num(value):
        band = "none"
    elif t["direction"] == "low":
        band = "ok" if value <= t["ok"] else "warn" if value <= t["warn"] else "bad"
    else:
        band = "ok" if value >= t["ok"] else "warn" if value >= t["warn"] else "bad"
    return dict(band=band, word=BAND_WORDS[band])


def _per_kloc(n, code):
    return n * 1000 / code if _is_num(n) and _is_num(code) and code > 0 else None


def module_bands(m, thresholds=None):
    """The bands of one code_view module dict, one per metric key of QUALITY_DEFAULTS; a module with no files measured has none."""
    t = thresholds or QUALITY_DEFAULTS
    figures = dict(complexity=m.get("cc"), maintainability=m.get("mi"), lint=_per_kloc(m.get("lint"), m.get("code")),
                   dead=_per_kloc(m.get("dead"), m.get("code")), cycles=m.get("cyc"), duplication=m.get("dup"),
                   docstrings=m.get("doc"), coverage=m.get("cov"), tests=m.get("testRatio"))
    measured = bool(m.get("filesN"))
    return {k: band_of(figures[k] if measured else None, t[k]) for k in QUALITY_DEFAULTS}


# -- Token Usage tab: the proxy's token counts (spec dispatcher-and-workers 3h, 4h) --
USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
WINDOW_SECONDS = 5 * 3600


def _json_lines(text):
    """The JSON objects of a JSON-lines text; an unreadable line is skipped. None for no text (missing file)."""
    if text is None:
        return None
    out = []
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _epoch(s):
    """Epoch seconds of an ISO time (a trailing Z is UTC); a time without a zone is local time. None if unreadable."""
    try:
        return dt.datetime.fromisoformat(re.sub(r"Z$", "+00:00", s)).astimezone().timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def _counts(rec):
    c = {k: rec.get(k) if isinstance(rec.get(k), int) and not isinstance(rec.get(k), bool) else 0 for k in USAGE_FIELDS}
    return dict(c, total=sum(c.values()))


def _add(into, c):
    for k in (*USAGE_FIELDS, "total"):
        into[k] = into.get(k, 0) + c[k]
    return into


HISTORY_DAYS = 90


def _pct(block, key):
    v = block.get(key) if isinstance(block, dict) else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _iso(epoch):
    """An epoch-seconds number as an ISO UTC time to the minute, or None."""
    if not isinstance(epoch, (int, float)) or isinstance(epoch, bool):
        return None
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat(timespec="minutes")


MONTHLY_MONTHS = 2      # whole months before the current one that are shown per month


def _period(t, anchor):
    """Where a reading at epoch t falls, seen from the newest reading `anchor`: (level, key, label) or None when older than shown.
    Per hour for the current day; per day for the earlier days of the current (Monday-first) week; per week for the earlier
    weeks of the current month (the first one cut at the month's start); per month for the MONTHLY_MONTHS months before the current one."""
    utc = dt.timezone.utc
    a, x = dt.datetime.fromtimestamp(anchor, utc), dt.datetime.fromtimestamp(t, utc)
    hour0 = a.replace(hour=0, minute=0, second=0, microsecond=0)
    week0 = (a - dt.timedelta(days=a.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    month0 = a.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    m = month0.year * 12 + month0.month - 1 - MONTHLY_MONTHS
    older = dt.datetime(m // 12, m % 12 + 1, 1, tzinfo=utc)
    if x >= hour0:
        return "hour", x.strftime("%Y-%m-%dT%H:00"), x.strftime("%Y-%m-%d %H:00")
    if x >= week0:
        return "day", x.strftime("%Y-%m-%d"), x.strftime("%Y-%m-%d")
    if x >= month0:
        mon = max((x - dt.timedelta(days=x.weekday())).replace(hour=0, minute=0, second=0, microsecond=0), month0)
        return "week", mon.strftime("%Y-%m-%d"), "week of " + mon.strftime("%Y-%m-%d")
    if x >= older:
        return "month", x.strftime("%Y-%m"), x.strftime("%Y-%m")
    return None


def _periods(reads):
    """Readings (oldest first) aggregated in the graded periods of _period(): the count and, per window, first, last, lowest and highest
    percentage, and the windows' reset times (of the last reading). Only periods with readings get a row: an hour without a recorded
    change has none, and nothing is carried forward into it (owner, 2026-11-24, reversing F-680)."""
    out = {}
    anchor = reads[-1]["t"] if reads else 0
    for r in reads:
        p = _period(r["t"], anchor)
        if p is None:
            continue
        level, key, label = p
        h = out.setdefault((level, key), dict(level=level, key=key, label=label, readings=0, five_hour={}, seven_day={}, reset5=None, reset7=None))
        h["readings"] += 1
        for k in ("five_hour", "seven_day"):
            v = r[k]
            if v is not None:
                s = h[k]
                s.update(first=s.get("first", v), last=v, low=min(s.get("low", v), v), high=max(s.get("high", v), v))
        h["reset5"], h["reset7"] = r["reset5"] or h["reset5"], r["reset7"] or h["reset7"]
    return list(out.values())


def _held_peaks(periods):
    """{(level, key): {window: highest}} of the quota periods (oldest first, as _periods() lists them), with an hour of the current day that
    has no reading holding the last value before it (from the previous hour with readings, else the newest earlier period); the quota rows
    themselves list only the hours with readings."""
    w = ("five_hour", "seven_day")
    out, carry, hours = {}, {}, {}
    for p in periods:
        out[(p["level"], p["key"])] = {k: p[k].get("high") for k in w}
        if p["level"] == "hour":
            hours[p["key"]] = p
        else:
            carry = {k: p[k].get("last", carry.get(k)) for k in w}
    if hours:
        day, last = min(hours)[:10], int(max(hours)[11:13])
        for h in range(last + 1):
            key = "%sT%02d:00" % (day, h)
            if key in hours:
                carry = {k: hours[key][k].get("last", carry.get(k)) for k in w}
            else:
                out[("hour", key)] = dict(carry)
    return out


def _quota(quota_text, end_day):
    """The vendor's readings -> (vendor window ends as epoch seconds, report dict or None for no file). end_day: the last day of the 90."""
    recs = _json_lines(quota_text)
    if recs is None:
        return [], None
    reads, resets = [], []
    for rec in recs:
        t = _epoch(rec.get("ts"))
        if t is None:
            continue
        f, w = rec.get("five_hour"), rec.get("seven_day")
        reads.append(dict(t=t, ts=dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds"),
                          five_hour=_pct(f, "used_percentage"), seven_day=_pct(w, "used_percentage"),
                          reset5=_iso(f.get("resets_at") if isinstance(f, dict) else None),
                          reset7=_iso(w.get("resets_at") if isinstance(w, dict) else None)))
        r = f.get("resets_at") if isinstance(f, dict) else None
        if isinstance(r, (int, float)) and not isinstance(r, bool):
            resets.append(r)
    reads.sort(key=lambda r: r["t"])
    ends = []
    for r in sorted(resets):  # the same window's reset time can wobble by seconds: merge ends closer than 10 minutes
        if not ends or r - ends[-1] > 600:
            ends.append(r)
    peaks = {}  # vendor window end -> the highest 5-hour percentage read in it; a reading without a reset time belongs to no window
    for rec in recs:
        f = rec.get("five_hour")
        r, v = (f.get("resets_at"), _pct(f, "used_percentage")) if isinstance(f, dict) else (None, None)
        if _epoch(rec.get("ts")) is not None and v is not None and isinstance(r, (int, float)) and not isinstance(r, bool):
            e = ends[bisect.bisect_right(ends, r) - 1]
            peaks[e] = max(peaks.get(e, v), v)
    if end_day is None and reads:
        end_day = dt.datetime.fromtimestamp(reads[-1]["t"], dt.timezone.utc).date()
    days = {}
    if end_day is not None:
        for i in range(HISTORY_DAYS):
            days[(end_day - dt.timedelta(days=HISTORY_DAYS - 1 - i)).isoformat()] = dict(five_hour=None, seven_day=None)
    for r in reads:
        d = days.get(dt.datetime.fromtimestamp(r["t"], dt.timezone.utc).date().isoformat())
        if d is not None:
            for k in ("five_hour", "seven_day"):
                if r[k] is not None and (d[k] is None or r[k] > d[k]):
                    d[k] = r[k]
    return ends, dict(days=[dict(key=k, **v) for k, v in days.items()], periods=_periods(reads), anchor=reads[-1]["t"] if reads else None,
                      windows=[dict(end=e, peak=v) for e, v in sorted(peaks.items())])


SESSIONS_SHOWN = 20


def _message_counts(rec):
    return dict(_counts(rec), messages=1)


def _period_skeleton(anchor):
    """Every graded period of _period() up to `anchor`, oldest first: (level, key, label). Hours still to come are not listed."""
    out, seen = [], set()
    t = anchor - anchor % 3600 - 3 * 31 * 86400
    while t <= anchor:
        p = _period(t, anchor)
        if p is not None and p[:2] not in seen:
            seen.add(p[:2])
            out.append(p)
        t += 3600
    return out


def record_kind(rec):
    """A transcript-usage record's `kind`; a record without one (earlier runs) is headless when its source label starts with `headless`."""
    k = rec.get("kind")
    return k if k in ("interactive", "headless") else "headless" if str(rec.get("source") or "").startswith("headless") else "interactive"


def transcript_report(text, quota=None, aggregate=None):
    """The counts of interactive sessions (contract transcript-usage.jsonl) -> dict(periods, sources, sessions, aggregate_days, headless), or None when
    neither file exists. periods: the graded rows of _period(), anchored on the newest quota reading (else the newest message or aggregate
    day), each with the message count, the four counts, their sum and the highest 5-hour and weekly percentage of the same period from
    `quota` (the report of _quota()); on a day, week or month row the 5-hour value is instead the mean of the peaks of the vendor 5-hour
    windows ending in the period (five_hour_windows: their number, five_hour_high: the highest; a window without readings is not counted).
    sources, sessions: the last HISTORY_DAYS days; sessions are the SESSIONS_SHOWN with the most tokens.
    aggregate: the text of stats-cache-usage.jsonl (its contract): its token totals per day count in a period's `total` and `aggregate`
    only (no split, no messages), never in sources or sessions; a day with per-message records ignores them (the exact data wins).
    aggregate_days: {day: tokens} of the aggregates used. Only records of kind interactive (record_kind) count in periods, sources and
    sessions; headless: the graded rows of the headless records alike (no aggregates), [] when there are none."""
    recs, agg = _json_lines(text), _json_lines(aggregate)
    if recs is None and agg is None:
        return None
    msgs = []
    for rec in recs or []:
        t = _epoch(rec.get("ts"))
        if t is not None:
            msgs.append((t, str(rec.get("source") or "?"), str(rec.get("session") or "?"), _message_counts(rec), record_kind(rec) == "headless"))
    exact = {dt.datetime.fromtimestamp(m[0], dt.timezone.utc).date().isoformat() for m in msgs}
    days = {}
    for rec in agg or []:
        d, n = rec.get("day"), rec.get("tokens")
        if rec.get("source") == "stats-cache" and isinstance(n, int) and not isinstance(n, bool) and _epoch(f"{d}T00:00:00+00:00") is not None and d not in exact:
            days[d] = days.get(d, 0) + n
    anchor = (quota or {}).get("anchor") or max([m[0] for m in msgs] + [_epoch(f"{d}T00:00:00+00:00") for d in days], default=None)
    empty = lambda: {k: 0 for k in ("messages", *USAGE_FIELDS, "total")}
    peaks = _held_peaks((quota or {}).get("periods", []))
    skeleton = _period_skeleton(anchor) if anchor is not None else []
    rows, hrows = ({p[:2]: dict(level=p[0], key=p[1], label=p[2], aggregate=0, **empty()) for p in skeleton} for _ in range(2))
    sources, sessions = {}, {}
    since = anchor - HISTORY_DAYS * 86400 if anchor is not None else None
    for t, src, ses, c, headless in msgs:
        p = _period(t, anchor)
        if p is not None and p[:2] in rows:
            _add_messages((hrows if headless else rows)[p[:2]], c)
        if t >= since and not headless:
            _add_messages(sources.setdefault(src, empty()), c)
            row = sessions.setdefault((src, ses), dict(source=src, **empty()))
            _add_messages(row, c)
    for d, n in days.items():
        p = _period(_epoch(f"{d}T00:00:00+00:00"), anchor)
        if p is not None and p[:2] in rows:
            rows[p[:2]]["aggregate"] += n
            rows[p[:2]]["total"] += n
    wins = {}
    for w in (quota or {}).get("windows", []) if anchor is not None else []:
        p = _period(w["end"], anchor)
        if p is not None and p[0] != "hour":
            wins.setdefault(p[:2], []).append(w["peak"])
    for k, row in [(k, r) for k in rows for r in (rows[k], hrows[k])]:  # the interactive rows and the headless ones alike (PC-77)
        q = peaks.get(k, {})
        row["five_hour"], row["seven_day"] = q.get("five_hour"), q.get("seven_day")
        if row["level"] != "hour":  # the mean of the peaks of the vendor 5-hour windows ending in the period (owner, 2026-11-25, PC-110)
            ws = wins.get(k, [])
            row["five_hour"] = round(sum(ws) / len(ws), 1) if ws else None
            row["five_hour_windows"], row["five_hour_high"] = len(ws), max(ws, default=None)
    top = sorted(sessions.items(), key=lambda kv: (-kv[1]["total"], kv[0]))[:SESSIONS_SHOWN]
    return dict(periods=_history(rows.values(), [m[0] for m in msgs if not m[4]] + [_epoch(f"{d}T00:00:00+00:00") for d in days]),
                headless=_history(hrows.values(), [m[0] for m in msgs if m[4]]), sources=[dict(key=k, **v) for k, v in sorted(sources.items(), key=lambda kv: (-kv[1]["total"], kv[0]))],
                sessions=[dict(v, key=k[1]) for k, v in top], aggregate_days=dict(sorted(days.items())))


def _history(rows, times):
    """The rows of the Interactive sessions history: none before the first month with data (all keys start with YYYY-MM), and no hour
    without messages (as in Today's readings)."""
    if not times:
        return []
    first = dt.datetime.fromtimestamp(min(times), dt.timezone.utc).strftime("%Y-%m")
    return [r for r in rows if r["key"][:7] >= first and not (r["level"] == "hour" and not r["messages"] and not r["total"])]


def _add_messages(into, c):
    for k in ("messages", *USAGE_FIELDS, "total"):
        into[k] += c[k]


MONTHS_SHOWN = 3                          # owner, 2026-11-25 (PC-113): the current month and the two before it
UNATTENDED_KINDS = ("headless", "unattended")  # a transcript record whose `kind` (PC-77) is one of these is unattended, not interactive
UNASSIGNED = "unassigned"                  # the row group of transcript sources and aggregate days no project claims


def _months(today):
    y, m = today.year, today.month
    out = []
    for _ in range(MONTHS_SHOWN):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def source_project(label, projects):
    """The project a transcript source (its label, the transcript folder's name) belongs to, or UNASSIGNED. projects: [dict(name, dir,
    sources)]. An explicit `sources` list wins; otherwise the default folder rule: the label is the project's directory name or the tool's
    encoded path of it (ends with "-DIR") or of a worktree inside it ("-DIR--..."); the longest matching directory wins."""
    for p in projects:
        if label in (p.get("sources") or ()):
            return p["name"]
    hits = [p for p in projects if p.get("dir") and (label == p["dir"] or label.endswith("-" + p["dir"]) or f"-{p['dir']}--" in label
                                                    or label.startswith(p["dir"] + "--"))]
    return max(hits, key=lambda p: len(p["dir"]))["name"] if hits else UNASSIGNED


def monthly_by_project(projects, transcripts, aggregates, today):
    """The Token Usage card "Per project, by month". projects: [dict(name, dir, sources, start (YYYY-MM or None), usage (proxy file text or
    None), plog (processing log text or None))]; transcripts, aggregates: the texts of every transcript-usage and stats-cache-usage file
    (None for a missing one; a message or day seen in several files counts once); today: a date (UTC).
    -> dict(months, projects=[dict(name, rows)]), a row per month newest first: dict(month, state) with state "before_start" (the project's
    start month is later), "no_data" (no source exists at all) or "data" with the four counts, `total`, `interactive` (transcripts),
    `unattended` (proxy and transcript records of an unattended kind), `aggregate` (stats cache, no split: in `total` only), `tasks`
    (launched that month) and `last_day`. total = interactive + unattended + aggregate. An aggregate day goes to the only project started
    by its month, else to UNASSIGNED; so do transcript sources no project claims. The UNASSIGNED group is listed only when it has tokens."""
    months = _months(today)
    names = [p["name"] for p in projects]
    blank = lambda: dict({k: 0 for k in (*USAGE_FIELDS, "total", "interactive", "unattended", "aggregate", "tasks")}, last_day=None)
    acc = {n: {m: blank() for m in months} for n in names + [UNASSIGNED]}

    def put(name, day, c, kind):
        r = acc[name].get(day[:7])
        if r is None:
            return
        if kind != "aggregate":
            _add(r, c)
        else:
            r["total"] += c["total"]
        r[kind] += c["total"]
        if c["total"] and (r["last_day"] is None or day > r["last_day"]):
            r["last_day"] = day
    day_of = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()
    seen, exact = set(), set()
    for text in transcripts:
        for rec in _json_lines(text) or []:
            t, key = _epoch(rec.get("ts")), (rec.get("session"), rec.get("agent"), rec.get("message"))
            if t is None or key in seen:
                continue
            seen.add(key)
            exact.add(day_of(t))
            put(source_project(str(rec.get("source") or "?"), projects), day_of(t), _counts(rec),
                "unattended" if rec.get("kind") in UNATTENDED_KINDS else "interactive")
    agg_seen = set()
    for text in aggregates:
        for rec in _json_lines(text) or []:
            d, n = rec.get("day"), rec.get("tokens")
            if (rec.get("source") != "stats-cache" or not isinstance(n, int) or isinstance(n, bool) or _epoch(f"{d}T00:00:00+00:00") is None
                    or d in exact or (d, rec.get("model")) in agg_seen):
                continue
            agg_seen.add((d, rec.get("model")))
            live = [p["name"] for p in projects if not p.get("start") or p["start"] <= d[:7]]
            put(live[0] if len(live) == 1 else UNASSIGNED, d, dict(total=n), "aggregate")
    for p in projects:
        for rec in _json_lines(p.get("usage")) or []:
            t = _epoch(rec.get("ts"))
            if t is not None:
                put(p["name"], day_of(t), _counts(rec), "unattended")
        for rec in _json_lines(p.get("plog")) or []:
            t = _epoch(rec.get("time"))
            if t is not None and rec.get("state") == "launched" and day_of(t)[:7] in acc[p["name"]]:
                acc[p["name"]][day_of(t)[:7]]["tasks"] += 1
    shared = any(x is not None for x in (*transcripts, *aggregates))
    out = []
    for p in projects:
        some = shared or p.get("usage") is not None or p.get("plog") is not None
        out.append(dict(name=p["name"], rows=[dict(month=m, state="before_start") if p.get("start") and m < p["start"] else
                                               dict(acc[p["name"]][m], month=m, state="data" if some else "no_data") for m in months]))
    if any(acc[UNASSIGNED][m]["total"] for m in months):
        out.append(dict(name=UNASSIGNED, rows=[dict(acc[UNASSIGNED][m], month=m, state="data") for m in months]))
    return dict(months=months, projects=out)


def usage_report(usage_text, plog_text, goodbye_tokens, quota_text=None):
    """The Usage tab. usage_text / plog_text / quota_text: the file contents or None when missing; goodbye_tokens(folder) -> the `tokens` of the
    task's good-bye note or None. -> dict(days, windows (each a list of {key, counts}), tasks, quota) or None parts where there is no data.
    days: the days with usage (a sum above zero) among the last 90 UTC days counted back from the latest day of use, newest first; windows: the
    5-hour windows with usage, newest first. Windows are the vendor's 5-hour windows (from
    the quota readings' `resets_at`, kind "vendor") and, for requests outside them or without readings, fixed 5-hour blocks counted from the
    epoch (kind "fixed"). A task launched with a Worker name counts only that Worker's requests between its launch and its last log entry; an
    older task counts all Workers' requests in that span (`overlap` marks spans that overlap another task's)."""
    reqs = []
    for rec in _json_lines(usage_text) or []:
        t = _epoch(rec.get("ts"))
        if t is not None:
            reqs.append((t, rec.get("worker") or "?", _counts(rec)))
    end_day = max((dt.datetime.fromtimestamp(t, dt.timezone.utc).date() for t, _, _ in reqs), default=None)
    ends, quota = _quota(quota_text, end_day)
    groups = {"days": {}, "windows": {}}
    if end_day is not None:
        for i in range(HISTORY_DAYS):
            groups["days"][(end_day - dt.timedelta(days=HISTORY_DAYS - 1 - i)).isoformat()] = {k: 0 for k in (*USAGE_FIELDS, "total")}
    for t, _, c in reqs:
        u = dt.datetime.fromtimestamp(t, dt.timezone.utc)
        if u.date().isoformat() in groups["days"]:
            _add(groups["days"][u.date().isoformat()], c)
        win = next((e for e in ends if e - WINDOW_SECONDS <= t < e), None)
        if win is not None:
            key = dict(key=dt.datetime.fromtimestamp(win - WINDOW_SECONDS, dt.timezone.utc).strftime("%Y-%m-%d %H:%M"), kind="vendor")
        else:
            key = dict(key=dt.datetime.fromtimestamp(t - t % WINDOW_SECONDS, dt.timezone.utc).strftime("%Y-%m-%d %H:%M"), kind="fixed")
        _add(groups["windows"].setdefault((key["key"], key["kind"]), {}), c)
    rep = {"days": None if usage_text is None else [dict(key=key, **v) for key, v in sorted(groups["days"].items(), reverse=True) if v["total"]]}
    rep["windows"] = None if usage_text is None else [dict(key=k[0], kind=k[1], **v) for k, v in sorted(groups["windows"].items(), reverse=True)
                                                      if v["total"]]
    rep["quota"] = quota
    spans = {}
    for rec in _json_lines(plog_text) or []:
        t, task = _epoch(rec.get("time")), rec.get("task")
        if t is None or not task:
            continue
        s = spans.setdefault(task, dict(task=task, folder=rec.get("folder"), start=None, end=t, worker=None))
        if rec.get("state") == "launched":
            s["start"], s["folder"], s["worker"] = t, rec.get("folder"), rec.get("worker") or None
        s["end"] = max(s["end"], t)
    tasks = []
    for s in sorted((s for s in spans.values() if s["start"] is not None), key=lambda s: s["start"]):
        proxy = None if usage_text is None else sum(c["total"] for t, w, c in reqs
                                                    if s["start"] <= t <= s["end"] and (s["worker"] is None or w == s["worker"]))
        note = goodbye_tokens(s["folder"]) if s["folder"] else None
        tasks.append(dict(task=s["task"], start=dt.datetime.fromtimestamp(s["start"], dt.timezone.utc).isoformat(timespec="seconds"),
                          worker=s["worker"], proxy=proxy, note=note, diff=None if proxy is None or note is None else proxy - note,
                          overlap=s["worker"] is None and any(o is not s and o["start"] is not None and o["start"] <= s["end"] and s["start"] <= o["end"]
                                                              for o in spans.values())))
    rep["tasks"] = None if plog_text is None else tasks
    return rep


# -- Quota estimates: the percentage of a window without readings, from its token counts (owner, 2026-11-25, PC-111) --
WEEK_SECONDS = 7 * 86400
QUOTA_KINDS = (("seven_day", "weekly", WEEK_SECONDS, True), ("five_hour", "5-hour", WINDOW_SECONDS, False))  # key, name, length, extrapolated


def _utc(t, fmt="%Y-%m-%d %H:%M"):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime(fmt)


def vendor_windows(ends, span, lo, hi, extrapolate=True):
    """The vendor's windows of length `span` from the known window ends (reset times, sorted, merged), oldest first: (start, end, basis), basis
    None for a window whose end is a known reset, else the known reset its boundaries are extrapolated from by whole steps of `span`. With
    `extrapolate` the windows cover lo..hi: before the first known reset back from it; between two, back from the later one (a shorter rest
    is one window, a rest under an hour widens the first); after the last, forward from it. A known window never reaches back over the previous known end."""
    out, prev = [], None
    if extrapolate and ends:
        e = ends[0] - span
        while e > lo:
            out.insert(0, (e - span, e, ends[0]))
            e -= span
    for r in ends:
        if extrapolate and prev is not None:
            seg, e = [], r - span
            while e - span >= prev:
                seg.insert(0, (e - span, e, r))
                e -= span
            if seg and e - prev < 3600:  # a wobble of the reset time, not a shift: the first window absorbs it
                seg[0] = (prev, seg[0][1], r)
            elif e > prev:
                seg.insert(0, (prev, e, r))
            out += seg
        out.append((r - span if prev is None else max(r - span, prev), r, None))
        prev = r
    while extrapolate and prev is not None and prev <= hi:
        out.append((prev, prev + span, ends[-1]))
        prev += span
    return out


def estimate_percent(tokens, calibration):
    """The quota percentage `tokens` stand for under a calibration of quota_estimates() (one decimal), None without one."""
    return round(tokens / calibration["factor"], 1) if calibration else None


def _quota_kind(key, name, span, extrapolate, reads, events, plan, weak, lo, hi):
    """The rows and the calibration of one window kind (see quota_estimates)."""
    ends = []
    for t, rec in reads:
        r = _num(rec[key].get("resets_at")) if isinstance(rec.get(key), dict) else None
        if r is not None:
            ends.append(r)
    merged = []
    for r in sorted(ends):  # the same window's reset time wobbles by seconds to minutes
        if not merged or r - merged[-1] > 600:
            merged.append(r)
    new = lambda s, e, kind, basis: dict(start=s, end=e, kind=kind, basis=basis, tokens=0, cal_tokens=0, readings=0, peak=None, peak_t=None)
    vend = [new(s, e, "vendor" if b is None else "extrapolated", b) for s, e, b in vendor_windows(merged, span, lo, hi, extrapolate)]
    starts, fixed = [w["start"] for w in vend], {}

    def window(t):
        i = bisect.bisect_right(starts, t) - 1
        if i >= 0 and t < vend[i]["end"]:
            return vend[i]
        b = t - t % span
        return fixed.setdefault(b, new(b, b + span, "fixed", None))
    for t, rec in reads:  # the readings first: the peak's time decides which tokens calibrate
        p = _pct(rec.get(key), "used_percentage")
        if p is not None:
            w = window(t)
            w["readings"] += 1
            if w["peak"] is None or p >= w["peak"]:
                w["peak"], w["peak_t"] = p, t
    for t, n in events:
        w = window(t)
        w["tokens"] += n
        if w["peak_t"] is not None and t <= w["peak_t"]:
            w["cal_tokens"] += n
    wins = sorted([w for w in vend if w["end"] > lo] + list(fixed.values()), key=lambda w: (w["start"], w["end"]))
    base = [w for w in wins if w["start"] >= plan and w["peak"] and w["cal_tokens"] > 0]
    cal = None
    if base:
        cal = dict(factor=sum(w["cal_tokens"] for w in base) / sum(w["peak"] for w in base), readings=sum(w["readings"] for w in base),
                   windows=len(base), first=_utc(min(w["start"] for w in base), "%Y-%m-%d"), last=_utc(max(w["end"] for w in base), "%Y-%m-%d"))
        cal["weak"] = cal["readings"] < weak
    nf = lambda x: f"{round(x):,}"
    rows = []
    for w in wins:
        if not (w["tokens"] or w["readings"] or (extrapolate and w["kind"] != "fixed")):
            continue
        row = dict(start=_iso(w["start"]), end=_iso(w["end"]), kind=w["kind"], tokens=w["tokens"], readings=w["readings"], percent=None, weak=False)
        if w["peak"] is not None:
            row.update(state="measured", percent=w["peak"], note=f"Measured: the highest of {w['readings']} readings in this window.")
        elif w["start"] < plan:
            row.update(state="other plan", note=f"No readings, other plan: before the plan period that starts on {_utc(plan, '%Y-%m-%d')} the quota "
                                                 "was different and the calibration does not carry over.")
        elif cal is None:
            row.update(state="none", note=f"No calibration yet: no {name} window of the plan period has a reading and token counts.")
        else:
            row.update(state="estimate", percent=estimate_percent(w["tokens"], cal), weak=cal["weak"],
                       note=f"Estimate: {nf(w['tokens'])} tokens at {nf(cal['factor'])} tokens per percent, calibrated on {cal['readings']} readings in "
                            f"{cal['windows']} {name} windows with readings, {cal['first']} to {cal['last']} (UTC)."
                            + (f" Weak: fewer than {weak} readings." if cal["weak"] else ""))
        if w["kind"] == "extrapolated":
            row["note"] += (f" No reset is known for this window: its boundaries are extrapolated by whole {span // 86400}-day steps from the known "
                            f"reset of {_utc(w['basis'])} UTC.")
        elif w["kind"] == "fixed":
            row["note"] += f" No vendor window is known here: a fixed {name} block counted from the epoch (UTC)."
        rows.append(row)
    return dict(calibration=cal, rows=rows)


def quota_estimates(quota_text, usage_text=None, transcript_text=None, settings=None):
    """The quota percentage per vendor window, measured or estimated (owner, 2026-11-25, PC-111), for the weekly and the 5-hour windows alike.
    quota_text: quota-history.jsonl; usage_text / transcript_text: the token counts of the Workers (proxy) and of the interactive sessions;
    settings: quota_plan_start and quota_weak_readings (QUOTA_SETTING_DEFAULTS). -> dict(plan_start, weak_readings, weekly, five_hour) or None
    when there is neither a reading nor a token count; each kind: dict(calibration, rows oldest first, the last HISTORY_DAYS days).
    Windows: the vendor's, from the readings' reset times (the weekly ones extrapolated by whole 7-day steps where no reset is known, kind
    "extrapolated"; a 5-hour period outside every known window is a fixed block counted from the epoch, kind "fixed"). A window's tokens are
    the sum of the four counts of every request and message in it. Calibration: tokens per percent = the tokens up to the highest reading of
    each window of the plan period with a reading above zero and token counts, summed, over those readings summed; `readings` counts them all.
    A row: measured (its highest reading; always wins), estimate (tokens / factor, `weak` below quota_weak_readings readings), none (no
    calibration yet: no number) or other plan (the window starts before the plan period: never estimated); `note` is the tooltip."""
    s = validate_quota_settings(settings or {}, strict=False)
    plan = dt.datetime.fromisoformat(s["quota_plan_start"]).replace(tzinfo=dt.timezone.utc).timestamp()
    reads = [(t, rec) for rec in _json_lines(quota_text) or [] if (t := _epoch(rec.get("ts"))) is not None]
    events = [(t, _counts(rec)["total"]) for text in (usage_text, transcript_text) for rec in _json_lines(text) or [] if (t := _epoch(rec.get("ts"))) is not None]
    times = [t for t, _ in reads] + [t for t, _ in events]
    if not times:
        return None
    hi = max(times)
    lo = max(min(times), hi - HISTORY_DAYS * 86400)
    reads = sorted((r for r in reads if r[0] >= lo), key=lambda r: r[0])
    events = [e for e in events if e[0] >= lo]
    out = dict(plan_start=s["quota_plan_start"], weak_readings=s["quota_weak_readings"])
    for key, name, span, extrapolate in QUOTA_KINDS:
        out[key] = _quota_kind(key, name, span, extrapolate, reads, events, plan, s["quota_weak_readings"], lo, hi)
    return out


# ---------- gaps in the project metadata (checker: tools/metadata_gaps.py, contract: docs/contracts/metadata-gaps.md) ----------
QUALITY_METRICS = ("complexity", "maintainability", "lint", "dead", "cycles", "duplication", "docstrings", "coverage", "tests")  # docs/contracts/quality-bands.md
GAPS_MAX_AGE_H = 24    # standing policy: the components of the project metadata are refreshed within 24 hours


def _gap(project, kind, what, module=None, age_h=None):
    return dict(project=project, module=module, kind=kind, what=what, age_h=None if age_h is None else round(age_h, 1))


def _age_h(iso, now):
    """Hours since an ISO time (a time without a zone is UTC), or None when unreadable."""
    try:
        t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max((now - (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc))).total_seconds() / 3600, 0)


def _latest_ts(text):
    """Newest `ts` of a JSON-lines text -> ISO string, or None (no text, no readable record)."""
    ts = [r["ts"] for r in _json_lines(text) or [] if _epoch(r.get("ts")) is not None]
    return max(ts, key=_epoch) if ts else None


def _stale(project, kind, label, age, max_h, module=None):
    return [_gap(project, kind, f"{label} is older than {max_h:g} hours", module, age)] if age is not None and age > max_h else []


def metadata_gaps(project, now, modules, cards, metrics, usage, policies, max_age_h=GAPS_MAX_AGE_H, descs=None,
                  tracked=None, assign=None, docs_heads=None, content=None, data_root=None, data_entries=None, timings=None, timing_limit=3.0, quality=None,
                  retention=None, backup=None, snapshot=None):
    """Pure detection of what the Cockpit shows as no data / not wired / not measured / stale / missing. Every fact comes from the project's own
    data, already read; -> list of gap dicts {project, module|None, kind, what, age_h|None}; an empty list means clean. A check whose input is
    None is not run (e.g. no clone: no `tracked`).
      modules       list of module ids of the module map (None: no map, no per-module checks); descs {id: map desc}
      cards         {kind: parse_card(...) result} for footprint, churn, ambiguity
      metrics       parsed metrics.json of the code snapshot, or None (not configured or unreadable)
      usage         {quota, transcript, proxy: file text or None}
      policies      dict(config=rules_config(...) or None, files=tracked paths or None when there is no clone)
      tracked / assign: the clone's files and the map's `assign`
      docs_heads    {path: first text} of the tracked docs/*.md; content {id: {txt: bool, svg: bool}} of docs/modules/
      quality       dict(modules={id: the `/api/code` module payload}, thresholds=`quality_thresholds` of the card settings or None for the defaults)
      data_root     the project's data folder, data_entries the names in it; timings [(request, seconds|None)] of a live check
      retention     dict(path, text|None, key, min_days) of the coding tool's settings file (transcript_retention_gaps)
      backup        dict(text=the daily backup's status record or None when missing) (backup_status_gaps)
      snapshot      dict(configured, collector, last) of the code snapshot settings and the last refresh record (_snapshot_gaps)"""
    g = []
    add = lambda kind, what, mod=None, age=None: g.append(_gap(project, kind, what, mod, age))
    mods = modules or []
    for kind in CARD_KINDS:
        c = cards.get(kind) or parse_card(None, kind)
        if c["status"] != "ok":
            add("card_data", f"{kind} card data: no data ({c['reason']})")
            continue
        g += _stale(project, "stale", f"{kind} card data", c["age_h"], max_age_h)
        have = c["data"].get("modules") if isinstance(c["data"].get("modules"), dict) else {}
        for mid in mods:
            if mid not in have:
                add("card_data", f"{kind} card: no data for this module", mid, c["age_h"])
        if kind == "footprint":
            _footprint_gaps(add, mods, have, c["age_h"], data_root, data_entries)
        if kind == "ambiguity":
            for mid, v in ambiguity_view(c["data"])["modules"].items():
                if v["high"]:
                    add("ambiguity", f"{len(v['unsorted'])} of {v['total']} files unsorted ({v['share']:.0%}, above {UNSORTED_WARN:.0%})", mid, c["age_h"])
        if kind == "churn" and tracked is not None and assign is not None:
            actual = sum(1 for f in tracked if map_path(f, assign) == "unassigned")
            shown = max((_num(w.get("files")) or 0 for w in (c["data"].get("unassigned") or {}).values() if isinstance(w, dict)), default=0)
            if shown > actual:
                add("churn", f"churn data counts {shown} unassigned files, the repository has {actual}: files that no longer exist", None, c["age_h"])
    _snapshot_gaps(add, g, project, now, mods, metrics, max_age_h, snapshot)
    for mid in mods:
        if content is not None:
            c = content.get(mid) or {}
            if not (c.get("txt") or (descs or {}).get(mid)):
                add("module_page", "module has no description text (no docs/modules file, no desc in the map)", mid)
            if not c.get("svg"):
                add("module_page", "module page has no diagram", mid)
    for key, label in (("quota", "quota history"), ("transcript", "transcript usage file"), ("proxy", "proxy usage file")):
        text = usage.get(key)
        last = _latest_ts(text)
        if last is None:
            add("token_usage", f"Token Usage: no {label}" if text is None else f"Token Usage: {label} has no readable record")
        elif key != "proxy":  # the proxy runs only while tasks run: idle is not stale
            g += _stale(project, "stale", label, _age_h(last, now), max_age_h)
    cfg, files = policies.get("config"), policies.get("files")
    if not cfg:
        add("policies", "Policies not configured (no usable `rules` in the module map)")
    elif files is None:
        add("policies", "Policies not configured (no local clone, so no files can be listed)")
    else:
        empty = [x["name"] for x in cfg["groups"] if not rule_files(files, {}, [x])]
        if len(empty) == len(cfg["groups"]):
            add("policies", "Policies list no files (no tracked file matches a rule group)")
        elif empty:
            add("policies", f"Policies: group(s) list no files: {', '.join(empty)}")
    for f in sorted(x for x in (tracked or []) if assign is not None and not map_entry(x, assign)):
        add("map", f"tracked file has no entry in the module map: {f}")
    if docs_heads is not None:
        for path, text in sorted(docs_heads.items()):
            if not doc_modules(text) and pathlib.PurePath(path).name != "README.md":
                add("documents", f"document has no Modules: line: {path}")
    if quality is not None:
        _quality_gaps(add, quality, mods)
    if retention is not None and usage.get("transcript") is not None:  # a project without collected transcripts does not use the tool
        for what in transcript_retention_gaps(**retention):
            add("transcript_retention", what)
    if backup is not None:
        for what, age in backup_status_gaps(backup.get("text"), now, max_age_h):
            add("backup", what, None, age)
    for name, secs in timings or []:
        if secs is None or secs > timing_limit:
            add("slow", f"request {name} " + ("failed" if secs is None else f"took {secs:.1f} s (limit {timing_limit:g} s)"))
    return g


BACKUP_STATUS_FILE = "backup-status.json"  # in the project's data folder; written by tools/project_backup.py after every run (PC-62)


def backup_status(text):
    """The daily backup's status record (its text, None when missing) -> dict(time, ok, archive, size, files, reason, last_ok), or None when
    missing or unreadable."""
    try:
        r = json.loads(text) if text else None
    except ValueError:
        return None
    if not isinstance(r, dict) or not isinstance(r.get("ok"), bool) or _epoch(r.get("time")) is None:
        return None
    return {k: r.get(k) for k in ("time", "ok", "archive", "size", "files", "reason", "last_ok")}


def backup_status_gaps(text, now, max_age_h=GAPS_MAX_AGE_H):
    """The status record's text -> [(what, age_h|None)]: no backup recorded, the last run failed, or the last good backup is too old."""
    r = backup_status(text)
    if r is None:
        return [("no backup recorded: no daily backup has run, or its status record is unreadable", None)]
    out = []
    if not r["ok"]:
        out.append((f"last backup failed at {r['time']}: {r.get('reason') or 'no reason given'}", _age_h(r["time"], now)))
    age = _age_h(r["last_ok"], now) if _epoch(r.get("last_ok")) is not None else None
    if age is None and r["ok"]:
        age = _age_h(r["time"], now)
    if age is None:
        out.append(("no successful backup recorded", None))
    elif age > max_age_h:
        out.append((f"last successful backup is older than {max_age_h:g} hours", age))
    return out


# ---------- the last CI run of main (tools/ci_main.py, the Task Queue page) ----------
CI_WORKFLOW = "test.yml"
CI_FIELDS = "databaseId,status,conclusion,headSha,createdAt,url"
CI_RED = ("failure", "timed_out", "startup_failure")
RED_MAIN_TITLE = "CI is red on main: the test workflow fails"
RED_MAIN_POLICY = "Merging: a commit is merged only when it obeys all policies, tests passing included (CLAUDE.md, Merging)."


def ci_run(text):
    """`gh run list --json CI_FIELDS --limit 1` output -> dict(state: green|red|running|other|none, conclusion, sha, time, url)."""
    try:
        runs = json.loads(text or "[]")
    except ValueError:
        runs = None
    r = runs[0] if isinstance(runs, list) and runs and isinstance(runs[0], dict) else None
    if r is None:
        return dict(state="none", conclusion=None, sha=None, time=None, url=None)
    c = r.get("conclusion") or None
    state = "running" if r.get("status") != "completed" else "green" if c == "success" else "red" if c in CI_RED else "other"
    return dict(state=state, conclusion=c, sha=r.get("headSha"), time=r.get("createdAt"), url=r.get("url"))


def red_main_body(run):
    """The text of the red-main finding (tools/file_finding.py adds the policy, the files and the Origin block)."""
    return (f"The last run of the workflow `{CI_WORKFLOW}` on main is red ({run['conclusion']}): {run['url']}, commit {run['sha']}, "
            f"started {run['time']}.\n\nWork: find the failing jobs, fix the cause (not the test), and confirm that `tools/merge_gate` passes.")


RETENTION_MIN_DAYS = 3650  # owner, 2026-11-24: keep the session transcripts (the source of the token statistics) as long as possible


def transcript_retention_gaps(path, text, key, min_days=RETENTION_MIN_DAYS):
    """The coding tool's settings file (its text, None when missing) -> [what] when its transcript retention `key` is not a whole number of
    days at least `min_days`: unset means the tool's short default applies and old transcripts are deleted."""
    if text is None:
        return [f"transcript retention not set: no settings file {path} (the tool's default applies); set {key} to at least {min_days}"]
    try:
        data = json.loads(text)
    except ValueError:
        return [f"transcript retention unknown: settings file {path} is not valid JSON; set {key} to at least {min_days}"]
    v = data.get(key) if isinstance(data, dict) else None
    if v is None:
        return [f"transcript retention not set: no {key} in {path} (the tool's default applies); set it to at least {min_days}"]
    if isinstance(v, bool) or not isinstance(v, int):
        return [f"transcript retention unusable: {key} is {v!r} in {path}; set it to a whole number of at least {min_days} days"]
    if v < min_days:
        return [f"transcript retention {v} days in {path} is shorter than {min_days}; set {key} to at least {min_days}"]
    return []


def _quality_gaps(add, q, mods):
    """(l) a displayed metric without a band (no threshold, or the payload carries no band for it) and a measured module whose Quality view has no bar."""
    th, payload = q.get("thresholds"), q.get("modules") or {}
    measured = {mid: m for mid, m in payload.items() if isinstance(m, dict) and m.get("filesN") and (not mods or mid in mods)}
    for key in QUALITY_METRICS:
        t = (th or {}).get(key) if th is not None else True
        ok = isinstance(t, dict) and t.get("direction") in ("low", "high") and _num(t.get("ok")) is not None and _num(t.get("warn")) is not None
        if th is not None and not ok:
            add("quality_bands", f"quality metric {key} has no band: no usable threshold in quality_thresholds")
        elif any(key not in (m.get("bands") or {}) for m in measured.values()):
            add("quality_bands", f"quality metric {key} has no band in the code payload")
    for mid, m in sorted(measured.items()):
        if not any(isinstance(b, dict) and b.get("band") in ("ok", "warn", "bad") for b in (m.get("bands") or {}).values()):
            add("quality_bands", "Quality view shows a bar for no metric at all", mid)


def map_entry(path, assign):
    """True when some entry of the module map (an explicit `module: null` included) covers the path, or the file a test file tests."""
    mirrored = path
    if path.startswith("tests/"):
        parts = path[6:].split("/")
        parts[-1] = parts[-1].removeprefix("test_")
        mirrored = "/".join(parts)
    return any(p == a["path"] or p.startswith(a["path"] if a["path"].endswith("/") else a["path"] + "/") for a in assign for p in (path, mirrored))


def _footprint_gaps(add, mods, have, age, data_root, data_entries):
    declared = []
    for mid in mods:
        m = have.get(mid)
        if not isinstance(m, dict):
            continue
        pol = m.get("policy") if isinstance(m.get("policy"), dict) else {}
        for key in ("backup", "retention"):
            if policy_text(pol.get(key))[1]:
                add("footprint", f"persistence footprint: no {key} statement", mid, age)
        for f in m.get("files") or []:
            if isinstance(f, dict):
                declared.append(str(f.get("path", "")))
                if f.get("exists") is False:
                    add("footprint", f"declared file does not exist: {f.get('path')}", mid, age)
        for lg in m.get("logging") or []:
            if isinstance(lg, dict):
                if lg.get("file"):
                    declared.append(str(lg["file"]))
                elif str(lg.get("rotation") or "").strip().lower() in GAP_WORDS:
                    add("footprint", f"logger {lg.get('logger')}: no file and no stated destination", mid, age)
    if data_root and data_entries is not None:
        root = pathlib.Path(data_root).expanduser()
        ds = [pathlib.Path(x).expanduser() for x in declared if x.startswith(("/", "~"))]
        for name in sorted(data_entries):
            e = root / name
            if not any(d == e or e in d.parents or d in e.parents for d in ds):
                add("footprint", f"file in the project's data folder not declared by any footprint: {name}", None, age)


def _snapshot_gaps(add, g, project, now, mods, metrics, max_age_h, setting=None):
    """setting: dict(configured, collector, last) of the project (`code` set, `code_collect` set, the last refresh record or None);
    None: the settings are unknown and only the snapshot itself is checked."""
    s = setting or {}
    if setting is not None and not s.get("configured"):
        add("snapshot", "code snapshot not configured (no `code` folder in the project settings)")
        return
    if setting is not None and not s.get("collector"):
        add("snapshot", "code snapshot has no collector command (no `code_collect` in the project settings), so it is never refreshed")
    last = s.get("last")
    if isinstance(last, dict) and last.get("ok") is False:
        add("snapshot", f"code snapshot refresh failed at {last.get('ts')}: {last.get('reason')}", None, _age_h(last.get("ts"), now))
    if metrics is None:
        add("snapshot", "code snapshot not wired (not configured or metrics.json unreadable)" if setting is None else "code snapshot not wired (metrics.json missing or unreadable)")
        return
    view = code_view(metrics)
    age = _age_h(view["generated_at"], now) if view["generated_at"] else None
    if age is None:
        add("snapshot", "code snapshot has no readable generated_at, so its age is unknown")
    g += _stale(project, "stale", "code snapshot", age, max_age_h)
    for mid in mods:
        m = view["modules"].get(mid)
        if m is None:
            add("snapshot", "code snapshot: not wired (no metrics for this module)", mid, age)
        elif not m["filesN"]:
            add("snapshot", "quality metrics not measured (no analysed files)", mid, age)
        elif m["pyN"]:
            if m["cov"] is None:
                add("snapshot", "no coverage figure for a module with Python files", mid, age)
            if m["cc"] is None:
                add("no_data", "module card: worst complexity: no data", mid, age)
    cov = [_age_h(((m.get("metrics") or {}).get("test_coverage") or {}).get("data_file_modified"), now) for m in (metrics.get("modules") or {}).values()]
    cov = [a for a in cov if a is not None]
    g += _stale(project, "stale", "coverage file", max(cov) if cov else None, max_age_h)


# ---------- GitHub rate limit and the local part of the Task Queue ----------
def is_rate_limited(msg):
    """True when a gh error message says the API budget (primary or secondary) is used up."""
    return "rate limit" in (msg or "").lower()


def rate_limit_reset(msg, now):
    """The reset time (epoch seconds, in the future and within 2 hours) when the message carries one as a 10-digit epoch number, else None."""
    for m in re.findall(r"\b(\d{10})\b", msg or ""):
        if now < int(m) <= now + 7200:
            return int(m)
    return None


def _when(t):
    """A time for the Task Queue page (PC-114, PC-134): epoch seconds stay a number (the browser shows them in its own zone); an ISO string gets
    an offset: one without (the Dispatcher's processing log before PC-134) is the server machine's local time. Anything unreadable is None."""
    if isinstance(t, bool) or t is None:
        return None
    if isinstance(t, (int, float)):
        try:
            dt.datetime.fromtimestamp(t, dt.timezone.utc)
            return t
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(t, str):
        return None
    try:
        d = dt.datetime.fromisoformat(re.sub(r"Z$", "+00:00", t))
    except ValueError:
        return None
    return (d if d.tzinfo else d.astimezone()).isoformat(timespec="seconds")


def queue_local(state_text, plog_text, limit=15):
    """The Task Queue facts that need no GitHub: the Workers the Dispatcher's state file lists as running (worker, task id, launched, started)
    and the newest `limit` lines of the processing log (newest first). Missing or unreadable files give empty lists. Times as `_when` gives them."""
    try:
        st = json.loads(state_text) if state_text else {}
    except ValueError:
        st = {}
    ws = st.get("workers") if isinstance(st, dict) and isinstance(st.get("workers"), dict) else {}
    workers = [dict(worker=u, task=w.get("task_id"), launched=_when(w.get("launched")), started=bool(w.get("started")))
               for u, w in sorted(ws.items()) if isinstance(w, dict)]
    recs = [r for r in (_json_lines(plog_text) or []) if r.get("time")]
    log_ = [dict(time=_when(r["time"]), task=r.get("task"), state=r.get("state"), reason=r.get("reason"), worker=r.get("worker"), heartbeat=r.get("heartbeat"))
            for r in recs[-limit:]][::-1]
    return dict(workers=workers, log=log_)


# ---------- the live overlay on the cached task table (PC-119) ----------
LIVE_STAGE = {"launched": "wip", "wip": "wip", "unmerged": "unmerged", "failed": "failed"}   # processing-log state -> task stage
RECONCILED = re.compile(r"-> (unmerged|failed)$")   # the Dispatcher's PC-98 reconciliation entries ("wip with no Worker -> failed")
LIVE_REFRESH_S = 120                                # a newer log event refreshes the task list at most this often


def task_number(task_id, shorts):
    """The issue number of a Dispatcher task id `<short>-<number>` whose short name is one of `shorts` (lower case), else None."""
    m = re.fullmatch(r"(.+)-(\d+)", task_id) if isinstance(task_id, str) else None
    return int(m.group(2)) if m and m.group(1).lower() in shorts else None


def live_events(plog_text, shorts):
    """Issue number -> (epoch, stage) of the newest state change of each task of this project in the processing log."""
    out = {}
    for r in _json_lines(plog_text) or []:
        st = r.get("state") if isinstance(r.get("state"), str) else ""
        m = RECONCILED.search(st)
        stage, num = LIVE_STAGE.get(st) or (m and m.group(1)), task_number(r.get("task"), shorts)
        try:
            at = dt.datetime.fromisoformat(r.get("time")).timestamp()   # with an offset, or (before PC-134) the server's local time
        except (TypeError, ValueError):
            continue
        if stage and num is not None and (num not in out or at >= out[num][0]):
            out[num] = (at, stage)
    return out


def overlay_live(tasks, fetched_at, events, held, state_known):
    """The cached tasks with the newer facts of the processing log: a state change newer than `fetched_at` replaces the cached stage and is
    marked `live` (at, was); a WIP task that no running Worker (issue numbers in `held`) holds gets `noWorker`, when the Dispatcher's
    state file could be read (`state_known`). Merged and cancelled are final and stay. The input is not changed."""
    out = []
    for t in tasks:
        e = events.get(t["number"])
        if e and e[0] > fetched_at and e[1] != t["stage"] and t["stage"] not in ("merged", "cancelled"):
            t = dict(t, stage=e[1], running=is_running(e[1]), live=dict(at=e[0], was=t["stage"]))
        if state_known and t["stage"] == "wip" and t["number"] not in held:
            t = dict(t, noWorker=True)
        out.append(t)
    return out


def live_refresh_due(events, fetched_at, last_try, now, gh_ok):
    """True when the processing log has a state change newer than the cached task list, the last such refresh was LIVE_REFRESH_S or more
    ago and GitHub may be called (`gh_ok`: no rate-limit pause, room in the hourly cap, not an offline copy)."""
    newest = max((at for at, _ in events.values()), default=None)
    return bool(gh_ok) and newest is not None and newest > fetched_at and now - last_try >= LIVE_REFRESH_S


def local_modules(mods):
    """The module list the Code tab needs, from the module map file alone (no issues): the listed modules plus missing ancestors, each with
    its short name, parent and children; task counts are zero (they come from GitHub and the Code tab does not show them)."""
    mods = [dict(m) for m in mods]
    with_parents(mods)
    for m in mods:
        m["short"] = short_name(m["name"])
    if not any(m["id"] == "unassigned" for m in mods):
        mods.append(dict(id="unassigned", name="Not mapped to a module", short="Unassigned", virtual=True))
    add_tree(mods, [])
    return mods


# ---------- offline copy from a backup (PC-63) ----------
# What each pane reads, as groups of alternatives: a pane has its data when every group has at least one present item. Items: "cache:<what>"
# (the Cockpit's cache file of the project), "data:<path>" (a file under the project's data folder), "code" (the snapshot's metrics.json),
# "git" (a local clone), "norules" (no rules configured: the Policies parts have nothing to show). Settings fall back to the defaults, as online.
OFFLINE_PANES = (
    ("tasks and states", "/api/state", (("cache:state",),)),
    ("ranking", "/api/state", (("cache:state",),)),
    ("open questions", "/api/questions", (("cache:state",),)),
    ("timeline", "/api/timeline", (("cache:state",), ("cache:timeline", "cache:commits", "git"))),
    ("rules", "/api/rules", (("cache:rules", "git", "norules"),)),
    ("rules' open pull requests", "/api/rulesprs", (("cache:rulesprs", "norules"),)),
    ("task queue (local part)", "/api/queue", (("data:dispatcher-state.json", "data:processing/processing.jsonl"),)),
    ("token usage", "/api/usage", (("data:proxy-usage.jsonl", "data:quota-history.jsonl", "data:transcript-usage.jsonl", "data:processing/processing.jsonl"),)),
    ("settings", "/api/settings", ()),
    ("module card settings", "/api/cardsettings", ()),
    ("code", "/api/code", (("code",),)),
    ("module cards", "/api/cardmeta", (("cache:cardmeta",) + tuple(f"data:cardmeta/{k}.json" for k in CARD_KINDS),)),
)


def backup_gaps(present):
    """Set of present items -> [{pane, api, missing: [alternatives of each unmet group]}]: the panes whose data is not in the backup."""
    out = []
    for pane, api, groups in OFFLINE_PANES:
        missing = [" or ".join(g) for g in groups if not any(x in present for x in g)]
        if missing:
            out.append(dict(pane=pane, api=api, missing=missing))
    return out


def pane_problem(api, body):
    """None, or why the answer to a pane's request in an offline copy carries no data or would allow steering."""
    if not isinstance(body, dict):
        return "not a JSON object"
    if api == "/api/state":
        if not isinstance(body.get("tasks"), list) or not isinstance(body.get("source"), dict):
            return "no task state"
        return "steering is on" if body["source"].get("steering") else None
    if api == "/api/questions":
        bad = [p for p in body.get("projects", []) if p.get("error")]
        return f"project unreadable: {bad[0]['error']}" if bad else None
    if api == "/api/timeline":
        return None if isinstance(body.get("entries"), list) else "no timeline"
    if api == "/api/rules":
        return f"rules unavailable: {body['error']}" if body.get("error") else None
    if api == "/api/rulesprs":
        return None if body.get("available") or not body.get("configured") else f"open pull requests unavailable: {body.get('error')}"
    if api in ("/api/settings", "/api/cardsettings"):
        return "steering is on" if body.get("steering") else None
    if api == "/api/code":
        return None if body.get("wired") else "code snapshot not wired"
    return None


COMPLIANCE_REPORT_FILE = "compliance-report.md"  # in the project's data folder; written by tools/compliance_check.py (PC-108)


def compliance_summary(text):
    """The compliance report's text -> dict(checked, release, violations) for the Cockpit page, or None when missing or unreadable."""
    m = re.search(r"Checked (\S+) against constitution (\S+)\.", text or "")
    if not m:
        return None
    n = re.search(r"(?m)^(\d+) violation\(s\):", text)
    return dict(checked=m.group(1), release=m.group(2), violations=int(n.group(1)) if n else 0)

# ---------- compliance report (written by the compliance check, PC-108; read by Settings > Policies) ----------
COMPLIANCE_REPORT = "docs/compliance/compliance-report.md"  # one current report per project, inside its clone, overwritten by each run


def parse_compliance(text):
    """The compliance report (docs/contracts/compliance-report.md) -> dict(checked, violations=[{file, line, passage, rule, reason}]).
    `checked` is the `Checked:` line (None when absent). Only the `## Violations` section is read; "none" there means no violation."""
    m = re.search(r"^Checked:\s*(.+?)\s*$", text, re.M)
    sec = re.search(r"^## Violations[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    out = []
    for block in re.split(r"^### ", sec.group(1) if sec else "", flags=re.M)[1:]:
        head, *body = block.split("\n")
        file, _, line = head.strip().rpartition(":") if re.search(r":\d+$", head.strip()) else (head.strip(), "", "")
        field = lambda k: next((b.split(":", 1)[1].strip() for b in body if re.match(rf"-\s*{k}\s*:", b, re.I)), "")
        out.append(dict(file=file, line=int(line) if line else None, passage=" ".join(b.lstrip("> ").rstrip() for b in body if b.startswith(">")).strip(),
                        rule=field("Rule"), reason=field("Reason")))
    return dict(checked=m.group(1) if m else None, violations=out)


def compliance_run_state(status, alive):
    """The run status file's record (or None) and whether its process still lives -> the state the page shows:
    idle (never started here), running, done, failed (exit code not 0), interrupted (recorded as running, but the process is gone)."""
    if not status:
        return "idle"
    if status.get("state") == "running":
        return "running" if alive else "interrupted"
    return "done" if status.get("exit") == 0 else "failed"
