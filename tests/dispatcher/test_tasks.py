import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from dispatcher import tasks as T  # noqa: E402


def node(number, labels=(), body="text", blocked=(), assignees=(), state="OPEN", events=(), created="2026-11-01T00:00:00Z"):
    return {"number": number, "databaseId": number, "title": f"title {number}", "url": f"u{number}", "body": body, "state": state,
            "stateReason": None, "createdAt": created, "updatedAt": None, "closedAt": None, "author": {"login": "own"},
            "labels": {"nodes": [{"name": l} for l in labels]}, "assignees": {"nodes": [{"login": a} for a in assignees]},
            "parent": None, "blockedBy": {"nodes": [{"number": b} for b in blocked]}, "comments": {"nodes": []},
            "timelineItems": {"nodes": [{"__typename": "LabeledEvent", "createdAt": "2026-11-02T00:00:00Z", "actor": {"login": "own"},
                                         "label": {"name": l}} for l in events]}}


class Gh:
    def __init__(self, nodes=(), labels=()):
        self.nodes, self.labels, self.calls = list(nodes), list(labels), []

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ["api", "graphql"]:
            return json.dumps({"data": {"repository": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": self.nodes}}}})
        if args[:2] == ["issue", "view"]:
            return json.dumps({"labels": [{"name": l} for l in self.labels]})
        return ""


def make(gh, git=None, now=None):
    return T.GitHubTasks("own/project-cockpit", gh, git or (lambda a, c: ""), "/repo", short="cockpit", now=now)


def test_released_filters_and_reads_metadata():
    gh = Gh([
        node(1, ["P1", "must", "due:2026-12-01"], events=["P1", "due:2026-12-01"], body="do it"),
        node(2),
        node(3, ["wip"]), node(4, ["merged"]), node(5, ["question"]), node(6, ["type:decision"]),
        node(7, assignees=["x"]), node(8, state="CLOSED"),
        node(9, blocked=[2]),
    ])
    got = {t.id: t for t in make(gh).released()}
    assert sorted(got) == ["cockpit-1", "cockpit-2", "cockpit-9"]
    t = got["cockpit-1"]
    assert (t.repo, t.number, t.title, t.text, t.priority, t.due, t.must, t.blocked) == ("own/project-cockpit", 1, "title 1", "do it", 1, "2026-12-01", True, False)
    assert got["cockpit-2"].priority == 2 and got["cockpit-2"].due is None and not got["cockpit-2"].must
    assert got["cockpit-2"].unblocks == 1 and got["cockpit-9"].blocked


def test_author_labels_decide_release():
    import datetime as dt
    day = lambda d: (lambda: dt.datetime(2026, 11, d, tzinfo=dt.timezone.utc))
    gh = Gh([node(1, ["author:owner"]), node(2, ["author:interactive"]), node(3, ["author:job"]), node(4, ["author:job", "released"]),
             node(5, ["author:interactive", "released"])])
    assert sorted(t.id for t in make(gh, now=day(10)).released()) == ["cockpit-1", "cockpit-4", "cockpit-5"]   # 9 days old
    assert sorted(t.id for t in make(gh, now=day(11)).released()) == ["cockpit-1", "cockpit-2", "cockpit-4", "cockpit-5"]   # 10 days old


def test_policy_finding_is_released_whatever_its_author():
    gh = Gh([node(1, ["policy-finding", "author:job"], body="<!-- filed-by: worker1 -->"), node(2, ["policy-finding", "author:interactive"]),
             node(3, ["policy-finding", "question"]), node(4, ["policy-finding", "type:decision"]), node(5, ["author:job"])])
    got = {t.id: t for t in make(gh).released()}
    assert sorted(got) == ["cockpit-1", "cockpit-2"] and got["cockpit-1"].filed_by == "worker1" and got["cockpit-2"].filed_by is None


def test_unauthorised_priority_label_is_ignored():
    gh = Gh([node(1, ["P1"])])  # no labeled event by the manager
    assert make(gh).released()[0].priority == 2


def test_set_state_swaps_the_label():
    gh = Gh(labels=["wip", "bug"])
    make(gh).set_state("cockpit-5", "unmerged")
    assert ["label", "create", "unmerged", "-R", "own/project-cockpit", "--force"] in gh.calls
    assert gh.calls[-1] == ["issue", "edit", "5", "-R", "own/project-cockpit", "--add-label", "unmerged", "--remove-label", "wip"]


def test_set_state_first_label_and_same_state():
    gh = Gh(labels=[])
    make(gh).set_state("cockpit-5", "wip")
    assert "--remove-label" not in gh.calls[-1]
    gh = Gh(labels=["wip"])
    make(gh).set_state("cockpit-5", "wip")
    assert all(c[:2] != ["issue", "edit"] for c in gh.calls)


@pytest.mark.parametrize("current", ["merged", "failed"])
def test_set_state_refuses_to_leave_final_states(current):
    gh = Gh(labels=[current])
    with pytest.raises(ValueError, match="owner or an interactive agent"):
        make(gh).set_state("cockpit-5", "wip")
    assert all(c[:2] != ["issue", "edit"] for c in gh.calls)


def test_set_state_rejects_bad_input():
    t = make(Gh())
    with pytest.raises(ValueError):
        t.set_state("cockpit-5", "released")
    with pytest.raises(ValueError):
        t.set_state("other-5", "wip")


def test_fetch_branch_only_fetches():
    calls = []
    make(Gh(), lambda a, c: calls.append((a, c))).fetch_branch("cockpit-5", "/w/result.bundle", "feat/x")
    assert calls == [(["fetch", "/w/result.bundle", "+refs/heads/feat/x:refs/heads/worker/cockpit-5"], "/repo")]


@pytest.mark.parametrize("branch", ["--upload-pack=x", "a b", ""])
def test_fetch_branch_rejects_unusable_names(branch):
    with pytest.raises(ValueError):
        make(Gh()).fetch_branch("cockpit-5", "/b", branch)


def test_write_task_md(tmp_path):
    p = tmp_path / "task.md"
    T.write_task_md(T.Task("cockpit-1", "o/r", 1, "Title", "Body"), p)
    assert p.read_text() == "# Title\n\nBody\n"
