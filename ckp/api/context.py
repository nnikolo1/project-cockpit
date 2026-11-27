"""The shared world of the API layer: the project configuration (CFG, PROJECTS, DEFAULT), the data folders, the offline flag, the warm-up flag
and the per-request Context. Process-wide state lives here (and in cache.py, gh.py) so that every API module reads it at call time."""
import dataclasses
import json
import os
import pathlib
import re
import time
import typing

from ckp.core import modules as core_modules
from ckp.store import layout as store_layout


ROOT = pathlib.Path(__file__).resolve().parents[2]   # the Cockpit's own folder (web/, tools/, its git commit)


# Global switches (steer, simulate) live in CFG. A project is a dict of PROJECT_KEYS plus its name; PROJECTS maps name -> project.
# Without any registered project (tests, embedding) CFG itself doubles as the one project.
CFG = dict(repo=None, manager=None, steer=False, simulate=False, project=None, git=None, modules=None, phases=None, code=None, code_collect=None, compliance=None)


PROJECT_KEYS = ("repo", "manager", "project", "git", "modules", "phases", "code", "code_collect", "compliance")


PROJECTS, DEFAULT = {}, None


class UnknownProject(KeyError):
    pass


def proj(name=None):
    """The settings of project `name` (default: the configured default)."""
    if not PROJECTS:
        return CFG
    n = name or DEFAULT
    if n not in PROJECTS:
        raise UnknownProject(f"unknown project {n!r}; configured: {', '.join(PROJECTS)}")
    return PROJECTS[n]


def pk(p, what):
    """Cache key of `what` for project p: projects never share a cached value."""
    return f"{p.get('name', '')}:{what}"


def project_from_flags(a):
    """The implicit project of the single-project flags, named after the repo."""
    return dict(name=a["repo"].split("/")[-1], repo=a["repo"], manager=a.get("manager") or a["repo"].split("/")[0],
                **{k: a.get(k) for k in PROJECT_KEYS[2:-2]}, code_collect=core_modules.collect_command(a.get("code_collect")),
                compliance=core_modules.collect_command(a.get("compliance"), "compliance"))


