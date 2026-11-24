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

    def fetch_branch(self, task_id, bundle_path, branch):
        """Fetch the Worker's bundle into the task repository as `worker/<task-id>` (3e); no push, no pull request."""
        self._number(task_id)
        if not BRANCH.match(branch):
            raise ValueError(f"unusable branch name {branch!r}")
        self.git(["fetch", str(bundle_path), f"+refs/heads/{branch}:refs/heads/worker/{task_id}"], self.repo_dir)
        log.info("%s: fetched %s from %s as worker/%s", task_id, branch, bundle_path, task_id)
