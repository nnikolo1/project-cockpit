#!/usr/bin/env python3
"""One-shot, kept for the record: how cockpit.py was split into the ckp/api and ckp/server modules (refactoring slice 3).

It ran against the commit that held the original cockpit.py (`git rev-parse` of main before the change): the original is read with
`git show <commit>:cockpit.py`, never copied into the repository. Output: one generated file per target module under --out, which was then
completed by hand (the API entry functions, ckp/api/result.py, the Context, ckp/server/routes.py and handler.py, the cockpit.py shim).
  split_cockpit.py --commit COMMIT --out DIR     write the generated modules under DIR (default: print the table of targets only)
How: every top-level definition of cockpit.py is a unit (with the comments directly above it) and goes to the module ASSIGN names for it; a
unit is moved verbatim; a name another new module defines is qualified (`api_<module>.name`, `server_<module>.name`), `L.name` becomes
`core_<theme>.name` from the re-exports of logic.py, and the std and project imports are emitted per module by use. Local variables that
shadow a module-level name are left alone (scope analysis). `check_*` verify the result: every name lands in exactly one target, every path
of the old handlers is named in the routing table, every target module is reachable through the cockpit.py shim."""
import argparse
import ast
import logging
import pathlib
import re
import subprocess
import sys

log = logging.getLogger("ckp_split_cockpit")

ASSIGN = {
    "context": "CFG PROJECT_KEYS PROJECTS DEFAULT UnknownProject proj pk project_from_flags projects_from_file register DATA_DIR QUEUE_ROOT meta_dir OFFLINE "
               "CACHE_DIR WARM _clock load_modules queue_files queue_dir settings_path card_settings_path served_project _read_text CACHE_SCHEMA",
    "gh": "GH_CALLS_PER_HOUR RATE_LIMIT_PAUSE_S _gh_queue GH_SUMMARY_EVERY GH_REFRESH_S DISK_FRESH_S GitHubUnavailable _gh_calls _gh_lock _gh_pause "
          "_gh_gate _gh_failed run_gh run_git STATE_CALLS gh_room gh_calls_left",
    "cache": "_cache _lock _locks _refreshing _dc _disk_path _disk_load _jsonable _disk_save drop_cache STAGGER_WINDOW_S STAGGER_JITTER_S GH_RESOURCES "
             "_started _slots _slot _failures cached _disk_time _refresh refreshing _expire _reread _reread_lock _key_lock _store _deferred",
    "fetch": "PAGE TL_BATCH TIMELINE NODE GQL fetch_issues fetch_issue_nodes fetch_timelines _TL repair_events fetch_project fetch_module_deadlines",
    "settings": "get_settings get_card_settings save_card_settings save_settings",
    "usage": "USAGE_FILE PLOG_FILE AREA_DIR QUOTA_FILE TRANSCRIPT_FILE AGGREGATE_FILE SNAPSHOT_REFRESH_FILE _usage_one usage_served get_usage "
             "_reconcile_card _tool",
    "code": "SVGS build_rules get_rules get_rule_prs warm_module_pages line_counts clone_sizes module_lines ordered biggest_by_module code_snapshot "
            "local_modules_of card_metadata import_edges_of tracked_and_heads build_moduledoc",
    "state": "gh_user build_state state_from_raw build_questions get_state state_version stale_note steer_block _live_try live_state get_state_served "
             "_served get_task load_commits build_timeline get_timeline",
    "queue": "ACTION_LOG_MAX ACTION_LOG_KEEP _action_log_lock _version code_version action_log_path log_action read_action_log get_queue get_queue_all "
             "get_queue_poll",
    "health": "_compliance_lock compliance_status_path _write_json _alive compliance_of build_compliance start_compliance get_environments "
              "compliance_report_file local_gate get_ci MANIFEST BACKUP_PATHS unpack_backup open_backup backup_inventory offline_info start_offline",
    "actions": "TYPES _date _prio _task _int _edit _prio_due EDITABLE STALE_NOTE _edit_text plan_action _plan execute REREAD_DELAY_S APPLY_WAIT_S "
               "after_action _reread_safe reread ACTS_KEEP ACT_WAIT_S _acts_lock _acts_running acts_path _acts _acts_put applied_now do_act "
               "_logged_act _do_act page_events",
    "@server/handler": "redact Server H",
    "@server/routes": "_since",
    "@server/startup": "check_manager serve load_token resolve main warm",
}


def owners(assign=None):
    """{name: target module}; a name assigned twice is a mistake (ValueError)."""
    own = {}
    for mod, names in (assign or ASSIGN).items():
        for n in names.split():
            if n in own:
                raise ValueError(f"{n} is assigned to both {own[n]} and {mod}")
            own[n] = mod
    return own


def core_themes(logic_src):
    """{name: ckp.core theme} from the re-exports of logic.py."""
    out = {}
    for n in ast.parse(logic_src).body:
        if isinstance(n, ast.ImportFrom) and n.module.startswith("ckp.core."):
            for a in n.names:
                out[a.name] = n.module.split(".")[-1]
    return out


