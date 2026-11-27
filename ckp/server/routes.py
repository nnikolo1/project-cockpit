"""The routing table: method and path -> the ckp.api function that serves it and where its parameters come from in the query string.
Every path of the viewer is named here, as a string, and nowhere else."""
import typing

from ckp.api import actions as api_actions, code as api_code, health as api_health, pages as api_pages, queue as api_queue
from ckp.api import settings as api_settings, state as api_state, usage as api_usage


class Route(typing.NamedTuple):
    fn: typing.Callable
    params: dict = {}        # keyword of the API function -> reader(query, rest of the path)
    public: bool = False     # answered before the token check (the deploy's health check)
    project: bool = True     # the project of the request is resolved first (404 for an unknown one)


def flag(name):
    """True when the query carries `name` with a value (`?fresh=1`); the page's Refresh click."""
    return lambda q, rest: bool(q.get(name))


def text(name):
    return lambda q, rest: q.get(name, [""])[0]


def _since(q, rest=""):
    """The `since` query value (the fetchedAt of the page's copy) as a number; 0 when missing or not a number."""
    try:
        return float(q.get("since", ["0"])[0])
    except ValueError:
        return 0


PATH = lambda q, rest: rest   # noqa: E731  the part of the path after a prefix route's prefix


ROUTES = {
    ("GET", "/health"): Route(api_health.health, public=True, project=False),   # no data, no token: only that the server answers and whether it warms up (PC-84)
    ("GET", "/"): Route(api_pages.index, project=False),
    ("GET", "/index.html"): Route(api_pages.index, project=False),
    ("GET", "/api/projects"): Route(api_pages.projects, project=False),
    ("GET", "/api/state"): Route(api_state.state, dict(fresh=flag("fresh"))),
    ("GET", "/api/task"): Route(api_state.task, dict(n=text("n"))),
    ("GET", "/api/questions"): Route(api_state.questions, dict(fresh=flag("fresh"))),
    ("GET", "/api/timeline"): Route(api_state.timeline, dict(fresh=flag("fresh"))),
    ("GET", "/api/settings"): Route(api_settings.settings),
    ("GET", "/api/cardsettings"): Route(api_settings.cardsettings),
    ("GET", "/api/queue"): Route(api_queue.queue),
    ("GET", "/api/queueall"): Route(api_queue.queue_all),
    ("GET", "/api/queuepoll"): Route(api_queue.queue_poll, dict(since=_since)),
    ("GET", "/api/ci"): Route(api_health.ci),
    ("GET", "/api/usage"): Route(api_usage.usage),
    ("GET", "/api/environments"): Route(api_health.environments),
    ("GET", "/api/compliance"): Route(api_health.compliance),
    ("GET", "/api/actionlog"): Route(api_queue.action_log, dict(action=text("action"), outcome=text("outcome"), task=text("task"))),
    ("GET", "/compliance-report"): Route(api_health.compliance_report),
    ("GET", "/api/rules"): Route(api_code.rules),
    ("GET", "/api/rulesprs"): Route(api_code.rule_prs, dict(fresh=flag("fresh"))),
    ("GET", "/api/code"): Route(api_code.code),
    ("GET", "/api/moduledoc"): Route(api_code.moduledoc, dict(module=text("id"))),
    ("GET", "/api/cardmeta"): Route(api_code.cardmeta),
    ("POST", "/api/act"): Route(api_actions.act, project=False),
    ("POST", "/api/event"): Route(api_actions.event, project=False),
}
PREFIXES = {   # a path that starts with the key is served by the route; the rest of the path is its parameter
    ("GET", "/code/"): Route(api_code.diagram, dict(name=PATH)),
    ("GET", "/report/"): Route(api_code.report, dict(path=PATH)),
}


def match(method, path):
    """-> (route, rest of the path) or (None, "")."""
    if (method, path) in ROUTES:
        return ROUTES[method, path], ""
    for (m, prefix), route in PREFIXES.items():
        if m == method and path.startswith(prefix):
            return route, path.removeprefix(prefix)
    return None, ""
