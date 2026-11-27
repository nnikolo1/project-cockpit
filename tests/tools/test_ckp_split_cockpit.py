import ast
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "ckp_split"))
import split_cockpit as S  # noqa: E402

SRC = '''"""doc"""
import json
import time

log = None
HERE = 1
LIMIT = 5


def helper(x):
    return json.dumps(L.fmt(x))


def use(p):
    helper = 3          # a local that shadows a name of another module
    return helper + LIMIT


class H:
    def do_GET(self):
        if self.path == "/a":
            return helper(1)
        return self.path.startswith("/b/")

    def do_POST(self):
        return self.path in ("/c",)
'''
ASSIGN = {"one": "LIMIT use", "two": "helper", "@server/web": "H"}
CORE = {"fmt": "common"}


def test_every_name_is_placed_in_exactly_one_target():
    own = S.owners(ASSIGN)
    units = S.parse_units(SRC, own)
    names = [n for ns, *_ in units for n in ns]
    assert S.unassigned(units, own) == [] and sorted(set(names) - {"log", "HERE"}) == sorted(own)
    files, report = S.split(SRC, own, CORE)
    assert report == [] and set(files) == {"one", "two", "@server/web"}
    defined = [n.name for t in files.values() for n in ast.parse(t).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    assert sorted(defined) == ["H", "helper", "use"]


def test_a_name_assigned_twice_and_a_name_without_target_are_reported():
    with pytest.raises(ValueError):
        S.owners({"a": "x", "b": "x"})
    own = S.owners({"one": "use"})
    assert S.unassigned(S.parse_units(SRC, own), own) == ["LIMIT", "helper", "H"]


def test_cross_module_names_are_qualified_and_local_shadows_are_left_alone():
    files, _ = S.split(SRC, S.owners(ASSIGN), CORE)
    assert "api_one.LIMIT" not in files["one"] and "helper = 3" in files["one"] and "return helper + LIMIT" in files["one"]
    assert "core_common.fmt(x)" in files["two"] and "from ckp.core import common as core_common" in files["two"]
    assert "api_two.helper(1)" in files["@server/web"] and "from ckp.api import two as api_two" in files["@server/web"]


def test_routing_table_check_names_every_path_of_the_old_handlers():
    assert S.check_routes(SRC, '"/a" "/b/" "/c"') == []
    assert S.check_routes(SRC, '"/a" "/c"') == ["/b/"]


def test_shim_check_lists_the_target_modules_it_does_not_search():
    own = S.owners(ASSIGN)
    assert S.check_shim(own, ["ckp.api.one", "ckp.api.two", "ckp.server.web"]) == []
    assert S.check_shim(own, ["ckp.api.one"]) == ["ckp.api.two", "ckp.server.web"]


def test_the_real_assignment_is_consistent_and_the_routing_table_and_shim_cover_it():
    own = S.owners()
    assert len(own) > 150
    shim = ast.parse((ROOT / "cockpit.py").read_text())
    mods = next(n.value for n in shim.body if isinstance(n, ast.Assign) and n.targets[0].id == "_MODULES")
    assert S.check_shim(own, [e.value for e in mods.elts]) == []
    routes = (ROOT / "ckp" / "server" / "routes.py").read_text()
    assert all(f'"{p}"' in routes for p in ("/health", "/api/state", "/api/act", "/api/event", "/compliance-report"))
