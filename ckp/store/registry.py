"""The project registry: <home>/projects.json, one list of project records (contract docs/contracts/registry-interfaces.md, part A)."""
import logging
import pathlib

from ckp.store import io, layout

log = logging.getLogger(__name__)


def _file(home=None):
    return (pathlib.Path(home).expanduser() if home else layout.home()) / "projects.json"


def load(home=None):
    """The list of project records; a missing or broken file, or anything that is not a list of objects, gives what is usable."""
    d = io.read_json(_file(home), [])
    return [r for r in d if isinstance(r, dict)] if isinstance(d, list) else []


def save(records, home=None):
    io.atomic_write_json(_file(home), list(records), indent=2)
    log.info("saved %d project record(s)", len(records))
