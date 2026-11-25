import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import json
import pytest
import cockpit as C
import logic as L

M, R = "mgr", "o/r"


def iss(n, labels=(), **kw):
    d = dict(number=n, id=1000 + n, title=f"t{n}", url=f"https://x/issues/{n}", state="open", state_reason=None, assignees=[], created_at="2026-01-01T00:00:00Z",
             closed_at=None, labels=list(labels), parent=None, blocked_by=[], body="", author=M, comments=[])
    d.update(kw)
    return d


def ev(n, label, actor=M):
    return dict(number=n, name=label, kind="labeled", actor=actor, t="2026-01-01T00:00:00Z")


def state(issues, events, mods=("alpha",), deadlines=None, proposals=()):
    tasks, modules = L.derive(issues, events, {}, M, [dict(id=m, name=m) for m in mods], grow=False)
    return dict(tasks=tasks, modules=modules, modDeadline=deadlines or {}, proposals=list(proposals))


# 1 embryo, 2 released P2 + due, 3 WIP, 4 closed, 5 embryo with a FORGED released label, 6 plain embryo
S = state([iss(1), iss(2, ["released", "P2", "due:2026-12-01"]), iss(3, ["released", "P1"], assignees=["w"]), iss(4, state="closed", state_reason="completed"),
           iss(5, ["released"]), iss(6, ["module:alpha"], blocked_by=[1])],
          [ev(2, "released"), ev(2, "P2"), ev(2, "due:2026-12-01"), ev(3, "released"), ev(3, "P1"), ev(5, "released", "eve")],
          proposals=[dict(id="p1", n=2, key="#2", field="priority", value=1, reason="r"), dict(id="p2", n=2, key="#2", field="deadline", value="2026-12-20", reason="r")])
full = lambda form, **kw: C.plan_action(form, S, R, **kw)
plan = lambda form, **kw: ([o for o in full(form, **kw)[0] if o[0] != "label"], full(form, **kw)[1])   # without the label-create calls


@pytest.mark.ui_action("server:release")
def test_release_embryo_adds_released_priority_and_deadline():
    ops, msg = plan(dict(action="release", n=1, p="2", d="2026-12-24"))
    assert ops == [["issue", "edit", "1", "-R", R, "--add-label", "released", "--add-label", "P2", "--add-label", "due:2026-12-24"]]
    assert msg == "#1 released as P2, deadline 2026-12-24"
    assert plan(dict(action="release", n=1, p=3))[0] == [["issue", "edit", "1", "-R", R, "--add-label", "released", "--add-label", "P3"]]


@pytest.mark.ui_action("server:release/refused")
def test_release_only_embryos_and_valid_values():
    for n in (2, 3, 4):
        with pytest.raises(ValueError, match="only embryos"):
            plan(dict(action="release", n=n, p="1"))
    for bad in (dict(p="P9"), dict(p="1", d="2026-13-45"), dict(p="1", d="soon")):
        with pytest.raises(ValueError):
            plan(dict(action="release", n=1, **bad))
    with pytest.raises(ValueError, match="no such task"):
        plan(dict(action="release", n=99, p="1"))


def test_release_over_a_forged_released_label_removes_then_re_adds_it():
    assert plan(dict(action="release", n=5, p="1"))[0] == [["issue", "edit", "5", "-R", R, "--remove-label", "released"],
                                                          ["issue", "edit", "5", "-R", R, "--add-label", "released", "--add-label", "P1"]]


@pytest.mark.ui_action("server:change", "server:change/refused")
def test_change_priority_swaps_label_and_deadline_is_set_or_cleared():
    assert plan(dict(action="change", n=2, p="1", d="2026-12-24"))[0] == [["issue", "edit", "2", "-R", R, "--add-label", "P1", "--add-label", "due:2026-12-24", "--remove-label", "P2", "--remove-label", "due:2026-12-01"]]
    assert plan(dict(action="change", n=2, p="2", d=""))[0] == [["issue", "edit", "2", "-R", R, "--remove-label", "due:2026-12-01"]]
    assert plan(dict(action="change", n=2, p="2", d="2026-12-01"))[0] == []           # nothing to do
    assert plan(dict(action="change", n=3, p="3"))[0] == [["issue", "edit", "3", "-R", R, "--add-label", "P3", "--remove-label", "P1"]]
    with pytest.raises(ValueError, match="only on released"):
        plan(dict(action="change", n=1, p="1"))


