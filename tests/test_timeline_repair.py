"""ADR 0019: GitHub thins the nested timeline of big GraphQL pages; the actor of the latest label event must still be found."""
import sys, pathlib, json, re
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import cockpit as C
from ckp.api import fetch as api_fetch, gh as api_gh
import logic as L

P = dict(repo="o/r", manager="m")
LAB = {"__typename": "LabeledEvent", "createdAt": "2026-11-20T10:00:00Z", "actor": {"login": "m"}, "label": {"name": "released"}}


def issue(n, labels, upd="2026-11-21T00:00:00Z"):
    return dict(number=n, labels=labels, updated_at=upd)


def ev(n, name, actor, t, kind="labeled"):
    return dict(number=n, name=name, actor=actor, t=t, kind=kind)


def test_issues_needing_timeline_lists_only_missing_or_unlabeled_latest():
    issues = [issue(1, ["released"]), issue(2, ["released"]), issue(3, ["bug"]), issue(4, ["P1"]), issue(5, ["released"])]
    events = [ev(2, "released", "m", "2026-11-20T10:00:00Z"),
              ev(4, "P1", "m", "2026-11-20T10:00:00Z"), ev(4, "P1", "m", "2026-11-20T11:00:00Z", "unlabeled"),
              ev(5, "released", "intruder", "2026-11-20T10:00:00Z")]
    assert L.issues_needing_timeline(issues, events) == [1, 4]  # 5 is a genuine foreign change, 2 is fine, 3 has no protected label


def test_repair_finds_the_manager_event_and_unflags():
    issues = [issue(1, ["released"])]
    assert L.policy(issues, [], "m")[1] == (set(), {"released": "unknown"})  # before: flagged, stage would fall back to embryo
    calls = []
    out = C.repair_events(P, issues, [], fetch=lambda nums: calls.append(nums) or {1: [ev(1, "released", "m", "2026-11-20T17:55:02Z")]}, cache={})
    assert calls == [[1]]
    eff = L.policy(issues, out, "m")[1]
    assert eff == ({"released"}, {}) and L.stage(dict(state="open", assignees=[]), eff[0]) == "released"


def test_repair_is_cached_per_issue_and_updated_at():
    cache, calls = {}, []
    fetch = lambda nums: calls.append(list(nums)) or {1: [ev(1, "released", "m", "2026-11-20T17:55:02Z")]}
    C.repair_events(P, [issue(1, ["released"])], [], fetch=fetch, cache=cache)
    C.repair_events(P, [issue(1, ["released"])], [], fetch=fetch, cache=cache)
    assert len(calls) == 1  # second state build: served from the cache
    C.repair_events(P, [issue(1, ["released"], upd="2026-11-22T00:00:00Z")], [], fetch=fetch, cache=cache)
    assert len(calls) == 2  # the issue changed: fetched again


def test_label_without_any_event_stays_flagged_and_failure_is_not_fatal():
    issues = [issue(1, ["released"]), issue(2, ["P1"])]
    out = C.repair_events(P, issues, [], fetch=lambda nums: {}, cache={})
    assert L.policy(issues, out, "m")[1][1] == {"released": "unknown"}

    def boom(nums):
        raise RuntimeError("rate limited")
    cache = {}
    out = C.repair_events(P, issues, [], fetch=boom, cache=cache)
    assert out == [] and L.policy(issues, out, "m")[2][1] == {"P1": "unknown"} and cache == {}  # still flagged; failures are not cached


def test_repair_replaces_thinned_events_of_the_issue_only():
    issues = [issue(1, ["released"]), issue(2, ["released"])]
    events = [ev(2, "released", "m", "2026-11-20T10:00:00Z"), ev(1, "kind:gap", "m", "2026-11-13T10:00:00Z")]
    got = {1: [ev(1, "kind:gap", "m", "2026-11-13T10:00:00Z"), ev(1, "released", "m", "2026-11-20T17:55:02Z")]}
    out = C.repair_events(P, issues, events, fetch=lambda n: got, cache={})
    assert sorted((e["number"], e["name"]) for e in out) == [(1, "kind:gap"), (1, "released"), (2, "released")]


def test_fetch_timelines_batches_per_ten_issues(monkeypatch):
    queries = []

    def fake(args):
        q = next(a for a in args if a.startswith("query="))
        queries.append(q)
        return json.dumps({"data": {"repository": {f"i{n}": {"number": n, "stateReason": None, "timelineItems": {"nodes": [LAB]}} for n in map(int, re.findall(r"i(\d+):issue", q))}}})
    monkeypatch.setattr(api_gh, "run_gh", fake)
    out = C.fetch_timelines(P, list(range(1, 26)))
    assert len(queries) == 3 and set(out) == set(range(1, 26)) and out[7][0]["actor"] == "m"


def test_page_size_is_small_and_query_takes_it_as_variable():
    assert C.PAGE <= 30 and "first:$n" in C.GQL and "$n:Int!" in C.GQL and "updatedAt" in C.GQL and "timelineItems(last:100" in C.GQL


def test_fetch_issues_pages_and_repairs(monkeypatch):
    node = lambda n, tl: dict(number=n, databaseId=n, title=f"t{n}", url="u", body="", state="OPEN", stateReason=None, createdAt="2026-11-13T00:00:00Z",
                              updatedAt="2026-11-21T00:00:00Z", closedAt=None, author={"login": "m"}, labels={"nodes": [{"name": "released"}]},
                              assignees={"nodes": []}, parent=None, blockedBy={"nodes": []}, comments={"nodes": []}, timelineItems={"nodes": tl})
    seen = []

    def fake(args):
        seen.append(args)
        if any(a.startswith("query=query($o:String!,$r:String!)") for a in args):  # on-demand timelines
            return json.dumps({"data": {"repository": {"i1": dict(number=1, stateReason=None, timelineItems={"nodes": [LAB]})}}})
        second = "after=c1" in args
        page = dict(pageInfo=dict(hasNextPage=not second, endCursor="c1"), nodes=[node(2, [LAB])] if second else [node(1, [])])  # issue 1: thinned
        return json.dumps({"data": {"repository": {"issues": page}}})
    monkeypatch.setattr(api_gh, "run_gh", fake)
    monkeypatch.setattr(api_fetch, "_TL", {})
    issues, events = C.fetch_issues(dict(repo="o/r", manager="m"))
    assert [i["number"] for i in issues] == [1, 2]
    assert all(not bad for _, bad in L.policy(issues, events, "m").values())  # nobody is flagged
    assert any("n=20" in a for args in seen for a in args)
