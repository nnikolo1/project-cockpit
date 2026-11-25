import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import io
import json
import pytest
import cockpit as C

FILE = {"default": "beta", "projects": [
    {"name": "alpha", "repo": "acme/widgets", "project": 3, "manager": "alice", "modules": "~/m.json", "git": "/src/w", "code": "/snap/w"},
    {"name": "beta", "repo": "acme/gadgets"}]}


@pytest.fixture
def two(monkeypatch):
    """Register two projects for a test and restore the module state afterwards."""
    projects, default = C.projects_from_file(FILE)
    monkeypatch.setattr(C, "PROJECTS", {})
    monkeypatch.setattr(C, "DEFAULT", None)
    C.register(projects, default)
    monkeypatch.setitem(C.CFG, "steer", True)
    monkeypatch.setitem(C.CFG, "simulate", False)
    C._cache.clear()
    yield
    C._cache.clear()


def flags(**kw):
    return {**dict(repo=None, manager=None, project=None, git=None, modules=None, phases=None, code=None, projects=None), **kw}


def test_single_project_flags_make_one_implicit_project_named_after_the_repo():
    ps, default = C.resolve(flags(repo="acme/widgets", project=3, git="/src/w"))
    assert default == "widgets" and len(ps) == 1
    assert ps[0] == dict(name="widgets", repo="acme/widgets", manager="acme", project=3, git="/src/w", modules=None, phases=None, code=None, code_collect=None, compliance=None)
    assert C.resolve(flags(repo="acme/widgets", manager="alice"))[0][0]["manager"] == "alice"
    assert C.resolve(flags(repo="acme/widgets", code_collect="make snap"))[0][0]["code_collect"] == ["make", "snap"]


def test_collector_command_of_the_projects_file_is_an_argument_list():
    ps, _ = C.projects_from_file({"projects": [{"name": "a", "repo": "o/a", "code": "/s", "code_collect": ["python3", "snap.py"]}, {"name": "b", "repo": "o/b", "code_collect": "make snap"}]})
    assert [p["code_collect"] for p in ps] == [["python3", "snap.py"], ["make", "snap"]]
    with pytest.raises(ValueError, match="--projects: project 'a'"):
        C.projects_from_file({"projects": [{"name": "a", "repo": "o/a", "code_collect": 3}]})


def test_code_payload_carries_configured_age_and_stale(tmp_path):
    (tmp_path / "metrics.json").write_text(json.dumps({"generated_at": "2020-01-01T00:00:00Z", "modules": {}}))
    cs = C.code_snapshot(dict(name="synthetic", code=str(tmp_path)))
    assert cs["wired"] and cs["configured"] and cs["stale"] and cs["age_h"] > 24 and cs["max_age_h"] == 24
    assert C.code_snapshot(dict(name="synthetic", code=str(tmp_path / "none")))["configured"] is True
    assert C.code_snapshot(dict(name="synthetic", code=None))["configured"] is False


def test_projects_file_gives_the_same_shape_with_defaults(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps(FILE))
    ps, default = C.resolve(flags(projects=str(f)))
    assert default == "beta" and [p["name"] for p in ps] == ["alpha", "beta"]
    a, b = ps
    assert a["manager"] == "alice" and b["manager"] == "acme" and b["project"] is None and b["git"] is None
    assert a["modules"].endswith("/m.json") and not a["modules"].startswith("~")
    assert set(a) == set(C.project_from_flags(flags(repo="x/y"))) == {"name", *C.PROJECT_KEYS}


def test_default_falls_back_to_the_first_project():
    assert C.projects_from_file({"projects": [{"name": "a", "repo": "o/a"}, {"name": "b", "repo": "o/b"}]})[1] == "a"


@pytest.mark.parametrize("bad", [
    [], {"projects": []}, {"projects": [{"name": "a"}]}, {"projects": [{"repo": "o/a"}]}, {"projects": [{"name": "a", "repo": "oa"}]},
    {"projects": [{"name": "a", "repo": "o/a"}, {"name": "a", "repo": "o/b"}]},
    {"default": "zz", "projects": [{"name": "a", "repo": "o/a"}]}])
