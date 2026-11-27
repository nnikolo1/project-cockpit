"""One-shot, kept for the record: how logic.py was split into the themed ckp/core modules (refactoring slice 1).

It ran against commit 349b87d8 (`git rev-parse` of main before the change): the original logic.py is read with
`git show <commit>:logic.py`, never copied into the repository. Output: ckp/core/<theme>.py for each theme and the re-exporting logic.py shim.
  split_logic.py --commit 349b87d8 --out DIR        write the files under DIR (default: print the theme table only)
  split_logic.py --commit 349b87d8 --table          print the theme table (theme: names)
How: every top-level definition is a unit (with the comments above it); a unit goes to the theme of its section in logic.py, with the
overrides below that break import cycles; each module imports exactly the names it uses from the other themes; the dependency order is
common/settings/rules -> modules -> commits/usage/tasks_model -> queue_view -> health (acyclic, checked by `cycles`).
"""
import argparse
import ast
import collections
import logging
import pathlib
import re
import subprocess
import sys

log = logging.getLogger("ckp_split")

STD = ["bisect", "datetime as dt", "json", "logging", "math", "pathlib", "re", "shlex"]
STDNAME = {s.split(" as ")[-1]: s for s in STD}
HEADER_END = 11   # the lines before this are the docstring, the std imports and `log`
# (first line, last line, theme) of the sections of the original logic.py
RANGES = [(13, 237, "tasks_model"), (238, 525, "modules"), (526, 602, "tasks_model"), (603, 737, "commits"), (738, 802, "modules"),
          (803, 975, "tasks_model"), (976, 1123, "rules"), (1124, 1305, "modules"), (1306, 1451, "settings"), (1452, 1480, "modules"),
          (1481, 2072, "usage"), (2073, 2393, "health"), (2394, 2738, "queue_view"), (2739, 2907, "health"), (2908, 2939, "queue_view")]
OVERRIDES = dict(map_entry="modules", queue_interval="queue_view", map_path="modules", STAGES="common", STATES="common",
                 GAPS_MAX_AGE_H="common", _age_h="common")
DOC = {
    "common": "Shared constants and helpers of the other ckp.core modules. Depends on nothing.",
    "settings": "Validation of the Dispatcher, module-card, queue-refresh and quota settings. Depends on common only.",
    "modules": "Module map, files, code snapshot, code view, relations, card metadata, churn, bands, footprint. Depends on common and settings.",
    "rules": "Rule files, the rule feed and the policy text. Depends on nothing else in ckp.core.",
    "commits": "Git log parsing, commit lanes and the timeline feed. Depends on modules.",
    "usage": "Usage report, session transcripts, quota readings and estimates. Depends on modules and settings.",
    "tasks_model": "Task model: stage, derive, dispatchable, blocking, ranking, ops, proposals, questions. Depends on common and modules.",
    "queue_view": "Task Queue view: queue state, capacity, log rows, the live overlay, open-issue counts. "
                  "Depends on modules, settings, tasks_model and usage.",
    "health": "Gaps in the project metadata, backup, CI, compliance and the action log. Depends on common, modules, rules, commits and usage.",
}


def parse_units(text, header_end=HEADER_END):
    """Source text -> units [{names, cs (first line incl. leading comments), e (last line), uses, text, gapblank}] of the top-level statements
    (imports and docstrings are skipped)."""
    lines = text.split("\n")
    units = []
    for n in ast.parse(text).body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            names = [n.name]
        elif isinstance(n, ast.Assign):
            names = [x.id for t in n.targets for x in ast.walk(t) if isinstance(x, ast.Name)]
        elif isinstance(n, ast.AnnAssign):
            names = [n.target.id]
        elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant):
            continue
        else:
            raise ValueError(f"unsupported top-level statement at line {n.lineno}: {type(n).__name__}")
        s = min([n.lineno] + [d.lineno for d in getattr(n, "decorator_list", [])])
        units.append(dict(names=names, s=s, e=n.end_lineno, node=n))
    prev = header_end
    for u in units:
        s = u["s"]
        while s - 1 > prev and lines[s - 2].lstrip().startswith("#"):
            s -= 1
        u["cs"] = s
        gap = [l for l in lines[prev:s - 1] if l.startswith("#")]
        u["text"] = "\n".join(gap + lines[s - 1:u["e"]])
        u["gapblank"] = s - 1 - prev
        prev = u["e"]
    defined = {n for u in units for n in u["names"]}
    for u in units:
        u["uses"] = sorted({x.id for x in ast.walk(u["node"]) if isinstance(x, ast.Name) and x.id in defined and x.id not in u["names"]})
        del u["node"]
    log.info("parsed %d units", len(units))
    return units


