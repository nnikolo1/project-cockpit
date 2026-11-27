#!/usr/bin/env python3
"""Every UI action has a test (constitution, owner, 2026-11-25; PC-137): the actions a project's code defines, and the tests that name them.

    python3 tools/ui_actions.py [--root DIR] [--config FILE]

Actions are read from the code, never typed by hand:
- server actions: the string constants the action variable is compared with (`a == "x"`, `a in ("x", "y")`, `form.get("action") == "x"`)
  inside the dispatch functions of the server file (ast);
- page actions: the `data-act="x"` names of the page and the `case 'x'` labels of its click handler (`page:x`), and the form kinds of its
  submit handler (`kind==='x'`, `form:x`).
A test names an action with the marker `@pytest.mark.ui_action("server:release", "server:release/refused", ...)`: the plain id is the happy
path, the suffix `/refused` the refusal (a wrong state, a bad value; the error text is checked). The enumeration test
(tests/test_ui_actions.py) and the policy harness both call `gaps`; one parser, one place. Another project with a UI gets its own config
(a JSON file with the keys of CONFIG, paths relative to its root). Exit code: 0 every action is named by a test, 1 violations.
"""
import argparse
import ast
import json
import logging
import pathlib
import re
import sys

log = logging.getLogger("ui_actions")
ROOT = pathlib.Path(__file__).resolve().parents[1]
REFUSED = "/refused"
POLICY = "Every action the UI offers is covered by a test (owner, 2026-11-25)"
# This project's registry entry: where the actions and the tests are.
CONFIG = dict(server="ckp/api/actions.py", dispatch=["_plan", "do_act", "_do_act"], var="a", page="web/index.html", click="function act(a,ds){",
              submit="document.addEventListener('submit'", tests=["tests"])


def server_actions(src, dispatch, var="a"):
    """The action names the dispatch functions compare with: `var == "x"`, `var in ("x", ...)`, `form.get("action") == "x"`."""
    found = set()

    def is_action(node):
        if isinstance(node, ast.Name) and node.id == var:
            return True
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "action")

    for fn in ast.walk(ast.parse(src)):
        if not (isinstance(fn, ast.FunctionDef) and fn.name in dispatch):
            continue
        for c in ast.walk(fn):
            if isinstance(c, ast.Compare) and is_action(c.left) and isinstance(c.ops[0], (ast.Eq, ast.In)):
                v = c.comparators[0]
                for x in (v.elts if isinstance(v, (ast.Tuple, ast.List, ast.Set)) else [v]):
                    if isinstance(x, ast.Constant) and isinstance(x.value, str):
                        found.add(x.value)
    return found


def _body(text, start):
    """The text of the brace block that opens at the first `{` from `start` (the page's handler; braces in strings are balanced there)."""
    i = text.index(start)
    i = text.index("{", i + len(start) - 1) if not start.endswith("{") else i + len(start) - 1
    depth = 0
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i:j + 1]
    raise ValueError(f"unbalanced block after {start!r}")


def page_actions(html, click, submit):
    """-> (page actions, form kinds): the data-act names and the click handler's cases; the submit handler's form kinds."""
    acts = set(re.findall(r'data-act="([A-Za-z_]\w*)"', html)) | set(re.findall(r"case '([^']+)'", _body(html, click)))
    forms = set(re.findall(r"kind==='([^']+)'", _body(html, submit)))
    return acts, forms


def actions(root=ROOT, cfg=CONFIG):
    """Every action id the code defines: server:<a>, page:<a>, form:<kind>."""
    root = pathlib.Path(root)
    ids = {f"server:{a}" for a in server_actions((root / cfg["server"]).read_text(), cfg["dispatch"], cfg.get("var", "a"))}
    if cfg.get("page"):
        acts, forms = page_actions((root / cfg["page"]).read_text(), cfg["click"], cfg["submit"])
        ids |= {f"page:{a}" for a in acts} | {f"form:{f}" for f in forms}
    return ids


def covered(root=ROOT, cfg=CONFIG):
    """{action id or id/refused: [test file::function, ...]} from the `ui_action` markers of the test files (ast, nothing is imported)."""
    root, out = pathlib.Path(root), {}
    files = sorted(f for d in cfg["tests"] for f in (root / d).rglob("test_*.py"))
    for f in files:
        for fn in ast.walk(ast.parse(f.read_text())):
            if not (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name.startswith("test")):
                continue
            for d in fn.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "ui_action":
                    for a in d.args:
                        if isinstance(a, ast.Constant) and isinstance(a.value, str):
                            out.setdefault(a.value, []).append(f"{f.relative_to(root)}::{fn.name}")
    return out


def gaps(root=ROOT, cfg=CONFIG, state_changing=None):
    """-> list of violations (strings): an action no test names; with `state_changing` (the set of ids that change state), such an
    action without a refusal test; a marker that names no action of the code (stale)."""
    ids, cov = actions(root, cfg), covered(root, cfg)
    out = [f"{a}: no test names this action" for a in sorted(ids) if a not in cov]
    out += [f"{a}: no test of its refusal ({a}{REFUSED})" for a in sorted(ids & set(state_changing or ())) if a + REFUSED not in cov]
    out += [f"{k}: marker names an action the code does not define ({', '.join(v)})" for k, v in sorted(cov.items())
            if k.removesuffix(REFUSED) not in ids]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(ROOT), help="the project's checkout")
    ap.add_argument("--config", help="JSON file with the keys of CONFIG (another project's paths)")
    ap.add_argument("--list", action="store_true", help="also list every action with the tests that name it")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = json.loads(pathlib.Path(a.config).read_text()) if a.config else CONFIG
    if a.list:
        cov = covered(a.root, cfg)
        for x in sorted(actions(a.root, cfg)):
            log.info("%s: %s | refused: %s", x, ", ".join(cov.get(x, ["-"])), ", ".join(cov.get(x + REFUSED, ["-"])))
    v = gaps(a.root, cfg)
    for x in v:
        log.warning("%s: %s", POLICY, x)
    log.info("%d actions, %d violations", len(actions(a.root, cfg)), len(v))
    return 1 if v else 0


if __name__ == "__main__":
    sys.exit(main())
