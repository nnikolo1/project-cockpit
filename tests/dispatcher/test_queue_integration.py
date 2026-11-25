import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_tasks import node  # noqa: E402

from dispatcher import core  # noqa: E402
from dispatcher import tasks as T  # noqa: E402


class TwoRepoGh:
    """A hosting-service fake that answers the listing for either repository and records every call."""

    def __init__(self, by_repo):
        self.by_repo, self.calls = by_repo, []

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ["api", "graphql"]:
            repo = next(a.split("=", 1)[1] for a in args if a.startswith("r="))
            return json.dumps({"data": {"repository": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                                                  "nodes": self.by_repo[repo]}}}})
        if args[:2] == ["issue", "view"]:
            return json.dumps({"labels": []})
        return ""


SETTINGS = {"projects": [{"short": "alpha", "repo": "own/alpha", "repo_dir": "/a"},
                         {"short": "beta", "repo": "own/beta", "repo_dir": "/b", "first": True}]}


def test_build_tasks_gives_the_real_adapter_that_lists_and_routes_across_projects():
    gh = TwoRepoGh({"alpha": [node(1, ["released"])], "beta": [node(2, ["released"])]})
    tasks, clones, first = core.build_tasks(SETTINGS, None, None, gh, lambda a, c: "")
    assert isinstance(tasks, T.ProjectsTasks) and clones == {"alpha": "/a", "beta": "/b"} and first == ("beta",)
    assert [t.id for t in tasks.released()] == ["beta-2", "alpha-1"]       # the first project leads
    tasks.set_state("alpha-1", "wip")
    assert any(c[:2] == ["issue", "edit"] and "own/alpha" in c for c in gh.calls)   # routed to the right repository


def test_one_listed_project_keeps_the_single_project_path():
    tasks, clones, first = core.build_tasks({"projects": SETTINGS["projects"][:1]}, None, None, TwoRepoGh({}), lambda a, c: "")
    assert isinstance(tasks, T.GitHubTasks) and clones is None and first == ()
