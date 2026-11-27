"""PC-132: the structured action log. Every action writes one line with its outcome; refused and failed actions carry their reason; a page's
failure is appended with its page id; tokens and task texts never reach the file; the file rotates keeping five; the view filters."""
import json
import pathlib
import re
import shutil
import stat
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import cockpit as C  # noqa: E402
from ckp.api import context as api_context, queue as api_queue
import logic as L  # noqa: E402
from test_actions import M, ev, iss, state  # noqa: E402

PAGE = (ROOT / "web" / "index.html").read_text()
P = dict(name="proj", repo="o/proj")
S = state([iss(1), iss(2, ["released", "P2"])], [ev(2, "released"), ev(2, "P2")])
PLANTED = dict(text="PLANTED-TASK-TEXT-7f3a", token="PLANTED-TOKEN-91bd", gh="ghp_PLANTEDghTokenValue123456")


@pytest.fixture
def steer(monkeypatch, tmp_path):
    monkeypatch.setattr(api_context, "DATA_DIR", tmp_path)
    monkeypatch.setitem(C.CFG, "steer", True)
    monkeypatch.setitem(C.CFG, "simulate", False)
    monkeypatch.setattr(C.H, "token", PLANTED["token"])
    monkeypatch.setattr(api_queue, "_version", ["abc1234"])
    return tmp_path / "proj" / "actions.jsonl"


def lines(f):
    return [json.loads(x) for x in f.read_text().splitlines()]


def act(form, run=lambda op: ""):
    return C.do_act(dict(form, page="pg-1"), run=run, state=S, p=P)


def test_an_action_writes_one_structured_line(steer):
    assert act(dict(action="release", n=1, p="1", d="2026-12-24"))[0] == 200
    [e] = lines(steer)
    assert {k: e[k] for k in ("source", "action", "task", "outcome", "page", "version")} == dict(
        source="server", action="release", task="#1", outcome="ok", page="pg-1", version="abc1234")
    assert e["params"] == dict(p="1", d="2026-12-24") and e["msg"].startswith("#1 released") and isinstance(e["ms"], int) and e["t"].endswith("+00:00")
    assert stat.S_IMODE(steer.stat().st_mode) == 0o600
    act(dict(action="cancel", n=2))
    assert [x["action"] for x in lines(steer)] == ["release", "cancel"]  # appended


def test_refused_and_failed_actions_are_logged_with_their_reason(steer, monkeypatch):
    assert act(dict(action="release", n=2, p="1"))[0] == 400

    def down(op):
        raise RuntimeError("gh: HTTP 502 'P2' not found")
    assert act(dict(action="cancel", n=1), run=down)[0] == 502

    def bug(op):
        raise KeyError("boom")
    assert act(dict(action="cancel", n=1), run=bug)[0] == 500  # an unexpected error is answered and logged, not dropped
    monkeypatch.setitem(C.CFG, "steer", False)
    assert act(dict(action="cancel", n=1))[0] == 403
    r, f, x, ro = lines(steer)
    assert (r["outcome"], f["outcome"], x["outcome"], ro["outcome"]) == ("refused", "failed", "failed", "refused")
    assert "only embryos" in r["msg"] and "'P2' not found" in f["msg"] and "boom" in x["msg"] and "read-only" in ro["msg"]


def test_texts_and_secrets_never_reach_the_log(steer, monkeypatch):
    monkeypatch.setattr(api_context, "PROJECTS", {"proj": P})
    monkeypatch.setattr(api_context, "DEFAULT", "proj")
    body = f"secret plan {PLANTED['text']} {PLANTED['token']}"
    act(dict(action="edit", n=1, title="New title", body=body, t=PLANTED["token"]))
    act(dict(action="new", title="A task", desc=PLANTED["text"], type="feature", cls="nice"))

    def leak(op):
        raise RuntimeError(f"gh failed with token {PLANTED['gh']} and {PLANTED['token']} for ?t={PLANTED['token']}")
    act(dict(action="cancel", n=1), run=leak)
    C.page_events(dict(project="", events=[dict(kind="network", page="pg-2", msg=f"Failed to fetch {PLANTED['token']} {PLANTED['gh']}")]))
    raw = steer.read_text()
    for v in PLANTED.values():
        assert v not in raw
    edit, new, failed, page = lines(steer)
    assert failed["outcome"] == "failed" and page["source"] == "page" and "<secret>" in failed["msg"] and "<secret>" in page["msg"]
    assert edit["params"] == dict(title="New title", body_chars=len(body)) and new["params"]["desc_chars"] == len(PLANTED["text"])


def test_a_page_event_is_appended_with_its_page_id(steer, monkeypatch):
    monkeypatch.setattr(api_context, "PROJECTS", {"proj": P})
    monkeypatch.setattr(api_context, "DEFAULT", "proj")
    code, body = C.page_events(dict(project="proj", events=[
        dict(kind="network", page="pg-7", action="release", task="#1", msg="Failed to fetch", ms=12.4, at="2026-11-25T10:02:24Z"),
        dict(kind="script", page="pg-7", action="draw", task="Cockpit", msg="x is undefined"),
        dict(kind="bogus", page="pg-7"), dict(kind="script"), "not an object"]))
    assert code == 200 and body == dict(ok=True, logged=2, refused=3)
    n, s = lines(steer)
    assert (n["source"], n["kind"], n["outcome"], n["page"], n["task"], n["ms"]) == ("page", "network", "failed", "pg-7", "#1", 12)
    assert s["kind"] == "script" and s["outcome"] == "failed" and s["msg"] == "x is undefined"
    assert C.page_events(dict(project="proj", events=[]))[0] == 400
    assert C.page_events(dict(project="nope", events=[dict(kind="script", page="p")]))[0] == 404


