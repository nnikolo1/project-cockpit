import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import ckp_parity as P  # noqa: E402

SERVER = '''
class H:
    def do_GET(self):
        if u.path == "/health":
            pass
        if u.path in ("/api/a", "/api/b"):
            pass
        if u.path.startswith("/code/"):
            pass
    def do_POST(self):
        if u.path not in ("/api/act",):
            pass
        self.other(a)
def do_act(form):
    if a == "release":
        pass
    if a in ("take", "drop"):
        pass
    if form.get("action") == "new":
        pass
'''
PAGE = '''<button data-act="open"></button><script>function act(a,ds){switch(a){case 'go':break;}}
document.addEventListener('submit',e=>{if(kind==='edit'){}});</script>'''
CLI = '''
import argparse
def helper(sub):
    g = sub.add_parser("grp").add_subparsers(dest="s")
    g.add_parser("one")
    g.add_parser("two").add_argument("x")
def build_parser():
    p = argparse.ArgumentParser(prog="ckp")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("watch")
    d = sub.add_parser("deploy")
    helper(sub)
    return p
'''
CFG = {**P.CONFIG, "ui": dict(server="cockpit.py", dispatch=["do_act"], var="a", page="web/index.html", click="function act(a,ds){",
                              submit="document.addEventListener('submit'", tests=["tests"])}


def tree(tmp_path, logic="def foo():\n    pass\n\nclass Bar:\n    pass\n\ndef _private():\n    pass\n", mapping=None, files=None):
    r = tmp_path
    for name, text in {"logic.py": logic, "cockpit.py": SERVER, "web/index.html": PAGE, "ckp/cli.py": CLI, "dispatcher/__init__.py": "",
                       "dispatcher/core.py": "def f():\n    pass\n", "dispatcher/old.py": "from ckp.queue.old import *\n",
                       **(files or {})}.items():
        (r / name).parent.mkdir(parents=True, exist_ok=True)
        (r / name).write_text(text)
    if mapping is not None:
        (r / "docs").mkdir(exist_ok=True)
        (r / "docs/ckp-parity-map.json").write_text(json.dumps({"items": mapping}))
    return r


def ent(target, basis="because"):
    return {"target": target, "basis": basis}


def test_inventory_lists_every_kind(tmp_path):
    inv = P.inventory(tree(tmp_path), CFG)
    assert {i for i, k in inv.items() if k == "logic"} == {"logic:foo", "logic:Bar"}        # public def and class, not _private
    assert {i for i, k in inv.items() if k == "dispatcher"} == {"dispatcher:core", "dispatcher:old"}   # not __init__
    assert {i for i, k in inv.items() if k == "command"} == {"command:ckp watch", "command:ckp deploy", "command:ckp grp one", "command:ckp grp two"}


def test_routes_by_method_and_form(tmp_path):
    inv = P.inventory(tree(tmp_path), CFG)
    assert {i for i, k in inv.items() if k == "route"} == {"route:GET /health", "route:GET /api/a", "route:GET /api/b", "route:GET /code/*",
                                                          "route:POST /api/act"}


def test_actions_server_page_and_form(tmp_path):
    inv = P.inventory(tree(tmp_path), CFG)
    assert {i for i, k in inv.items() if k == "action"} == {"action:server:release", "action:server:take", "action:server:drop",
                                                            "action:server:new", "action:page:open", "action:page:go", "action:form:edit"}


def test_item_without_mapping_fails_check_and_names_it(tmp_path):
    r = tree(tmp_path, mapping={"logic:foo": ent("ckp/core/x")})
    bad = P.check(r, CFG)
    assert "logic:Bar: no mapping entry" in bad and not any(b.startswith("logic:foo") for b in bad)


def test_complete_mapping_passes_check(tmp_path):
    base = tree(tmp_path)
    mapping = {i: ent("ckp/core/x") for i in P.inventory(base, CFG)}
    (base / "docs").mkdir()
    (base / "docs/ckp-parity-map.json").write_text(json.dumps({"items": mapping}))
    assert P.check(base, CFG) == []


def test_bad_target_and_missing_basis_fail_check(tmp_path):
    r = tree(tmp_path, mapping={"logic:foo": {"target": "somewhere/else", "basis": "x"}, "logic:Bar": {"target": "ckp/core/x", "basis": " "}})
    bad = [b for b in P.check(r, CFG) if b.startswith("logic:")]
    assert any("logic:foo: target" in b for b in bad) and "logic:Bar: no basis" in bad