STD_IMPORTS = {  # name used -> import line
    "argparse": "import argparse", "json": "import json", "logging": "import logging", "importlib": "import importlib.util", "os": "import os",
    "pathlib": "import pathlib", "random": "import random", "re": "import re", "secrets": "import secrets", "shutil": "import shutil",
    "tarfile": "import tarfile", "tempfile": "import tempfile", "collections": "import collections", "subprocess": "import subprocess",
    "sys": "import sys", "threading": "import threading", "time": "import time", "dt": "import datetime as dt",
    "ThreadPoolExecutor": "from concurrent.futures import ThreadPoolExecutor", "SimpleCookie": "from http.cookies import SimpleCookie",
    "BaseHTTPRequestHandler": "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer",
    "ThreadingHTTPServer": "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer",
    "parse_qs": "from urllib.parse import parse_qs, quote, urlparse", "quote": "from urllib.parse import parse_qs, quote, urlparse",
    "urlparse": "from urllib.parse import parse_qs, quote, urlparse",
    "core_environments": "from ckp.core import environments as core_environments",
    "store_io": "from ckp.store import io as store_io", "store_layout": "from ckp.store import layout as store_layout",
    "DiskCache": "from ckp.store.cache import DiskCache",
    "SETTING_DEFAULTS": "from dispatcher.core import DEFAULTS as SETTING_DEFAULTS", "load_settings": "from dispatcher.core import load_settings",
    "RuntimeConfig": "from dispatcher.runtime import Config as RuntimeConfig",
}


def bound(fn):
    """Names local to a function-like scope node."""
    out = set()
    args = fn.args
    for a in args.posonlyargs + args.args + args.kwonlyargs + ([args.vararg] if args.vararg else []) + ([args.kwarg] if args.kwarg else []):
        out.add(a.arg)
    glob = set()

    def walk(n):
        for c in ast.iter_child_nodes(n):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(c.name)
                continue
            if isinstance(c, ast.Lambda):
                continue
            if isinstance(c, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                # the comprehension's own targets are local to it; its first iterable and walrus bind here
                for sub in ast.walk(c):
                    if isinstance(sub, ast.NamedExpr) and isinstance(sub.target, ast.Name):
                        out.add(sub.target.id)
                continue
            if isinstance(c, ast.Name) and isinstance(c.ctx, (ast.Store, ast.Del)):
                out.add(c.id)
            elif isinstance(c, ast.ExceptHandler) and c.name:
                out.add(c.name)
            elif isinstance(c, (ast.Import, ast.ImportFrom)):
                for a in c.names:
                    out.add((a.asname or a.name).split(".")[0])
            elif isinstance(c, (ast.Global, ast.Nonlocal)):
                glob.update(c.names)
            walk(c)
    walk(fn)
    return out - glob


def comp_targets(c):
    out = set()
    for g in c.generators:
        for t in ast.walk(g.target):
            if isinstance(t, ast.Name):
                out.add(t.id)
    return out


def find_refs(node, own_module, report, own, core_of):
    """[(lineno, col, endcol, newtext)] replacements inside `node` plus the set of used external names."""
    repl, used = [], set()

    def visit(n, scopes):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if isinstance(n, ast.Lambda):
                loc = {a.arg for a in n.args.args + n.args.kwonlyargs} | ({n.args.vararg.arg} if n.args.vararg else set())
            else:
                loc = bound(n)
                for d in n.decorator_list + [x for x in n.args.defaults + n.args.kw_defaults if x]:
                    visit(d, scopes)
            sc = scopes + [loc]
            for c in (n.body if isinstance(n.body, list) else [n.body]):
                visit(c, sc)
            return
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            sc = scopes + [comp_targets(n)]
            for c in ast.iter_child_nodes(n):
                visit(c, sc)
            return
        if isinstance(n, ast.ClassDef):
            for c in n.body + n.bases:
                visit(c, scopes)
            return
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "L" and not any("L" in s for s in scopes):
            theme = core_of.get(n.attr)
            if theme is None:
                report.append(f"L.{n.attr}: not found in logic.py")
            else:
                repl.append((n.value.lineno, n.value.col_offset, n.value.end_col_offset, f"core_{theme}"))
                used.add(f"core_{theme}")
            return
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if not any(n.id in s for s in scopes):
                if n.id in own and own[n.id] != own_module:
                    short = own[n.id].split("/")[-1]
                    pre = "server" if own[n.id].startswith("@server") else "api"
                    repl.append((n.lineno, n.col_offset, n.end_col_offset, f"{pre}_{short}.{n.id}"))
                    used.add(f"{pre}_{short}")
                else:
                    used.add(n.id)
            return
        for c in ast.iter_child_nodes(n):
            visit(c, scopes)

    visit(node, [])
    return repl, used


def apply(unit_src, first_line, repl):
    ls = unit_src.splitlines(keepends=True)
    byline = {}
    for (ln, c0, c1, text) in repl:
        byline.setdefault(ln - 1 - first_line, []).append((c0, c1, text))
    for i, items in byline.items():
        b = ls[i].encode()
        for c0, c1, text in sorted(items, reverse=True):
            b = b[:c0] + text.encode() + b[c1:]
        ls[i] = b.decode()
    return "".join(ls)


def parse_units(src, own):
    """[(names, node, first line, last line)] of the top-level definitions of `src` (imports, the docstring and `if` blocks left out)."""
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    units, prev_end = [], 0
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom, ast.Expr, ast.If)):
            continue
        start = n.lineno - 1
        while start > 0 and lines[start - 1].lstrip().startswith("#") and (start - 1) >= prev_end:
            start -= 1
        prev_end = n.end_lineno
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            names = [n.name]
        elif isinstance(n, ast.Assign):
            names = [t.id for t in ast.walk(ast.Tuple(elts=n.targets, ctx=ast.Store())) if isinstance(t, ast.Name)]
        else:
            raise ValueError(f"unhandled top-level statement at line {n.lineno}")
        units.append((names, n, start, n.end_lineno))
    return units