def test_rotation_keeps_five_files(steer, monkeypatch):
    monkeypatch.setattr(api_queue, "ACTION_LOG_MAX", 600)
    for i in range(60):
        C.log_action(P, dict(t=str(i), action="release", task=f"#{i}", outcome="ok", pad="x" * 100))
    files = sorted(p.name for p in steer.parent.iterdir())
    assert files == ["actions.jsonl"] + [f"actions.jsonl.{i}" for i in range(1, 5)]
    assert all(stat.S_IMODE((steer.parent / f).stat().st_mode) == 0o600 for f in files)
    got = C.read_action_log(P)["events"]
    assert got[0]["t"] == "59" and [int(e["t"]) for e in got] == sorted((int(e["t"]) for e in got), reverse=True)  # newest first, across the files
    assert len(got) < 60  # the oldest went with the dropped sixth file


def test_an_offline_copy_writes_no_log(steer, monkeypatch):
    monkeypatch.setattr(api_context, "OFFLINE", dict(root="/x", created=0))
    C.log_action(P, dict(action="x"))
    assert not steer.exists()


def test_the_view_filters_newest_first_and_caps():
    evs = [dict(t=i, action="release" if i % 2 else "cancel", outcome="failed" if i % 3 == 0 else "ok", task=f"#{i % 5}") for i in range(500)]
    newest_first = [json.dumps(e) for e in reversed(evs)] + ["{broken"]
    assert len(L.filter_events(newest_first)) == 200 and L.filter_events(newest_first)[0]["t"] == 499
    got = L.filter_events(newest_first, action="release", outcome="failed", task="3")
    assert got and all(e["action"] == "release" and e["outcome"] == "failed" and e["task"] == "#3" for e in got)
    assert L.filter_events(newest_first, task="#3") == L.filter_events(newest_first, task="3")
    assert L.filter_events(newest_first, outcome="refused") == []


def test_the_view_is_served_filtered(steer):
    act(dict(action="release", n=1, p="1"))
    act(dict(action="release", n=2, p="1"))
    out = C.read_action_log(P, outcome="refused")
    assert [e["task"] for e in out["events"]] == ["#2"] and out["limit"] == 200


def test_the_page_reports_its_failures_and_shows_the_log():
    assert "page:PAGE_ID" in PAGE and "'/api/event'" in PAGE                      # actions carry the page id; failures go to the endpoint
    guard = PAGE[PAGE.index("/* safe-block:start"):PAGE.index("/* safe-block:end */")]
    assert "note('script'" in guard                                             # the block guard of PC-114 reports what it caught
    assert "addEventListener('error'" in PAGE and "addEventListener('unhandledrejection'" in PAGE
    assert "['actionlog','Action log']" in PAGE and "/api/actionlog" in PAGE
    assert re.search(r"!e\.logged", PAGE)                           # the server's own answers are not logged twice


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_page_keeps_events_while_the_server_is_unreachable():
    block = PAGE[PAGE.index("/* action-log:start"):PAGE.index("/* action-log:end */")]
    code = "let PROJ='proj',up=false,sent=[];globalThis.fetch=async(u,o)=>{if(!up)throw new TypeError('Failed to fetch');sent.push(JSON.parse(o.body));return {ok:true,status:200};};" + block + """
(async()=>{note('network','release','#1','Failed to fetch',5);note('script','draw','Cockpit','x');await new Promise(r=>setTimeout(r,10));
 if(PENDING.length!==2)throw new Error('not kept: '+PENDING.length);
 note('script','draw','Cockpit','x');if(PENDING.length!==2)throw new Error('not deduplicated');
 up=true;await flush();if(PENDING.length!==0||sent[0].events.length!==2||sent[0].events[0].page!==PAGE_ID)throw new Error('not sent');})()
 .catch(e=>{console.error(e);process.exit(1);});"""
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_endpoints_over_http(steer, monkeypatch):
    import threading
    import urllib.request
    monkeypatch.setattr(api_context, "PROJECTS", {"proj": P})
    monkeypatch.setattr(api_context, "DEFAULT", "proj")
    srv = C.Server(("127.0.0.1", 0), C.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, hdr = f"http://127.0.0.1:{srv.server_address[1]}", {"Cookie": "t=" + PLANTED["token"], "Content-Type": "application/json"}
    try:
        body = json.dumps(dict(project="proj", events=[dict(kind="network", page="pg-9", action="cancel", msg="Failed to fetch")])).encode()
        with urllib.request.urlopen(urllib.request.Request(base + "/api/event", body, hdr)) as r:
            assert json.load(r)["logged"] == 1
        with urllib.request.urlopen(urllib.request.Request(base + "/api/actionlog?project=proj&outcome=failed", headers=hdr)) as r:
            assert [e["page"] for e in json.load(r)["events"]] == ["pg-9"]
    finally:
        srv.shutdown()
        srv.server_close()
