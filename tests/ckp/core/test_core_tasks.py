import json

from ckp.core import tasks as T

PROJECT = {"name": "cp", "repo": "own/cockpit", "short": "PC", "repo_dir": "/nowhere"}


def node(number, labels=(), body="text", state="OPEN"):
    return {"number": number, "databaseId": number, "title": f"title {number}", "url": f"https://x/{number}", "body": body, "state": state,
            "stateReason": None, "createdAt": "2026-11-01T00:00:00Z", "updatedAt": None, "closedAt": None, "author": {"login": "own"},
            "labels": {"nodes": [{"name": l} for l in labels]}, "assignees": {"nodes": []}, "parent": None,
            "blockedBy": {"nodes": []}, "comments": {"nodes": []}, "timelineItems": {"nodes": []}}


class Run:
    def __init__(self, nodes=(), issue_url="https://github.com/own/cockpit/issues/42"):
        self.nodes, self.calls, self.url = list(nodes), [], issue_url

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:3] == ["gh", "api", "graphql"]:
            return json.dumps({"data": {"repository": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": self.nodes}}}})
        if args[:3] == ["gh", "issue", "create"]:
            return self.url + "\n"
        return ""


def test_list_all_and_by_state():
    run = Run([node(1), node(2, ["merged"]), node(3, ["wip"])])
    r = T.list_tasks(PROJECT, run=run)
    assert r["ok"] and [t["id"] for t in r["data"]["tasks"]] == ["PC-1", "PC-2", "PC-3"]
    assert {t["id"]: t["stage"] for t in r["data"]["tasks"]} == {"PC-1": "embryo", "PC-2": "merged", "PC-3": "wip"}
    r = T.list_tasks(PROJECT, "wip", run=run)
    assert [t["id"] for t in r["data"]["tasks"]] == ["PC-3"]


def test_list_unknown_state_is_refused_without_a_call():
    run = Run()
    r = T.list_tasks(PROJECT, "bogus", run=run)
    assert not r["ok"] and "unknown state" in r["error"] and run.calls == []


def test_list_failure_is_a_result():
    def boom(args):
        raise RuntimeError("no network")
    r = T.list_tasks(PROJECT, run=boom)
    assert not r["ok"] and "no network" in r["error"]


def test_show_returns_text():
    r = T.get_task(PROJECT, "PC-2", run=Run([node(1), node(2, body="the work")]))
    assert r["ok"] and r["data"]["id"] == "PC-2" and r["data"]["text"] == "the work" and r["data"]["url"] == "https://x/2"


def test_show_missing_and_foreign_id():
    run = Run([node(1)])
    assert "no task PC-9" in T.get_task(PROJECT, "PC-9", run=run)["error"]
    assert "not a task of PC" in T.get_task(PROJECT, "XX-1", run=run)["error"]


def test_short_defaults_to_repository_name():
    r = T.list_tasks({"repo": "own/cockpit"}, run=Run([node(1)]))
    assert r["data"]["tasks"][0]["id"] == "cockpit-1"


def test_file_creates_an_embryo():
    run = Run()
    r = T.file_task(PROJECT, " A title ", "body", ["bug", "module:x"], run=run)
    assert r["ok"] and r["data"] == {"id": "PC-42", "number": 42, "url": "https://github.com/own/cockpit/issues/42"}
    c = run.calls[0]
    assert c[:5] == ["gh", "issue", "create", "--repo", "own/cockpit"] and "A title" in c and c.count("--label") == 2


def test_file_refuses_privileged_labels_and_empty_title():
    run = Run()
    for lab in ("released", "P1", "cancelled", "due:2026-12-01", "wip", "merged"):
        r = T.file_task(PROJECT, "t", "b", [lab], run=run)
        assert not r["ok"] and lab in r["error"]
    assert not T.file_task(PROJECT, "  ", "b", run=run)["ok"]
    assert run.calls == []


def test_comment():
    run = Run()
    r = T.comment(PROJECT, "PC-7", "hello", run=run)
    assert r["ok"] and r["data"]["number"] == 7
    assert run.calls == [["gh", "issue", "comment", "7", "--repo", "own/cockpit", "--body", "hello"]]


def test_comment_refusals():
    run = Run()
    assert not T.comment(PROJECT, "PC-7", " ", run=run)["ok"]
    assert not T.comment(PROJECT, "XX-7", "hi", run=run)["ok"]
    assert run.calls == []


def test_no_release_cancel_or_priority_function():
    assert not [n for n in dir(T) if any(w in n for w in ("release", "cancel", "priority"))]