@pytest.mark.ui_action("server:cancel", "server:cancel/refused")
def test_cancel_labels_then_closes_as_not_planned():
    assert plan(dict(action="cancel", n=1))[0] == [["issue", "edit", "1", "-R", R, "--add-label", "cancelled"], ["issue", "close", "1", "-R", R, "--reason", "not planned"]]
    with pytest.raises(ValueError):
        plan(dict(action="cancel", n=4))


@pytest.mark.ui_action("server:new", "server:new/refused")
def test_new_task_labels_and_parent_link_use_the_created_id():
    ops, _ = plan(dict(action="new", title="Do it", desc="why", module="alpha", type="bug", cls="must", parent="2"))
    assert ops == [["api", "-X", "POST", f"repos/{R}/issues", "-f", "title=Do it", "-f", "body=why", "-f", "labels[]=type:bug", "-f", "labels[]=must", "-f", "labels[]=module:alpha"],
                   ["api", "-X", "POST", f"repos/{R}/issues/2/sub_issues", "-F", "sub_issue_id={id}"]]
    assert full(dict(action="new", title="x", type="doc", cls="nice"))[0][0] == ["label", "create", "type:doc", "-R", R]
    for bad in (dict(title=""), dict(title="x", type="nope"), dict(title="x", cls="maybe"), dict(title="x", module="zzz"), dict(title="x", parent="99")):
        with pytest.raises(ValueError):
            plan(dict(action="new", **bad))


@pytest.mark.ui_action("server:block", "server:block/refused", "server:unblock", "server:unblock/refused")
def test_blockers_use_native_dependencies_with_the_blockers_database_id():
    assert plan(dict(action="block", n=2, b=3))[0] == [["api", "-X", "POST", f"repos/{R}/issues/2/dependencies/blocked_by", "-F", "issue_id=1003"]]
    assert plan(dict(action="unblock", n=6, b=1))[0] == [["api", "-X", "DELETE", f"repos/{R}/issues/6/dependencies/blocked_by/1001"]]
    for form in (dict(action="block", n=2, b=2), dict(action="block", n=6, b=1), dict(action="unblock", n=2, b=3), dict(action="block", n=4, b=1)):
        with pytest.raises(ValueError):
            plan(form)


@pytest.mark.ui_action("server:moddl", "server:moddl/refused")
def test_module_deadline_variable_merges_and_clears():
    S2 = dict(S, modDeadline={"beta": "2026-03-01"})
    assert C.plan_action(dict(action="moddl", module="alpha", d="2026-12-31"), S2, R)[0] == [["variable", "set", "MODULE_DEADLINES", "--body", '{"alpha": "2026-12-31", "beta": "2026-03-01"}', "-R", R]]
    assert C.plan_action(dict(action="moddl", module="alpha", d=""), S2, R)[0][0][4] == '{"beta": "2026-03-01"}'
    with pytest.raises(ValueError):
        plan(dict(action="moddl", module="zzz", d="2026-12-31"))
    with pytest.raises(ValueError):
        plan(dict(action="moddl", module="unassigned", d="2026-12-31"))


@pytest.mark.ui_action("server:accept", "server:accept/refused", "server:dismiss")
def test_accept_applies_as_manager_and_resolves_dismiss_only_resolves():
    ops, _ = plan(dict(action="accept", n=2, id="p1"))
    assert ops[0] == ["issue", "edit", "2", "-R", R, "--add-label", "P1", "--remove-label", "P2"]
    assert ops[1][:6] == ["issue", "comment", "2", "-R", R, "--body"] and ops[1][6].startswith("<!-- pm:resolved p1 -->")
    ops, _ = plan(dict(action="accept", n=2, id="p2"))
    assert ops[0] == ["issue", "edit", "2", "-R", R, "--add-label", "due:2026-12-20", "--remove-label", "due:2026-12-01"]
    ops, _ = plan(dict(action="dismiss", n=2, id="p1"))
    assert len(ops) == 1 and ops[0][1] == "comment"
    with pytest.raises(ValueError, match="no such open proposal"):
        plan(dict(action="accept", n=2, id="zz"))


