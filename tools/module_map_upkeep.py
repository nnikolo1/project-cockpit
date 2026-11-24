#!/usr/bin/env python3
"""Module-map upkeep: apply incremental changes to the module map, flag breaking ones for a complete rebuild.

Reads the git changes since the map was last committed (or since --since) and sorts each one:
- incremental: a file with its own (exact) entry is renamed or deleted -> the entry follows or goes; a folder entry
  that no longer covers any file goes; a new or moved file that an existing entry covers needs no edit.
- breaking: a module appears (a new file in a new top-level folder) or disappears (a module had files and has none now).
  Nothing is edited: a complete rebuild of the map is needed, proposed as a task.
- ambiguous: a new file no entry covers that is not in a new top-level folder (e.g. a new root file). Reported, never guessed.

Default: report only (JSON on stdout). --write applies the incremental edits to the map file in the working tree (never
when something is breaking); it does not commit. --file-task files the rebuild task with gh as an unreleased embryo with
the label author:job (spec 2b), unless an open issue with the same title exists.
Exit: 0 unchanged or incremental, 2 breaking (rebuild needed), 1 error.

Usage: module_map_upkeep.py [--repo-dir DIR] [--map docs/cockpit-modules.json] [--since REF] [--write]
                            [--file-task --repo OWNER/NAME]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cardmeta_map as mp  # noqa: E402

log = logging.getLogger("module_map_upkeep")
TIMEOUT = 60
TASK_TITLE = "Rebuild the module map: modules appeared or disappeared"
TASK_LABELS = ("author:job", "module:internal/metadata")


def parse_name_status(text: str) -> list[tuple]:
    """`git diff --name-status -M` lines -> [("A"|"D"|"M"|"R", path) or ("R", old, new)]; copies count as additions."""
    out = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        f = ln.split("\t")
        k = f[0][0]
        if k == "R":
            out.append(("R", f[1], f[2]))
        elif k == "C":
            out.append(("A", f[2]))
        else:
            out.append((k, f[1]))
    return out


def _covers(entry_path: str, files) -> bool:
    return any(f.startswith(entry_path) for f in files) if entry_path.endswith("/") else entry_path in files


def _has_files(mid: str, files, mmap: dict) -> bool:
    return any(mp.in_module(mp.module_of(f, mmap), mid) for f in files)


def classify(changes: list[tuple], old_files: list[str], new_files: list[str], mmap: dict) -> dict:
    """Pure: the report for `changes` between two trees under `mmap` (the map as of the old tree)."""
    old_set, new_set = set(old_files), set(new_files)
    exact = {a["path"]: a for a in mmap["assign"] if not a["path"].endswith("/") and not a.get("alias")}
    old_tops = {f.split("/", 1)[0] for f in old_files if "/" in f}
    edits, breaking, ambiguous, covered = [], [], [], []
    added = []
    for c in changes:
        if c[0] == "R":
            old, new = c[1], c[2]
            if old in exact:
                edits.append({"op": "rename", "path": old, "to": new, "module": exact[old]["module"]})
            else:
                added.append(new)
        elif c[0] == "A":
            added.append(c[1])
        elif c[0] == "D" and c[1] in exact and c[1] not in new_set:
            edits.append({"op": "remove", "path": c[1], "module": exact[c[1]]["module"]})
    for a in mmap["assign"]:
        p = a["path"]
        if p.endswith("/") and not a.get("alias") and _covers(p, old_set) and not _covers(p, new_set):
            edits.append({"op": "remove", "path": p, "module": a["module"]})
    new_map = apply_edits(mmap, edits)
    for f in sorted(set(added)):
        if f not in new_set:
            continue
        e = mp.entry_of(f, new_map)
        top = f.split("/", 1)[0]
        if e is not None:
            covered.append({"path": f, "module": e["module"]})
        elif "/" in f and top not in old_tops:
            breaking.append({"kind": "module appears", "path": f, "folder": top + "/"})
        else:
            ambiguous.append({"path": f, "reason": "no map entry covers this new file and it is not in a new top-level folder"})
    for m in mmap["modules"]:
        if _has_files(m["id"], old_files, mmap) and not _has_files(m["id"], new_files, new_map):
            breaking.append({"kind": "module disappears", "module": m["id"]})
    status = "breaking" if breaking else ("incremental" if edits or covered else "unchanged")
    return {"status": status, "edits": edits, "covered": covered, "breaking": breaking, "ambiguous": ambiguous}


def apply_edits(mmap: dict, edits: list[dict]) -> dict:
    """Pure: a copy of `mmap` with the rename and remove edits applied (order of entries kept)."""
    ren = {e["path"]: e["to"] for e in edits if e["op"] == "rename"}
    rem = {e["path"] for e in edits if e["op"] == "remove"}
    out = dict(mmap)
    out["assign"] = [dict(a, path=ren.get(a["path"], a["path"])) for a in mmap["assign"] if a["path"] not in rem]
    return out


def dump_map(mmap: dict) -> str:
    """The map in the committed layout: one key per line, one list element per line."""
    parts = []
    for k, v in mmap.items():
        if isinstance(v, list):
            body = ",\n".join("  " + json.dumps(x, ensure_ascii=False) for x in v)
            parts.append(f" {json.dumps(k)}: [\n{body}\n ]")
        else:
            parts.append(f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def task_body(report: dict, since: str, head: str) -> str:
    lines = []
    for b in report["breaking"]:
        lines.append(f"- {b['kind']}: `{b.get('folder') or b.get('module')}`" + (f" (e.g. `{b['path']}`)" if "path" in b else ""))
    amb = "".join(f"\n- `{a['path']}`" for a in report["ambiguous"])
    return ("The module-map upkeep job found breaking changes and edited nothing; the module map needs a complete rebuild "
            f"(changes {since[:12]}..{head[:12]}).\n\n**Breaking:**\n" + "\n".join(lines)
            + (f"\n\n**Ambiguous files (not placed):**{amb}" if amb else "")
            + "\n\n<details><summary>Origin</summary>\n\nFiled by the scheduled module-map upkeep job "
              "(tools/module_map_upkeep.py) as an unreleased embryo; the owner releases it.\n\n</details>\n")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", *args],
                          capture_output=True, text=True, check=True, timeout=TIMEOUT).stdout


def gh_run(args: list[str]) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=TIMEOUT, check=True).stdout.strip()


def file_task(repo: str, report: dict, since: str, head: str, run=gh_run) -> str | None:
    """File the rebuild task unless an open issue has the same title; -> its URL, or None when one is already open."""
    found = json.loads(run(["issue", "list", "-R", repo, "--state", "open", "--search", f'"{TASK_TITLE}" in:title',
                            "--json", "title,url"]) or "[]")
    if any(i["title"] == TASK_TITLE for i in found):
        log.info("rebuild task already open: %s", found[0]["url"])
        return None
    args = ["issue", "create", "-R", repo, "--title", TASK_TITLE, "--body", task_body(report, since, head)]
    for lab in TASK_LABELS:
        args += ["--label", lab]
    return run(args)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-dir", default=".")
    ap.add_argument("--map", default="docs/cockpit-modules.json", help="path of the module map inside the repository")
    ap.add_argument("--since", help="git ref to compare with (default: the last commit that changed the map)")
    ap.add_argument("--write", action="store_true", help="apply incremental edits to the map file (no commit)")
    ap.add_argument("--file-task", action="store_true", help="file the rebuild task when something is breaking")
    ap.add_argument("--repo", help="OWNER/NAME for --file-task")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    if a.file_task and not a.repo:
        ap.error("--file-task needs --repo")
    repo = Path(a.repo_dir)
    try:
        since = a.since or git(repo, "log", "-1", "--format=%H", "--", a.map).strip()
        if not since:
            log.error("the map %s has no commit to compare with", a.map)
            return 1
        since = git(repo, "rev-parse", since).strip()
        head = git(repo, "rev-parse", "HEAD").strip()
        mmap = mp.load_map_text(git(repo, "show", f"{since}:{a.map}"))
        changes = parse_name_status(git(repo, "diff", "--name-status", "-M", since, head))
        old_files = git(repo, "ls-tree", "-r", "--name-only", since).splitlines()
        new_files = git(repo, "ls-tree", "-r", "--name-only", head).splitlines()
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as e:
        log.error("cannot read the repository or the map: %s", e)
        return 1
    report = {"since": since, "head": head, **classify(changes, old_files, new_files, mmap)}
    for x in report["ambiguous"]:
        log.warning("ambiguous, not placed: %s", x["path"])
    if report["status"] == "breaking":
        log.warning("breaking changes: the module map needs a complete rebuild; nothing edited")
        if a.file_task:
            try:
                report["task"] = file_task(a.repo, report, since, head)
            except (OSError, subprocess.SubprocessError, ValueError) as e:
                log.error("could not file the rebuild task: %s", e)
                return 1
    elif a.write and report["edits"]:
        path = repo / a.map
        current = json.loads(path.read_text(encoding="utf-8"))
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(dump_map(apply_edits(current, report["edits"])), encoding="utf-8")
        os.replace(tmp, path)
        log.info("applied %d edits to %s (not committed)", len(report["edits"]), path)
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 2 if report["status"] == "breaking" else 0


if __name__ == "__main__":
    sys.exit(main())
