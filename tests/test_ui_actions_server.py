"""Every server action through the one entry the page uses (do_act, PC-137): the happy path with the gh calls it makes and its message,
and the refusal for a wrong state or value with its status and error text. Nothing reaches GitHub: `run` records the calls."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest
import cockpit as C
import logic as L

M, R = "mgr", "o/r"
P = dict(name="proj", repo=R)
STALE = dict(body=C.STALE_NOTE, t="2026-01-02T00:00:00Z")


def iss(n, labels=(), **kw):
    d = dict(number=n, id=1000 + n, title=f"t{n}", url=f"https://x/issues/{n}", state="open", state_reason=None, assignees=[], created_at="2026-01-01T00:00:00Z",
             closed_at=None, labels=list(labels), parent=None, blocked_by=[], body="", author=M, comments=[])
    d.update(kw)
    return d


def ev(n, label):
    return dict(number=n, name=label, kind="labeled", actor=M, t="2026-01-01T00:00:00Z")


# 1 embryo, 2 released P2 + due (two proposals), 3 WIP, 4 merged, 6 embryo blocked by 1, 7 embryo with a stale analysis, 8 released blocked by WIP 3
_tasks, _mods = L.derive(
    [iss(1, ["module:alpha"]), iss(2, ["released", "P2", "due:2026-12-01"]), iss(3, ["released", "P1"], assignees=["w"]),
     iss(4, state="closed", state_reason="completed", closed_at="2026-01-02T00:00:00Z"), iss(6, blocked_by=[1]), iss(7, comments=[STALE]),
     iss(8, ["released", "P3"], blocked_by=[3])],
    [ev(2, "released"), ev(2, "P2"), ev(2, "due:2026-12-01"), ev(3, "released"), ev(3, "P1"), ev(8, "released"), ev(8, "P3")], {}, M,
    [dict(id="alpha", name="alpha")], grow=False)
S = dict(tasks=_tasks, modules=_mods, modDeadline={}, proposals=[dict(id="p1", n=2, key="#2", field="priority", value=1, reason="r"),
                                                               dict(id="p2", n=2, key="#2", field="deadline", value="2026-12-20", reason="r")])


@pytest.fixture(autouse=True)
def steer(monkeypatch):
    monkeypatch.setitem(C.CFG, "steer", True)
    monkeypatch.setitem(C.CFG, "simulate", False)


def act(**form):
    """-> (status, body, gh calls without the tolerated label creations)."""
    calls = []
    code, body = C.do_act(form, run=lambda op: calls.append(op) or "", state=S, p=P)
    return code, body, [c for c in calls if c[0] != "label"]


def refused(want, **form):
    code, body, calls = act(**form)
    assert (code, body.get("error"), calls) == (400, want, []), (code, body)


def test_fixture_stages():
    assert {t["number"]: t["stage"] for t in S["tasks"]} == {1: "embryo", 2: "released", 3: "wip", 4: "merged", 6: "embryo", 7: "embryo", 8: "released"}
    assert next(t for t in S["tasks"] if t["number"] == 7)["analysis"] == "stale"


@pytest.mark.ui_action("server:new", "server:new/refused")
def test_new():
    code, body, calls = act(action="new", title="Do it", desc="why", module="alpha", type="bug", cls="must")
    assert code == 200 and body["msg"] == "task filed as an embryo: Do it"
    assert calls == [["api", "-X", "POST", f"repos/{R}/issues", "-f", "title=Do it", "-f", "body=why", "-f", "labels[]=type:bug", "-f", "labels[]=must", "-f", "labels[]=module:alpha"]]
    refused("a title, a known type and must/nice are required", action="new", title=" ", type="bug", cls="must")
    refused("unknown module 'zzz'", action="new", title="x", type="bug", module="zzz")
    refused("no such task: '99'", action="new", title="x", type="bug", parent="99")


@pytest.mark.ui_action("server:moddl", "server:moddl/refused")
def test_moddl():
    code, body, calls = act(action="moddl", module="alpha", d="2026-12-31")
    assert code == 200 and body["msg"] == "module deadline of alpha set to 2026-12-31"
    assert calls == [["variable", "set", "MODULE_DEADLINES", "--body", '{"alpha": "2026-12-31"}', "-R", R]]
    refused("unknown module 'zzz'", action="moddl", module="zzz", d="2026-12-31")
    refused("bad date: '31.12.2026'", action="moddl", module="alpha", d="31.12.2026")


@pytest.mark.ui_action("server:edit", "server:edit/refused")
def test_edit():
    code, body, calls = act(action="edit", n=1, title="New title", body="New text.")
    assert code == 200 and body["msg"] == "#1 text saved; its authority analysis is now stale"
    assert calls[0][:4] == ["api", "-X", "PATCH", f"repos/{R}/issues/1"] and calls[1][-1] == C.STALE_NOTE
    refused("a title is required", action="edit", n=1, title="  ", body="x")
    refused("no such task: 99", action="edit", n=99, title="x", body="y")


@pytest.mark.ui_action("server:release", "server:release/refused")
def test_release():
    code, body, calls = act(action="release", n=1, p="P2", d="")
    assert code == 200 and body["msg"] == "#1 released as P2"
    assert calls == [["issue", "edit", "1", "-R", R, "--add-label", "released", "--add-label", "P2"]]
    refused("#2 is released; only embryos can be released", action="release", n=2, p="P2", d="")
    refused("#7 was edited; its authority analysis is stale and must be redone before release", action="release", n=7, p="P2", d="")
    refused("bad priority: 'P9'", action="release", n=1, p="P9", d="")
    refused("bad number: 'x'", action="release", n="x", p="P2")


@pytest.mark.ui_action("server:change", "server:change/refused")
def test_change():
    code, body, calls = act(action="change", n=2, p="1", d="")
    assert code == 200 and body["msg"] == "#2 updated"
    assert calls == [["issue", "edit", "2", "-R", R, "--add-label", "P1", "--remove-label", "P2", "--remove-label", "due:2026-12-01"]]
    refused("#1 is embryo; priority and deadline change only on released tasks", action="change", n=1, p="1")
    refused("bad date: 'soon'", action="change", n=2, d="soon")


@pytest.mark.ui_action("server:cancel", "server:cancel/refused")
def test_cancel():
    code, body, calls = act(action="cancel", n=1)
    assert code == 200 and body["msg"] == "#1 cancelled"
    assert calls == [["issue", "edit", "1", "-R", R, "--add-label", "cancelled"], ["issue", "close", "1", "-R", R, "--reason", "not planned"]]
    refused("#4 is merged", action="cancel", n=4)


@pytest.mark.ui_action("server:block", "server:block/refused")
def test_block():
    code, body, calls = act(action="block", n=2, b=3)
    assert code == 200 and body["msg"] == "#2 blocked by #3"
    assert calls == [["api", "-X", "POST", f"repos/{R}/issues/2/dependencies/blocked_by", "-F", "issue_id=1003"]]
    refused("#1 is already a blocker of #6", action="block", n=6, b=1)
    refused("cannot change blockers of this task", action="block", n=2, b=2)
    refused("cannot change blockers of this task", action="block", n=4, b=1)


@pytest.mark.ui_action("server:unblock", "server:unblock/refused")
def test_unblock():
    code, body, calls = act(action="unblock", n=6, b=1)
    assert code == 200 and body["msg"] == "#6 no longer blocked by #1"
    assert calls == [["api", "-X", "DELETE", f"repos/{R}/issues/6/dependencies/blocked_by/1001"]]
    refused("#3 is not a blocker of #2", action="unblock", n=2, b=3)


@pytest.mark.ui_action("server:accept", "server:accept/refused")
def test_accept():
    code, body, calls = act(action="accept", n=2, id="p1")
    assert code == 200 and body["msg"] == "proposal accepted on #2"
    assert calls[0] == ["issue", "edit", "2", "-R", R, "--add-label", "P1", "--remove-label", "P2"]
    assert calls[1][-1].startswith("<!-- pm:resolved p1 -->\nAccepted by the Manager: priority -> 1.")
    refused("no such open proposal", action="accept", n=2, id="zz")
    refused("no such open proposal", action="accept", n=1, id="p1")   # a proposal of another task


@pytest.mark.ui_action("server:dismiss", "server:dismiss/refused")
def test_dismiss():
    code, body, calls = act(action="dismiss", n=2, id="p2")
    assert code == 200 and body["msg"] == "proposal dismissed on #2"
    assert len(calls) == 1 and calls[0][:3] == ["issue", "comment", "2"] and "Dismissed by the Manager: deadline -> 2026-12-20." in calls[0][-1]
    refused("no such open proposal", action="dismiss", n=2, id="zz")


@pytest.mark.ui_action("server:take", "server:take/refused")
def test_take(monkeypatch):
    code, body, calls = act(action="take", n=2)
    assert (code, body["error"], calls) == (403, "simulation is off (start with --simulate-orchestrator)", [])
    monkeypatch.setitem(C.CFG, "simulate", True)
    code, body, calls = act(action="take", n=2)
    assert code == 200 and body["msg"] == "#2 WIP" and calls[0] == ["issue", "edit", "2", "-R", R, "--add-assignee", "@me"]
    refused("#3 is not a released, unblocked task", action="take", n=3)
    refused("#8 is not a released, unblocked task", action="take", n=8)   # released, but blocked by a WIP task


@pytest.mark.ui_action("server:complete", "server:complete/refused")
def test_complete(monkeypatch):
    assert act(action="complete", n=3)[0] == 403
    monkeypatch.setitem(C.CFG, "simulate", True)
    code, body, calls = act(action="complete", n=3)
    assert code == 200 and body["msg"] == "#3 completed" and calls[0] == ["issue", "close", "3", "-R", R, "--reason", "completed"]
    refused("#2 is not WIP", action="complete", n=2)


def test_every_action_is_refused_read_only_and_an_unknown_one_is_named(monkeypatch):
    refused("unknown action 'nope'", action="nope", n=1)
    monkeypatch.setitem(C.CFG, "steer", False)
    for a in ("new", "moddl", "edit", "release", "change", "cancel", "block", "unblock", "accept", "dismiss", "take", "complete",
              "settings", "cardsettings", "compliance"):
        code, body, calls = act(action=a, n=1)
        assert code == 403 and body["error"].startswith("read-only") and calls == [], a


def test_a_github_failure_is_502_with_its_text():
    code, body = C.do_act(dict(action="cancel", n=1), run=lambda op: (_ for _ in ()).throw(RuntimeError("gh down")), state=S, p=P)
    assert (code, body) == (502, dict(error="GitHub: gh down"))