def test_bad_projects_files_are_refused(bad):
    with pytest.raises(ValueError, match="--projects"):
        C.projects_from_file(bad)


def test_mixing_projects_with_single_project_flags_is_refused(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps(FILE))
    with pytest.raises(ValueError, match=r"--projects replaces --repo, --git"):
        C.resolve(flags(projects=str(f), repo="acme/widgets", git="/x"))
    with pytest.raises(ValueError, match="cannot read"):
        C.resolve(flags(projects=str(tmp_path / "missing.json")))
    with pytest.raises(ValueError, match="--repo OWNER/REPO or --projects"):
        C.resolve(flags())


def test_project_lookup_default_and_unknown(two):
    assert C.proj()["name"] == "beta" and C.proj("alpha")["repo"] == "acme/widgets"
    with pytest.raises(C.UnknownProject, match="unknown project 'nope'.*alpha, beta"):
        C.proj("nope")


def test_cache_keys_isolate_projects(two):
    a, b = C.proj("alpha"), C.proj("beta")
    assert C.pk(a, "state") != C.pk(b, "state")
    assert C.cached(C.pk(a, "rules"), lambda: "A", 60) == "A"
    assert C.cached(C.pk(b, "rules"), lambda: "B", 60) == "B"
    assert C.cached(C.pk(a, "rules"), lambda: "X", 60) == "A"


def test_state_is_built_and_cached_per_project(two, monkeypatch):
    built = []
    monkeypatch.setattr(C, "build_state", lambda p: built.append(p["name"]) or dict(of=p["name"]))
    assert C.get_state(p=C.proj("alpha")) == dict(of="alpha") and C.get_state(p=C.proj("beta")) == dict(of="beta")
    C.get_state(p=C.proj("alpha"))
    assert built == ["alpha", "beta"]
    C.get_state(fresh=True, p=C.proj("alpha"))
    assert built == ["alpha", "beta", "alpha"]


def test_stale_copy_is_kept_per_project(two):
    def bad():
        raise RuntimeError("rate limit")
    a, b = C.pk(C.proj("alpha"), "state"), C.pk(C.proj("beta"), "state")
    C.cached(a, lambda: "A", -1)
    assert C.cached(a, bad, -1) == "A"
    with pytest.raises(RuntimeError):
        C.cached(b, bad, -1)


def test_steering_uses_the_named_projects_repo_and_manager(two, monkeypatch):
    S = dict(tasks=[dict(number=1, key="T1", stage="embryo", labels=[], bad=[], blockedBy=[], title="t")], modules=[], modDeadline={}, proposals=[])
    seen = []
    for name, repo in (("alpha", "acme/widgets"), ("beta", "acme/gadgets")):
        ran = []
        code, body = C.do_act(dict(action="release", n=1, p="1", project=name), run=lambda op: ran.append(op) or "", state=S)
        assert code == 200 and ran and all(op[op.index("-R") + 1] == repo for op in ran if "-R" in op), (name, ran)
        seen.append(ran)
    assert seen[0] != seen[1]
    # no project in the request: the default one
    ran = []
    C.do_act(dict(action="release", n=1, p="1"), run=lambda op: ran.append(op) or "", state=S)
    assert ran[-1][ran[-1].index("-R") + 1] == "acme/gadgets"


def test_steering_unknown_project_is_404_and_read_only_stays_global(two, monkeypatch):
    ran = []
    assert C.do_act(dict(action="release", n=1, p="1", project="nope"), run=ran.append, state={})[0] == 404 and ran == []
    monkeypatch.setitem(C.CFG, "steer", False)
    assert C.do_act(dict(action="release", n=1, p="1", project="alpha"), run=ran.append, state={})[0] == 403 and ran == []


