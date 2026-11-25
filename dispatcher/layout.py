"""Where the Task Queue's files are: one place (contract docs/contracts/queue-interfaces.md, "The shared processing-data root").

`queue(root, project)` is the layout of the one queue for all projects; `per_project(data_dir, name)` is today's layout, one folder per
project, which keeps working until the owner moves the live data (PC-67). Pure functions: no file is read, created or checked.
"""
import pathlib

DATA_DIR = pathlib.Path("~/.local/share/project-cockpit").expanduser()                 # as cockpit.DATA_DIR
QUEUE_ROOT = pathlib.Path("~/.local/share/project-cockpit/task-queue").expanduser()   # the shared root, argument --data-root


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
