#!/usr/bin/env python3
"""footprint.json collector: what each module persists (read-only: files are only stat-ed, never opened).

The committed declaration (default docs/footprint-declared.json) is the decision part: per module the files and folders it writes, its
loggers and its backup / retention statements. This collector adds the measured part from the live state:
- files: `size_bytes` (a file) or `count` (a folder: regular files in it) and `exists`; paths are repo-relative, absolute or `~/...`;
  the placeholder `<project>` is replaced by the project name. A `*` in a path is a glob. Symlinks are not followed. No file is read.
- logging: the declared loggers, with `size_bytes` of their file when they have one.
- volume: only for declared files with `"volume": true`: files modified in the last day / 7 / 30 days (`rows`) and their bytes (`bytes`),
  from modification times. This is an approximation of what was written, not a write counter; modules without such files get `{}`.
- tables: always [] here (the cockpit keeps no database); the key is there for the contract.
- policy: the declared strings; a missing one is written as "undocumented", which the cockpit shows as a gap. `gaps` lists them.
Shape: docs/contracts/card-metadata.md.

Usage: python3 tools/cardmeta_footprint.py --project NAME [--repo .] [--declared docs/footprint-declared.json] [--out-dir DIR] [--format json|md]
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cardmeta_ambiguity as amb  # noqa: E402  (shared CLI helpers)
import cardmeta_common as cm  # noqa: E402
import cardmeta_map as mp  # noqa: E402

log = logging.getLogger("cardmeta.footprint")
UNDOCUMENTED = "undocumented"
DAY = 86400
WINDOWS = {"day": 1, "week": 7, "month": 30}


def expand_path(path: str, project: str, repo: Path) -> str:
    """Declared path -> absolute path (pure string logic apart from `~` expansion): `<project>` substituted, relative = inside the repo."""
    p = os.path.expanduser(path.replace("<project>", project))
    return p if os.path.isabs(p) else str(repo / p)


def walk_files(path: str) -> list[tuple[str, int, float]]:
    """(path, size, mtime) of every regular file at/under a glob path; symlinks are not followed; unreadable entries are skipped."""
    out = []
    for hit in glob.glob(path):
        try:
            st = os.lstat(hit)
        except OSError:
            continue
        if os.path.isdir(hit) and not os.path.islink(hit):
            for root, _dirs, names in os.walk(hit, followlinks=False):
                for n in names:
                    fp = os.path.join(root, n)
                    try:
                        s = os.lstat(fp)
                    except OSError:
                        continue
                    if os.path.isfile(fp) and not os.path.islink(fp):
                        out.append((fp, s.st_size, s.st_mtime))
        elif os.path.isfile(hit) and not os.path.islink(hit):
            out.append((hit, st.st_size, st.st_mtime))
    return out


def volume_of(files: list[tuple[str, int, float]], now: float) -> dict:
    """Pure: files modified within each window -> {day|week|month: {rows: files, bytes}}."""
    return {k: {"rows": sum(1 for _p, _s, m in files if now - m <= n * DAY), "bytes": sum(s for _p, s, m in files if now - m <= n * DAY)}
            for k, n in WINDOWS.items()}


def merge_volume(vols: list[dict]) -> dict:
    out: dict = {}
    for v in vols:
        for k, w in v.items():
            o = out.setdefault(k, {"rows": 0, "bytes": 0})
            o["rows"] += w["rows"]
            o["bytes"] += w["bytes"]
    return out


def policy_of(decl: dict) -> dict:
    """Pure: declared policy -> {backup, retention}, 'undocumented' where missing or empty."""
    pol = decl.get("policy") if isinstance(decl.get("policy"), dict) else {}
    return {k: (str(pol.get(k) or "").strip() or UNDOCUMENTED) for k in ("backup", "retention")}


def module_record(decl: dict, project: str, repo: Path, now: float) -> dict:
    files, logs, vols = [], [], []
    for f in decl.get("files", []):
        real = expand_path(f["path"], project, repo)
        found = walk_files(real)
        rec = {"path": f["path"].replace("<project>", project), "kind": f.get("kind", "file"), "personal_data": bool(f.get("personal_data", False)),
               "written_by": f.get("written_by", ""), "exists": bool(found)}
        if f.get("kind") == "dir":
            rec["count"] = len(found)
        else:
            rec["size_bytes"] = sum(s for _p, s, _m in found)
        files.append(rec)
        if f.get("volume"):
            vols.append(volume_of(found, now))
    for lg in decl.get("logging", []):
        rec = {"logger": lg["logger"], "file": lg.get("file"), "rotation": lg.get("rotation", UNDOCUMENTED)}
        if lg.get("file"):
            found = walk_files(expand_path(lg["file"], project, repo) + "*")
            rec["size_bytes"], rec["files"] = sum(s for _p, s, _m in found), len(found)
        logs.append(rec)
    return {"tables": [], "files": files, "logging": logs, "volume": merge_volume(vols), "policy": policy_of(decl)}


def gaps_of(modules: dict, declared: dict) -> list[dict]:
    """Pure: modules without a declaration or with an undocumented backup / retention statement."""
    out = []
    for mid, rec in modules.items():
        if mid not in declared:
            out.append({"module": mid, "what": "no footprint declaration"})
            continue
        out += [{"module": mid, "what": f"{k} undocumented"} for k, v in rec["policy"].items() if v == UNDOCUMENTED]
    return out


def aggregate(declared: dict, mmap: dict, project: str, repo: Path, now: float) -> dict:
    """Declared footprints + map -> {modules, gaps}; a module of the map without a declaration gets an empty record and a gap."""
    mods = {m["id"]: module_record(declared.get(m["id"], {}), project, repo, now) for m in mmap["modules"]}
    return {"modules": mods, "gaps": gaps_of(mods, declared)}


def render_md(res: dict) -> str:
    o = ["# Persistence footprint", ""]
    for mid, r in res["modules"].items():
        o.append(f"## {mid}")
        o += [f"- file {f['path']} ({f['kind']}, {'exists' if f['exists'] else 'missing'}, {f.get('size_bytes', f.get('count'))})" for f in r["files"]]
        o += [f"- backup: {r['policy']['backup']}", f"- retention: {r['policy']['retention']}", ""]
    o += ["Gaps:"] + [f"- {g['module']}: {g['what']}" for g in res["gaps"]]
    return "\n".join(o) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    amb.add_common_args(ap)
    ap.add_argument("--declared", help="declaration JSON (default: <repo>/docs/footprint-declared.json)")
    ap.add_argument("--format", choices=["json", "md"], default="json", help="md prints a summary and writes nothing")
    a = ap.parse_args(argv)
    repo, map_path, project = amb.setup(a)
    out_dir = cm.resolve_out_dir(a.out_dir, repo, project)
    declared_path = Path(a.declared) if a.declared else repo / "docs" / "footprint-declared.json"
    declared = json.loads(declared_path.read_text(encoding="utf-8")).get("modules", {})
    res = aggregate(declared, mp.load_map(map_path), project, repo, time.time())
    for g in res["gaps"]:
        log.warning("footprint gap: %s: %s", g["module"], g["what"])
    if a.format == "md":
        sys.stdout.write(render_md(res))
        return 0
    cm.write_kind(out_dir, "footprint", {**cm.header("footprint", project), **res})
    return 0


if __name__ == "__main__":
    sys.exit(main())
