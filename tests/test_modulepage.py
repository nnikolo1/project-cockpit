import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import logic as L

ROOT = pathlib.Path(__file__).parent.parent
ASSIGN = [dict(path="app/", module="app"), dict(path="app/ui/", module="app/ui"), dict(path="lib/", module="lib"), dict(path="tests/app/", module="app")]


def test_module_stem_and_unsafe_ids():
    assert L.module_stem("a/b") == "a-b" and L.module_stem("a") == "a"
    assert L.module_stem("../x") is None and L.module_stem("A") is None and L.module_stem("") is None and L.module_stem(None) is None


def test_paragraphs_fall_back_to_desc():
    assert L.module_paragraphs("One\nline.\n\n\nTwo.\n", "d") == ["One line.", "Two."]
    assert L.module_paragraphs(None, "d") == ["d"] and L.module_paragraphs("  \n", None) == []


def test_hl_ids_keeps_only_ids():
    assert L.hl_ids("wf-fire\n\n# note\n  wf-disk  \nbad id\nfire\n") == ["wf-fire", "wf-disk"]


GOOD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" class="x"><defs><marker id="wa" orient="auto"><path d="M0 0L1 1"/></marker></defs><rect class="box" x="1" y="1" width="2" height="2" rx="1"/><path class="ar" d="M0 0H5" marker-end="url(#wa)"/><text x="1" y="2">a &amp; b<tspan x="1" dy="16">c</tspan></text></svg>'


def test_sanitiser_keeps_a_drawing_and_forces_the_wf_class():
    out = L.sanitise_svg(GOOD)
    assert out.startswith('<svg viewBox="0 0 10 10" class="wf">') and 'marker-end="url(#wa)"' in out
    assert "a &amp; b" in out and out.count("<rect") == 1 and "xmlns" not in out


def test_sanitiser_strips_scripts_links_and_handlers():
    bad = '<svg><script>alert(1)</script><a href="http://x"><rect x="1" onclick="f()" class="box"/></a><image href="http://x"/><foreignObject/>' \
          '<path d="M0 0" marker-end="url(http://evil/x)" style="fill:url(http://e)"/><rect x="javascript:1"/></svg>'
    out = L.sanitise_svg(bad)
    for w in ("script", "href", "onclick", "image", "foreignObject", "http", "javascript", "<a"):
        assert w not in out
    assert "<path" in out  # the element stays, its unsafe attributes go


def test_sanitiser_refuses_unusable_input():
    assert L.sanitise_svg("<html/>") is None and L.sanitise_svg("<svg>") is None and L.sanitise_svg(None) is None
    assert L.sanitise_svg('<!DOCTYPE svg [<!ENTITY a "b">]><svg/>') is None
    assert L.sanitise_svg("<svg>" + " " * L.SVG_MAX + "</svg>") is None


def test_group_files_by_folder_with_counts_and_cap():
    g = L.group_files(["a/x.py", "a/y.py", "b/z.py", "r.md"] + [f"c/{i}.py" for i in range(10)], limit=3)
    assert [x["dir"] for x in g] == [".", "a", "b", "c"]
    assert g[1] == dict(dir="a", count=2, names=["x.py", "y.py"]) and g[3]["count"] == 10 and len(g[3]["names"]) == 3


def test_module_files_includes_sub_modules_and_mirrored_tests():
    paths = ["app/a.py", "app/ui/b.py", "lib/c.py", "tests/app/test_a.py", "other.txt"]
    assert L.module_files(paths, ASSIGN, "app") == ["app/a.py", "app/ui/b.py", "tests/app/test_a.py"]
    assert L.module_files(paths, ASSIGN, "app/ui") == ["app/ui/b.py"]


def test_doc_modules_title_and_module_docs():
    d1 = "# Title one\n\nStatus: x\nModules: app, lib/sub\n"
    d2 = "Intro\n**Modules**: `app/ui`\n"
    assert L.doc_modules(d1) == ["app", "lib/sub"] and L.doc_modules(d2) == ["app/ui"] and L.doc_modules("# t\nno line") == []
    docs = L.module_docs({"docs/a.md": d1, "docs/b.md": d2, "docs/c.md": "# C\n"}, "app", "https://github.com/o/r")
    assert [d["path"] for d in docs] == ["docs/a.md", "docs/b.md"]       # a module lists the documents of its sub-modules too
    assert docs[0]["title"] == "Title one" and docs[1]["title"] == "b.md" and docs[0]["url"] == "https://github.com/o/r/blob/main/docs/a.md"
    assert [d["path"] for d in L.module_docs({"docs/a.md": d1}, "lib", "u")] == ["docs/a.md"] and L.module_docs({"docs/a.md": d1}, "lib/other", "u") == []


SRC = {
    "app/main.py": "import lib.util\nfrom lib import helper as h\nfrom app import ui\nimport os, json\n",
    "app/__init__.py": "",
    "app/ui/__init__.py": "from . import widget\nfrom .. import main\n",
    "app/ui/widget.py": "from lib.util import f\nimport nothing.here\n",
    "lib/__init__.py": "",
    "lib/util.py": "import app.main\n",
    "lib/helper.py": "def broken(:\n",
}


def test_import_edges_resolve_files_across_modules_only():
    e = L.import_edges(SRC, ASSIGN)
    pairs = {(x["src"], x["dst"]) for x in e}
    assert ("app/main.py", "lib/util.py") in pairs and ("app/main.py", "lib/helper.py") in pairs    # from lib import helper -> lib/helper.py
    assert ("app/ui/widget.py", "lib/util.py") in pairs and ("lib/util.py", "app/main.py") in pairs
    assert ("app/main.py", "app/ui/__init__.py") in pairs                                          # a sub-module is another module
    assert ("app/ui/__init__.py", "app/main.py") in pairs                                          # a relative import resolves too
    assert not any(x["src_mod"] == x["dst_mod"] for x in e) and len(pairs) == len(e)
    assert not any(x["src"] == "lib/helper.py" for x in e)                                         # a file that does not parse is skipped


def test_module_relations_count_examples_and_scope():
    e = L.import_edges(SRC, ASSIGN)
    app = L.module_relations(e, "app")
    assert [r["module"] for r in app["uses"]] == ["lib"] and app["uses"][0]["count"] == 3 and len(app["uses"][0]["examples"]) == 3
    assert app["used_by"][0]["module"] == "lib" and app["used_by"][0]["examples"] == ["lib/util.py -> app/main.py"]   # app/ui inside app is not shown
    ui = L.module_relations(e, "app/ui")
    assert {r["module"] for r in ui["uses"]} == {"app", "lib"} and [r["module"] for r in ui["used_by"]] == ["app"]    # parent and siblings are shown
    assert L.module_relations(e, "nowhere") == dict(uses=[], used_by=[])


def test_real_module_files_use_ids_and_sanitise():
    mods = ROOT / "docs" / "modules"
    for f in sorted(mods.glob("*.svg")):
        assert L.sanitise_svg(f.read_text()), f"{f.name} is refused by the sanitiser"
        assert len(re.sub(r"<[^>]+>", " ", L.sanitise_svg(f.read_text())).split()) > 3
    for f in sorted(mods.glob("*.txt")):
        text = f.read_text()
        assert len(text.split()) < 250, f"{f.name}: 250 words or more"
        assert text.rstrip().rsplit("\n\n", 1)[-1].startswith("Relations"), f"{f.name}: the last paragraph is 'Relations'"


def test_every_highlighted_id_exists_in_the_workflow_drawing():
    page = (ROOT / "web" / "index.html").read_text()
    drawn = set(re.findall(r"G\('([a-z0-9-]+)'", page[page.index("function wfSvg(){"):page.index("function vHow(){")]))
    files = sorted((ROOT / "docs" / "modules").glob("*.hl"))
    assert files
    for f in files:
        named = [l.strip() for l in f.read_text().splitlines() if l.strip() and not l.startswith("#")]
        assert named and all(L.HLID.fullmatch(n) for n in named), f"{f.name}: lines that are not Workflow ids"
        missing = [n for n in named if n[3:] not in drawn]
        assert not missing, f"{f.name} names elements the Workflow no longer draws: {missing}"


def test_build_moduledoc_reads_content_next_to_the_map_without_a_clone(tmp_path):
    import json
    import cockpit
    (tmp_path / "modules").mkdir()
    (tmp_path / "map.json").write_text(json.dumps(dict(modules=[dict(id="a", name="A", desc="from the map"), dict(id="a/b", name="B")], assign=[])))
    (tmp_path / "modules" / "a-b.txt").write_text("Short.\n\nMore.\n")
    (tmp_path / "modules" / "a-b.hl").write_text("wf-fire\n")
    (tmp_path / "modules" / "a-b.svg").write_text('<svg><script/><rect x="1"/></svg>')
    p = dict(name="t", repo="o/r", modules=str(tmp_path / "map.json"))
    d = cockpit.build_moduledoc(p, "a/b")
    assert d["paragraphs"] == ["Short.", "More."] and d["hl"] == ["wf-fire"] and "script" not in d["svg"] and d["files"] is None and d["docs"] is None
    d = cockpit.build_moduledoc(p, "a")
    assert d["paragraphs"] == ["from the map"] and d["svg"] is None and d["hl"] == []
    try:
        cockpit.build_moduledoc(p, "../x")
        assert False
    except ValueError:
        pass


def test_file_kind_classifies_code_tests_and_other():
    assert [L.file_kind(x) for x in ("cockpit.py", "tests/test_x.py", "pkg/test_y.py", "a_test.go", "docs/a.md", "web/index.html", "x/tests/data.json")] == [
        "code", "test", "test", "test", "other", "other", "test"]


def test_biggest_files_takes_code_first_then_tests_then_other():
    sizes = {"a.py": 10, "b.py": 500, "c.py": 40, "tests/test_a.py": 900, "docs/x.md": 2000}
    assert [r["path"] for r in L.biggest_files(sizes)] == ["b.py", "c.py", "a.py"]
    assert [(r["path"], r["kind"]) for r in L.biggest_files({"b.py": 5, "tests/test_a.py": 9, "tests/test_b.py": 3, "docs/x.md": 2000})] == [
        ("b.py", "code"), ("tests/test_a.py", "test"), ("tests/test_b.py", "test")]
    assert [r["path"] for r in L.biggest_files({"b.py": 5, "docs/x.md": 7, "docs/y.md": 70})] == ["b.py", "docs/y.md", "docs/x.md"]
    assert L.biggest_files({}) == []
