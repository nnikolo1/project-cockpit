import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_tasks import node  # noqa: E402

from dispatcher import tasks as T  # noqa: E402


def _repo_of(args):
    return next(a[2:] for a in args if a.startswith("r="))


class MultiGh:
    """gh over several repositories: repo name -> issue nodes; records every call; a repo in `fail` raises on listing."""
    def __init__(self, repos, fail=()):
        self.repos, self.fail, self.calls = repos, set(fail), []

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ["api", "graphql"]:
            repo = _repo_of(args)
            if repo in self.fail:
                raise RuntimeError("boom")
            return json.dumps({"data": {"repository": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": self.repos[repo]}}}})
        if args[:2] == ["issue", "view"]:
            return json.dumps({"labels": []})
        return ""

    def repos_queried(self):
        return {_repo_of(c) for c in self.calls if c[:2] == ["api", "graphql"]}


PROJECTS = [{"short": s, "repo": f"own/{s}-repo", "repo_dir": f"/repos/{s}"} for s in ("cockpit", "alpha", "beta")]


def make(repos, projects=PROJECTS, first=("cockpit",), fail=(), git=None):
    gh = MultiGh(repos, fail)
    return T.ProjectsTasks(projects, gh, git or (lambda a, c: ""), first=first), gh


def test_project_of_and_task_project_field():
    assert T.project_of("cockpit-44") == "cockpit" and T.project_of("my-proj-3") == "my-proj"
    assert T.Task("alpha-1", "o/r", 1, "t", "x").project == "alpha"


def test_released_carries_the_project():
    t, _ = make({"cockpit-repo": [node(1)], "alpha-repo": [node(2)], "beta-repo": []})
    assert {x.id: x.project for x in t.released()} == {"cockpit-1": "cockpit", "alpha-2": "alpha"}


def test_global_order_first_project_first_then_the_rule_without_round_robin():
    t, _ = make({"cockpit-repo": [node(1, ["P3"], events=["P3"]), node(2, ["P3"], events=["P3"])],
                 "alpha-repo": [node(1, ["P1"], events=["P1"]), node(2), node(3)],
                 "beta-repo": [node(1, ["P1", "must", "due:2026-12-01"], events=["P1", "due:2026-12-01"])]})
    ids = [x.id for x in t.released()]
    assert ids[:2] == ["cockpit-1", "cockpit-2"]                    # the first project, though its priority is worse
    assert ids[2:] == ["beta-1", "alpha-1", "alpha-2", "alpha-3"]   # then by the rule across projects, not alternating


def test_first_order_is_respected_and_unknown_first_is_refused():
    repos = {"cockpit-repo": [node(1)], "alpha-repo": [node(1)], "beta-repo": [node(1)]}
    t, _ = make(repos, first=("beta", "alpha"))
    assert [x.id for x in t.released()] == ["beta-1", "alpha-1", "cockpit-1"]
    with pytest.raises(ValueError):
        make(repos, first=("nope",))


def test_a_project_not_listed_is_never_queried_or_touched():
    repos = {"cockpit-repo": [node(1)], "alpha-repo": [node(1)], "beta-repo": [node(1)]}
    t, gh = make(repos, projects=PROJECTS[:1])
    assert [x.id for x in t.released()] == ["cockpit-1"] and gh.repos_queried() == {"cockpit-repo"}
    n = len(gh.calls)
    for call in (lambda: t.set_state("alpha-1", "wip"), lambda: t.fetch_branch("beta-1", "/b", "main"),
                 lambda: t.set_state("garbage", "wip"), lambda: t.set_state("", "wip")):
        with pytest.raises(ValueError):
            call()
    assert len(gh.calls) == n


def test_routing_by_task_id():
    fetched = []
    t, gh = make({"cockpit-repo": [], "alpha-repo": [], "beta-repo": []}, git=lambda a, c: fetched.append((a, c)) or "")
    t.set_state("alpha-7", "wip")
    assert any(c[:2] == ["issue", "edit"] and "own/alpha-repo" in c for c in gh.calls)
    assert not any("own/beta-repo" in c or "own/cockpit-repo" in c for c in gh.calls)
    t.fetch_branch("beta-3", "/bundle", "worker-x")
    assert fetched == [(["fetch", "/bundle", "+refs/heads/worker-x:refs/heads/worker/beta-3"], "/repos/beta")]


def test_a_failing_listing_does_not_hide_the_others(caplog):
    t, _ = make({"cockpit-repo": [node(1)], "alpha-repo": [node(1)], "beta-repo": [node(1)]}, fail=["alpha-repo"])
    with caplog.at_level("ERROR"):
        assert [x.id for x in t.released()] == ["cockpit-1", "beta-1"]
    assert "alpha" in caplog.text


def test_reconcile_wip_over_all_projects(caplog):
    repos = {"cockpit-repo": [node(5, ["wip"])], "alpha-repo": [node(6, ["wip"]), node(7, ["wip"])], "beta-repo": [node(8, ["wip"])]}
    t, _ = make(repos, fail=["beta-repo"])
    with caplog.at_level("ERROR"):
        done = t.reconcile_wip({"alpha-7"})
    assert sorted(d[0] for d in done) == ["alpha-6", "cockpit-5"]
    assert "beta" in caplog.text


def test_duplicate_project_is_refused():
    with pytest.raises(ValueError):
        T.ProjectsTasks(PROJECTS + PROJECTS[:1], lambda a: "", lambda a, c: "")
