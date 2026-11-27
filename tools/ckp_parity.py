#!/usr/bin/env python3
"""Parity inventory of the port onto the `ckp` structure (refactoring slice 0; docs/contracts/ckp-architecture.md).

    python3 tools/ckp_parity.py --check  [--root DIR] [--config FILE]     exit 1: an old item without mapping, a stale entry, a moved item its target lacks
    python3 tools/ckp_parity.py --report [--root DIR] [--config FILE]     the items not yet ported, grouped by target, with counts

The old surface is read from the code (ast, nothing is imported, no network): every public function and class of logic.py (`logic:NAME`),
every HTTP route of cockpit.py (`route:GET /path`), every UI action (`action:server:x`, `action:page:x`, `action:form:x`; the parser is
tools/ui_actions.py), every module of dispatcher/ (`dispatcher:NAME`) and every `ckp` subcommand (`command:ckp env set`).
The hand-decided mapping docs/ckp-parity-map.json says where each item goes: {"items": {ITEM: {"target": T, "basis": "one line",
"as": "new name, optional"}}}; T is a module under ckp/core, ckp/queue, ckp/server, ckp/adapters or ckp/store, or a `ckp ...` command.
The status is computed here, never typed in the mapping. An item named in the mapping is part of the inventory even when its old definition is
gone (so the counts do not shrink as code moves). An item is ported when its target really provides it and the old name is only a shim:
- logic:   the target module imports (importlib, in a subprocess with the root on the path) and exposes the name (`as` if given) as a callable
           or class, and logic.py no longer defines it with def or class; placed is not ported;
- dispatcher: the target module exists and the old module defines nothing (only imports) or is gone;
- route, action:server: the target module names the path or action and the old server file no longer routes it;
- action:page, action:form: the page stays in place; the target (an API function's module or a command) exists;
- command: the command exists (it is already on the new structure).
A DECISION marker at the start of a basis flags a placement that is a real design choice; --report counts those.
Generated output goes to stdout. Config: a JSON file with the keys of CONFIG (paths relative to the root).
--check violations: an old item without a mapping entry; a malformed entry; a mapped item gone from the old code whose target does not provide
it (for a logic item: "defined elsewhere" or, when it exists nowhere in ckp/, "stale"); a page or form action that no longer exists.
Exit code: 0 ok, 1 (--check) violations.
"""
import argparse
import ast
import json
import logging
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ui_actions  # noqa: E402

log = logging.getLogger("ckp_parity")
ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = dict(logic=["logic.py"], server="cockpit.py", handlers=["do_GET", "do_POST"], dispatcher="dispatcher", cli="ckp/cli.py",
              cli_builder="build_parser", map="docs/ckp-parity-map.json", ui=ui_actions.CONFIG)
TARGET_PREFIXES = ("ckp/core/", "ckp/queue/", "ckp/server/", "ckp/adapters/", "ckp/store/")
DECISION = "DECISION"
KINDS = {"logic": "logic", "route": "route", "action": "action", "dispatcher": "dispatcher", "command": "command"}
PROBE = """
import importlib, json, sys
out = {}
for m in sys.argv[1:]:
    try:
        mod = importlib.import_module(m)
        out[m] = [n for n in dir(mod) if not n.startswith("_") and callable(getattr(mod, n, None))]
    except Exception:
        out[m] = None
print(json.dumps(out))
"""


def _tree(path):
    p = pathlib.Path(path)
    return ast.parse(p.read_text()) if p.is_file() else None


