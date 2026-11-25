"""Where the Cockpit's files are: one place (contract docs/contracts/queue-interfaces.md, "The shared processing-data root").

CP_HOME is the Cockpit's home: instance data outside every repository (environment variable CP_HOME, default `~/.local/share/project-cockpit`).
`queue(root, project)` is the layout of the one queue for all projects; `per_project(data_dir, name)` is today's layout, one folder per
project, which keeps working until the owner moves the live data (PC-67). Pure functions: no file is read, created or checked.
"""
import os
import pathlib

DATA_DIR = pathlib.Path("~/.local/share/project-cockpit").expanduser()                 # the default home, as cockpit.DATA_DIR
QUEUE_ROOT = pathlib.Path("~/.local/share/project-cockpit/task-queue").expanduser()   # the shared queue root, argument --data-root
CONFIG_DIR = pathlib.Path("~/.config/project-cockpit").expanduser()                   # owner-held settings and tokens


def home():
    """The Cockpit's home: $CP_HOME when set, else the default data folder."""
    v = os.environ.get("CP_HOME")
    return pathlib.Path(v).expanduser() if v else DATA_DIR


def project_dir(name, data_dir=None):
    """The generated data of one project: <home>/<name>/."""
    return pathlib.Path(data_dir).expanduser() / name if data_dir else home() / name


def cache_dir(schema, data_dir=None):
    """The rebuildable caches: <home>/cache/s<schema>/. A change of the cached shape bumps the schema, so a new folder is used and the
    old shape is never read; the old folder can be deleted at any time."""
    return (pathlib.Path(data_dir).expanduser() if data_dir else home()) / "cache" / f"s{int(schema)}"


def per_project(data_dir=DATA_DIR, name=""):
    """The per-project files, under <data dir>/<name>/ (the folder the Cockpit reads: settings, usage, processing log, area)."""
    d = pathlib.Path(data_dir).expanduser() / name
    return dict(dir=d, settings=d / "dispatcher-settings.json", state=d / "dispatcher-state.json", area=d / "area",
                socket=d / "proxy.sock", usage=d / "proxy-usage.jsonl", quota=d / "quota-history.jsonl", processing=d / "processing",
                plog=d / "processing" / "processing.jsonl", launchd=d / "launchd")


def queue(root=QUEUE_ROOT, project=None):
    """The files of the one queue under the shared root. With a project: its task folders (`area/<project>`), its synopsis
    (`synopsis/<project>.md`) and its memory folder (`memory/<project>`); without: the root's own areas."""
    d = pathlib.Path(root).expanduser()
    out = dict(dir=d, settings=d / "dispatcher-settings.json", state=d / "dispatcher-state.json", area=d / "area",
               socket=d / "proxy.sock", usage=d / "proxy-usage.jsonl", quota=d / "quota-history.jsonl", processing=d / "processing",
               plog=d / "processing" / "processing.jsonl", launchd=d / "launchd")
    if project:
        if "/" in project or project in (".", "..") or "\\" in project:
            raise ValueError(f"unusable project name {project!r}")
        out.update(area=d / "area" / project, synopsis=d / "synopsis" / f"{project}.md", memory=d / "memory" / project)
    return out


_home = home  # the parameter of deploy_lock shadows the function name


def deploy_lock(home=None):
    """The lock file of the deploy command: <home>/deploy.lock."""
    return (pathlib.Path(home).expanduser() if home else _home()) / "deploy.lock"
