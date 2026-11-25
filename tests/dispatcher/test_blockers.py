import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_tasks import Gh, make, node  # noqa: E402


def released(nodes):
    return {t.id: t for t in make(Gh(nodes)).released()}


def test_a_released_task_is_blocked_until_its_blocker_is_merged():
    for blocker_labels, blocker_state in ((["released"], "OPEN"), ([], "OPEN"), (["wip"], "OPEN"), (["unmerged"], "OPEN"), (["failed"], "OPEN")):
        got = released([node(1, ["released"], blocked=[2]), node(2, blocker_labels, state=blocker_state)])
        assert got["cockpit-1"].blocked, (blocker_labels, blocker_state)


def test_a_merged_blocker_releases_the_task():
    got = released([node(1, ["released"], blocked=[2]), node(2, ["merged"], state="CLOSED")])
    assert not got["cockpit-1"].blocked
    got = released([node(1, ["released"], blocked=[2]), node(2, [], state="CLOSED")])   # closed without a state label counts as merged
    assert not got["cockpit-1"].blocked


def test_all_blockers_must_be_merged():
    got = released([node(1, ["released"], blocked=[2, 3]), node(2, ["merged"], state="CLOSED"), node(3, ["wip"])])
    assert got["cockpit-1"].blocked


def test_the_dispatcher_never_chooses_a_blocked_task():
    import dispatcher.core as C
    from dispatcher.tasks import Task
    ts = [Task(id="c-1", repo="o/c", number=1, title="t", text="x", blocked=True, priority=1),
          Task(id="c-2", repo="o/c", number=2, title="t", text="x", priority=3)]
    chosen = C.choose_task(ts, set(), "worker1")
    assert chosen is not None and chosen.id == "c-2"
    assert C.choose_task(ts[:1], set(), "worker1") is None
