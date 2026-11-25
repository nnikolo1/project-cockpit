"""Projects (PC-140 slice 2, part A): add, list, get and remove a project of the registry. Every function returns a Result
`{ok, error, data}`; git is reached through an injected `run(args) -> str`, so tests need no clone."""
import logging
import pathlib
import re

from ckp.adapters import run as _run
from ckp.store import env as store_env
from ckp.store import layout, registry

log = logging.getLogger("ckp.projects")

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_KEYS = ("name", "repo", "repo_dir", "short", "first", "project_board", "modules", "env")


def _res(ok, data=None, error=None, **more):
    return {"ok": ok, "data": data, "error": error, **more}


def _normalise(record):
    r = {k: record[k] for k in _KEYS if record.get(k) is not None}
    r.setdefault("short", str(r.get("name", "")).upper())
    r.setdefault("first", 1)
    return r


def _problems(r, run):
    p = []
    if not _NAME.match(str(r.get("name", ""))):
        p.append(f"unusable project name {r.get('name')!r}")
    if not _REPO.match(str(r.get("repo", ""))):
        p.append(f"repo {r.get('repo')!r} is not OWNER/NAME")
    if not r.get("short") or not re.match(r"^[A-Za-z0-9]+$", str(r["short"])):
        p.append(f"unusable task prefix {r.get('short')!r}")
    if not isinstance(r.get("first"), int) or isinstance(r.get("first"), bool) or r["first"] < 1:
        p.append(f"first task number {r.get('first')!r} is not a positive integer")
    if p:
        return p
    try:
        if run(["git", "-C", str(r.get("repo_dir")), "rev-parse", "--is-inside-work-tree"]).strip() != "true":
            return [f"{r.get('repo_dir')} is not a git clone"]
        url = run(["git", "-C", str(r["repo_dir"]), "remote", "get-url", "origin"]).strip()
    except Exception as e:  # noqa: BLE001 - git failing means the folder is not usable as a clone
        return [f"{r.get('repo_dir')} is not a git clone with a remote 'origin' ({type(e).__name__})"]
    if not re.search(r"[:/]" + re.escape(r["repo"]) + r"(\.git)?/?$", url):
        p.append(f"the remote of {r['repo_dir']} is {url!r}, not {r['repo']}")
    return p


def add_project(record, *, dry_run=False, home=None, run=_run):
    """Register a project, like `git init`: validate, create its data folder, per-project queue folder and empty environment record.
    Idempotent for an identical record; a clash of name or short with another project is refused."""
    r = _normalise(record)
    problems = _problems(r, run)
    if problems:
        return _res(False, None, "; ".join(problems), dry_run=dry_run)
    records = registry.load(home)
    same = [x for x in records if x.get("name") == r["name"]]
    if same:
        if _normalise(same[0]) == r:
            return _res(True, {"project": r, "created": False}, dry_run=dry_run)
        return _res(False, None, f"project {r['name']!r} exists with a different record", dry_run=dry_run)
    clash = [x["name"] for x in records if str(x.get("short", "")).lower() == str(r["short"]).lower()]
    if clash:
        return _res(False, None, f"task prefix {r['short']!r} is already used by {clash[0]!r}", dry_run=dry_run)
    if not dry_run:
        layout.project_dir(r["name"], home).mkdir(parents=True, exist_ok=True)
        root = (pathlib.Path(home).expanduser() if home else layout.home()) / "task-queue"
        layout.queue(root, r["name"])["area"].mkdir(parents=True, exist_ok=True)
        if not store_env.load(r["name"], home):
            store_env.save(r["name"], {}, home)
        registry.save(records + [r], home)
        log.info("added project %s (%s, prefix %s)", r["name"], r["repo"], r["short"])
    return _res(True, {"project": r, "created": True}, dry_run=dry_run)


def list_projects(home=None):
    return _res(True, {"projects": registry.load(home)})


def get_project(name, home=None):
    for r in registry.load(home):
        if r.get("name") == name:
            return _res(True, {"project": r})
    return _res(False, None, f"no project {name!r}")


def remove_project(name, home=None):
    """Remove the registry entry only; the project's data is never touched."""
    records = registry.load(home)
    kept = [r for r in records if r.get("name") != name]
    if len(kept) == len(records):
        return _res(False, None, f"no project {name!r}")
    registry.save(kept, home)
    log.info("removed project %s from the registry (data kept)", name)
    return _res(True, {"removed": name})
