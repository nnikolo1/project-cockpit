"""Editing the text of a task in the Cockpit: only in the pre-WIP states (embryo, released), the save call, the kept markers, the stale authority analysis."""
import sys, pathlib, json, re, shutil, subprocess
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
    """The derived state; every label counts as set by the manager (a labeled event each), so `released` holds."""
    ev = [dict(number=i["number"], actor=M, t="2026-01-01T00:00:00Z", kind="labeled", name=l) for i in issues for l in i["labels"]]
    tasks, modules = L.derive(list(issues), ev, {}, M, [], grow=False)
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


LABEL = dict(embryo=[], released=["released"], wip=["wip"], unmerged=["unmerged"], failed=["failed"], merged=["merged"], cancelled=["cancelled"])
CLOSED = dict(state="closed", state_reason="completed", closed_at="2026-01-02T00:00:00Z")


def one(st, **kw):
    """A state with one task #9 in the stage `st` (or a closed issue for "closed"), its body carrying the provenance block and the marker."""
    S1 = state(iss(9, **(CLOSED if st == "closed" else dict(labels=LABEL[st])), **kw))
    assert st == "closed" or S1["tasks"][0]["stage"] == st
    return S1


@pytest.mark.parametrize("st", ["embryo", "released"])
def test_a_pre_wip_task_saves_and_keeps_the_provenance_block_and_the_filed_by_marker(steer, st):
    code, out, calls = save(dict(n=9, title="New", body="New text."), one(st))
    body = fields(calls[0])["body"]
    assert code == 200 and "saved" in out["msg"] and body.startswith("New text.") and body.count(ORIGIN) == 1 and body.count(MARK) == 1


@pytest.mark.ui_action("server:edit/refused")
@pytest.mark.parametrize("st", ["wip", "unmerged", "failed", "merged", "cancelled", "closed"])
def test_the_save_is_refused_in_every_other_state(steer, st):
    code, out, calls = save(dict(n=9, title="New", body="New text."), one(st))
    assert code == 400 and calls == [] and "only embryo and released tasks can be edited" in out["error"]


def test_a_closed_issue_is_refused_even_with_a_pre_wip_label(steer):
    S1 = state(iss(9, labels=["released"], **CLOSED))
    assert save(dict(n=9, title="New", body="x"), S1)[0] == 400


def test_the_page_offers_edit_for_embryo_and_released_only():
    """The page's own `editable` rule, run in node over every stage; the drawer shows the button and the form only through it."""
    page = (pathlib.Path(__file__).parent.parent / "web" / "index.html").read_text()
    line = next(l for l in page.splitlines() if l.startswith("const editable="))
    assert "if(ST()&&editable(t))h+=ed?editForm(t):" in page and "const ed=ST()&&editable(t)&&UI.drawer.edit;" in page
    assert set(re.findall(r"t\.stage===?'(\w+)'", line)) == {"embryo", "released"} and "!t.closed" in line
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stages = ["embryo", "released", "wip", "unmerged", "failed", "merged", "cancelled"]
    cases = [dict(stage=x, closed=None) for x in stages] + [dict(stage="released", closed="2026-01-02")]
    js = line + "\nconsole.log(JSON.stringify(" + json.dumps(cases) + ".map(editable)))"
    got = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert dict(zip(stages, got)) == dict(embryo=True, released=True, wip=False, unmerged=False, failed=False, merged=False, cancelled=False)
    assert got[-1] is False


def test_the_wip_warning_flow_is_gone():
    page = (pathlib.Path(__file__).parent.parent / "web" / "index.html").read_text()
    assert "A Worker is working on this task." not in page and "nothing reopens it" not in page
    assert not hasattr(C, "WIP_NOTE")


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
    for s in ('data-form="edit"', "action:'edit'", ">edited '+", "re-attached on save", "editedChip(t)+'</td>"):
        assert s in page, s
