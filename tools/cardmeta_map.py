"""Module map logic (pure except `tracked_files` and `load_map`): which module a file belongs to.

Map file: {"modules": [{id, name, desc}], "assign": [{path, module | null, basis?, candidates?, alias?}]}.
- Longest matching path prefix wins; a path ending in '/' is a prefix, any other path is one exact file.
- `module: null` is an explicit "unassigned"; no match is "unassigned" too.
- Sub-modules use slash ids (`parent/child`); a file that cannot be placed in one sub-module is mapped to the parent.
- A test file follows its mirrored source (tests/tools/test_x.py -> tools/x.py, then tools/, then tests-less parents);
  when no mirror matches, an entry for the test path itself decides.
- `candidates` is decision data only: it never changes an assignment.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("cardmeta.map")
UNASSIGNED = "unassigned"


def load_map(path: Path) -> dict:
    return load_map_text(Path(path).read_text(encoding="utf-8"))


def load_map_text(text: str) -> dict:
    """Parse and validate a module map given as JSON text."""
    data = json.loads(text)
    ids = [m["id"] for m in data["modules"]]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate module ids in the module map")
    for i in ids:
        if "/" in i and i.rsplit("/", 1)[0] not in ids:
            raise ValueError(f"sub-module {i!r} has no declared parent module")
    for a in data["assign"]:
        if a["module"] is not None and a["module"] not in ids:
            raise ValueError(f"assign {a['path']}: unknown module {a['module']!r}")
        for c in a.get("candidates", []):
            if c not in ids:
                raise ValueError(f"assign {a['path']}: unknown candidate module {c!r}")
    return data


def top_of(mid: str) -> str:
    return mid.split("/", 1)[0]


def parent_of(mid: str) -> str | None:
    return mid.rsplit("/", 1)[0] if "/" in mid else None


def in_module(file_module: str, mid: str) -> bool:
    """True when a file whose own module is `file_module` counts for `mid` (itself or a sub-module of it)."""
    return file_module == mid or file_module.startswith(mid + "/")


def is_test_path(path: str) -> bool:
    parts = path.split("/")
    return parts[0] == "tests" or parts[-1].startswith("test_")


def test_mirror_candidates(path: str) -> list[str]:
    """Source paths a file under tests/ mirrors, most specific first (tests/a/test_c.py -> a/c.py, a/)."""
    parts = path.split("/")
    if parts[0] != "tests" or len(parts) < 2:
        return []
    rest = parts[1:]
    fname = rest[-1]
    stem = fname[5:] if fname.startswith("test_") else fname
    out = []
    if fname.startswith("test_") and stem.endswith(".py"):
        out.append("/".join(rest[:-1] + [stem]))
    d = rest[:-1]
    while d:
        out.append("/".join(d) + "/")
        d = d[:-1]
    return out


def _best(path: str, assign: list) -> dict | None:
    best = None
    for a in assign:
        p = a["path"]
        hit = path.startswith(p) if p.endswith("/") else path == p
        if hit and (best is None or len(p) > len(best["path"])):
            best = a
    return best


def entry_of(path: str, mmap: dict) -> dict | None:
    """The map entry that decides `path` (mirror candidates first for tests, then the path itself)."""
    for c in test_mirror_candidates(path) + [path]:
        e = _best(c, mmap["assign"])
        if e:
            return e
    return None


def module_of(path: str, mmap: dict) -> str:
    e = entry_of(path, mmap)
    return (e["module"] or UNASSIGNED) if e else UNASSIGNED


def basis_of(path: str, mmap: dict) -> str | None:
    e = entry_of(path, mmap)
    return e.get("basis") if e else None


def candidates_of(path: str, mmap: dict) -> list[str] | None:
    e = entry_of(path, mmap)
    return list(e["candidates"]) if e and "candidates" in e else None


def tracked_files(repo: Path) -> list[str]:
    """Tracked files of the repository (read-only `git ls-files`)."""
    r = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", "ls-files"], capture_output=True, text=True, check=True)
    return [ln for ln in r.stdout.splitlines() if ln]


def check_map(tracked: list[str], mmap: dict) -> dict[str, list[str]]:
    """Consistency findings (pure). `stale_paths`: assign path matching no tracked file and not an alias;
    `unassigned`: tracked files with no module (reported, not an error: explicit null entries are decisions)."""
    tracked_set = set(tracked)
    stale = []
    for a in mmap["assign"]:
        p = a["path"]
        if a.get("alias"):
            continue
        ok = any(t.startswith(p) for t in tracked) if p.endswith("/") else p in tracked_set
        if not ok:
            stale.append(p)
    return {"stale_paths": stale, "unassigned": sorted(f for f in tracked if module_of(f, mmap) == UNASSIGNED)}
