"""Module map, files, code snapshot, code view, relations, card metadata, churn, bands, footprint. Depends on common and settings."""
import logging
import datetime as dt
import json
import math
import re
import shlex
from ckp.core.common import GAPS_MAX_AGE_H, STAGES, _age_h
from ckp.core.settings import QUALITY_DEFAULTS, _is_num

log = logging.getLogger("cockpit")


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


def map_entry(path, assign):
    """True when some entry of the module map (an explicit `module: null` included) covers the path, or the file a test file tests."""
    mirrored = path
    if path.startswith("tests/"):
        parts = path[6:].split("/")
        parts[-1] = parts[-1].removeprefix("test_")
        mirrored = "/".join(parts)
    return any(p == a["path"] or p.startswith(a["path"] if a["path"].endswith("/") else a["path"] + "/") for a in assign for p in (path, mirrored))
