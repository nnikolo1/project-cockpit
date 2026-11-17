import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import datetime as dt
import logic as L

M = "mgr"


def iss(n, labels=(), state="open", reason=None, assignees=(), title=None, **kw):
    d = dict(number=n, id=1000 + n, title=title or f"t{n}", url=f"https://x/issues/{n}", state=state, state_reason=reason,
             assignees=list(assignees), created_at="2026-01-01T00:00:00Z", closed_at=None, labels=list(labels), parent=None,
             blocked_by=[], body="", author=M, comments=[])
    d.update(kw)
    return d


def ev(n, label, kind, actor, t="2026-01-01T00:00:00Z"):
    return dict(number=n, name=label, kind=kind, actor=actor, t=t)


def derive(issues, events=(), project=None, mods=(), grow=True):
    return L.derive(issues, list(events), project or {}, M, list(mods), grow)


def test_stage_derivation():
    assert L.stage(iss(1), set()) == "embryo"
    assert L.stage(iss(1), {"released"}) == "released"
    assert L.stage(iss(1, assignees=["w"]), {"released"}) == "in_progress"
    assert L.stage(iss(1, state="closed", reason="completed"), set()) == "completed"
    assert L.stage(iss(1, state="closed", reason="not_planned"), set()) == "cancelled"
    assert L.stage(iss(1), {"cancelled"}) == "cancelled"


def test_policy_manager_release_counts():
    eff, bad = L.policy([iss(1, ["released", "P1", "must"])], [ev(1, "released", "labeled", M), ev(1, "P1", "labeled", M)], M)[1]
    assert eff == {"released", "P1", "must"} and bad == {}


def test_policy_forged_release_ignored_and_flagged():
    eff, bad = L.policy([iss(1, ["released"])], [ev(1, "released", "labeled", "mallory")], M)[1]
    assert "released" not in eff and bad == {"released": "mallory"}
    assert L.stage(iss(1, ["released"]), eff) == "embryo"


def test_policy_forged_actor_on_priority_and_deadline_labels():
    labels = ["released", "P1", "due:2026-12-01"]
    events = [ev(1, "released", "labeled", M), ev(1, "P1", "labeled", "eve"), ev(1, "due:2026-12-01", "labeled", "eve")]
    eff, bad = L.policy([iss(1, labels)], events, M)[1]
    assert eff == {"released"} and bad == {"P1": "eve", "due:2026-12-01": "eve"}
    t = derive([iss(1, labels)], events)[0][0]
    assert t["prio"] is None and t["deadline"] is None and t["stage"] == "released" and t["bad"] == ["P1", "due:2026-12-01"]
    assert t["flags"] == ["unauthorised: P1 set by eve", "unauthorised: due:2026-12-01 set by eve"]


def test_policy_priority_changed_twice_latest_wins():
    events = [ev(1, "P1", "labeled", M, "2026-01-01T00:00:00Z"), ev(1, "P1", "unlabeled", M, "2026-01-02T00:00:00Z"),
              ev(1, "P3", "labeled", M, "2026-01-02T00:00:00Z")]
    assert L.policy([iss(1, ["P3"])], events, M)[1] == ({"P3"}, {})
    events += [ev(1, "P3", "unlabeled", M, "2026-01-03T00:00:00Z"), ev(1, "P3", "labeled", "eve", "2026-01-04T00:00:00Z")]
    assert L.policy([iss(1, ["P3"])], events[::-1], M)[1] == (set(), {"P3": "eve"})  # latest actor decides, input order irrelevant


def test_policy_no_event_is_unauthorised_and_plain_labels_pass():
    assert L.policy([iss(1, ["released"])], [], M)[1][1] == {"released": "unknown"}
    assert L.policy([iss(1, ["must", "module:a"])], [], M)[1][0] == {"must", "module:a"}


def test_parse_due_and_title_key():
    assert L.parse_due(["P1", "due:2026-12-01", "due:2026-02-30", "due:soon", "due:2026-01-05"]) == ["2026-01-05", "2026-12-01"]
    assert L.split_title("[AB-05] Fix address", 7) == ("AB-05", "Fix address")
    assert L.split_title("[ab-5]x", 7) == ("AB-5", "x")
    assert L.split_title("No prefix [AB-05]", 7) == ("#7", "No prefix [AB-05]")
    assert L.split_title("[module] Demo", 16) == ("#16", "[module] Demo")


