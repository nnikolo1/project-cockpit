"""The pages the viewer serves: the one web page and the list of projects it shows (the page itself stays in web/)."""
import logging

from ckp.api import context as api_context, health as api_health
from ckp.api.result import File, ok

log = logging.getLogger("cockpit")


def index(ctx):
    """GET / and /index.html: the web page."""
    return ok(File((api_context.ROOT / "web" / "index.html").read_bytes(), "text/html; charset=utf-8"))


def projects(ctx):
    """GET /api/projects: the default project and the projects served (and the offline copy's facts)."""
    return ok(dict(default=api_context.DEFAULT, projects=[dict(name=n, repo=p["repo"]) for n, p in api_context.PROJECTS.items()],
                   **({"offline": api_health.offline_info()} if api_context.OFFLINE else {})))
