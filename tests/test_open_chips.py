"""PC-128: the Task Queue's module chips show the open issues of each module (sub-modules rolled up) with the breakdown by stage as
tooltip, instead of the code lines. Synthetic issues and a synthetic module map; no network."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import logic as L

PAGE = (pathlib.Path(__file__).parent.parent / "web" / "index.html").read_text()
MODS = [dict(id="alpha"), dict(id="alpha/one"), dict(id="alpha/two"), dict(id="beta"), dict(id="gamma"), dict(id="unassigned")]


def task(n, module, stage, labels=()):
    return dict(key=f"PJ-{n}", number=n, module=module, stage=stage, labels=list(labels))


TASKS = [
    task(1, "alpha", "embryo"), task(2, "alpha/one", "released"), task(3, "alpha/one", "wip"), task(4, "alpha/two", "unmerged"),
    task(5, "alpha/two", "failed"), task(6, "alpha", "embryo", ["question", "type:decision"]), task(7, "alpha/one", "merged"),
    task(8, "alpha", "cancelled"), task(9, "beta", "embryo"), task(10, "beta", "merged", ["question"]),  # an answered question: closed
    task(11, "unassigned", "released"), task(12, "unassigned", "embryo", ["question"]), task(13, "gamma", "merged"),
]


def test_counts_per_module_roll_up_the_sub_modules_and_skip_merged_and_cancelled():
    oc = L.open_counts(TASKS, MODS)
    assert oc["alpha"]["n"] == 6 and oc["alpha/one"]["n"] == 2 and oc["alpha/two"]["n"] == 2
    assert oc["beta"]["n"] == 1 and oc["gamma"] == dict(n=0, tip="no open issue")


def test_questions_count_and_have_their_own_kind():
    oc = L.open_counts(TASKS, MODS)
    assert oc["alpha"]["tip"] == "1 embryo, 1 released, 1 WIP, 1 unmerged, 1 failed, 1 question"
    assert oc["unassigned"] == dict(n=2, tip="1 released, 1 question")


def test_all_modules_counts_every_open_issue():
    oc = L.open_counts(TASKS, MODS)
    assert oc["all"]["n"] == 9 == sum(oc[m]["n"] for m in ("alpha", "beta", "gamma", "unassigned"))
    assert oc["all"]["tip"] == "2 embryo, 2 released, 1 WIP, 1 unmerged, 1 failed, 2 question"


def test_tooltip_text_follows_the_stage_order_and_omits_zeros():
    assert L.open_tip(dict(question=4, embryo=3, unmerged=2, wip=1, released=4)) == "3 embryo, 4 released, 1 WIP, 2 unmerged, 4 question"
    assert L.open_tip({}) == "no open issue"
    assert L.open_kind(task(1, "a", "failed")) == "failed" and L.open_kind(task(1, "a", "merged", ["question"])) is None


def test_a_task_without_a_module_counts_as_unassigned():
    oc = L.open_counts([dict(key="PJ-1", stage="wip", labels=[])], [dict(id="unassigned")])
    assert oc["unassigned"] == dict(n=1, tip="1 WIP") == oc["all"]


def test_a_module_prefix_is_not_a_parent():
    oc = L.open_counts([task(1, "alphabet", "embryo")], [dict(id="alpha"), dict(id="alphabet")])
    assert oc["alpha"]["n"] == 0 and oc["alphabet"]["n"] == 1


def test_the_page_shows_open_counts_on_the_task_queue_chips_only():
    fn = PAGE[PAGE.index("function modFilter("):PAGE.index("return h;}", PAGE.index("function modFilter("))]
    assert "oc=act==='modf'&&D&&D.openCounts" in fn        # only the Task Queue's chips (the Timeline and the Code tab keep lines)
    assert "' open</span>'" in fn and "(o.n?'':' muted')" in fn and "tip(v)" in fn
    assert "cnt(c.id)" in fn and "cnt(top)" in fn and "cnt('all')" in fn   # the sub-module row, the parent and all modules
    assert "lines(m)" in fn                                 # the other views keep the code lines
