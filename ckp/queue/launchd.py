"""The LaunchAgent property lists: the Dispatcher (3c) and the quota collector; with `--projects-file` also the viewer and the watch of the
heartbeats and backups (PC-142), so every service definition of an environment comes from here. The proxy is started by the Dispatcher (4d).

Pure rendering (plistlib); the command line only writes the files and prints the install commands, it installs nothing.

    python3 -m dispatcher.launchd render --project NAME --repo OWNER/NAME --repo-dir DIR --token-file F [--out DIR]
    python3 -m dispatcher.launchd render --queue --token-file F [--data-root DIR] [--out DIR]    # one job for all projects the settings list
    ... [--env NAME] [--cockpit-dir DIR | --release NAME] [--projects-file F --viewer-token-file F [--viewer-port N]]

The environment (PC-84) is a setting, not a copy of the code: `--env` (default production) names the jobs' labels and the Workers'
container, and sets CP_ENV for the jobs; production keeps the unprefixed labels and the container `cockpit-workers`, every other
environment gets `com.project-cockpit.<env>.*` and `cockpit-workers-<env>`. Production must start from a release folder
(`<home>/releases/<project>/current`, given as `--cockpit-dir` and kept as a link, never resolved), never from a git working clone:
render refuses that.
"""
import argparse
import os
import pathlib
import plistlib
import shutil
import sys

from dispatcher import layout
from ckp.queue import runtime as rt

COCKPIT_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
DATA_DIR, QUEUE_ROOT = layout.DATA_DIR, layout.QUEUE_ROOT   # the places are defined in dispatcher/layout.py
QUEUE = "queue"     # the name of the one job that serves all projects
SOCKET_LIMIT = 104  # bytes, 4f
INTERVAL = 120      # seconds, 3c
QUOTA_INTERVAL = 30  # seconds, the quota collector
PREFIX = "com.project-cockpit"
PRODUCTION = "production"
CONTAINER = "cockpit-workers"   # the production container; dispatcher.core's default


def label(kind, project, env=PRODUCTION):
    """The job label: com.project-cockpit.<kind>.<project>, with the environment after the prefix unless it is production."""
    return f"{PREFIX}.{kind}.{project}" if env == PRODUCTION else f"{PREFIX}.{env}.{kind}.{project}"


def in_working_clone(path):
    """True when `path` or a folder above it holds a .git entry: a development checkout, not a release folder."""
    p = pathlib.Path(os.path.abspath(pathlib.Path(path).expanduser()))
    return any((d / ".git").exists() for d in (p, *p.parents))


def paths(project, data_dir=DATA_DIR):
    """The per-project files (see layout.per_project)."""
    return layout.per_project(data_dir, project)


def queue_paths(root=QUEUE_ROOT):
    """The files of the one queue, directly under the shared root (see layout.queue)."""
    return layout.queue(root)


def check_socket(sock):
    if len(str(sock).encode()) >= SOCKET_LIMIT:
        raise ValueError(f"socket path {sock} is {len(str(sock).encode())} bytes; a Unix socket path must be shorter than {SOCKET_LIMIT}")
    return str(sock)


# launchd starts jobs with a bare PATH (/usr/bin:/bin:...): python3, gh, git and the container tool live elsewhere.
JOB_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def _env(cockpit_dir, env=PRODUCTION):
    return {"PYTHONPATH": str(cockpit_dir), "PATH": JOB_PATH, "CP_ENV": env}


