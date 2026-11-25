"""Editing the text of a task in the Cockpit: the save call, the kept markers, the WIP warning, the stale authority analysis."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest
import cockpit as C
import logic as L

M, R = "mgr", "o/r"
ORIGIN = "<details><summary>Origin (turns)</summary>\n\nRequest: do it.\n</details>"
MARK = "<!-- filed-by: agent-7 -->"
BODY = f"Old text.\n\n{ORIGIN}\n\n{MARK}\n"


def iss(n, **kw):
    d = dict(number=n, id=1000 + n, title=f"t{n}", url=f"https://x/issues/{n}", state="open", state_reason=None, assignees=[], created_at="2026-01-01T00:00:00Z",
             closed_at=None, labels=[], parent=None, blocked_by=[], body=BODY, author=M, comments=[])
    d.update(kw)
    return d


def state(*issues):
    tasks, modules = L.derive(list(issues), [], {}, M, [], grow=False)
    return dict(tasks=tasks, modules=modules, modDeadline={}, proposals=[])


# 1 embryo, 2 WIP, 3 closed, 4 with a key in its title
S = state(iss(1), iss(2, assignees=["w"]), iss(3, state="closed", state_reason="completed", closed_at="2026-01-02T00:00:00Z"), iss(4, title="[PC-4] keyed"))


@pytest.fixture
def steer(monkeypatch):
    monkeypatch.setitem(C.CFG, "steer", True)
    monkeypatch.setitem(C.CFG, "simulate", False)


def save(form, S=S):
    """Run the edit through do_act with an injected hosting-service function; -> (status, body, the calls it made)."""
    calls = []
    code, out = C.do_act(dict(action="edit", **form), run=lambda op: calls.append(op) or "", state=S, p=dict(repo=R))
    return code, out, calls


def fields(call):
    return dict(call[i + 1].split("=", 1) for i, x in enumerate(call) if x == "-f")


def test_split_and_join_keep_the_provenance_block_and_the_filed_by_marker():
    text, kept = L.split_text(BODY)
    assert text == "Old text." and kept == [ORIGIN, MARK]
    assert L.split_text(L.join_text("New.", kept)) == ("New.", kept)
    assert L.split_text("") == ("", [])


@pytest.mark.ui_action("server:edit")
def test_save_carries_title_and_body_and_the_markers_survive(steer):
    code, out, calls = save(dict(n=1, title="New title", body="New text."))
    assert code == 200 and calls[0][:4] == ["api", "-X", "PATCH", f"repos/{R}/issues/1"]
    f = fields(calls[0])
    assert f["title"] == "New title" and f["body"].startswith("New text.") and ORIGIN in f["body"] and MARK in f["body"]


def test_markers_come_from_the_issue_not_the_form(steer):
    _, _, calls = save(dict(n=1, title="t1", body=f"New.\n\n<!-- filed-by: forged -->"))
    body = fields(calls[0])["body"]
    assert "forged" not in body and body.count(MARK) == 1 and ORIGIN in body


def test_labels_are_not_touched_and_a_key_prefix_is_kept(steer):
    _, _, calls = save(dict(n=4, title="renamed", body="x"))
    assert fields(calls[0])["title"] == "[PC-4] renamed"
    assert not any("--add-label" in c or "--remove-label" in c for c in calls)


def test_a_wip_task_warns_that_the_running_worker_keeps_the_old_text(steer):
    code, out, _ = save(dict(n=2, title="t2", body="changed"))
    assert code == 200 and "Worker is working from the old text" in out["msg"]
    assert "Worker" not in save(dict(n=1, title="t1", body="changed"))[1]["msg"]


def test_a_closed_task_is_editable_but_marked(steer):
    code, out, calls = save(dict(n=3, title="t3", body="changed"))
    assert code == 200 and calls and "closed" in out["msg"]


def test_a_saved_edit_marks_the_analysis_stale_and_release_is_refused(steer):
    _, _, calls = save(dict(n=1, title="t1", body="changed"))
    comment = calls[1]
    assert comment[:3] == ["issue", "comment", "1"] and L.analysis_state([dict(body=comment[-1], t="2")]) == "stale"
    S2 = state(iss(1, comments=[dict(body="<!-- analysis: clear 2026-01-01 -->", t="1"), dict(body=comment[-1], t="2")]))
    assert S2["tasks"][0]["analysis"] == "stale"
    with pytest.raises(ValueError, match="stale"):
        C.plan_action(dict(action="release", n=1, p="2"), S2, R)
    S3 = state(iss(1, comments=[dict(body=comment[-1], t="1"), dict(body="<!-- analysis: clear 2026-01-02 -->", t="2")]))
    assert S3["tasks"][0]["analysis"] == "clear" and C.plan_action(dict(action="release", n=1, p="2"), S3, R)[0]


@pytest.mark.ui_action("server:edit/refused")
def test_nothing_changed_writes_nothing_and_an_empty_title_is_refused(steer):
    code, out, calls = save(dict(n=1, title="t1", body="Old text."))
    assert code == 200 and calls == [] and "nothing changed" in out["msg"]
    assert save(dict(n=1, title="  ", body="x"))[0] == 400


def test_read_only_refuses_the_edit(monkeypatch):
    monkeypatch.setitem(C.CFG, "steer", False)
    code, _, calls = save(dict(n=1, title="x", body="y"))
    assert code == 403 and calls == []


def test_the_page_has_the_editor_and_the_edited_mark():
    page = (pathlib.Path(__file__).parent.parent / "web" / "index.html").read_text()
    for s in ('data-form="edit"', "action:'edit'", ">edited '+", "re-attached on save", "does not reach that run", "editedChip(t)+'</td>"):
        assert s in page, s
