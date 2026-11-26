"""The Environments view and its deploy button (PC-142): what the page shows of a project's environments, and the start of `ckp deploy` in
the background. A thin client of the deploy command: the deploy itself, its checks and its log are `ckp deploy`'s; this module only reads
the records and starts the command detached (a deploy of the Cockpit restarts the viewer that started it, so the run must outlive it).

The run of a project: `<home>/deploy-run/<project>.json` `{pid, started, env, mode}`, with the command's stdout (its JSON Result) in
`<project>.out` and its log in `<project>.err`. It is running while its process lives; afterwards its Result is read from the output.
"""
import datetime as dt
import json
import logging
import os
import pathlib
import subprocess
import sys

from ckp.store import env as store_env, io, layout, releases

log = logging.getLogger("ckp.environments")

MODES = {"check": [], "apply": ["--apply"], "rollback": ["--rollback", "--apply"]}
LOG_ROWS = 20
CODE_DIR = pathlib.Path(__file__).resolve().parents[2]   # the Cockpit code this module belongs to: its bin/ckp is the one started


def _home(home):
    return pathlib.Path(home).expanduser() if home else layout.home()


def _run_files(project, home):
    d = _home(home) / "deploy-run"
    return d / f"{project}.json", d / f"{project}.out", d / f"{project}.err"


def _alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def run_state(project, home=None, alive=_alive):
    """The last deploy started from the page: None, or `{state: running|ended, started, env, mode, ok, error, steps}`."""
    status, out, _err = _run_files(project, home)
    rec = io.read_json(status)
    if not isinstance(rec, dict):
        return None
    if alive(rec.get("pid")):
        return dict(rec, state="running")
    try:
        res = json.loads(io.read_text(out) or "")
    except ValueError:
        res = None
    if not isinstance(res, dict):
        return dict(rec, state="ended", ok=False, error="the deploy ended without a result (see " + str(_run_files(project, home)[2]) + ")")
    data = res.get("data") or {}
    return dict(rec, state="ended", ok=bool(res.get("ok")), error=res.get("error"), tag=data.get("tag"), steps=data.get("steps") or [],
                dry_run=bool(res.get("dry_run")))


def view(project, home=None, alive=_alive):
    """Everything the Environments view shows of one project: the environments, the live and the previous release, the installed
    releases, the deploy log of the project (newest first) and the last run started from the page."""
    try:
        envs = store_env.load(project, home)
        live, prev, installed = releases.current(project, home), releases.previous(project, home), releases.list_releases(project, home)
    except ValueError as e:
        return {"ok": False, "data": None, "error": str(e)}
    rows = [r for r in io.read_jsonl(layout.deploy_log(home)) if r.get("project") == project]
    return {"ok": True, "error": None, "data": {
        "project": project, "environments": [dict(name=k, **{f: v.get(f) for f in ("port", "label", "data_dir")}) for k, v in sorted(envs.items())],
        "live": live, "previous": prev, "releases": installed, "log": rows[::-1][:LOG_ROWS], "run": run_state(project, home, alive)}}


def start_deploy(project, repo, repo_dir, env, mode, home=None, *, spawn=subprocess.Popen, alive=_alive, python=sys.executable, now=None):
    """Start `ckp deploy` for the page, detached. Refused (ok False) for an unknown mode or environment, a missing clone, or while a run
    started from the page is still going."""
    if mode not in MODES:
        return {"ok": False, "data": None, "error": f"unknown deploy mode {mode!r}: one of {', '.join(MODES)}"}
    try:
        envs = store_env.load(project, home)
    except ValueError as e:
        return {"ok": False, "data": None, "error": str(e)}
    if env not in envs:
        return {"ok": False, "data": None, "error": f"no environment {env!r} for {project}: create it with `ckp env set`"}
    if not repo or not repo_dir:
        return {"ok": False, "data": None, "error": f"{project} has no repository and clone (`repo`, `git`) to deploy from"}
    cur = run_state(project, home, alive)
    if cur and cur["state"] == "running":
        return {"ok": False, "data": None, "error": f"a deploy of {project} is already running (started {cur.get('started')})"}
    status, out, err = _run_files(project, home)
    status.parent.mkdir(parents=True, exist_ok=True)
    args = [python, str(CODE_DIR / "bin" / "ckp"), "deploy", "--project", project, "--repo", repo, "--repo-dir", str(repo_dir), "--env", env,
            *MODES[mode]]
    environ = dict(os.environ, **({"CP_HOME": str(_home(home))} if home else {}))
    with open(out, "w") as fo, open(err, "w") as fe:
        proc = spawn(args, stdout=fo, stderr=fe, stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(CODE_DIR), env=environ)
    started = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    io.atomic_write_json(status, {"pid": proc.pid, "started": started, "env": env, "mode": mode})
    log.info("deploy of %s to %s started from the page (%s, pid %s)", project, env, mode, proc.pid)
    what = {"check": "readiness check", "apply": "deploy", "rollback": "rollback"}[mode]
    return {"ok": True, "data": {"pid": proc.pid, "started": started}, "error": None, "msg": f"{what} of {project} to {env} started"}
