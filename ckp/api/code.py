"""API group `code`: the Code tab (snapshot, module pages, module sizes, card metadata) and the rule view with its pull requests."""
import logging
from urllib.parse import quote
import datetime as dt
import json
import pathlib
import time

from ckp.core import modules as core_modules, queue_view as core_queue_view, rules as core_rules
from ckp.api import cache as api_cache, context as api_context, gh as api_gh, settings as api_settings

from ckp.api.result import File, fail, ok
log = logging.getLogger("cockpit")


SVGS = {"erd": "erd.svg", "dataflow": "dataflow.svg", "deps": "deps.svg", "js": "js.svg"}


def build_rules(p=None):
    p = p or api_context.proj()
    rc = core_rules.rules_config(api_context.load_modules(p))
    if not rc or not p["git"]:
        return dict(configured=False)
    t0, gs, spec = time.time(), rc["groups"], core_rules.rule_pathspecs(rc["groups"])
    fmt = ["log", "--no-renames", f"--format={core_rules.LOG_FMT}"]
    changes = core_rules.parse_rule_changes(api_gh.run_git(p, fmt + ["--since=90.days.ago", "--numstat", "--", *spec]), api_gh.run_git(p, fmt + ["--since=90.days.ago", "-p", "-U0", "--", *spec]), gs, rc["managers"], rc["bots"])
    last = core_rules.parse_last_changes(api_gh.run_git(p, fmt + ["--name-only", "--", *spec]), gs)
    files = core_rules.rule_files([p for p in api_gh.run_git(p, ["ls-files", "-z", "--", *spec]).split("\0") if p], last, gs)
    log.info("rules: %d files, %d changes in %.1fs", len(files), len(changes), time.time() - t0)
    return dict(configured=True, groups=[g["name"] for g in gs], files=files, changes=changes, repo=p["repo"])


def get_rules(p=None):
    """The rule view from local files and local git only (no GitHub): Settings > Policies renders from it whatever GitHub does."""
    p = p or api_context.proj()
    return api_cache.cached(api_context.pk(p, "rules"), lambda: build_rules(p), 120, swr=True, persist=True)


def get_rule_prs(p, fresh=False):
    """The open pull requests that touch rule files: the one GitHub-sourced part of Policies, served separately so that it can never block or fail
    the rest. Hourly cache; fresh (the owner's Refresh click on the pane) bypasses it. -> dict(configured, available, pending, at, error)."""
    rc = core_rules.rules_config(api_context.load_modules(p))
    if not rc or not (p.get("git") or api_context.OFFLINE):  # an offline copy may hold the pull requests without the clone
        return dict(configured=False, available=False, pending=[])
    k = api_context.pk(p, "rulesprs")
    if fresh:
        api_cache._expire(k)
        log.info("owner Refresh of the rule pull requests of %s", p.get("name", ""))
    fetch = lambda: dict(at=time.time(), prs=json.loads(api_gh.run_gh(["pr", "list", "-R", p["repo"], "--state", "open", "--json", "number,title,author,files,url,createdAt"])))
    try:
        v = api_cache.cached(k, fetch, api_gh.GH_REFRESH_S, swr=not fresh, persist=True, disk_fresh=api_gh.DISK_FRESH_S, queue=True)
    except (RuntimeError, ValueError) as ex:
        log.warning("rules: open pull requests unavailable (%s)", str(ex)[:80])
        return dict(configured=True, available=False, pending=[], error=str(ex)[:200])
    return dict(configured=True, available=True, pending=core_rules.pending_rule_prs(v["prs"], rc["groups"]), at=v["at"])


def warm_module_pages(p):
    """The shared inputs of the module pages (file list, document heads, import edges), so the first module page opens fast."""
    cfgm = api_context.load_modules(p)
    if p.get("git") and cfgm:
        tracked, _ = tracked_and_heads(p)
        api_cache.cached(api_context.pk(p, "importedges"), lambda: import_edges_of(p, tracked, cfgm["assign"]), 120, swr=True)


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
        tracked = [x for x in api_gh.run_git(p, ["ls-files", "-z"]).split("\0") if x]
        return tracked, line_counts(p, tracked)
    return api_cache.cached(api_context.pk(p, "sizes"), build, 120, swr=True)


