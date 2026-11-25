"""The GitHub task adapter (spec docs/specs/dispatcher-and-workers.md: 2a-2e, 3d, 3e).

Task metadata lives in GitHub (2d). A task is `<repository short name>-<issue number>`; its states wip, unmerged, failed and merged are
labels on its issue. Priority, deadline, blockers, unblocks and must/nice are read with the cockpit's own logic (logic.derive and
the helpers below, copied from the retired bootfill.core). Every call goes through two injected functions: `gh(args) -> stdout` and `git(args, cwd) -> stdout`.
"""
import datetime as dt
import json
import logging
import re
from dataclasses import dataclass

import cockpit
import logic as L

log = logging.getLogger("dispatcher.tasks")

STATES = ("wip", "unmerged", "failed", "merged")
FINAL = ("merged", "failed")              # 2c: only the owner or an interactive agent moves a task out of these
NOT_RELEASABLE = ("question", "type:decision", "interactive-only")   # 2b: embryos apart from questions, decisions and the Task Queue's own tasks count as released
TASK_ID = re.compile(r"^(?P<short>[A-Za-z0-9][A-Za-z0-9._-]*)-(?P<number>\d+)$")
INTERACTIVE_DAYS = 10                     # 2b: an interactive agent's embryo counts as released after this many days
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


@dataclass
class Task:
    id: str
    repo: str
    number: int
    title: str
    text: str
    priority: int = 2
    due: str = None
    blocked: bool = False
    unblocks: int = 0
    must: bool = False
    filed_by: str = None
    project: str = None                   # the repository short name (docs/contracts/queue-interfaces.md, "The project key")

    def __post_init__(self):
        if self.project is None:
            self.project = project_of(self.id)


def project_of(task_id):
    """The project key of a task id: the repository short name, the part before the issue number. The one place that parses it."""
    return task_id.rsplit("-", 1)[0]


def write_task_md(task, path):
    """The task's title and text, as the Worker reads it (3d)."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {task.title}\n\n{task.text}\n")


def _unblock_counts(tasks):
    """key -> number of open tasks that name it as a blocker."""
    open_ = {t["key"] for t in tasks if t["stage"] in ("embryo", "released", "wip")}
    out = {}
    for t in tasks:
        if t["key"] in open_:
            for b in t["blockedBy"]:
                out[b] = out.get(b, 0) + 1
    return out


def _rank_inputs(task, by_key, unblocks):
    """-> dict(deadline, blocked, unblocks): the task's own deadline, else its parent chain's; blocked while a blocker is not merged."""
    seen, t, deadline = set(), task, None
    while t and t["key"] not in seen:
        seen.add(t["key"])
        if t.get("deadline"):
            deadline = t["deadline"]
            break
        t = by_key.get(t.get("parent"))
    return dict(deadline=deadline, blocked=any(by_key[b]["stage"] != "merged" for b in task["blockedBy"] if b in by_key),
                unblocks=unblocks.get(task["key"], 0))


class GitHubTasks:
    def __init__(self, repo, gh, git, repo_dir, manager=None, short=None, now=None):
        """repo: `owner/name`; repo_dir: the task repository's clone on the host; manager: the login whose protected labels count
        (the cockpit's rule; default the repository owner); short: the repository short name in task ids (default: the repository name)."""
        self.repo, self.gh, self.git, self.repo_dir = repo, gh, git, repo_dir
        self.manager = manager or repo.split("/")[0]
        self.short = short or repo.split("/")[1]
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))

    def _issues(self):
        owner, name = self.repo.split("/")
        issues, events, after = [], [], None
        while True:
            args = ["api", "graphql", "-f", f"query={cockpit.GQL}", "-f", f"o={owner}", "-f", f"r={name}", "-F", f"n={cockpit.PAGE}"]
            page = json.loads(self.gh(args + (["-f", f"after={after}"] if after else [])))["data"]["repository"]["issues"]
            for n in page["nodes"]:
                issues.append(L.norm_issue(n))
                events += L.norm_events(n)
            if not page["pageInfo"]["hasNextPage"]:
                return issues, events
            after = page["pageInfo"]["endCursor"]

    def released(self):
        """Open, unassigned tasks without a state label, except questions and decisions, that count as released (2b), as Tasks: any task
        labelled `released` or `policy-finding` (a finding, released at once; its stage is set in logic.stage); an embryo with no author label or `author:owner`; an `author:interactive` embryo 10 days after its creation."""
        issues, events = self._issues()
        tasks, _ = L.derive(issues, events, {}, self.manager, [], False)
        by_key = {t["key"]: t for t in tasks}
        unblocks = _unblock_counts(tasks)
        labels = {i["number"]: set(i["labels"]) for i in issues}
        body = {i["number"]: i["body"] for i in issues}
        created = {i["number"]: i["created_at"] for i in issues}
        now = self.now()
        out = []
        for t in tasks:
            lab = labels[t["number"]]
            if t["stage"] not in ("embryo", "released") or lab & set(STATES) or lab & set(NOT_RELEASABLE) or t["type"] == "decision":
                continue
            if not (t["stage"] == "released" or "released" in lab or self._author_releases(lab, created[t["number"]], now)):
                continue
            r = _rank_inputs(t, by_key, unblocks)
            out.append(Task(id=f"{self.short}-{t['number']}", repo=self.repo, number=t["number"], title=t["title"], text=body[t["number"]],
                            priority=t["prio"] or 2, due=r["deadline"], blocked=r["blocked"], unblocks=r["unblocks"], must=t["cls"] == "must",
                            filed_by=t["filed_by"]))
        log.info("%s: %d released task(s)", self.repo, len(out))
        return out

    @staticmethod
    def _author_releases(lab, created, now):
        """2b: whether an embryo counts as released by its author label alone."""
        if "author:job" in lab:
            return False
        if "author:interactive" in lab and "author:owner" not in lab:
            return L.age_days(created, now) >= INTERACTIVE_DAYS
        return True

    def _number(self, task_id):
        m = TASK_ID.match(task_id)
        if not m or m["short"] != self.short:
            raise ValueError(f"not a task of {self.short}: {task_id!r}")
        return m["number"]

    def set_state(self, task_id, state):
        """Give the issue exactly one state label. A task in merged or failed is not moved (2c)."""
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        n = self._number(task_id)
        have = {l["name"] for l in json.loads(self.gh(["issue", "view", n, "-R", self.repo, "--json", "labels"]))["labels"]}
        current = next((s for s in STATES if s in have), None)
        if current == state:
            return
        if current in FINAL:
            raise ValueError(f"{task_id} is {current}: only the owner or an interactive agent changes it")
        self.gh(["label", "create", state, "-R", self.repo, "--force"])
        args = ["issue", "edit", n, "-R", self.repo, "--add-label", state]
        if current:
            args += ["--remove-label", current]
        self.gh(args)
        log.info("%s: %s -> %s", task_id, current or "no state label", state)

    def reconcile_wip(self, running):
        """PC-98: a task labelled `wip` that no running Worker holds is `unmerged` when its branch is in the task repository, otherwise `failed`;
        a closed issue loses a stale `wip` label. `running`: the task ids the Dispatcher's Workers hold. -> list of (task id, what was done)."""
        issues, _ = self._issues()
        done = []
        for i in issues:
            if "wip" not in i["labels"]:
                continue
            tid = f"{self.short}-{i['number']}"
            if i["state"] == "closed":
                self.gh(["issue", "edit", str(i["number"]), "-R", self.repo, "--remove-label", "wip"])
                done.append((tid, "stale wip label removed from a closed issue"))
            elif tid not in running:
                state = "unmerged" if self._has_branch(tid) else "failed"
                self.set_state(tid, state)
                self.gh(["issue", "comment", str(i["number"]), "-R", self.repo, "--body", f"Labelled wip but no Worker is running it: set to {state} (reconciliation, PC-98)."])
                done.append((tid, f"wip with no Worker -> {state}"))
        return done

    def _has_branch(self, task_id):
        try:
            return bool(self.git(["rev-parse", "--verify", "--quiet", f"refs/heads/worker/{task_id}"], self.repo_dir).strip())
        except Exception:
            return False

    def fetch_branch(self, task_id, bundle_path, branch):
        """Fetch the Worker's bundle into the task repository as `worker/<task-id>` (3e); no push, no pull request."""
        self._number(task_id)
        if not BRANCH.match(branch):
            raise ValueError(f"unusable branch name {branch!r}")
        self.git(["fetch", str(bundle_path), f"+refs/heads/{branch}:refs/heads/worker/{task_id}"], self.repo_dir)
        log.info("%s: fetched %s from %s as worker/%s", task_id, branch, bundle_path, task_id)


