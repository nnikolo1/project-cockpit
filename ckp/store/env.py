"""Deploy environments of a project: <home>/env/<project>.json = {environment: {"port", "data_dir", "label"}}."""
import logging
import pathlib

from ckp.store import io, layout

log = logging.getLogger(__name__)


def _file(project, home=None):
    if not project or "/" in project or "\\" in project or project in (".", ".."):
        raise ValueError(f"unusable project name {project!r}")
    return (pathlib.Path(home).expanduser() if home else layout.home()) / "env" / f"{project}.json"


def load(project, home=None):
    d = io.read_json(_file(project, home), {})
    return d if isinstance(d, dict) else {}


def save(project, envs, home=None):
    io.atomic_write_json(_file(project, home), envs, mode=0o600, indent=2)
    log.info("saved %d environment(s) of %s", len(envs), project)


def validate(all_envs):
    """The problems of {project: {environment: record}} as sentences; empty when valid."""
    problems, ports, labels, dirs = [], {}, {}, []
    for project, envs in sorted(all_envs.items()):
        for name, rec in sorted(envs.items()):
            who = f"{project}/{name}"
            port, label, data = rec.get("port"), rec.get("label"), rec.get("data_dir")
            if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
                problems.append(f"{who}: port {port!r} is outside 1024 to 65535")
            elif port in ports:
                problems.append(f"{who} and {ports[port]} share port {port}")
            else:
                ports[port] = who
            if label is not None:
                if label in labels:
                    problems.append(f"{who} and {labels[label]} share the label {label!r}")
                else:
                    labels[label] = who
            if data:
                p = pathlib.PurePosixPath(str(data))
                for other, op in dirs:
                    if p == op:
                        problems.append(f"{who} and {other} share the data folder {data}")
                    elif p in op.parents or op in p.parents:
                        problems.append(f"the data folder of {who} and that of {other} contain one another")
                dirs.append((who, p))
    return problems
