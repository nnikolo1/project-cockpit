import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import logic as L
from test_logic import iss, derive

MODS = [dict(id="alpha", name="Alpha / core"), dict(id="alpha/one", name="One", desc="First part"), dict(id="alpha/two", name="Two"), dict(id="beta", name="Beta")]


def test_ids_parent_top_chain():
    assert (L.parent_id("alpha/one"), L.parent_id("alpha"), L.top_id("alpha/one"), L.top_id("beta")) == ("alpha", None, "alpha", "beta")
    assert L.chain("alpha/one/x") == ["alpha/one/x", "alpha/one", "alpha"] and L.chain("beta") == ["beta"]


def test_task_module_precedence():
    assert L.task_module("alpha", ["alpha/one"]) == "alpha/one"       # sub-label under the field's top
    assert L.task_module("", ["alpha/one"]) == "alpha/one"            # no field at all
    assert L.task_module("beta", ["alpha/one"]) == "beta"             # field wins over a foreign sub-label
    assert L.task_module("alpha", ["alpha"]) == "alpha" and L.task_module("", ["alpha"]) == "alpha"
    assert L.task_module("unassigned", ["alpha/one"]) == "unassigned" and L.task_module("", []) == "unassigned"


def test_resolve_module_sub_ids_and_top_names_only():
    assert L.resolve_module("alpha/one", MODS) == "alpha/one"
    assert L.resolve_module("One", MODS) == "unassigned"              # name prefixes match top-level modules only
    assert L.resolve_module("Alpha", MODS) == "alpha"
    grown = []
    assert L.resolve_module("alpha/one", grown, True) == "alpha/one" and L.resolve_module("Alpha / core", grown, True) == "alpha-core"


def test_derive_tree_counts_rollup_and_unsorted():
    issues = [iss(1, ["module:alpha/one"]), iss(2, ["module:alpha/two"], state="closed", reason="completed"), iss(3, ["module:alpha"]), iss(4), iss(5, ["module:alpha/one"])]
    project = {5: dict(module="Beta")}                                 # field says beta: the alpha/one label loses
    tasks, mods = derive(issues, [], project, MODS, grow=False)
    assert [t["module"] for t in tasks] == ["alpha/one", "alpha/two", "alpha", "unassigned", "beta"]
    m = {x["id"]: x for x in mods}
    assert (m["alpha"]["parent"], m["alpha"]["children"], m["alpha/one"]["parent"], m["alpha/one"]["children"]) == (None, ["alpha/one", "alpha/two"], "alpha", [])
    assert m["alpha"]["counts"]["embryo"] == 1 and m["alpha"]["rollup"]["embryo"] == 2 and m["alpha"]["rollup"]["merged"] == 1
    assert m["alpha/one"]["counts"] == m["alpha/one"]["rollup"] and m["alpha/one"]["desc"] == "First part"
    assert m["beta"]["rollup"]["embryo"] == 1 and m["unassigned"]["parent"] is None


def test_derive_flat_modules_have_no_children():
    _, mods = derive([iss(1, ["module:alpha"])], [], {}, [dict(id="alpha", name="Alpha")], grow=False)
    assert mods[0]["children"] == [] and mods[0]["parent"] is None and mods[0]["counts"] == mods[0]["rollup"]


def test_missing_parent_is_added_and_grown_sub_label():
    tasks, mods = derive([iss(1, ["module:gamma/deep"])])
    assert tasks[0]["module"] == "gamma/deep" and [m["id"] for m in mods] == ["gamma/deep", "gamma"]
    assert next(m for m in mods if m["id"] == "gamma")["children"] == ["gamma/deep"] and mods[1]["name"] == "Gamma"


def test_module_deadline_chain_with_source():
    dl = {"alpha": "2027-01-01", "alpha/one": "2026-12-01"}
    assert L.module_deadline(dl, "alpha/one") == ("2026-12-01", "alpha/one")   # the sub-module's own wins
    assert L.module_deadline(dl, "alpha/two") == ("2027-01-01", "alpha")       # else the module's
    assert L.module_deadline(dl, "alpha") == ("2027-01-01", "alpha") and L.module_deadline(dl, "beta") == (None, None)


def test_map_path_prefers_the_more_specific_sub_module():
    assign = [dict(path="src/", module="alpha"), dict(path="src/one", module="alpha/one")]
    assert (L.map_path("src/one/x.py", assign), L.map_path("src/other.py", assign), L.map_path("tests/src/one/test_x.py", assign)) == ("alpha/one", "alpha", "alpha/one")


def test_code_view_passes_sub_module_entries_through():
    m = dict(generated_at="t", git_commit_short="abc", modules={k: dict(name=k, metrics=dict(files_total=1, loc=dict(python_source=dict(code=n, files=1)))) for k, n in (("alpha", 30), ("alpha/one", 10))})
    v = L.code_view(m)["modules"]
    assert (v["alpha"]["code"], v["alpha/one"]["code"]) == (30, 10) and "alpha/two" not in v