@pytest.mark.ui_action("server:take", "server:take/refused", "server:complete", "server:complete/refused")
def test_simulation_actions_need_the_flag_and_the_right_stage():
    with pytest.raises(PermissionError):
        plan(dict(action="take", n=2))
    ops, _ = plan(dict(action="take", n=2), simulate=True)
    assert ops[0] == ["issue", "edit", "2", "-R", R, "--add-assignee", "@me"] and ops[1][1] == "comment"
    assert plan(dict(action="complete", n=3), simulate=True)[0][0] == ["issue", "close", "3", "-R", R, "--reason", "completed"]
    with pytest.raises(ValueError):
        plan(dict(action="take", n=3), simulate=True)       # already WIP
    with pytest.raises(ValueError):
        plan(dict(action="take", n=6), simulate=True)       # embryo
    with pytest.raises(ValueError):
        plan(dict(action="complete", n=2), simulate=True)   # not WIP


def test_due_labels_are_created_first():
    ops, _ = full(dict(action="release", n=1, p="1", d="2026-12-24"))
    assert ops[0] == ["label", "create", "due:2026-12-24", "-R", R] and len(ops) == 2


def test_execute_passes_the_created_id_to_the_next_call_and_tolerates_label_create_failures():
    calls = []

    def run(op):
        calls.append(op)
        if op[0] == "label":
            raise RuntimeError("already exists")
        return json.dumps({"id": 777}) if op[1] == "-X" and op[3].endswith("/issues") else ""
    C.execute([["label", "create", "x"], ["api", "-X", "POST", "repos/o/r/issues"], ["api", "-X", "POST", "repos/o/r/issues/2/sub_issues", "-F", "sub_issue_id={id}"]], run)
    assert calls[-1][-1] == "sub_issue_id=777"
    with pytest.raises(RuntimeError):
        C.execute([["issue", "edit"]], lambda op: (_ for _ in ()).throw(RuntimeError("boom")))


def test_read_only_guard_returns_403_and_runs_nothing(monkeypatch):
    monkeypatch.setitem(C.CFG, "steer", False)
    ran = []
    assert C.do_act(dict(action="release", n=1, p="1"), run=ran.append, state=S)[0] == 403 and ran == []


def test_do_act_status_codes(monkeypatch):
    monkeypatch.setitem(C.CFG, "steer", True)
    monkeypatch.setitem(C.CFG, "simulate", False)
    monkeypatch.setitem(C.CFG, "repo", R)
    ran = []
    assert C.do_act(dict(action="release", n=1, p="1"), run=lambda op: ran.append(op) or "", state=S)[0] == 200 and len(ran) == 1
    assert C.do_act(dict(action="release", n=2, p="1"), run=ran.append, state=S)[0] == 400
    assert C.do_act(dict(action="take", n=2), run=ran.append, state=S)[0] == 403
    def boom(op): raise RuntimeError("gh down")
    assert C.do_act(dict(action="cancel", n=1), run=boom, state=S)[0] == 502


def test_token_file_is_created_private_and_reused(tmp_path):
    p = tmp_path / "sub" / "token"
    t = C.load_token(str(p))
    assert t and C.load_token(str(p)) == t
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert C.load_token() != C.load_token()


def test_request_log_hides_the_token():
    assert C.redact('"GET /?t=abc_DEF-1 HTTP/1.1" 303 -') == '"GET /?t=<token> HTTP/1.1" 303 -'


def test_submit_button_is_release_as_p2_without_deadline():
    """The page's Submit column posts exactly {action: release, p: P2, d: ''}; Cancel posts {action: cancel}."""
    ops, msg = plan(dict(action="release", n=1, p="P2", d=""))
    assert ops == [["issue", "edit", "1", "-R", R, "--add-label", "released", "--add-label", "P2"]] and msg == "#1 released as P2"
    with pytest.raises(ValueError):
        plan(dict(action="release", n=2, p="P2", d=""))   # already released: a second Submit click is refused
    assert plan(dict(action="cancel", n=2))[0][-1][:3] == ["issue", "close", "2"]
