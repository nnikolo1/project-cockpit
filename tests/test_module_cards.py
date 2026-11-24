"""The module and sub-module cards carry no "Documents" and no "Open gaps" line (owner, 2026-11-24); the module page keeps its documents."""
import pathlib
import re

PAGE = (pathlib.Path(__file__).parent.parent / "web" / "index.html").read_text()


def cards_source():
    """The page script that builds the module cards: subBlock (sub-module cards) and vCockpit (module cards)."""
    start = PAGE.index("function subBlock(")
    return PAGE[start:PAGE.index("\nfunction ", PAGE.index("function vCockpit(") + 1)]


def test_no_documents_or_open_gaps_line_on_module_cards():
    src = cards_source()
    assert not re.search(r"Documents|Open gaps|ndocs|m\.gaps", src)
    assert "Code files / lines" in src  # the other card facts are still there


def test_module_page_keeps_its_documents():
    assert "sec('Documents'" in PAGE