def unassigned(units, own, skip=("log", "HERE")):
    """The names of the units no target takes (the per-module `log` and `HERE` are expected)."""
    return [n for names, *_ in units for n in names if n not in own and n not in skip]


def split(src, own, core_of):
    """The original source -> ({module: generated text}, [problems])."""
    lines = src.splitlines(keepends=True)
    out, report = {}, []
    for names, node, s, e in parse_units(src, own):
        mod = own.get(names[0])
        if mod is None:
            if names[0] not in ("log", "HERE"):
                report.append(f"UNASSIGNED {names}")
            continue
        repl, used = find_refs(node, mod, report, own, core_of)
        out.setdefault(mod, []).append((apply("".join(lines[s:e]), s, repl), used))
    files = {}
    for mod, items in out.items():
        used = set().union(*[u for _, u in items])
        imp = sorted({STD_IMPORTS[n] for n in used if n in STD_IMPORTS})
        imp_api = sorted(n for n in used if n.startswith("api_"))
        imp_srv = sorted(n for n in used if n.startswith("server_"))
        imp_core = sorted(n for n in used if n.startswith("core_"))
        std = [i for i in imp if i.startswith(("import ", "from concurrent", "from http", "from urllib"))]
        local = [i for i in imp if i not in std]
        h2 = (["from ckp.core import " + ", ".join(f"{n[5:]} as {n}" for n in imp_core)] if imp_core else []) + local
        if imp_api:
            h2.append("from ckp.api import " + ", ".join(f"{n[4:]} as {n}" for n in imp_api))
        if imp_srv:
            h2.append("from ckp.server import " + ", ".join(f"{n[7:]} as {n}" for n in imp_srv))
        if "L" in used:
            report.append(f"{mod}: bare L used")
        head = "\n".join(std) + "\n\n" + ("\n".join(h2) + "\n\n" if h2 else "")
        if "log" in used:
            head = ("import logging\n" if "logging" not in used else "") + head + 'log = logging.getLogger("cockpit")\n\n\n'
        files[mod] = head + "\n\n".join(t.rstrip("\n") + "\n" for t, _ in items)
    return files, report


def check_routes(src, routes_src):
    """The paths the old handlers compare with that the routing table does not name (should be []): strings starting with `/` inside do_GET/do_POST."""
    paths = set()
    for fn in ast.walk(ast.parse(src)):
        if isinstance(fn, ast.FunctionDef) and fn.name in ("do_GET", "do_POST"):
            paths |= {c.value for c in ast.walk(fn) if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value.startswith("/")}
    return sorted(p for p in paths if p not in routes_src)


def check_shim(own, shim_modules):
    """The target modules the cockpit.py shim does not search (should be []): `shim_modules` are dotted names like ckp.api.state."""
    wanted = {"ckp.server." + m.split("/")[-1] if m.startswith("@server/") else "ckp.api." + m for m in set(own.values())}
    return sorted(wanted - set(shim_modules))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=".", help="the repository holding the original cockpit.py and logic.py")
    ap.add_argument("--commit", required=True, help="the commit whose cockpit.py is split")
    ap.add_argument("--out", help="write the generated modules under this folder (api/<module>.py, server/<module>.py)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    git = lambda path: subprocess.run(["git", "-C", a.repo, "show", f"{a.commit}:{path}"], capture_output=True, text=True, check=True).stdout
    own = owners()
    files, report = split(git("cockpit.py"), own, core_themes(git("logic.py")))
    for r in report:
        log.warning("%s", r)
    for mod, text in sorted(files.items()):
        log.info("%s: %d lines", mod, text.count("\n"))
        if a.out:
            kind, name = ("server", mod.split("/")[-1]) if mod.startswith("@server/") else ("api", mod)
            f = pathlib.Path(a.out) / kind / f"{name}.py"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
    return 1 if report else 0


if __name__ == "__main__":
    sys.exit(main())