def _ported(tmp_path, logic, mapping, files=None):
    r = tree(tmp_path, logic=logic, mapping=mapping, files=files)
    return P.evaluate(r, CFG)[4]


def test_logic_ported_when_target_defines_it_and_old_is_only_an_import(tmp_path):
    ported = _ported(tmp_path, "from ckp.core.t import foo, Bar\n",
                     {"logic:foo": ent("ckp/core/t"), "logic:Bar": ent("ckp/core/t")},
                     {"ckp/core/t.py": "def foo():\n    pass\n\nclass Bar:\n    pass\n"})
    assert {"logic:foo", "logic:Bar"} <= ported


def test_logic_not_ported_while_the_old_name_is_still_a_definition(tmp_path):
    ported = _ported(tmp_path, "def foo():\n    pass\n", {"logic:foo": ent("ckp/core/t")}, {"ckp/core/t.py": "def foo():\n    pass\n"})
    assert "logic:foo" not in ported


def test_logic_not_ported_when_target_missing_or_lacks_the_name(tmp_path):
    shim = "from ckp.core.t import foo\n"
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert "logic:foo" not in _ported(tmp_path / "a", shim, {"logic:foo": ent("ckp/core/t")})
    assert "logic:foo" not in _ported(tmp_path / "b", shim, {"logic:foo": ent("ckp/core/t")}, {"ckp/core/t.py": "def other():\n    pass\n"})


def test_logic_renamed_target_uses_as(tmp_path):
    ported = _ported(tmp_path, "from ckp.core.t import foo2 as foo\n", {"logic:foo": {**ent("ckp/core/t"), "as": "foo2"}},
                     {"ckp/core/t.py": "def foo2():\n    pass\n"})
    assert "logic:foo" in ported


def test_logic_not_ported_when_target_has_the_name_but_not_as_a_callable_or_does_not_import(tmp_path):
    shim = "from ckp.core.t import foo\n"
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert "logic:foo" not in _ported(tmp_path / "a", shim, {"logic:foo": ent("ckp/core/t")}, {"ckp/core/t.py": "foo = 1\n"})
    assert "logic:foo" not in _ported(tmp_path / "b", shim, {"logic:foo": ent("ckp/core/t")},
                                      {"ckp/core/t.py": "import no_such_module_anywhere\ndef foo():\n    pass\n"})


def test_moved_item_stays_in_the_inventory_and_report_when_its_old_definition_is_gone(tmp_path):
    r = tree(tmp_path, logic="from ckp.core.t import foo\n", mapping={"logic:foo": ent("ckp/core/t")},
             files={"ckp/core/t.py": "def foo():\n    pass\n"})
    items, _, _, _, ported = P.evaluate(r, CFG)
    assert items["logic:foo"] == "logic" and "logic:foo" in ported
    assert any(line.startswith("logic") and " 1 " in line for line in P.report(r, CFG).splitlines())


def test_check_flags_a_moved_item_missing_from_its_target(tmp_path):
    r = tree(tmp_path, logic="", mapping={"logic:foo": ent("ckp/core/t")},
             files={"ckp/core/t.py": "x = 1\n", "ckp/core/u.py": "def foo():\n    pass\n"})
    bad = [b for b in P.check(r, CFG) if b.startswith("logic:foo")]
    assert len(bad) == 1 and "does not define" in bad[0] and "ckp/core/u" in bad[0]


def test_check_flags_a_stale_entry_that_exists_nowhere(tmp_path):
    r = tree(tmp_path, logic="", mapping={"logic:gone": ent("ckp/core/t"), "action:page:vanished": ent("ckp/server/pages")},
             files={"ckp/core/t.py": "def other():\n    pass\n", "ckp/server/pages.py": "x = 1\n"})
    bad = P.check(r, CFG)
    assert any(b.startswith("logic:gone: stale") for b in bad) and any(b.startswith("action:page:vanished: stale") for b in bad)


def test_check_flags_an_old_item_without_mapping_even_when_others_are_moved(tmp_path):
    r = tree(tmp_path, logic="def foo():\n    pass\n", mapping={"logic:moved": ent("ckp/core/t")},
             files={"ckp/core/t.py": "def moved():\n    pass\n"})
    bad = P.check(r, CFG)
    assert "logic:foo: no mapping entry" in bad and not any(b.startswith("logic:moved") for b in bad)