def projects_from_file(data):
    """Parsed --projects JSON -> (list of project dicts, default name). Raises ValueError with a clear message."""
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list) or not data["projects"]:
        raise ValueError('--projects: expected {"default": NAME, "projects": [{"name", "repo", ...}]}')
    out = []
    for e in data["projects"]:
        name, repo = (e.get("name") or "").strip() if isinstance(e, dict) else "", e.get("repo") if isinstance(e, dict) else None
        if not name or not isinstance(repo, str) or repo.count("/") != 1:
            raise ValueError(f"--projects: every project needs a name and a repo OWNER/REPO (got {e!r})")
        if any(name == p["name"] for p in out):
            raise ValueError(f"--projects: duplicate project name {name!r}")
        p = dict(name=name, repo=repo, manager=e.get("manager") or repo.split("/")[0], project=e.get("project"))
        p.update({k: os.path.expanduser(e[k]) if e.get(k) else None for k in ("git", "modules", "phases", "code")})
        if e.get("meta"):
            p["meta"] = os.path.expanduser(e["meta"])  # optional: absent means the default folder, see meta_dir()
        if e.get("stats_cache"):
            p["stats_cache"] = os.path.expanduser(e["stats_cache"])  # optional: the tool's statistics cache, for "How these numbers fit"
        if e.get("start_month") is not None:  # optional: the month the project started; Token Usage shows nothing of it before (PC-113)
            if not (isinstance(e["start_month"], str) and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", e["start_month"])):
                raise ValueError(f"--projects: project {name!r}: start_month must be YYYY-MM (got {e['start_month']!r})")
            p["start_month"] = e["start_month"]
        if e.get("transcript_sources") is not None:  # optional: the project's transcript folders (source labels); default: the folder rule
            if not (isinstance(e["transcript_sources"], list) and all(isinstance(x, str) and x for x in e["transcript_sources"])):
                raise ValueError(f"--projects: project {name!r}: transcript_sources must be a list of folder names")
            p["transcript_sources"] = list(e["transcript_sources"])
        try:
            p["code_collect"] = core_modules.collect_command(e.get("code_collect"))  # the snapshot's collector command, run by the daily refresh
            p["compliance"] = core_modules.collect_command(e.get("compliance"), "compliance")  # the compliance check, the same command as the terminal and the daily job
        except ValueError as ex:
            raise ValueError(f"--projects: project {name!r}: {ex}") from None
        out.append(p)
    default = data.get("default") or out[0]["name"]
    if default not in [p["name"] for p in out]:
        raise ValueError(f"--projects: default {default!r} is not one of the projects")
    return out, default


def register(projects, default):
    PROJECTS.clear()
    PROJECTS.update({p["name"]: p for p in projects})
    global DEFAULT
    DEFAULT = default


DATA_DIR = pathlib.Path("~/.local/share/project-cockpit").expanduser()  # where deployment.md keeps each project's token folder


QUEUE_ROOT = store_layout.QUEUE_ROOT  # the one Task Queue's data root (PC-67, spec 5c), the Dispatcher's --data-root default


def meta_dir(p):
    """Folder of the project's card metadata files: its `meta` setting, else <data dir>/<project name>/cardmeta."""
    return pathlib.Path(p.get("meta") or DATA_DIR / (p.get("name") or p["repo"].split("/")[-1]) / "cardmeta")


OFFLINE = {}  # set by --offline: dict(root, created, gh_attempts). An offline copy makes no GitHub call and serves every cached copy as is.


def _clock(t):
    return time.strftime("%H:%M", time.localtime(t))


CACHE_DIR = None  # folder of the on-disk copies of the slow answers (set by main); None = memory only. Lets a restarted service answer at once.


CACHE_SCHEMA = 1  # the shape of what is cached: bump it when a cached payload changes its shape; a new folder (cache/s<N>) is then used and no old shape is read


# ---------- state ----------
def load_modules(p):
    return json.load(open(p["modules"])) if p.get("modules") else None


def queue_files(p):
    """The Task Queue's files (settings, state, processing log), always from the layout module (dispatcher/layout.py, PC-138): the one
    queue's data root once it holds a settings file (migrated, deployment.md 5a; the same queue and settings for every project, spec 3c, 6a),
    else the project's own folder (before)."""
    shared = store_layout.queue(QUEUE_ROOT)
    return shared if shared["settings"].exists() else store_layout.per_project(DATA_DIR, p.get("name") or p["repo"].split("/")[-1])


def queue_dir(p):
    """The folder of the Task Queue's files (`queue_files`)."""
    return queue_files(p)["dir"]


def settings_path(p):
    """The Dispatcher's settings file (spec 6a): dispatcher-settings.json in `queue_dir`."""
    return queue_files(p)["settings"]


def served_project(listed):
    """The Cockpit's own project entry for a project the queue lists (same repository, else same name), or None when this Cockpit does
    not serve it (then its tasks cannot be read: it has no manager or task configuration here)."""
    ps = list((PROJECTS or {CFG.get("name", ""): CFG}).values())
    return next((q for q in ps if q.get("repo", "").lower() == listed["repo"].lower()), None) or next((q for q in ps if q.get("name") == listed["short"]), None)


def card_settings_path(p):
    """The module-card options of the project: <data dir>/<project name>/cockpit-settings.json."""
    return DATA_DIR / (p.get("name") or p["repo"].split("/")[-1]) / "cockpit-settings.json"


def _read_text(path):
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


WARM = dict(on=False, since=0.0)  # on from the moment the server listens until the startup warm-up is done (PC-131): the page shows "warming up, data from <time>"


SECRET_SOURCES = []  # callables returning the secrets (the access token) that never reach a log line; the server registers its own


def secrets():
    """The secrets the action log and the page events must scrub."""
    return tuple(s for src in SECRET_SOURCES for s in src())


@dataclasses.dataclass
class Context:
    """What one API call runs with: `p`, the project it acts on (None: the action takes it from its form), and `run`, the runner for gh calls
    (None: the default gateway, ckp.api.gh.run_gh). The caches and the configuration are the process-wide modules ckp.api.cache and this one."""
    p: typing.Optional[dict] = None
    run: typing.Optional[typing.Callable] = None
    referer: str = ""