def test_parse_proposals_open_resolved_and_malformed():
    c = lambda b: dict(body=b, author="orc", t="2026-01-01T00:00:00Z")
    p1 = '<!-- pm:proposal {"id": "a", "field": "deadline", "value": "2026-12-01", "reason": "blocked"} -->'
    p2 = '<!-- pm:proposal {"id":"b","field":"priority","value":"P2","reason":"urgent"} -->\ntext'
    bad = ['<!-- pm:proposal {"id":"c","field":"priority","value":7} -->', '<!-- pm:proposal {"id":"d","field":"deadline","value":"x"} -->',
           '<!-- pm:proposal not json -->', '<!-- pm:proposal {"id":"e","field":"colour","value":1} -->']
    out = L.parse_proposals([c(p1), c(p2)] + [c(b) for b in bad])
    assert [(p["id"], p["field"], p["value"], p["reason"]) for p in out] == [("a", "deadline", "2026-12-01", "blocked"), ("b", "priority", 2, "urgent")]
    assert [p["id"] for p in L.parse_proposals([c(p1), c(p2), c("<!-- pm:resolved a -->\nAccepted")])] == ["b"]
    assert L.parse_proposals([]) == []


def test_parse_module_deadlines():
    assert L.parse_module_deadlines('{"alpha": "2026-12-31", "beta": "bad", "g": 5}') == {"alpha": "2026-12-31"}
    assert L.parse_module_deadlines("not json") == {} and L.parse_module_deadlines("[1]") == {} and L.parse_module_deadlines(None) == {}


def test_derive_full_task():
    issues = [iss(5, ["released", "P2", "must", "type:bug", "module:alpha", "due:2026-12-01"], title="[AB-05] Fix it", assignees=["w"], parent=2,
                  blocked_by=[3, 99], body="Why.\n\n**Evidence**\n- [doc](https://x/y)"),
              iss(2, title="[AB-02] Parent"), iss(3, state="closed", reason="completed")]
    events = [ev(5, l, "labeled", M) for l in ("released", "P2", "due:2026-12-01")]
    tasks, mods = derive(issues, events, {5: dict(est=100, used=40, hint="Should", status="Todo", module="")})
    t = {x["key"]: x for x in tasks}["AB-05"]
    assert (t["title"], t["module"], t["type"], t["cls"], t["prio"], t["deadline"], t["stage"]) == ("Fix it", "alpha", "bug", "must", 2, "2026-12-01", "in_progress")
    assert (t["parent"], t["blockedBy"], t["assignee"], t["est"], t["used"], t["hint"]) == ("AB-02", ["#3"], "w", 100, 40, "Should")
    assert t["desc"] == "Why." and t["evidence"] == [dict(text="doc", url="https://x/y")] and [m["id"] for m in mods if m["id"] != "unassigned"] == ["alpha"]


def test_hint_is_never_the_priority_and_class_falls_back_to_hint():
    t = derive([iss(1, ["prio:must", "kind:bug-risk"])], [], {1: {}})[0][0]
    assert t["prio"] is None and t["hint"] == "Must" and t["cls"] == "must" and t["type"] == "bug"
    t = derive([iss(1)], [], {1: dict(hint="Could")})[0][0]
    assert t["prio"] is None and t["hint"] == "Could" and t["cls"] == "nice"


def test_two_due_labels_latest_wins():
    events = [ev(1, "due:2026-12-01", "labeled", M, "2026-01-01T00:00:00Z"), ev(1, "due:2026-12-09", "labeled", M, "2026-01-02T00:00:00Z")]
    assert derive([iss(1, ["due:2026-12-01", "due:2026-12-09"])], events)[0][0]["deadline"] == "2026-12-09"


def test_modules_from_config_project_and_unassigned():
    mods = [dict(id="alpha", name="Alpha / core"), dict(id="beta", name="Beta / extra")]
    assert [L.resolve_module(x, mods) for x in ("Alpha", "Beta", "beta", "Nope", "")] == ["alpha", "beta", "beta", "unassigned", "unassigned"]
    tasks, out = derive([iss(1), iss(2), iss(3, ["module:alpha"])], [], {1: dict(module="Beta"), 2: dict(module="")}, mods, grow=False)
    assert [t["module"] for t in tasks] == ["beta", "unassigned", "alpha"]
    assert {m["id"]: m["short"] for m in out}["beta"] == "Beta"           # the Project option spelling
    assert {m["id"]: m["short"] for m in out}["alpha"] == "Alpha"               # else the name before ' / '
    assert out[-1]["id"] == "unassigned" and out[-1]["virtual"]


