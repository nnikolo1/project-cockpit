"""PC-135: a state change made in the UI shows at once (the known effect laid on the cached state), only the touched issues are re-read from
GitHub (coalesced, within the hourly cap), a refused action changes nothing, and the page refreshes its tab and marks the others stale.
A fake GitHub (FakeHub) answers both the action calls and the re-read; the tests are generalized over every state-changing action."""
import json
import pathlib
import re
import sys
import time
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest
import cockpit as C
from ckp.api import code as api_code, context as api_context, gh as api_gh
import logic as L

M = "mgr"
P = dict(name="proj", repo="o/r", manager=M, project=None, git=None, code=None, modules=None, phases=None)
K = C.pk(P, "state")
PAGE = (pathlib.Path(__file__).parent.parent / "web" / "index.html").read_text()
T0 = "2026-01-01T00:00:00Z"


class FakeHub:
    """The hosting service: issues as GraphQL nodes, changed by the gh calls of the actions, read by the GraphQL re-read. Counts every call."""
    def __init__(self):
        self.nodes, self.calls, self.deadlines = {}, [], {}
        for n, labels, extra in ((1, [], {}), (2, ["released", "P2", "due:2026-12-01"], {}), (3, ["released", "P1"], {}), (4, [], {}),
                                 (5, ["released", "P3"], dict(blocked=[4])), (6, ["question"], {})):
            self.add(n, f"t{n}", "", labels, **extra)
        self.nodes[2]["comments"]["nodes"].append(dict(body='<!-- pm:proposal {"id":"p1","field":"priority","value":1,"reason":"r"} -->', createdAt=T0, author=dict(login="bot")))

    def add(self, n, title, body, labels, blocked=()):
        self.nodes[n] = dict(number=n, databaseId=1000 + n, title=title, url=f"https://x/{n}", body=body, state="OPEN", stateReason=None, createdAt=T0,
                             updatedAt=T0, lastEditedAt=None, closedAt=None, author=dict(login=M), labels=dict(nodes=[dict(name=l) for l in labels]),
                             assignees=dict(nodes=[]), parent=None, blockedBy=dict(nodes=[dict(number=b) for b in blocked]), comments=dict(nodes=[]),
                             timelineItems=dict(nodes=[dict(__typename="LabeledEvent", createdAt=T0, actor=dict(login=M), label=dict(name=l)) for l in labels]))

    def raw(self):
        return dict(issues=[L.norm_issue(n) for n in self.nodes.values()], events=[e for n in self.nodes.values() for e in L.norm_events(n)], project={})

    def run(self, op):
        """`gh` as the actions call it."""
        self.calls.append(op)
        now = "2026-06-01T00:00:00Z"
        lab = lambda n, name, kind: self.nodes[n]["timelineItems"]["nodes"].append(dict(__typename=kind, createdAt=now, actor=dict(login=M), label=dict(name=name)))
        if op[0] == "label":
            return ""
        if op[0] == "variable":
            self.deadlines = json.loads(op[op.index("--body") + 1])
            return ""
        if op[0] == "issue":
            x = self.nodes[int(op[2])]
            for k, v in zip(op, op[1:]):
                if k == "--add-label":
                    x["labels"]["nodes"] = [l for l in x["labels"]["nodes"] if l["name"] != v] + [dict(name=v)]
                    lab(x["number"], v, "LabeledEvent")
                if k == "--remove-label":
                    x["labels"]["nodes"] = [l for l in x["labels"]["nodes"] if l["name"] != v]
                    lab(x["number"], v, "UnlabeledEvent")
                if k == "--add-assignee":
                    x["assignees"]["nodes"].append(dict(login=M))
                if k == "--body" and op[1] == "comment":
                    x["comments"]["nodes"].append(dict(body=v, createdAt=now, author=dict(login=M)))
            if op[1] == "close":
                x.update(state="CLOSED", stateReason="NOT_PLANNED" if "not planned" in op else "COMPLETED", closedAt=now)
            return ""
        path, f = next(a for a in op if a.startswith("repos/")), dict(a.split("=", 1) for a in op if "=" in a)
        parts = path.split("/")
        if path.endswith("/issues"):
            n = max(self.nodes) + 1
            self.add(n, f["title"], f["body"], [a.split("=", 1)[1] for a in op if a.startswith("labels[]=")])
            return json.dumps(dict(number=n, id=1000 + n, html_url=f"https://x/{n}", created_at=now))
        x = self.nodes[int(parts[4])]
        if op[2] == "PATCH":
            x.update(title=f["title"], body=f["body"], lastEditedAt=now)
        elif parts[-1] == "sub_issues":
            self.nodes[int(f["sub_issue_id"]) - 1000]["parent"] = dict(number=x["number"])
        elif op[2] == "POST":
            x["blockedBy"]["nodes"].append(dict(number=int(f["issue_id"]) - 1000))
        else:
            x["blockedBy"]["nodes"] = [b for b in x["blockedBy"]["nodes"] if b["number"] != int(parts[-1]) - 1000]
        return ""

    def gh(self, args):
        """`gh api graphql` as the re-read calls it: answers every `iN:issue(number:N)` alias."""
        self.calls.append(args)
        q = next(a for a in args if a.startswith("query="))
        return json.dumps(dict(data=dict(repository={f"i{n}": self.nodes.get(int(n)) for n in re.findall(r"i(\d+):issue", q)})))