def defined(tree, kinds=(ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
    """The top-level names a module binds with def/class (default) or, with ast.Assign in `kinds`, also by assignment."""
    out = set()
    for n in tree.body if tree else []:
        if isinstance(n, kinds) and hasattr(n, "name"):
            out.add(n.name)
        if ast.Assign in kinds and isinstance(n, ast.Assign):
            out |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        if ast.AnnAssign in kinds and isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
    return out


def defined_loosely(tree):
    """def, class and assignment: what a target module may use to define a ported name."""
    return defined(tree, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign))


def logic_items(root, cfg):
    return {f"logic:{n}" for f in cfg["logic"] for n in defined(_tree(root / f)) if not n.startswith("_")}


def routes(tree, handlers):
    """{"METHOD /path"}: the paths the handler methods (do_GET -> GET) compare `<x>.path` with: ==, in, not in, startswith (shown with a `*`)."""
    found = set()

    def is_path(n):
        return isinstance(n, ast.Attribute) and n.attr == "path"

    for fn in ast.walk(tree) if tree else []:
        if not (isinstance(fn, ast.FunctionDef) and fn.name in handlers):
            continue
        method = fn.name.removeprefix("do_")
        for c in ast.walk(fn):
            vals = []
            if isinstance(c, ast.Compare) and is_path(c.left) and isinstance(c.ops[0], (ast.Eq, ast.In, ast.NotIn)):
                v = c.comparators[0]
                vals = [x.value for x in (v.elts if isinstance(v, (ast.Tuple, ast.List, ast.Set)) else [v])
                        if isinstance(x, ast.Constant) and isinstance(x.value, str)]
            elif (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "startswith" and is_path(c.func.value)
                  and c.args and isinstance(c.args[0], ast.Constant) and isinstance(c.args[0].value, str)):
                vals = [c.args[0].value + "*"]
            found |= {f"{method} {v}" for v in vals}
    return found


def commands(tree, builder):
    """{"ckp env set"}: the leaf subcommands built by `builder`, following add_subparsers/add_parser through variables and helper calls."""
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef)} if tree else {}
    allp, parents = set(), set()

    def ev(node, env):
        if isinstance(node, ast.Name):
            return env.get(node.id)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "ArgumentParser":
            return ("parser", ())
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            return None
        base = ev(node.func.value, env)
        for a in node.args:
            ev(a, env)
        if node.func.attr == "add_subparsers" and base and base[0] == "parser":
            return ("subs", base[1])
        if (node.func.attr == "add_parser" and base and base[0] == "subs" and node.args
                and isinstance(node.args[0], ast.Constant)):
            path = base[1] + (node.args[0].value,)
            allp.add(path)
            if base[1]:
                parents.add(base[1])
            return ("parser", path)
        return None

    def run(fn, env, depth=0):
        for st in _ordered(fn):
            if isinstance(st, ast.Assign):
                val = ev(st.value, env)
                for t in st.targets:
                    if isinstance(t, ast.Name):
                        env[t.id] = val
            elif isinstance(st, ast.Expr):
                ev(st.value, env)
            if isinstance(st, (ast.Assign, ast.Expr)) and isinstance(st.value, ast.Call):
                call = st.value
                if isinstance(call.func, ast.Name) and call.func.id in funcs and depth < 5 and call.func.id != fn.name:
                    callee = funcs[call.func.id]
                    params = [a.arg for a in callee.args.args]
                    sub = {p: ev(a, env) for p, a in zip(params, call.args)}
                    run(callee, {k: v for k, v in sub.items() if v}, depth + 1)

    if builder in funcs:
        run(funcs[builder], {})
    return {"ckp " + " ".join(p) for p in allp - parents}


def _ordered(fn):
    """The simple statements of a function in source order (top-level and inside for/if/with bodies; not nested defs)."""
    for st in fn.body:
        yield from _flat(st)


def _flat(st):
    if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return
    if isinstance(st, (ast.For, ast.If, ast.With, ast.While, ast.Try)):
        for sub in [*getattr(st, "body", []), *getattr(st, "orelse", []), *getattr(st, "finalbody", [])]:
            yield from _flat(sub)
    else:
        yield st


