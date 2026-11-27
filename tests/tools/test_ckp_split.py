import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools" / "ckp_split"))
import split_logic as S  # noqa: E402

SRC = '''"""doc"""
import re

# section a
A = 1


def f(x):
    return A + x


# section b
def g():
    return f(2) + B


B = 3
log = None
'''
RANGES = [(1, 9, "one"), (10, 30, "two")]
DOCS = {"one": "First.", "two": "Second."}


def units():
    return S.assign_themes(S.parse_units(SRC, header_end=2), RANGES, {})


def test_every_name_is_placed_in_exactly_one_theme():
    u = units()
    table = S.theme_table(u, ("one", "two", "LOG"))
    placed = [n for ns in table.values() for n in ns]
    assert sorted(placed) == sorted(["A", "f", "g", "B", "log"]) and len(placed) == len(set(placed))
    assert table["LOG"] == ["log"]


def test_a_name_without_a_theme_is_refused():
    with pytest.raises(ValueError):
        S.assign_themes(S.parse_units(SRC, header_end=2), [(1, 5, "one")], {})


def test_override_beats_the_section():
    u = S.assign_themes(S.parse_units(SRC, header_end=2), RANGES, {"B": "one"})
    assert {n: x["theme"] for x in u for n in x["names"]}["B"] == "one"


def test_cycles_are_detected_and_acyclic_order_is_clean():
    assert S.cycles(units()) == []   # only two -> one (g uses f)
    u = S.assign_themes(S.parse_units(SRC, header_end=2), RANGES, {"A": "two"})
    assert S.cycles(u) == ["one", "two"]


def test_generated_module_imports_what_it_uses():
    u = units()
    src = S.module_source(u, "two", DOCS)
    assert "from ckp.core.one import f\n" in src and "def g():" in src and src.startswith('"""Second."""')
    compile(src, "two.py", "exec")


def test_shim_exports_every_old_name():
    u = units()
    shim = S.shim_source(u, ("one", "two"))
    for n in ("A", "f", "g", "B"):
        assert f" {n}," in shim
    assert 'log = logging.getLogger("cockpit")' in shim