class Timers(list):
    def __call__(self, delay, fn, args):
        t = type("T", (), dict(start=lambda s: self.append((delay, fn, args))))()
        return t


@pytest.fixture
def hub(tmp_path, monkeypatch):
    for d in (C._cache, C._failures, C._reread):
        d.clear()
    C._gh_calls.clear()
    C._gh_pause.update(until=0.0)
    monkeypatch.setattr(api_context, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(api_context, "DATA_DIR", tmp_path / "data")
    monkeypatch.setitem(C.CFG, "steer", True)
    monkeypatch.setitem(C.CFG, "simulate", False)
    monkeypatch.setattr(api_code, "ordered", lambda p, mods: mods)
    h = FakeHub()
    h.timers = Timers()
    monkeypatch.setattr(C.threading, "Timer", h.timers)
    monkeypatch.setattr(api_gh, "run_gh", lambda args: (C._gh_gate(), h.gh(args))[1])
    C._cache[K] = (time.time(), C.state_from_raw(P, h.raw(), {}, dict(name="proj", repo="o/r", user=M, manager=M, fetchedAt=time.time() - 60, fetched="10:00:00")))
    yield h
    C._cache.clear()
    C._reread.clear()


def task(S, n):
    return next(t for t in S["tasks"] if t["number"] == n)


FIELDS = ("stage", "prio", "deadline", "title", "text", "blockedBy", "parent", "analysis", "flags", "labels")
ACTIONS = [  # (form, the issue it touches, what the page must show at once)
    (dict(action="release", n=1, p="P2", d=""), 1, dict(stage="released", prio=2)),
    (dict(action="release", n=1, p="1", d="2026-12-24"), 1, dict(stage="released", prio=1, deadline="2026-12-24")),
    (dict(action="cancel", n=3), 3, dict(stage="cancelled")),
    (dict(action="edit", n=1, title="new title", body="new text"), 1, dict(title="new title", text="new text", analysis="stale")),
    (dict(action="block", n=1, b=4), 1, dict(blockedBy=["#4"])),
    (dict(action="unblock", n=5, b=4), 5, dict(blockedBy=[])),
    (dict(action="change", n=2, p="1"), 2, dict(prio=1, deadline="2026-12-01")),
    (dict(action="change", n=2, d="2026-12-31"), 2, dict(prio=2, deadline="2026-12-31")),
    (dict(action="change", n=2, d=""), 2, dict(deadline=None)),
    (dict(action="accept", n=2, id="p1"), 2, dict(prio=1)),
    (dict(action="new", title="brand new", desc="why", type="bug", cls="must", parent="2"), 7, dict(stage="embryo", title="brand new", parent="#2", type="bug")),
]


@pytest.mark.parametrize("form,n,want", ACTIONS, ids=[f"{a[0]['action']}-{i}" for i, a in enumerate(ACTIONS)])
def test_every_action_shows_at_once_and_re_reads_only_its_issue(hub, form, n, want):
    code, body = C.do_act(dict(form), run=hub.run, p=P)
    assert code == 200 and body["issues"] == [n]
    acted = len(hub.calls)
    assert all(c[:2] != ["api", "graphql"] for c in hub.calls)                        # the action itself reads nothing
    now = task(body["state"], n)                                                     # 1. the page's copy, in the POST answer
    assert {k: now[k] for k in want} == want
    assert "raw" not in body["state"] and body["state"]["stale"] is True              # the page asks again for the re-read
    assert body["state"]["source"]["patchedAt"] and body["state"]["source"]["fetchedAt"] < body["state"]["source"]["patchedAt"]
    assert C.get_state_served(P)["tasks"] == body["state"]["tasks"]                  # every tab reads the same updated cache
    assert [t[1] for t in hub.timers] == [C._reread_safe]                            # 2. one re-read queued ...
    C.reread(P)
    reads = hub.calls[acted:]
    assert len(reads) == 1 and reads[0][:2] == ["api", "graphql"] and f"i{n}:issue(number:{n})" in reads[0][3]   # ... one light call, this issue only
    after = C.get_state_served(P)
    assert after["stale"] is False and after["source"]["rereadAt"] >= after["source"]["patchedAt"]
    assert {k: task(after, n)[k] for k in FIELDS} == {k: now[k] for k in FIELDS}   # the known effect was exact: GitHub says the same
    assert bool(task(after, n)["closed"]) == bool(now["closed"])                      # (GitHub's own time of closing replaces the page's)
    fresh = C.state_from_raw(P, hub.raw(), hub.deadlines, after["source"])            # and the same as a full load would say
    assert {k: task(fresh, n)[k] for k in FIELDS} == {k: task(after, n)[k] for k in FIELDS}


def test_dismiss_and_module_deadline_need_no_re_read_of_other_issues(hub):
    code, body = C.do_act(dict(action="dismiss", n=2, id="p1"), run=hub.run, p=P)
    assert code == 200 and body["issues"] == [2] and body["state"]["proposals"] == []
    code, body = C.do_act(dict(action="moddl", module="unassigned", d="2026-12-24"), run=hub.run, p=P)
    assert code == 400   # a virtual module has no deadline: refused, nothing changes
    C._cache[K][1]["modules"].append(dict(id="alpha", name="alpha"))
    code, body = C.do_act(dict(action="moddl", module="alpha", d="2026-12-24"), run=hub.run, p=P)
    assert code == 200 and body["issues"] == [] and body["state"]["modDeadline"] == {"alpha": "2026-12-24"}


@pytest.mark.parametrize("form,code", [(dict(action="release", n=2, p="1"), 400), (dict(action="cancel", n=99), 400), (dict(action="nope", n=1), 400)])
def test_a_refused_action_changes_nothing(hub, form, code):
    before = json.dumps(C._cache[K][1], sort_keys=True)
    got, body = C.do_act(form, run=hub.run, p=P)
    assert got == code and body["error"] and "state" not in body
    assert json.dumps(C._cache[K][1], sort_keys=True) == before and hub.calls == [] and hub.timers == [] and not C._reread


def test_a_failed_action_changes_nothing(hub):
    before = json.dumps(C._cache[K][1], sort_keys=True)
    def down(op):
        raise RuntimeError("gh down")
    assert C.do_act(dict(action="cancel", n=1), run=down, p=P)[0] == 502
    assert json.dumps(C._cache[K][1], sort_keys=True) == before and not C._reread


def test_a_burst_is_coalesced_into_one_re_read(hub):
    for form in (dict(action="release", n=1, p="P2", d=""), dict(action="change", n=2, p="3"), dict(action="change", n=2, p="1"), dict(action="cancel", n=3)):
        assert C.do_act(form, run=hub.run, p=P)[0] == 200
    assert len(hub.timers) == 1 and hub.timers[0][0] == C.REREAD_DELAY_S
    acted = len(hub.calls)
    C.reread(P)
    assert len(hub.calls) == acted + 1 and all(f"i{n}:issue" in hub.calls[-1][3] for n in (1, 2, 3))
    C.reread(P)
    assert len(hub.calls) == acted + 1   # nothing left to read
    S = C.get_state_served(P)
    assert (task(S, 1)["stage"], task(S, 2)["prio"], task(S, 3)["stage"]) == ("released", 1, "cancelled")


def test_the_re_read_counts_against_the_hourly_cap_and_a_pause_keeps_the_applied_change(hub, monkeypatch):
    C.do_act(dict(action="release", n=1, p="P2", d=""), run=hub.run, p=P)
    monkeypatch.setattr(api_gh, "GH_CALLS_PER_HOUR", 0)   # the cap is reached: no call is made
    acted = len(hub.calls)
    C.reread(P)
    S = C.get_state_served(P)
    assert len(hub.calls) == acted and task(S, 1)["stage"] == "released"
    assert "could not be asked" in S["source"]["rereadNote"] and "#1" in S["source"]["rereadNote"]
    monkeypatch.setattr(api_gh, "GH_CALLS_PER_HOUR", 600)
    C.do_act(dict(action="cancel", n=3), run=hub.run, p=P)   # (PC-18: while a pause runs the write side refuses actions, so the pause starts after it)
    assert C.get_state_served(P)["source"]["rereadNote"] is None   # a new action: its note comes with its own re-read
    C._gh_pause.update(until=time.time() + 600)       # a rate-limit pause: the same
    C.reread(P)
    S = C.get_state_served(P)
    assert task(S, 3)["stage"] == "cancelled" and "rate limit" in S["source"]["rereadNote"]


def test_one_call_reads_the_touched_issues_and_is_counted(hub):
    issues, events = C.fetch_issue_nodes(P, [1, 3])
    assert [i["number"] for i in issues] == [1, 3] and len(C._gh_calls) == 1
    assert {e["number"] for e in events} == {3}   # issue 1 has no label events


def test_an_issue_acted_on_again_keeps_its_newer_change_until_its_own_re_read(hub):
    C.do_act(dict(action="release", n=1, p="P2", d=""), run=hub.run, p=P)

    def fetch(ns):   # GitHub is read, then the owner acts on the same issue before the reading is stored
        before = C.fetch_issue_nodes(P, ns)
        C.do_act(dict(action="cancel", n=1), run=hub.run, p=P)
        return before
    C.reread(P, fetch=fetch)
    assert task(C._cache[K][1], 1)["stage"] == "cancelled" and C._reread[K] == {1}
    C.reread(P)
    assert task(C._cache[K][1], 1)["stage"] == "cancelled" and K not in C._reread


def test_an_old_copy_without_its_raw_part_is_reloaded_instead(hub):
    t, S = C._cache[K]
    C._cache[K] = (t, {k: v for k, v in S.items() if k != "raw"})
    assert C.do_act(dict(action="release", n=1, p="P2", d=""), run=hub.run, p=P)[0] == 200
    assert C._cache[K][0] == 0 and not C._reread   # expired: the next look loads it; no re-read without a copy to put it in


def test_the_timeline_follows_an_applied_change(hub):
    S = C._cache[K][1]
    v0 = C.state_version(S)
    C.do_act(dict(action="release", n=1, p="P2", d=""), run=hub.run, p=P)
    assert C.state_version(C._cache[K][1]) > v0


def test_the_page_takes_the_answer_refreshes_its_tab_and_marks_the_others_stale():
    post = PAGE[PAGE.index("async function post("):PAGE.index("/* Review state of rule changes")]
    assert "if(j.state)setState(j.state)" in post                         # 1. at once
    assert "await refreshTab(R)" in post                                   # 2. the tab where it happened
    assert "STALE.add(r)" in post and "if(r!==R)" in post                  # 3. the others are marked stale, not reloaded
    assert post.index("say('Refused: '") < post.index("setState")          # a refused action changes nothing on the page
    assert "if(STALE.has(R)){STALE.delete(R);refreshTab(R)" in PAGE        # a stale tab refreshes when opened
    for tab in ("questions", "timeline", "backlog", "settings", "cockpit"):  # each tab knows what to fetch
        assert tab + ":()=>" in PAGE[PAGE.index("async function refreshTab("):]
    assert "your change applied" in PAGE and "confirmed by GitHub" in PAGE and "D.source.rereadNote" in PAGE   # the "data from" time shows it
    assert "staleTimer=setTimeout(poll,4000)" in PAGE and "if(busy()){staleTimer=setTimeout(poll,4000);return;}" in PAGE   # open forms are not redrawn
    assert "FLASH.keys.forEach" in PAGE and ".flash{animation" in PAGE     # the acted-on row flashes
    assert "press Refresh on the pane to see the change" not in PAGE