def inventory(root, cfg=CONFIG):
    """-> {item id: kind}: the old surface, read from the code."""
    root = pathlib.Path(root)
    items = {i: "logic" for i in logic_items(root, cfg)}
    server = _tree(root / cfg["server"])
    items |= {f"route:{r}": "route" for r in routes(server, set(cfg["handlers"]))}
    if (root / cfg["ui"]["server"]).is_file():
        items |= {f"action:{a}": "action" for a in ui_actions.actions(root, cfg["ui"])}
    d = root / cfg["dispatcher"]
    if d.is_dir():
        items |= {f"dispatcher:{f.stem}": "dispatcher" for f in sorted(d.glob("*.py")) if f.stem != "__init__"}
    tree = _tree(root / cfg["cli"])
    items |= {f"command:{c}": "command" for c in commands(tree, cfg["cli_builder"])}
    return items


def load_map(root, cfg=CONFIG):
    p = pathlib.Path(root) / cfg["map"]
    return json.loads(p.read_text()).get("items", {}) if p.is_file() else {}


def entry_problems(item, entry):
    t, basis = (entry or {}).get("target", ""), (entry or {}).get("basis", "")
    out = []
    if not isinstance(t, str) or not (t.startswith(TARGET_PREFIXES) or t.startswith("ckp ")):
        out.append(f"{item}: target {t!r} is neither a module under {', '.join(TARGET_PREFIXES)} nor a `ckp ...` command")
    if not isinstance(basis, str) or not basis.strip():
        out.append(f"{item}: no basis")
    return out


def _module_file(root, target):
    base = root / target
    return base.with_suffix(".py") if base.with_suffix(".py").is_file() else (base / "__init__.py" if (base / "__init__.py").is_file() else None)


def _names(tree):
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)} if tree else set()