def test_path_helpers_use_the_projects_own_paths(two, tmp_path):
    snap = tmp_path / "snap"
    (snap / "report").mkdir(parents=True)
    (snap / "metrics.json").write_text("{}")
    (snap / "report" / "x.html").write_text("<p>x</p>")
    C.proj("alpha")["code"] = str(snap)
    assert C.code_snapshot(C.proj("alpha")).get("wired") is not None
    cs = C.code_snapshot(C.proj("beta"))
    assert (cs["wired"], cs["modules"], cs["diagrams"]) == (False, {}, [])  # beta has no snapshot dir
    assert C.L.report_file(C.proj("alpha")["code"], "x.html") and C.proj("beta")["code"] is None
    assert C.build_rules(C.proj("beta")) == dict(configured=False)


class FakeHandler(C.H):
    """A handler fed a request line without a socket; returns (status, parsed body)."""
    def __init__(self, path, cookie, referer=""):
        self.path, self.headers, self.out = path, {"Cookie": cookie, "Referer": referer}, None
        self.wfile = io.BytesIO()

    def send(self, code, body=b"", ctype="application/json", headers=()):
        self.out = (code, body)


def get(path, referer=""):
    C.H.token = "tok"
    h = FakeHandler(path, "t=tok", referer)
    h.do_GET()
    return h.out


def test_api_projects_lists_names_and_repos_only(two):
    code, body = get("/api/projects")
    assert code == 200 and body == dict(default="beta", projects=[dict(name="alpha", repo="acme/widgets"), dict(name="beta", repo="acme/gadgets")])
    assert "manager" not in json.dumps(body) and "/src/w" not in json.dumps(body)


def test_api_unknown_project_is_404_with_a_clear_error(two):
    code, body = get("/api/state?project=nope")
    assert code == 404 and "unknown project 'nope'" in body["error"]


def test_api_picks_the_project_from_the_query(two, monkeypatch):
    monkeypatch.setattr(C, "build_state", lambda p: dict(of=p["name"]))
    assert get("/api/state?project=alpha") == (200, dict(of="alpha", stale=False, staleNote=None, refreshAfter=3600)) and get("/api/state") == (200, dict(of="beta", stale=False, staleNote=None, refreshAfter=3600))


def test_report_without_a_query_follows_the_referer(two, tmp_path):
    (tmp_path / "report").mkdir()
    (tmp_path / "report" / "x.css").write_text("a{}")
    C.proj("alpha")["code"] = str(tmp_path)
    assert get("/report/x.css", referer="http://h/report/p.html?project=alpha")[0] == 200
    assert get("/report/x.css")[0] == 404  # the default project has no snapshot


def test_timeline_is_rebuilt_when_the_state_it_came_from_was_replaced(two, monkeypatch, tmp_path):
    monkeypatch.setattr(C, "CACHE_DIR", None)
    p = C.proj("alpha")
    built = []
    monkeypatch.setattr(C, "build_timeline", lambda S, p=None, fresh=False: built.append(S["source"]["fetchedAt"]) or dict(entries=[]))
    state = dict(source=dict(fetchedAt=1))
    monkeypatch.setattr(C, "get_state", lambda fresh=False, p=None: state)
    assert C.get_timeline(p)["stateAt"] == 1
    assert C.get_timeline(p)["stateAt"] == 1 and built == [1]  # same state: served from the cache
    state = dict(source=dict(fetchedAt=2))
    assert C.get_timeline(p)["stateAt"] == 2 and built == [1, 2]  # new state: rebuilt at once


def test_served_state_says_when_a_newer_copy_is_being_fetched(two, monkeypatch):
    p = C.proj("alpha")
    monkeypatch.setattr(C, "get_state", lambda fresh=False, p=None: dict(tasks=[]))
    assert C.get_state_served(p)["stale"] is False
    monkeypatch.setattr(C, "_refreshing", {C.pk(p, "state")})
    assert C.get_state_served(p)["stale"] is True


def test_warm_up_survives_a_failing_part(two, monkeypatch):
    monkeypatch.setattr(C, "get_state", lambda p=None, fresh=False: 1 / 0)
    for n in ("get_rules", "get_timeline", "code_snapshot", "usage_served", "warm_module_pages"):
        monkeypatch.setattr(C, n, lambda p=None: None)
    C.warm()  # logs the failure, does not raise
