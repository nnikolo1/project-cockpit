"""The task functions of one project (PC-140 slice 2, part B; contract docs/contracts/registry-interfaces.md): list, show, file, comment.

Thin and project-scoped over dispatcher.tasks. `project` is the project record `{"repo": "owner/name", "short": ..., ...}`; every
GitHub call goes through the injected `run(args) -> stdout` (default: the real `gh`). `file_task` makes an embryo only; release, cancel and
priority are not exposed here (they stay the owner's). Every function returns a Result `{ok, error, data}`.
"""
import logging
import re

import logic as L
from ckp.adapters import github, run as _run
from dispatcher import tasks as T

log = logging.getLogger("ckp.tasks")

_FORBIDDEN = lambda name: L.is_protected(name) or name in L.STATES   # noqa: E731 - labels only the owner or the Dispatcher sets


def _result(ok, data=None, error=None):
    return {"ok": ok, "error": error, "data": data}


def _source(project, run):
    short = project.get("short") or project["repo"].split("/")[1]

    def no_git(args, cwd):
        raise RuntimeError("the task functions use no local git")
    return T.GitHubTasks(project["repo"], github.gh_of(run), no_git, project.get("repo_dir") or ".", short=short), short


def _view(t, short, with_text=False):
    d = dict(id=f"{short}-{t['number']}", number=t["number"], title=t["title"], stage=t["stage"], priority=t["prio"] or 2,
             deadline=t["deadline"], labels=t["labels"], url=t["url"], blocked_by=t["blockedBy"], filed_by=t["filed_by"])
    if with_text:
        d["text"] = t["text"]
    return d


def _derived(project, run):
    src, short = _source(project, run)
    issues, events = src._issues()
    tasks, _ = L.derive(issues, events, {}, src.manager, [], False)
    return src, short, tasks


def list_tasks(project, state=None, *, run=_run):
    """All tasks of the project (any stage), optionally only those in `state` (embryo, released, wip, unmerged, failed, merged, cancelled)."""
    try:
        if state is not None and state not in L.STAGES:
            return _result(False, error=f"unknown state {state!r}; one of {', '.join(L.STAGES)}")
        _, short, tasks = _derived(project, run)
        out = [_view(t, short) for t in tasks if state in (None, t["stage"])]
        log.info("%s: %d task(s) listed (state=%s)", short, len(out), state)
        return _result(True, {"project": short, "tasks": out})
    except Exception as e:  # noqa: BLE001 - a function returns its failure as a Result
        log.exception("list_tasks failed")
        return _result(False, error=f"{type(e).__name__}: {e}")


def get_task(project, task_id, *, run=_run):
    """One task with its text. `task_id` is `<short>-<number>`."""
    try:
        src, short, tasks = _derived(project, run)
        number = int(src._number(task_id))
        for t in tasks:
            if t["number"] == number:
                return _result(True, _view(t, short, with_text=True))
        return _result(False, error=f"no task {task_id} in {project['repo']}")
    except Exception as e:  # noqa: BLE001
        log.exception("get_task failed")
        return _result(False, error=f"{type(e).__name__}: {e}")


def file_task(project, title, body, labels=(), *, run=_run):
    """File an embryo. Refused: an empty title, and any label that releases, cancels, ranks, dates or sets a state."""
    try:
        if not title or not title.strip():
            return _result(False, error="a task needs a title")
        bad = [l for l in labels if _FORBIDDEN(l)]
        if bad:
            return _result(False, error=f"labels not allowed on a new task (the owner sets them): {', '.join(bad)}")
        _, short = _source(project, run)
        url = github.create_issue(project["repo"], title.strip(), body or "", list(labels), run=run)
        m = re.search(r"/(\d+)\s*$", url)
        number = int(m.group(1)) if m else None
        log.info("%s: task filed as an embryo: %s", short, url)
        return _result(True, {"id": f"{short}-{number}" if number else None, "number": number, "url": url})
    except Exception as e:  # noqa: BLE001
        log.exception("file_task failed")
        return _result(False, error=f"{type(e).__name__}: {e}")


def comment(project, task_id, text, *, run=_run):
    """Add a comment to the task's issue."""
    try:
        if not text or not text.strip():
            return _result(False, error="a comment needs text")
        src, short = _source(project, run)
        number = int(src._number(task_id))
        github.comment_issue(project["repo"], number, text, run=run)
        return _result(True, {"id": f"{short}-{number}", "number": number})
    except Exception as e:  # noqa: BLE001
        log.exception("comment failed")
        return _result(False, error=f"{type(e).__name__}: {e}")