def dispatcher_plist(project, repo, repo_dir, token_file, image=None, data_dir=DATA_DIR, cockpit_dir=COCKPIT_DIR, python="python3", queue_root=None,
                     env=PRODUCTION):
    """3c: the Dispatcher job, started every 2 minutes with the arguments of dispatcher.core.main().
    With `queue_root` it is the job of the one queue (all projects the settings list): the arguments are --data-root and the token file; project, repo and repo_dir are not used."""
    if queue_root is not None:
        p = queue_paths(queue_root)
        project = QUEUE
        args = [python, "-m", "dispatcher.core", "--data-root", p["dir"], "--token-file", token_file]
        check_socket(p["socket"])
    else:
        p = paths(project, data_dir)
        args = [python, "-m", "dispatcher.core", "--settings", p["settings"], "--state", p["state"], "--area", p["area"],
                "--socket", check_socket(p["socket"]), "--token-file", token_file, "--usage-file", p["usage"],
                "--repo", repo, "--repo-dir", repo_dir, "--data-dir", p["processing"]]
    if image:
        args += ["--image", image]
    if env != PRODUCTION:
        args += ["--container-name", f"{CONTAINER}-{env}"]   # never the production container
    return dict(Label=label("dispatcher", project, env), ProgramArguments=[str(a) for a in args], StartInterval=INTERVAL,
                AbandonProcessGroup=True,  # the Workers and the proxy outlive the firing that started them
                WorkingDirectory=str(cockpit_dir), EnvironmentVariables=_env(cockpit_dir, env),
                StandardOutPath=str(p["dir"] / "dispatcher.log"), StandardErrorPath=str(p["dir"] / "dispatcher.log"))


def quota_plist(project, data_dir=DATA_DIR, cockpit_dir=COCKPIT_DIR, python="python3", queue_root=None, env=PRODUCTION):
    """The quota collector, one reading every 30 seconds into <data dir>/<project>/quota-history.jsonl (with `queue_root`: <root>/quota-history.jsonl)."""
    if queue_root is not None:
        p, project = queue_paths(queue_root), QUEUE
    else:
        p = paths(project, data_dir)
    args = [python, "-m", "dispatcher.quota", "--history", p["quota"]]
    return dict(Label=label("quota", project, env), ProgramArguments=[str(a) for a in args], StartInterval=QUOTA_INTERVAL,
                WorkingDirectory=str(cockpit_dir), EnvironmentVariables=_env(cockpit_dir, env),
                StandardOutPath=str(p["dir"] / "quota.log"), StandardErrorPath=str(p["dir"] / "quota.log"))


VIEWER_PORT = 8795   # cockpit.py's default
WATCH_INTERVAL = 3600  # seconds: the watch of heartbeats and backups (ckp watch)


def viewer_label(env=PRODUCTION):
    return f"{PREFIX}.viewer" if env == PRODUCTION else f"{PREFIX}.{env}.viewer"


def viewer_plist(projects_file, token_file, log_file, cockpit_dir=COCKPIT_DIR, python="python3", env=PRODUCTION, port=VIEWER_PORT):
    """The viewer service (cockpit.py), kept alive by launchd; from `cockpit_dir` (production: the release link)."""
    args = [python, str(pathlib.Path(cockpit_dir) / "cockpit.py"), "--projects", str(projects_file), "--token-file", str(token_file)]
    if port != VIEWER_PORT:
        args += ["--port", str(port)]
    return dict(Label=viewer_label(env), ProgramArguments=[str(a) for a in args], WorkingDirectory=str(cockpit_dir), RunAtLoad=True, KeepAlive=True,
                EnvironmentVariables=_env(cockpit_dir, env), StandardOutPath=str(log_file), StandardErrorPath=str(log_file))


def watch_plist(log_file, cockpit_dir=COCKPIT_DIR, python="python3", env=PRODUCTION):
    """The hourly watch: a desktop notification for a missing heartbeat or a failed or stale backup (`python -m ckp watch`)."""
    return dict(Label=f"{PREFIX}.watch" if env == PRODUCTION else f"{PREFIX}.{env}.watch",
                ProgramArguments=[str(python), "-m", "ckp", "watch"], StartInterval=WATCH_INTERVAL, WorkingDirectory=str(cockpit_dir),
                EnvironmentVariables=_env(cockpit_dir, env), StandardOutPath=str(log_file), StandardErrorPath=str(log_file))


