import pathlib

PAGE = pathlib.Path(__file__).parent.parent / "web" / "index.html"
STATES = ["embryo", "released", "wip", "unmerged", "failed", "merged"]
OLD = ["orchestrator", "in progress", "completed"]


def drawing_text():
    t = PAGE.read_text()
    return t[t.index("function wfSvg(){"):t.index("/* ---------- drawer")].lower()


def test_workflow_is_current():
    d = drawing_text()
    missing = [s for s in STATES if s not in d]
    stale = [w for w in OLD if w in d]
    assert not missing and not stale, "the Workflow is outdated: redraw it (missing states: %s; old words: %s)" % (missing, stale)
