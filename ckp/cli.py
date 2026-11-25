"""The command line, `cockpit` and `ckp` (PC-140): thin; every command calls a function of the Cockpit and prints its Result as JSON.

    ckp env set --project P --env production --port 8800 --label com.example.job --data-dir DIR
    ckp deploy --project P --repo OWNER/NAME --repo-dir DIR [--env production] [--tag vYYYY.MM.DD-N] [--apply]

`deploy` is a dry run unless `--apply` is given. Exit status: 0 ok, 1 the result is not ok, 2 usage.
"""
import argparse
import datetime as dt
import json
import logging
import sys

from ckp.adapters import git as git_adapter
from ckp.adapters.deploy import GitDeployAdapter
from ckp.core import deploy as core_deploy
from ckp.core import projects as core_projects
from ckp.store import env as store_env

log = logging.getLogger("ckp.cli")


def _out(result):
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if result.get("ok") else 1


def cmd_env_set(a):
    envs = store_env.load(a.project)
    envs[a.env] = {"port": a.port, "data_dir": a.data_dir, "label": a.label}
    problems = store_env.validate({a.project: envs})
    if problems:
        return _out({"ok": False, "error": "; ".join(problems), "data": None})
    store_env.save(a.project, envs)
    return _out({"ok": True, "data": {"project": a.project, "environments": envs}, "error": None})


def cmd_deploy(a, adapter=None, today=None):
    envs = store_env.load(a.project)
    if a.env not in envs:
        return _out({"ok": False, "error": f"no environment {a.env!r} for {a.project}: create it with `ckp env set`", "data": None})
    ad = adapter or GitDeployAdapter(a.project, a.repo, a.repo_dir, a.env)
    tag = a.tag or git_adapter.next_tag(a.repo_dir, today or dt.date.today())
    if a.apply and not a.tag:
        pre = core_deploy.deploy(a.project, tag, envs[a.env], ad, dry_run=True)   # readiness first: a refused deploy leaves no tag behind
        if not pre["ok"]:
            return _out(pre)
        git_adapter.tag(a.repo_dir, tag, "HEAD")
        try:
            git_adapter.push_tag(a.repo_dir, tag)
        except Exception as e:  # noqa: BLE001 - a missing remote must not stop a local deploy; the record will say so
            log.warning("tag %s not pushed: %s", tag, e)
    try:
        return _out(core_deploy.deploy(a.project, tag, envs[a.env], ad, dry_run=not a.apply))
    except Exception as e:  # noqa: BLE001 - a command prints its failure as a Result, never a traceback
        log.exception("deploy failed")
        return _out({"ok": False, "error": f"{type(e).__name__}: {e}", "data": {"project": a.project, "tag": tag}, "dry_run": not a.apply})


# --- project registry (PC-140 slice 2, part A) ---
def cmd_project(a, run=None):
    if a.sub == "add":
        rec = {"name": a.name, "repo": a.repo, "repo_dir": a.repo_dir, "short": a.short, "first": a.first}
        return _out(core_projects.add_project(rec, dry_run=a.dry_run, **({"run": run} if run else {})))
    if a.sub == "list":
        return _out(core_projects.list_projects())
    if a.sub == "show":
        return _out(core_projects.get_project(a.name))
    return _out(core_projects.remove_project(a.name))


def _project_parser(sub):
    ps = sub.add_parser("project", help="the project registry").add_subparsers(dest="sub", required=True)
    add = ps.add_parser("add", help="register a project (like git init; --dry-run changes nothing)")
    add.add_argument("--name", required=True)
    add.add_argument("--repo", required=True, help="OWNER/NAME on the hosting service")
    add.add_argument("--repo-dir", required=True)
    add.add_argument("--short", help="task-id prefix (default: the name in capitals)")
    add.add_argument("--first", type=int, help="first task number (default 1)")
    add.add_argument("--dry-run", action="store_true")
    ps.add_parser("list")
    ps.add_parser("show").add_argument("name")
    ps.add_parser("remove", help="remove the registry entry only; data is kept").add_argument("name")
    for p in ps.choices.values():
        p.set_defaults(func=cmd_project)


def build_parser():
    p = argparse.ArgumentParser(prog="ckp", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("env", help="environment records").add_subparsers(dest="sub", required=True).add_parser("set")
    e.add_argument("--project", required=True)
    e.add_argument("--env", required=True)
    e.add_argument("--port", type=int, required=True)
    e.add_argument("--label", required=True)
    e.add_argument("--data-dir", required=True)
    e.set_defaults(func=cmd_env_set)
    d = sub.add_parser("deploy", help="deploy a release (a dry run unless --apply)")
    d.add_argument("--project", required=True)
    d.add_argument("--repo", required=True, help="OWNER/NAME on the hosting service")
    d.add_argument("--repo-dir", required=True)
    d.add_argument("--env", default="production")
    d.add_argument("--tag")
    d.add_argument("--apply", action="store_true")
    d.set_defaults(func=cmd_deploy)
    _project_parser(sub)
    return p


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s", stream=sys.stderr)
    try:
        a = build_parser().parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return a.func(a)