def _rank_key(t):
    """The Dispatcher's ranking rule (core.rank_key): priority, deadline, what it unblocks, must before nice."""
    return (t.priority, t.due or "9999-99-99", -t.unblocks, not t.must)


class ProjectsTasks:
    """One task source over several projects: one GitHubTasks per listed project, calls routed by `project_of(task_id)`. Only the listed
    projects are ever touched. Same methods and signatures as GitHubTasks, so the Dispatcher takes either."""

    def __init__(self, projects, gh, git, first=(), manager=None, now=None):
        """projects: list of {"short", "repo": "owner/name", "repo_dir"}; first: short names served before all others, in that order."""
        self.sources = {}
        for p in projects:
            if p["short"] in self.sources:
                raise ValueError(f"project {p['short']!r} is listed twice")
            self.sources[p["short"]] = GitHubTasks(p["repo"], gh, git, p["repo_dir"], manager=manager, short=p["short"], now=now)
        unknown = [f for f in first if f not in self.sources]
        if unknown:
            raise ValueError(f"`first` names projects that are not listed: {unknown}")
        self.first = list(first)

    def _source(self, task_id):
        short = project_of(task_id) if TASK_ID.match(task_id or "") else None
        if short not in self.sources:
            raise ValueError(f"not a task of a listed project: {task_id!r}")
        return self.sources[short]

    def released(self):
        """The released tasks of all listed projects in one list: `first` projects (in that order), then the others; within each group by
        the ranking rule, with no per-project share. A project whose listing fails is logged and skipped."""
        tasks = []
        for short, src in self.sources.items():
            try:
                tasks += src.released()
            except Exception:
                log.exception("%s: listing the released tasks failed; the other projects are still served", short)
        rank = {short: i for i, short in enumerate(self.first)}
        return sorted(tasks, key=lambda t: (rank.get(t.project, len(rank)), _rank_key(t)))

    def set_state(self, task_id, state):
        self._source(task_id).set_state(task_id, state)

    def fetch_branch(self, task_id, bundle_path, branch):
        self._source(task_id).fetch_branch(task_id, bundle_path, branch)

    def reconcile_wip(self, running):
        """PC-98 over all listed projects; a failing project is logged and skipped. -> list of (task id, what was done)."""
        done = []
        for short, src in self.sources.items():
            try:
                done += src.reconcile_wip(running)
            except Exception:
                log.exception("%s: reconciling wip tasks failed; the other projects are still reconciled", short)
        return done