def test_modules_grow_from_labels_without_config():
    tasks, mods = derive([iss(1, ["module:alpha"]), iss(2, ["module:Beta"])])
    assert [t["module"] for t in tasks] == ["alpha", "beta"] and [m["id"] for m in mods] == ["alpha", "beta"]


def test_swap_priority():
    assert L.swap_priority({"P1", "must"}, "P3") == (["P3"], ["P1"])
    assert L.swap_priority({"P2"}, "P2") == ([], [])
    assert L.swap_priority(set(), "P1") == (["P1"], [])


def test_task_events_and_unauthorised_suffix():
    events = [ev(1, "released", "labeled", M, "2026-01-01T10:00:00Z"), ev(1, "must", "labeled", M), ev(1, "P2", "labeled", "eve", "2026-01-01T11:00:00Z"),
              dict(number=1, name="w", kind="assigned", actor=M, t="2026-01-01T12:00:00Z")]
    out = L.task_events(events, {1: "AB-01"}, M)
    assert [(e["key"], e["actor"], e["text"]) for e in out] == [("AB-01", M, "assigned to w"), ("AB-01", "eve", "set priority P2 (unauthorised)"), ("AB-01", M, "released")]


A = [{"path": "src/", "module": "alpha"}, {"path": "core/", "module": "alpha"},
     {"path": "core/misc.py", "module": None}, {"path": "lib/", "module": "beta"}]


def test_map_path_longest_prefix_and_unassigned():
    assert L.map_path("src/x.py", A) == "alpha"
    assert L.map_path("core/misc.py", A) == "unassigned"   # explicit null wins (longer prefix)
    assert L.map_path("docs/readme.md", A) == "unassigned"
    assert L.map_path("tests/lib/test_x.py", A) == "beta"   # tests follow the mirrored source
    assert L.map_path("core/misc.pyx", A) == "alpha"


def test_commit_lanes_multi_and_no_modules():
    assert L.commit_lanes(["src/a.py", "lib/b.py"], A) == {"alpha", "beta"}
    assert L.commit_lanes([], A) == {"unassigned"} and L.commit_lanes(["a.py"], []) == {"all"}


def test_parse_git_full_and_group_commits_by_day_and_module():
    txt = "@aaa1111222|2026-01-02 10:00:00 +0100|second\n\nsrc/a.py\nlib/b.py\n@bbb2222333|2026-01-02 09:00:00 +0100|first\nsrc/c.py\n@ccc3333444|2026-01-01 08:00:00 +0100|older\nREADME.md\n"
    cs = L.parse_git_full(txt)
    assert [c["sha"] for c in cs] == ["aaa1111222", "bbb2222333", "ccc3333444"] and cs[0]["files"] == ["src/a.py", "lib/b.py"]
    g = {(e["d"], e["mod"]): e for e in L.group_commits(cs, A, "o/r")}
    h = g[("2026-01-02", "alpha")]
    assert h["text"] == "second (+1 more commits)" and [l[0] for l in h["links"]] == ["aaa1111", "bbb2222"]
    assert h["links"][0][1] == "https://github.com/o/r/commit/aaa1111222"
    assert ("2026-01-02", "beta") in g and ("2026-01-01", "unassigned") in g


def test_issue_entries_collapse_by_day_and_module():
    mk = lambda n, c, cl=None: dict(key=f"#{n}", title=f"t{n}", url=f"https://x/issues/{n}", module="alpha", created=c, closed=cl)
    out = L.issue_entries([mk(1, "2026-01-01T00:00:00Z"), mk(2, "2026-01-01T05:00:00Z", "2026-01-03T00:00:00Z")])
    assert {(e["d"], e["text"]) for e in out} == {("2026-01-01", "2 issues opened"), ("2026-01-03", "#2 closed: t2")}
    assert L.filter_feed(out, typ="issue", module="alpha") and L.filter_feed(out, module="beta") == []


