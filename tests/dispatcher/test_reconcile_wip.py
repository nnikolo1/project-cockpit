import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_tasks import Gh, make, node  # noqa: E402

import logic as L  # noqa: E402


def run(nodes, running=(), branches=()):
    gh = Gh(nodes, labels=["wip"])
    t = make(gh, git=lambda a, c: "abc" if a[-1].rsplit("/", 1)[-1] in branches or any(a[-1].endswith(b) for b in branches) else "")
    return t.reconcile_wip(set(running)), gh


def edits(gh):
    return [c for c in gh.calls if c[:2] == ["issue", "edit"]]


def test_wip_with_no_worker_and_no_branch_is_failed():
    done, gh = run([node(5, ["wip"])])
    assert done == [("cockpit-5", "wip with no Worker -> failed")]
    assert any("--add-label" in c and "failed" in c for c in edits(gh))


def test_wip_with_no_worker_but_a_branch_is_unmerged():
    done, gh = run([node(5, ["wip"])], branches=["cockpit-5"])
    assert done == [("cockpit-5", "wip with no Worker -> unmerged")]


def test_wip_held_by_a_running_worker_is_left_alone():
    done, gh = run([node(5, ["wip"])], running=["cockpit-5"])
    assert done == [] and edits(gh) == []


def test_closed_issue_loses_a_stale_wip_label():
    done, gh = run([node(7, ["wip", "released"], state="CLOSED")])
    assert done == [("cockpit-7", "stale wip label removed from a closed issue")]
    assert any("--remove-label" in c and "wip" in c for c in edits(gh))


def test_tasks_without_wip_are_untouched():
    done, gh = run([node(1, ["released"]), node(2, ["unmerged"])])
    assert done == [] and edits(gh) == []


def test_the_page_never_shows_a_closed_issue_as_wip():
    base = dict(number=1, labels=["wip"], assignees=[])
    assert L.stage(dict(base, state="closed"), set()) == "merged"
    assert L.stage(dict(base, state="open"), set()) == "wip"


def test_firing_reconciles_with_the_running_task_ids_and_survives_a_failure():
    from dispatcher.core import Dispatcher

    class Tasks:
        def __init__(self, fail=False):
            self.fail, self.seen = fail, None

        def reconcile_wip(self, running):
            self.seen = running
            if self.fail:
                raise RuntimeError("rate limited")
            return [("cockpit-5", "wip with no Worker -> failed")]

    class Log:
        def __init__(self):
            self.events = []

        def event(self, *a):
            self.events.append(a)

    d = Dispatcher.__new__(Dispatcher)
    d.tasks, d.plog = Tasks(), Log()
    d.reconcile({"workers": {"worker1": {"task_id": "cockpit-9"}}})
    assert d.tasks.seen == {"cockpit-9"} and d.plog.events == [("cockpit-5", "-", "wip with no Worker -> failed")]
    d.tasks = Tasks(fail=True)
    d.reconcile({"workers": {}})   # no exception
