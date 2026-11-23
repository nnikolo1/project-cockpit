import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import check_brief as cb  # noqa: E402

ROWS = {"F-1", "F-2"}


def test_tagged_requirements_pass():
    brief = "## Goal\nx\n## Requirements\n- a [owner F-1]\n- b [approved F-2]\n"
    assert cb.problems(brief, ROWS) == []


def test_untagged_or_unknown_row_fails():
    brief = "## Requirements\n- a\n- b [owner F-9]\n"
    assert len(cb.problems(brief, ROWS)) == 2


def test_missing_section_fails():
    assert cb.problems("## Goal\nx\n", ROWS) == ["no '## Requirements' section"]
