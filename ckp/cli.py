"""The command line, `cockpit` and `ckp` (PC-140): thin; every command calls a function of the Cockpit and prints its Result as JSON.

    ckp env set --project P --env production --port 8800 --label com.example.job --data-dir DIR
    ckp deploy --project P --repo OWNER/NAME --repo-dir DIR [--env production] [--tag vYYYY.MM.DD-N | --rollback] [--apply]

    ckp task list|show|file|comment --project P [--repo OWNER/NAME --short S] ...   (file makes embryos only)

`deploy` is a dry run unless `--apply` is given; a rollback is a deploy of an installed (older) tag, `--rollback` names the previous one;
every applied deploy writes a line to the deploy log (<home>/deploy-log.jsonl). Exit status: 0 ok, 1 the result is not ok, 2 usage.
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
from ckp.core import tasks as core_tasks
from ckp.store import env as store_env, io as store_io, layout, releases

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
    if a.rollback:
        if a.tag:
            return _out({"ok": False, "error": "--rollback and --tag exclude one another: --rollback deploys the previous release", "data": None})
        a.tag = releases.previous(a.project)
        if not a.tag:
            return _out({"ok": False, "error": f"no previous release of {a.project} to roll back to", "data": None})
    ad = adapter or GitDeployAdapter(a.project, a.repo, a.repo_dir, a.env)
    tag = a.tag or git_adapter.next_tag(a.repo_dir, today or dt.date.today())
    try:
        if a.apply and not a.tag:
            pre = core_deploy.deploy(a.project, tag, envs[a.env], ad, dry_run=True)   # readiness first: a refused deploy leaves no tag behind
            if not pre["ok"]:
                return _out(_logged(a, pre))
            git_adapter.tag(a.repo_dir, tag, "HEAD")
            try:
                git_adapter.push_tag(a.repo_dir, tag)
            except Exception as e:  # noqa: BLE001 - a missing remote must not stop a local deploy; the record will say so
                log.warning("tag %s not pushed: %s", tag, e)
        res = core_deploy.deploy(a.project, tag, envs[a.env], ad, dry_run=not a.apply)
    except Exception as e:  # noqa: BLE001 - a command prints its failure as a Result, never a traceback
        log.exception("deploy failed")
        res = {"ok": False, "error": f"{type(e).__name__}: {e}", "data": {"project": a.project, "tag": tag}, "dry_run": not a.apply}
    return _out(_logged(a, res) if a.apply else res)


def _logged(a, res):
    """One line per applied deploy, rollback or refusal in the deploy log (<home>/deploy-log.jsonl, outside every repository)."""
    data = res.get("data") or {}
    rec = {"time": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "project": a.project, "env": a.env,
           "tag": data.get("tag", a.tag), "rollback": bool(a.rollback), "ok": bool(res.get("ok")), "error": res.get("error")}
    try:
        store_io.append_jsonl(layout.deploy_log(), rec)
    except OSError as e:
        log.warning("deploy log not written: %s", e)
    return res


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
# --- ckp task (PC-140 slice 2, part B) ---
def _task_project(a):
    """The project record of the task commands: from the registry when it has `a.project`, else from --repo/--short."""
    try:
        from ckp.store import registry
        got = registry.get_project(a.project)
        rec = got.get("data") if isinstance(got, dict) and "ok" in got else got
        if rec:
            return rec
    except (ImportError, AttributeError):
        pass
    if not a.repo:
        return None
    return {"name": a.project, "repo": a.repo, "short": a.short or a.repo.split("/")[1]}


def _task_cmd(a, fn, *args, run=None):
    proj = _task_project(a)
    if proj is None:
        return _out({"ok": False, "error": f"unknown project {a.project!r}: register it or give --repo OWNER/NAME", "data": None})
    return _out(fn(proj, *args, **({"run": run} if run else {})))


def cmd_task_list(a, run=None):
    return _task_cmd(a, core_tasks.list_tasks, a.state, run=run)


def cmd_task_show(a, run=None):
    return _task_cmd(a, core_tasks.get_task, a.id, run=run)


def cmd_task_file(a, run=None):
    return _task_cmd(a, core_tasks.file_task, a.title, a.body, a.label, run=run)


def cmd_task_comment(a, run=None):
    return _task_cmd(a, core_tasks.comment, a.id, a.text, run=run)


def _add_task_parsers(sub):
    t = sub.add_parser("task", help="the tasks of a project: list, show, file (an embryo), comment").add_subparsers(dest="sub", required=True)

    def common(p):
        p.add_argument("--project", required=True)
        p.add_argument("--repo", help="OWNER/NAME, when the project is not in the registry")
        p.add_argument("--short", help="task-id prefix (default: the repository name)")
    p = t.add_parser("list")
    common(p)
    p.add_argument("--state")
    p.set_defaults(func=cmd_task_list)
    p = t.add_parser("show")
    common(p)
    p.add_argument("id")
    p.set_defaults(func=cmd_task_show)
    p = t.add_parser("file")
    common(p)
    p.add_argument("--title", required=True)
    p.add_argument("--body", default="")
    p.add_argument("--label", action="append", default=[])
    p.set_defaults(func=cmd_task_file)
    p = t.add_parser("comment")
    common(p)
    p.add_argument("id")
    p.add_argument("--text", required=True)
    p.set_defaults(func=cmd_task_comment)


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
    d.add_argument("--rollback", action="store_true", help="deploy the previous release again (no tag, no gate: it is installed)")
    d.set_defaults(func=cmd_deploy)
    _project_parser(sub)
    _add_task_parsers(sub)
    return p


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s", stream=sys.stderr)
    try:
        a = build_parser().parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return a.func(a)