def module_lines(p, ids):
    """{module id: code lines} that orders the modules: from the clone with the module map, else from the code snapshot, else {}."""
    try:
        cfgm = api_context.load_modules(p)
        if p.get("git") and cfgm:
            return core_modules.code_lines(clone_sizes(p)[1], cfgm.get("assign", []), ids)
        if p.get("code"):
            return core_modules.snapshot_lines(json.load(open(pathlib.Path(p["code"]).expanduser() / "metrics.json")))
    except Exception as ex:  # the module lists must not break because the sizes could not be read; they keep the map order
        log.info("module sizes of %s unavailable (%s)", p.get("name", ""), str(ex)[:100])
    return {}


def ordered(p, mods):
    """The module list in the one order of every list (logic.order_modules), each module with the `lines` it was ordered by."""
    return core_modules.order_modules(mods, module_lines(p, [m["id"] for m in mods if not m.get("virtual")]))


def biggest_by_module(p, ids):
    """{module id: its biggest files (logic.biggest_files)} from the clone and the module map; {} without --git or a map."""
    cfgm = api_context.load_modules(p)
    if not (p.get("git") and cfgm):
        return {}
    tracked, sizes = clone_sizes(p)
    assign = cfgm.get("assign", [])
    base = f"https://github.com/{p['repo']}/blob/main/"
    return {mid: [dict(r, url=base + quote(r["path"])) for r in core_modules.biggest_files({x: sizes[x] for x in core_modules.module_files(tracked, assign, mid) if x in sizes})]
            for mid in ids}


def code_snapshot(p=None):
    """The Code tab's payload. `configured`: the project names a snapshot folder (without one the cards hide the snapshot lines);
    `age_h`, `stale`, `max_age_h`: the snapshot's age against the 24 hours of the standing policy."""
    try:
        p = p or api_context.proj()
        d = pathlib.Path(p["code"]).expanduser()
        view = core_modules.code_view(json.load(open(d / "metrics.json")), api_settings.get_card_settings(p)["values"]["quality_thresholds"])
        big = api_cache.cached(api_context.pk(p, "biggest"), lambda: biggest_by_module(p, list(view["modules"])), 120, swr=True, persist=True)
        for mid, m in view["modules"].items():
            m["biggest"] = big.get(mid, [])
        age = core_modules.snapshot_age(view["generated_at"], dt.datetime.now(dt.timezone.utc))
        return dict(wired=True, configured=True, **view, **age, diagrams=[k for k, f in SVGS.items() if (d / f).exists()], mods=local_modules_of(p))
    except (OSError, ValueError, TypeError, KeyError) as ex:
        log.info("code snapshot not wired (%s)", ex)
        return dict(wired=False, configured=bool(p and p.get("code")), modules={}, diagrams=[], mods=local_modules_of(p) if p else [])


def local_modules_of(p):
    """The Code tab's module list from the local module map file (and the local clone for the gap and document counts); no GitHub."""
    try:
        cfgm = api_context.load_modules(p)
    except (OSError, ValueError) as ex:
        log.warning("module map of %s unreadable: %s", p.get("name", ""), ex)
        cfgm = None
    return ordered(p, core_queue_view.local_modules((cfgm or {}).get("modules", [])))


def card_metadata(p=None, now=None):
    """Read footprint/churn/ambiguity.json of the project (read-only). Anything missing or broken is reported as 'no data', never raised."""
    p = p or api_context.proj()
    d, texts = api_context.meta_dir(p), {}
    for kind in core_modules.CARD_KINDS:
        try:
            texts[kind] = (d / f"{kind}.json").read_text()
        except FileNotFoundError:
            texts[kind] = None
        except (OSError, UnicodeDecodeError) as ex:
            log.warning("card metadata %s unreadable: %s", d / f"{kind}.json", ex)
            texts[kind] = "\0unreadable"  # parse_card reports it as invalid
    out = core_modules.card_meta(texts, p.get("name") or None, now)
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
    return core_modules.import_edges(src, assign)