def assign_themes(units, ranges=RANGES, overrides=OVERRIDES):
    """Set u['theme'] on every unit: an override by name, else the section its first line lies in; `log` is the pseudo-theme LOG."""
    for u in units:
        if u["names"] == ["log"]:
            u["theme"] = "LOG"
            continue
        o = [overrides[n] for n in u["names"] if n in overrides]
        sec = [t for a, b, t in ranges if a <= u["cs"] <= b]
        if not (o or sec):
            raise ValueError(f"no theme for {u['names']} (line {u['cs']})")
        u["theme"] = (o or sec)[0]
    return units


def edges(units):
    """-> {theme: {theme it imports from}} over the cross-theme uses."""
    by = {n: u["theme"] for u in units for n in u["names"]}
    out = collections.defaultdict(set)
    for u in units:
        for d in u["uses"]:
            if by[d] not in (u["theme"], "LOG"):
                out[u["theme"]].add(by[d])
    return out


def cycles(units):
    """-> the themes that lie on an import cycle (empty when the order is acyclic)."""
    g, left = edges(units), set(edges(units))
    left |= {t for v in g.values() for t in v}
    while True:
        free = {t for t in left if not (g.get(t, set()) & left)}
        if not free:
            return sorted(left)
        left -= free
        if not left:
            return []


def module_source(units, theme, doc=DOC):
    """The source of ckp/core/<theme>.py."""
    us = [u for u in units if u["theme"] == theme]
    parts = []
    for u in us:
        if parts:
            same = units.index(u) > 0 and units[units.index(u) - 1]["theme"] == theme
            parts.append("\n" * (u["gapblank"] + 1 if same else 3))
        parts.append(u["text"])
    body = "".join(parts)
    by = {n: x["theme"] for x in units for n in x["names"]}
    imports = collections.defaultdict(set)
    for u in us:
        for d in u["uses"]:
            if by[d] not in (theme, "LOG"):
                imports[by[d]].add(d)
    std = [STDNAME[k] for k in STDNAME if re.search(r"\b%s\." % k, body)]
    if "log." in body and "logging" not in std:
        std.insert(0, "logging")
    hdr = f'"""{doc[theme]}"""\n' + "".join(f"import {s}\n" for s in std)
    hdr += "".join(f"from ckp.core.{t} import " + ", ".join(sorted(imports[t])) + "\n" for t in sorted(imports))
    hdr += '\nlog = logging.getLogger("cockpit")\n' if "log." in body else ""
    return hdr + "\n\n" + body + "\n"


def shim_source(units, themes=tuple(DOC)):
    """The source of the re-exporting logic.py."""
    sh = ['"""Pure logic of the cockpit: no network, no subprocess. Everything here is unit-tested.', '',
          'Shim (refactoring slice 1, docs/contracts/ckp-architecture.md): the code lives in the themed modules of ckp/core; this module',
          're-exports every name it used to define, private ones included, so `import logic as L` keeps working. '
          'Deleted only in the final cleanup slice."""']
    sh += [f"import {s}  # noqa: F401" for s in STD] + [""]
    for t in themes:
        sh.append(f"from ckp.core.{t} import (  # noqa: F401")
        cur = "   "
        for n in [n for u in units if u["theme"] == t for n in u["names"]]:
            if len(cur) + len(n) + 2 > 150:
                sh.append(cur.rstrip())
                cur = "   "
            cur += f" {n},"
        sh += [cur.rstrip(), ")"]
    return "\n".join(sh + ["", 'log = logging.getLogger("cockpit")']) + "\n"


def theme_table(units, themes=tuple(DOC)):
    return {t: [n for u in units if u["theme"] == t for n in u["names"]] for t in themes}


def original(repo, commit):
    r = subprocess.run(["git", "show", f"{commit}:logic.py"], cwd=repo, capture_output=True, text=True, check=True)
    return r.stdout


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=".", help="the repository holding the original logic.py")
    ap.add_argument("--commit", required=True, help="the commit whose logic.py is split")
    ap.add_argument("--out", help="write ckp/core/<theme>.py and logic.py under this folder")
    ap.add_argument("--table", action="store_true", help="print the theme table")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    units = assign_themes(parse_units(original(a.repo, a.commit)))
    bad = cycles(units)
    if bad:
        log.error("import cycle among %s", bad)
        return 1
    if a.table or not a.out:
        for t, ns in theme_table(units).items():
            print(f"{t}: {', '.join(ns)}")
    if a.out:
        out = pathlib.Path(a.out)
        (out / "ckp" / "core").mkdir(parents=True, exist_ok=True)
        for t in DOC:
            (out / "ckp" / "core" / f"{t}.py").write_text(module_source(units, t))
            log.info("wrote ckp/core/%s.py", t)
        (out / "logic.py").write_text(shim_source(units))
        log.info("wrote logic.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