def render(argv=None):
    ap = argparse.ArgumentParser(prog="dispatcher.launchd render", description="write the LaunchAgent files; installs nothing")
    ap.add_argument("--queue", action="store_true", help="render the one job for all projects the settings list (no --project, --repo, --repo-dir)")
    ap.add_argument("--data-root", default=str(QUEUE_ROOT), help="with --queue: the shared processing-data root")
    ap.add_argument("--project")
    ap.add_argument("--repo", help="owner/name")
    ap.add_argument("--repo-dir")
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--out", help="default: <data dir>/<project>/launchd (with --queue: <data root>/launchd)")
    ap.add_argument("--image")
    ap.add_argument("--env", default=PRODUCTION, help="the environment: names the labels and the container (default production)")
    ap.add_argument("--cockpit-dir", default=str(COCKPIT_DIR),
                    help="the Cockpit code the jobs start from; production: the release link <home>/releases/<project>/current (kept as a link)")
    ap.add_argument("--python", default=shutil.which("python3") or sys.executable,
                    help="absolute path of the python program the jobs run (launchd does not search PATH for it; a bare python3 is the Xcode shim); default: the python3 on PATH now")
    ap.add_argument("--release", metavar="NAME", help="start from the release link of the Cockpit's project NAME, <home>/releases/NAME/current "
                    "(replaces --cockpit-dir)")
    ap.add_argument("--projects-file", help="the viewer's --projects file: also write the viewer and the watch jobs")
    ap.add_argument("--viewer-token-file", help="with --projects-file: the viewer's --token-file (default: <data dir>/token)")
    ap.add_argument("--viewer-port", type=int, default=VIEWER_PORT)
    a = ap.parse_args(argv)
    if not os.path.isabs(a.python):
        sys.exit("--python must be an absolute path")
    absolute = lambda s: str(pathlib.Path(s).expanduser().resolve())
    if not a.env or not all(c.isalnum() or c == "-" for c in a.env):
        sys.exit(f"--env {a.env!r}: letters, digits and '-' only")
    if a.release:
        from ckp.store import releases
        a.cockpit_dir = str(releases.current_link(a.release))
    cockpit_dir = os.path.abspath(pathlib.Path(a.cockpit_dir).expanduser())   # not resolved: a `current` link must stay a link
    if a.env == PRODUCTION and in_working_clone(cockpit_dir):
        sys.exit(f"production must start from a release folder (<home>/releases/<project>/current), not from the working clone {cockpit_dir}; "
                 "deploy a release with `ckp deploy` and pass its link as --cockpit-dir, or render another --env")
    if a.queue:
        root = absolute(a.data_root)
        out, repo_dir, kw = pathlib.Path(absolute(a.out or queue_paths(root)["launchd"])), None, dict(queue_root=root)
        a.project = a.repo = None
    else:
        if not (a.project and a.repo and a.repo_dir):
            sys.exit("--project, --repo and --repo-dir are required (or --queue)")
        out, repo_dir, kw = pathlib.Path(absolute(a.out or paths(a.project)["launchd"])), absolute(a.repo_dir), {}
    token = absolute(a.token_file)
    kw.update(cockpit_dir=cockpit_dir, env=a.env)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [quota_plist(a.project, python=a.python, **kw), dispatcher_plist(a.project, a.repo, repo_dir, token, a.image, python=a.python, **kw)]
    if a.projects_file:
        logs = pathlib.Path(DATA_DIR)
        jobs += [viewer_plist(absolute(a.projects_file), absolute(a.viewer_token_file or logs / "token"), logs / "cockpit.log", cockpit_dir, a.python,
                              a.env, a.viewer_port),
                 watch_plist(logs / "watch.log", cockpit_dir, a.python, a.env)]
    for plist in (jobs[1], jobs[0], *jobs[2:]):
        f = out / f"{plist['Label']}.plist"
        f.write_bytes(plistlib.dumps(plist))
        print(f)
    print("To install (by hand; a job already loaded is first unloaded with `launchctl bootout gui/$(id -u)/LABEL`):")
    for plist in jobs:
        print(f"  launchctl bootstrap gui/$(id -u) {out / (plist['Label'] + '.plist')}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "render":
        sys.exit("usage: python3 -m dispatcher.launchd render (--project NAME --repo OWNER/NAME --repo-dir DIR | --queue [--data-root DIR]) --token-file F [--out DIR]")
    render(argv[1:])


if __name__ == "__main__":
    main()