def tracked_and_heads(p):
    """(tracked files of the clone, {docs/*.md path: its first 1500 characters}); shared by all module pages, so read once per minute, not per page."""
    def build():
        tracked = [x for x in api_gh.run_git(p, ["ls-files", "-z"]).split("\0") if x]
        heads = {}
        for x in [x for x in tracked if x.startswith("docs/") and x.endswith(".md")][:2000]:
            try:
                heads[x] = (pathlib.Path(p["git"]).expanduser() / x).read_text(errors="replace")[:1500]
            except OSError:
                continue
        return tracked, heads
    return api_cache.cached(api_context.pk(p, "tracked"), build, 60, swr=True)


def build_moduledoc(p, mid):
    """The content of a module page: description paragraphs (docs/modules/<id>.txt next to the module map, else the map's `desc`), the sanitised
    drawing (.svg), the Workflow elements to highlight (.hl), the key files grouped and the documents naming the module. Anything missing is None / []."""
    stem = core_modules.module_stem(mid)
    if not stem:
        raise ValueError(f"unusable module id {mid!r}")
    cfgm = api_context.load_modules(p)
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
    svg = core_modules.sanitise_svg(svg_raw) if svg_raw else None
    if svg_raw and svg is None:
        log.warning("module page %s.svg refused by the sanitiser", stem)
    files = docs = rel = None
    if p.get("git"):
        tracked, heads = tracked_and_heads(p)
        files = core_modules.group_files(core_modules.module_files(tracked, (cfgm or {}).get("assign", []), mid)) if cfgm else None
        docs = core_modules.module_docs(heads, mid, f"https://github.com/{p['repo']}")
        if cfgm:
            rel = core_modules.module_relations(api_cache.cached(api_context.pk(p, "importedges"), lambda: import_edges_of(p, tracked, cfgm["assign"]), 120, swr=True), mid)
    log.info("module page %s: text %s, svg %s, hl %s", mid, bool(txt), bool(svg), bool(hl))
    return dict(id=mid, paragraphs=core_modules.module_paragraphs(txt, desc), svg=svg, hl=core_modules.hl_ids(hl), files=files, docs=docs, relations=rel)


# ---------- API entry points (Result) ----------
def rules(ctx):
    """GET /api/rules: the rule view (local files and git only)."""
    return ok(get_rules(ctx.p))


def rule_prs(ctx, fresh=False):
    """GET /api/rulesprs: the open pull requests that touch rule files."""
    return ok(get_rule_prs(ctx.p, fresh=fresh))


def code(ctx):
    """GET /api/code: the Code tab's payload."""
    return ok(code_snapshot(ctx.p))


def moduledoc(ctx, module=""):
    """GET /api/moduledoc: the content of a module page."""
    if not core_modules.module_stem(module):
        return fail("bad module id", 400)
    return ok(api_cache.cached(api_context.pk(ctx.p, "moduledoc:" + module), lambda: build_moduledoc(ctx.p, module), 60, swr=True, persist=True))


def cardmeta(ctx):
    """GET /api/cardmeta: footprint, churn and ambiguity data of the module cards."""
    return ok(api_cache.cached(api_context.pk(ctx.p, "cardmeta"), lambda: card_metadata(ctx.p), 60, swr=True, persist=True))


def diagram(ctx, name=""):
    """GET /code/<name>.svg: one of the snapshot's drawings."""
    name = name.removesuffix(".svg")
    if name in SVGS and ctx.p.get("code"):
        return ok(File((pathlib.Path(ctx.p["code"]).expanduser() / SVGS[name]).read_bytes(), "image/svg+xml"))
    return fail("not found", 404)


def report(ctx, path=""):
    """GET /report/<file>: a file of the code snapshot's report."""
    f = core_rules.report_file(ctx.p["code"], path) if ctx.p.get("code") else None
    if f:
        return ok(File(f.read_bytes(), {".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".css": "text/css"}[f.suffix]))
    return fail("not found", 404)
