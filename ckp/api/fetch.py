"""Reading the issues, the Project items and the module deadlines from GitHub (GraphQL pages, single-issue re-reads, timeline repair)."""
import logging
import json

from ckp.core import tasks_model as core_tasks_model
from ckp.api import gh as api_gh

log = logging.getLogger("cockpit")


PAGE = 20  # issues per GraphQL page. GitHub silently thins the nested timelineItems of a big page (ADR 0019): 100 issues -> 5 events each, 50 -> 7, 30 -> complete


TL_BATCH = 10  # issues per on-demand timeline query


TIMELINE = """timelineItems(last:100,itemTypes:[LABELED_EVENT,UNLABELED_EVENT,ASSIGNED_EVENT,CLOSED_EVENT]){nodes{__typename
  ... on LabeledEvent{createdAt actor{login} label{name}} ... on UnlabeledEvent{createdAt actor{login} label{name}}
  ... on AssignedEvent{createdAt actor{login} assignee{... on User{login}}} ... on ClosedEvent{createdAt actor{login}}}}"""


NODE = """number databaseId title url body state stateReason createdAt updatedAt lastEditedAt closedAt author{login}
 labels(first:50){nodes{name}} assignees(first:5){nodes{login}} parent{number} blockedBy(first:50){nodes{number}}
 comments(last:10){nodes{body createdAt author{login}}}
 """ + TIMELINE  # the fields of one issue: the full load and the re-read of single issues (PC-135) read the same


GQL = """query($o:String!,$r:String!,$after:String,$n:Int!){repository(owner:$o,name:$r){issues(first:$n,after:$after,orderBy:{field:CREATED_AT,direction:ASC}){
 pageInfo{hasNextPage endCursor} nodes{""" + NODE + "}}}}"


def fetch_issues(p):
    """All issues of the repo in PAGE-issue GraphQL pages -> (raw issues, events). The events are then checked and, where a protected
    label has no determinable event, completed per issue (repair_events)."""
    owner, name = p["repo"].split("/")
    issues, events, after = [], [], None
    while True:
        args = ["api", "graphql", "-f", f"query={GQL}", "-f", f"o={owner}", "-f", f"r={name}", "-F", f"n={PAGE}"] + (["-f", f"after={after}"] if after else [])
        page = json.loads(api_gh.run_gh(args))["data"]["repository"]["issues"]
        for n in page["nodes"]:
            issues.append(core_tasks_model.norm_issue(n))
            events += core_tasks_model.norm_events(n)
        if not page["pageInfo"]["hasNextPage"]:
            return issues, repair_events(p, issues, events)
        after = page["pageInfo"]["endCursor"]


def fetch_issue_nodes(p, numbers):
    """PC-135: a fresh reading of the given issues only, TL_BATCH per GraphQL call (one call for an action's one or two issues)
    -> (raw issues, events). An issue GitHub no longer returns is left out."""
    owner, name = p["repo"].split("/")
    issues, events = [], []
    for k in range(0, len(numbers), TL_BATCH):
        chunk = numbers[k:k + TL_BATCH]
        body = " ".join(f"i{n}:issue(number:{n}){{{NODE}}}" for n in chunk)
        data = json.loads(api_gh.run_gh(["api", "graphql", "-f", f"query=query($o:String!,$r:String!){{repository(owner:$o,name:$r){{{body}}}}}", "-f", f"o={owner}", "-f", f"r={name}"]))["data"]["repository"]
        for n in chunk:
            if data.get(f"i{n}"):
                issues.append(core_tasks_model.norm_issue(data[f"i{n}"]))
                events += core_tasks_model.norm_events(data[f"i{n}"])
    return issues, events


def fetch_timelines(p, numbers):
    """Label/assign/close events of the given issues, TL_BATCH issues per query (small enough that GitHub does not thin the timeline) -> {number: [events]}."""
    owner, name = p["repo"].split("/")
    out = {}
    for k in range(0, len(numbers), TL_BATCH):
        chunk = numbers[k:k + TL_BATCH]
        body = " ".join(f"i{n}:issue(number:{n}){{number stateReason {TIMELINE}}}" for n in chunk)
        data = json.loads(api_gh.run_gh(["api", "graphql", "-f", f"query=query($o:String!,$r:String!){{repository(owner:$o,name:$r){{{body}}}}}", "-f", f"o={owner}", "-f", f"r={name}"]))["data"]["repository"]
        for n in chunk:
            if data.get(f"i{n}"):
                out[n] = core_tasks_model.norm_events(data[f"i{n}"])
    return out


_TL = {}  # (repo, issue, updatedAt, protected labels) -> events fetched on demand; a changed issue has a new updatedAt, so it is fetched again


def repair_events(p, issues, events, fetch=None, cache=None):
    """GitHub silently returns a thinned timeline for large GraphQL pages (ADR 0019), so a protected label may have no event in `events`
    although its actor is on record. For each issue whose protected label has no `labeled` event as its latest one, fetch that issue's own
    timeline (bounded: only those issues, TL_BATCH per call, cached per issue and updatedAt). A label that STILL has no event stays without actor,
    so policy() keeps flagging it. A failed fetch leaves the events as they are (conservative: flagged), never raises."""
    cache = _TL if cache is None else cache
    need = core_tasks_model.issues_needing_timeline(issues, events)
    if not need:
        return events
    by = {i["number"]: i for i in issues}
    key = lambda n: (p["repo"], n, by[n].get("updated_at"), tuple(sorted(l for l in by[n]["labels"] if core_tasks_model.is_protected(l))))
    todo = [n for n in need if key(n) not in cache]
    if todo:
        try:
            got = (fetch or (lambda nums: fetch_timelines(p, nums)))(todo)
        except Exception as ex:
            log.warning("timeline repair of %d issue(s) failed: %s: %s; their protected labels stay unverified", len(todo), type(ex).__name__, str(ex)[:150])
            got = {}
        if len(cache) > 5000:
            cache.clear()
        for n, evs in got.items():
            cache[key(n)] = evs
        log.info("timeline repair: %d of %d issue(s) with a protected label lacking an event fetched (%d got events)", len(todo), len(need), len(got))
    fixed = {n: cache[key(n)] for n in need if key(n) in cache}
    return [e for e in events if e["number"] not in fixed] + [e for evs in fixed.values() for e in evs]


def fetch_project(p):
    if not p["project"]:
        return {}
    owner = p["repo"].split("/")[0]
    d = json.loads(api_gh.run_gh(["project", "item-list", str(p["project"]), "--owner", owner, "--format", "json", "--limit", "1000"]))
    return core_tasks_model.parse_project(d["items"], p["repo"])


def fetch_module_deadlines(p):
    try:
        return core_tasks_model.parse_module_deadlines(api_gh.run_gh(["api", f"repos/{p['repo']}/actions/variables/MODULE_DEADLINES", "--jq", ".value"]))
    except RuntimeError as ex:
        log.info("no MODULE_DEADLINES variable (%s)", str(ex)[:80])
        return {}
