#!/usr/bin/env python3
"""ambiguity.json collector: files that cannot be placed unambiguously (read-only).

For every module of the map: own_files, sub_files and, for a module with sub-modules, `unsorted` (files kept in the parent, with
their `candidates` and the entry's `basis` as the reason). `module_level` lists explicit `module: null` entries that match a tracked
file and entries whose candidates span several top-level modules (they need the owner's decision). `unassigned_nondoc` counts tracked
files with no module, docs (docs/** and *.md) not counted. Shape: docs/contracts/card-metadata.md.

Usage: python3 tools/cardmeta_ambiguity.py --project NAME [--repo .] [--map docs/cockpit-modules.json] [--out-dir DIR] [--format json|md]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cardmeta_common as cm  # noqa: E402
import cardmeta_map as mp  # noqa: E402

log = logging.getLogger("cardmeta.ambiguity")


def is_doc(path: str) -> bool:
    return path.startswith("docs/") or path.endswith(".md")


def module_level(files: list[str], mmap: dict) -> list[dict]:
    """Map entries that need a decision about the MODULE (pure), one per entry, in map order."""
    tracked = set(files)
    res = []
    for a in mmap["assign"]:
        p = a["path"]
        live = any(t.startswith(p) for t in tracked) if p.endswith("/") else p in tracked or bool(a.get("alias"))
        cands = list(a.get("candidates", []))
        spans = len({mp.top_of(c) for c in cands}) > 1
        if (a["module"] is None and live) or spans:
            res.append({"path": p, "candidates": cands, "reason": a.get("basis") or "no basis recorded"})
    return res


def aggregate(files: list[str], mmap: dict) -> dict:
    """Pure: tracked files + map -> {modules, module_level, unassigned_nondoc}."""
    ids = [m["id"] for m in mmap["modules"]]
    own: dict[str, list[str]] = {i: [] for i in ids}
    unassigned_nondoc = 0
    for f in files:
        m = mp.module_of(f, mmap)
        if m == mp.UNASSIGNED:
            unassigned_nondoc += 0 if is_doc(f) else 1
        else:
            own[m].append(f)
    out = {}
    for mid in ids:
        children = [i for i in ids if mp.parent_of(i) == mid]
        unsorted = []
        if children:
            unsorted = [{"path": f, "candidates": mp.candidates_of(f, mmap) or [], "reason": mp.basis_of(f, mmap) or "no basis recorded"}
                        for f in sorted(own[mid])]
        out[mid] = {"own_files": 0 if children else len(own[mid]),
                    "sub_files": {c: sum(len(v) for k, v in own.items() if mp.in_module(k, c)) for c in children},
                    "unsorted": unsorted}
    return {"modules": out, "module_level": module_level(files, mmap), "unassigned_nondoc": unassigned_nondoc}


def render_md(res: dict) -> str:
    o = ["# Assignment ambiguity", "", "| module | own files | sub-module files | unsorted |", "|---|--:|---|--:|"]
    for mid, e in res["modules"].items():
        o.append(f"| {mid} | {e['own_files']} | {', '.join(f'{k}: {v}' for k, v in e['sub_files'].items())} | {len(e['unsorted'])} |")
    o += ["", f"Module-level ambiguity ({len(res['module_level'])} entries):"]
    o += [f"- {r['path']}  candidates: {', '.join(r['candidates']) or 'none'}  ({r['reason']})" for r in res["module_level"]]
    o.append(f"\nUnassigned non-doc files: {res['unassigned_nondoc']}")
    return "\n".join(o) + "\n"


def collect(repo: Path, map_path: Path, project: str) -> dict:
    files = mp.tracked_files(repo)
    res = aggregate(files, mp.load_map(map_path))
    log.info("ambiguity: %d tracked files, %d modules, %d module-level entries, %d unassigned non-doc files",
             len(files), len(res["modules"]), len(res["module_level"]), res["unassigned_nondoc"])
    return {**cm.header("ambiguity", project), **res}


def add_common_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--repo", default=".", help="repository to analyse (default: current directory)")
    ap.add_argument("--map", help="module map JSON (default: <repo>/docs/cockpit-modules.json)")
    ap.add_argument("--project", help="project name as in the --projects file (default: repository folder name)")
    ap.add_argument("--out-dir", help="output folder (default ~/.local/share/project-cockpit/<project>/cardmeta); never inside the repository")
    ap.add_argument("-v", "--verbose", action="store_true")


def setup(a: argparse.Namespace) -> tuple[Path, Path, str]:
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(name)s %(message)s", stream=sys.stderr)
    repo = Path(a.repo).resolve()
    return repo, Path(a.map) if a.map else repo / "docs" / "cockpit-modules.json", a.project or repo.name


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(ap)
    ap.add_argument("--format", choices=["json", "md"], default="json", help="md prints a summary and writes nothing")
    a = ap.parse_args(argv)
    repo, map_path, project = setup(a)
    payload = collect(repo, map_path, project)
    if a.format == "md":
        sys.stdout.write(render_md(payload))
        return 0
    cm.write_kind(cm.resolve_out_dir(a.out_dir, repo, project), "ambiguity", payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
