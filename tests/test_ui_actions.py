"""Every UI action has a test (constitution, owner, 2026-11-25; PC-137). The actions are read from the code (tools/ui_actions.py: the server's
dispatch, the page's data-act names, click cases and form kinds); each is classified below; a test names the actions it exercises with
`@pytest.mark.ui_action(...)`. This fails for an action that is not classified, for an action no test names, for a state-changing action
without a test of its refusal, and for a marker that names an action the code no longer defines. A new action ships with its tests."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ui_actions as U  # noqa: E402

STATE, VIEW = "state-changing", "view-only"
# state-changing: it asks the server to change something (GitHub or a local file), or keeps a mark in the browser's storage;
# view-only: it changes only what the page shows (a view, a filter, the drawer, a prefilled form). Refusal tests are required for the
# state-changing ones that go to the server; the review marks (rev, unrev, revall) never meet a refusal.
KIND = {
    **{f"server:{a}": STATE for a in ("new", "moddl", "edit", "release", "change", "cancel", "block", "unblock", "accept", "dismiss", "take",
                                      "complete", "settings", "cardsettings", "compliance")},
    **{f"form:{a}": STATE for a in ("edit", "release", "change", "block", "settings", "cardsettings", "moddl", "new")},
    "form:alog": VIEW,   # the action log's filter (PC-132): a read of the log
    **{f"page:{a}": STATE for a in ("arm", "take", "complete", "unblock", "accept", "dismiss", "rev", "unrev", "revall", "retryact")},
    **{f"page:{a}": VIEW for a in ("go", "code", "codetab", "codem", "fit", "mod", "filter", "modf", "qproj", "tlm", "tlt", "rg", "setsec",
                                   "open", "close", "edit", "noedit", "new", "subtask", "followup", "hotspot", "refresh", "qtdefaults",
                                   "toastx", "notex", "problems")},
}
LOCAL = {"page:rev", "page:unrev", "page:revall"}   # state-changing in the browser only: no server, so no refusal


def test_the_code_defines_server_page_and_form_actions():
    ids = U.actions()
    assert {"server:release", "server:settings", "page:arm", "page:go", "form:new"} <= ids
    assert len([a for a in ids if a.startswith("server:")]) >= 15 and len([a for a in ids if a.startswith("page:")]) >= 30


def test_every_action_is_classified():
    ids = U.actions()
    assert sorted(ids - set(KIND)) == [], "classify the new action in KIND (state-changing or view-only) and write its tests"
    assert sorted(set(KIND) - ids) == [], "the code no longer defines these actions: drop them from KIND"


def test_every_action_is_named_by_a_test_and_every_state_change_by_a_refusal_test():
    refusable = {a for a, k in KIND.items() if k == STATE} - LOCAL
    assert U.gaps(state_changing=refusable) == []


def test_the_parser_reads_comparisons_handler_cases_and_markers(tmp_path):
    """The enumeration is generic: another project is a config with its own paths (a registry entry of the policy harness)."""
    (tmp_path / "srv.py").write_text('def go(form):\n    a = form.get("action")\n    if a == "one":\n        pass\n'
                                     '    if a in ("two", "three"):\n        pass\n    if form.get("action") == "four":\n        pass\n'
                                     'def other(a):\n    return a == "not-an-action"\n')
    (tmp_path / "ui.html").write_text("<button data-act=\"x\"></button><script>function act(a,ds){switch(a){case 'x':f({});break;case 'y':break;}}\n"
                                      "document.addEventListener('submit',function(e){if(kind==='z'){g({a:1});}});</script>")
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "test_ui.py").write_text('import pytest\n@pytest.mark.ui_action("server:one", "server:one/refused", "page:x", "gone:q")\n'
                                               'def test_a():\n    pass\n')
    cfg = dict(server="srv.py", dispatch=["go"], var="a", page="ui.html", click="function act(a,ds){",
               submit="document.addEventListener('submit'", tests=["t"])
    assert U.actions(tmp_path, cfg) == {"server:one", "server:two", "server:three", "server:four", "page:x", "page:y", "form:z"}
    assert U.covered(tmp_path, cfg) == {k: ["t/test_ui.py::test_a"] for k in ("server:one", "server:one/refused", "page:x", "gone:q")}
    assert U.gaps(tmp_path, cfg, state_changing={"server:one", "server:two"}) == [
        "form:z: no test names this action", "page:y: no test names this action", "server:four: no test names this action",
        "server:three: no test names this action", "server:two: no test names this action",
        "server:two: no test of its refusal (server:two/refused)",
        "gone:q: marker names an action the code does not define (t/test_ui.py::test_a)"]
    (tmp_path / "c.json").write_text(json.dumps(cfg))
    assert U.main(["--root", str(tmp_path), "--config", str(tmp_path / "c.json")]) == 1
    assert U.main(["--list"]) == 0   # this project: every action is named by a test