def test_ref_link_and_module_dir():
    assert L.ref_link({"sha": "abcdef123"}, "o/r") == ("abcdef1", "https://github.com/o/r/commit/abcdef123")
    assert L.ref_link({"path": "docs/x.md:3", "text": "x"}, "o/r")[1] == "https://github.com/o/r/blob/main/docs/x.md#L3"
    assert L.ref_link({"text": "plain"}, "o/r") == ("plain", None)
    assert L.module_dir("/r", dict(id="m", docs="docs/project/m/README.md")) == pathlib.Path("/r/docs/project/m")
    assert L.module_dir("/r", dict(id="m", docs=None)) == pathlib.Path("/r/docs/project/m")


def test_static_entries_and_module_facts(tmp_path):
    assert L.static_entries(tmp_path, "m", "o/r") == [] and L.module_facts(tmp_path) == (None, None)
    (tmp_path / "timeline.json").write_text('{"events":[{"date":"2026-01-01","type":"milestone","text":"M","refs":[{"sha":"abc1234"}]}]}')
    (tmp_path / "backlog.json").write_text('{"read_on":"2026-01-02","items":[{"kind":"gap","status":"open"},{"kind":"gap","status":"done"}]}')
    (tmp_path / "documents.json").write_text('{"sources":[1,2,3]}')
    assert [(e["d"], e["type"]) for e in L.static_entries(tmp_path, "m", "o/r")] == [("2026-01-01", "milestone"), ("2026-01-02", "gap")]
    assert L.module_facts(tmp_path) == (1, 3)


def test_code_view_and_missing_metrics():
    m = dict(generated_at="t", git_commit_short="abc", modules={"a": dict(name="A", metrics=dict(
        files_total=3, loc={"python_source": dict(files=2, code=100), "python_test": dict(files=1, code=20)},
        lint=dict(total=5, per_kloc=1.5, top_codes={"E501": 4, "F401": 1}), complexity=dict(max=9, mean=2.5, rated_DEF=1, worst=[dict(path="x.py", line=3, name="f")]),
        hotspots=dict(top=[dict(path="x.py", churn=4, cc_max=9)]), test_coverage=dict(pct_statements_covered=None)))})
    v = L.code_view(m)
    a = v["modules"]["a"]
    assert v["commit"] == "abc" and a["code"] == 120 and a["cc"] == 9 and a["ccAt"] == "x.py:3 f" and a["hot"] == [["x.py", 4, 9]] and a["cov"] is None
    assert a["lintNote"] == "1.5 per 1,000 lines; E501 4, F401 1" and a["mi"] is None and a["files"] == "3 files (2 source, 1 test)"


def test_parse_project_fields_and_repo_filter():
    items = [dict(content=dict(type="Issue", number=4, repository="o/r"), module="Alpha", priority="Must", status="Todo", **{"estimate (tokens)": 5, "tokens used": 2}),
             dict(content=dict(type="Issue", number=5, repository="o/other")), dict(content=dict(type="DraftIssue"))]
    assert L.parse_project(items, "o/r") == {4: dict(module="Alpha", hint="Must", status="Todo", est=5, used=2)}


def test_graphql_normalisers():
    node = dict(number=3, databaseId=9, title="t", url="u", state="CLOSED", stateReason="NOT_PLANNED", createdAt="c", closedAt="d", author=None,
                labels=dict(nodes=[dict(name="P1")]), assignees=dict(nodes=[dict(login="w")]), parent=dict(number=1), blockedBy=dict(nodes=[dict(number=2)]),
                body=None, comments=dict(nodes=[dict(body="b", createdAt="t", author=dict(login="a"))]),
                timelineItems=dict(nodes=[dict(__typename="LabeledEvent", createdAt="t1", actor=dict(login="m"), label=dict(name="P1")),
                                          dict(__typename="UnlabeledEvent", createdAt="t2", actor=None, label=dict(name="P2")),
                                          dict(__typename="AssignedEvent", createdAt="t3", actor=dict(login="m"), assignee=dict(login="w")),
                                          dict(__typename="ClosedEvent", createdAt="t4", actor=dict(login="m"))]))
    i = L.norm_issue(node)
    assert (i["state"], i["state_reason"], i["labels"], i["parent"], i["blocked_by"], i["id"], i["author"]) == ("closed", "not_planned", ["P1"], 1, [2], 9, None)
    assert [(e["kind"], e["name"], e["actor"]) for e in L.norm_events(node)] == [("labeled", "P1", "m"), ("unlabeled", "P2", None), ("assigned", "w", "m"), ("closed", "not_planned", "m")]