def test_dispatcher_shim_is_ported_a_real_module_is_not(tmp_path):
    ported = _ported(tmp_path, "", {"dispatcher:old": ent("ckp/queue/old"), "dispatcher:core": ent("ckp/queue/core")},
                     {"ckp/queue/old.py": "x = 1\n", "ckp/queue/core.py": "def f():\n    pass\n"})
    assert "dispatcher:old" in ported and "dispatcher:core" not in ported


def test_route_ported_when_target_names_it_and_old_server_no_longer_routes_it(tmp_path):
    r = tree(tmp_path, mapping={"route:GET /health": ent("ckp/server/h"), "route:GET /api/a": ent("ckp/server/h")},
             files={"ckp/server/h.py": 'ROUTES = ["/health", "/api/a"]\n'})
    (r / "cockpit.py").write_text(SERVER.replace('u.path == "/health"', 'u.path == "/zzz"'))
    ported = P.evaluate(r, CFG)[4]
    assert "route:GET /health" in ported             # the old server no longer routes it and the target names it
    assert "route:GET /api/a" not in ported          # still routed by the old server


def test_route_not_ported_when_target_lacks_the_path(tmp_path):
    r = tree(tmp_path, mapping={"route:GET /api/a": ent("ckp/server/h")}, files={"ckp/server/h.py": "x = 1\n"})
    (r / "cockpit.py").write_text("class H:\n    def do_GET(self):\n        pass\n")
    assert "route:GET /api/a" not in P.evaluate(r, CFG)[4]


def test_server_action_ported_only_when_target_names_it_and_old_dispatch_dropped_it(tmp_path):
    r = tree(tmp_path, mapping={"action:server:new": ent("ckp/server/actions"), "action:server:take": ent("ckp/server/actions")},
             files={"ckp/server/actions.py": 'A = ("new", "take")\n'})
    (r / "cockpit.py").write_text(SERVER.replace('form.get("action") == "new"', 'form.get("action") == "zzz"'))
    ported = P.evaluate(r, CFG)[4]
    assert "action:server:new" in ported and "action:server:take" not in ported


def test_page_actions_are_ported_when_their_target_exists(tmp_path):
    r = tree(tmp_path, mapping={"action:page:open": ent("ckp/server/pages"), "action:page:go": ent("ckp/server/pages")},
             files={"ckp/server/pages.py": "x = 1\n"})
    ported = P.evaluate(r, CFG)[4]
    assert {"action:page:open", "action:page:go"} <= ported
    (r / "ckp/server/pages.py").unlink()
    assert "action:page:open" not in P.evaluate(r, CFG)[4]


def test_command_is_ported_when_it_exists(tmp_path):
    ported = _ported(tmp_path, "", {"command:ckp watch": ent("ckp watch"), "command:ckp deploy": ent("ckp nonexistent")})
    assert "command:ckp watch" in ported and "command:ckp deploy" not in ported


def test_report_groups_unported_by_target_with_counts_and_decisions(tmp_path):
    r = tree(tmp_path, logic="def foo():\n    pass\n", mapping={"logic:foo": ent("ckp/core/t", "DECISION: two homes")})
    text = P.report(r, CFG)
    assert "ckp/core/t (1)" in text and "  logic:foo" in text and "1 placements marked DECISION" in text
    assert "no mapping entry:" in text and "route:GET /health" in text


def test_main_check_exit_codes(tmp_path, capsys):
    r = tree(tmp_path, mapping={})
    cfgfile = tmp_path / "cfg.json"
    cfgfile.write_text(json.dumps({"ui": CFG["ui"]}))
    assert P.main(["--check", "--root", str(r), "--config", str(cfgfile)]) == 1
    mapping = {i: ent("ckp/core/x") for i in P.inventory(r, CFG)}
    (r / "docs").mkdir(exist_ok=True)
    (r / "docs/ckp-parity-map.json").write_text(json.dumps({"items": mapping}))
    assert P.main(["--check", "--root", str(r), "--config", str(cfgfile)]) == 0
    assert P.main(["--report", "--root", str(r), "--config", str(cfgfile)]) == 0


def test_the_committed_mapping_covers_this_repository():
    assert P.check(P.ROOT) == []