def exposed(root, targets):
    """-> {target path: set of callable public names, or None when the module is absent or does not import}; one fresh interpreter, root first on its path."""
    root = pathlib.Path(root)
    mods = {t: t.replace("/", ".") for t in targets if _module_file(root, t)}
    out = {t: None for t in targets}
    if mods:
        r = subprocess.run([sys.executable, "-c", PROBE, *mods.values()], cwd=root, capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": str(root)})
        try:
            got = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            log.warning("module probe failed: %s", r.stderr.strip()[-300:])
            got = {}
        out |= {t: set(got[m]) if got.get(m) is not None else None for t, m in mods.items()}
    return out


def status(item, kind, entry, root, cfg, cmds, avail):
    """-> True when the item is ported: its target exists and the old name is only a shim (rules in the module docstring)."""
    t = entry["target"]
    name = item.split(":", 1)[1]
    if t.startswith("ckp "):
        return t in cmds and kind in ("command", "action")
    mod = _module_file(root, t)
    if mod is None:
        return False
    tree = _tree(mod)
    if kind == "logic":
        return entry.get("as", name) in (avail.get(t) or ()) and not any(name in defined(_tree(root / f)) for f in cfg["logic"])
    if kind == "dispatcher":
        old = root / cfg["dispatcher"] / f"{name}.py"
        return not old.is_file() or not defined(_tree(old))
    if kind == "route":
        path = name.split(" ", 1)[1].removesuffix("*")
        return path in _names(tree) and item.split(":", 1)[1] not in routes(_tree(root / cfg["server"]), set(cfg["handlers"]))
    if item.startswith("action:server:"):
        act = name.removeprefix("server:")
        return act in _names(tree) and f"server:{act}" not in {f"server:{a}" for a in
                                                               ui_actions.server_actions((root / cfg["ui"]["server"]).read_text(), cfg["ui"]["dispatch"], cfg["ui"].get("var", "a"))}
    return True   # page and form actions: the page stays; the target module exists


def evaluate(root, cfg=CONFIG):
    """-> (items {id: kind}, mapping, unmapped [ids], problems [str], ported {id}); items = the old surface plus every mapped item."""
    root = pathlib.Path(root)
    old, mapping = inventory(root, cfg), load_map(root, cfg)
    items = {**{i: KINDS.get(i.split(":", 1)[0], "logic") for i in mapping}, **old}
    cmds = {i.split(":", 1)[1] for i, k in items.items() if k == "command"}
    unmapped = sorted(i for i in old if i not in mapping)
    problems = [p for i, e in sorted(mapping.items()) for p in entry_problems(i, e)]
    good = {i: e for i, e in mapping.items() if not entry_problems(i, e)}
    avail = exposed(root, {e["target"] for i, e in good.items() if items[i] == "logic" and e["target"].startswith(TARGET_PREFIXES)})
    ported = {i for i, e in good.items() if status(i, items[i], e, root, cfg, cmds, avail)}
    elsewhere = _defined_in_ckp(root) if any(i not in old and i not in ported and items[i] == "logic" for i in good) else {}
    for i, e in sorted(good.items()):
        name = e.get("as", i.split(":", 1)[1])
        if items[i] == "action" and not i.startswith("action:server:") and i not in old:
            problems.append(f"{i}: stale: the page or form action no longer exists")
            ported.discard(i)
        elif i in old or i in ported:
            continue
        elif items[i] == "logic" and not elsewhere.get(name):
            problems.append(f"{i}: stale: {name} is defined nowhere in ckp/ and the old file no longer has it")
        elif items[i] == "logic":
            problems.append(f"{i}: target {e['target']} does not define or expose {name} (defined in {', '.join(sorted(elsewhere[name]))})")
        else:
            problems.append(f"{i}: gone from the old code and target {e['target']} does not provide it (stale or not yet ported)")
    log.info("%d items, %d old, %d unmapped, %d ported", len(items), len(old), len(unmapped), len(ported))
    return items, mapping, unmapped, problems, ported


def _defined_in_ckp(root):
    """-> {name: {module path}} for every def, class or assignment at the top level of the modules under ckp/."""
    out = {}
    for f in sorted((root / "ckp").rglob("*.py")):
        for n in defined_loosely(_tree(f)):
            out.setdefault(n, set()).add(str(f.relative_to(root).with_suffix("")))
    return out


def check(root, cfg=CONFIG):
    """-> list of violations: an old item without a mapping entry, a malformed or stale entry, a moved item its target lacks."""
    _, _, unmapped, problems, _ = evaluate(root, cfg)
    return [f"{i}: no mapping entry" for i in unmapped] + problems


def report(root, cfg=CONFIG):
    items, mapping, unmapped, _, ported = evaluate(root, cfg)
    kinds = sorted(set(items.values()))
    lines = ["kind            items   placed   ported"]
    for k in kinds:
        ids = [i for i, kk in items.items() if kk == k]
        lines.append(f"{k:<11} {len(ids):>9} {sum(i in mapping for i in ids):>8} {sum(i in ported for i in ids):>8}")
    lines.append(f"{'all':<11} {len(items):>9} {sum(i in mapping for i in items):>8} {len(ported & set(items)):>8}")
    open_ = {i: mapping[i] for i in items if i in mapping and i not in ported}
    groups = {}
    for i, e in open_.items():
        groups.setdefault(e["target"], []).append(i)
    lines.append(f"\nnot yet ported: {len(open_)} placed, {len(unmapped)} without a mapping entry; "
                 f"{sum(e.get('basis', '').startswith(DECISION) for e in mapping.values())} placements marked {DECISION}")
    for t in sorted(groups):
        lines.append(f"\n{t} ({len(groups[t])})")
        lines += [f"  {i}" for i in sorted(groups[t])]
    if unmapped:
        lines += ["\nno mapping entry:"] + [f"  {i}" for i in unmapped]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--config", help="JSON file with the keys of CONFIG (default: this project's)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="exit 1: unmapped old item, stale entry, moved item missing from its target")
    g.add_argument("--report", action="store_true", help="the items not yet ported, grouped by target")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    cfg = {**CONFIG, **json.loads(pathlib.Path(a.config).read_text())} if a.config else CONFIG
    if a.report:
        print(report(a.root, cfg))
        return 0
    bad = check(a.root, cfg)
    print("\n".join(bad) if bad else "every old item has a mapping entry")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
