"""The Environments view of the server (PC-142): /api/environments gives what `ckp env show` gives; the deploy action starts `ckp deploy`
through ckp.core.environments (a fake spawn here: no process, no deploy) and refuses a second run, an unknown environment and read-only."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import cockpit as C  # noqa: E402
from ckp.core import environments as E  # noqa: E402
from ckp.store import env as store_env  # noqa: E402

P = dict(name="proj", repo="o/proj", git="/clone")


@pytest.fixture
def spawned(monkeypatch):
    calls, start, view = [], E.start_deploy, E.view
    monkeypatch.setattr(C.core_environments, "view", lambda *a, **kw: view(*a, alive=lambda pid: bool(calls), **kw))
    monkeypatch.setitem(C.CFG, "steer", True)
    monkeypatch.setattr(C.core_environments, "start_deploy", lambda *a, **kw: start(
        *a, spawn=lambda args, **k: calls.append(args) or type("P", (), {"pid": 77})(), alive=lambda pid: bool(calls), **kw))
    store_env.save("proj", {"production": {"port": 8800, "data_dir": "/d", "label": "com.example.viewer"}})
    return calls


def test_the_view_names_the_environment_and_whether_buttons_are_offered(spawned, monkeypatch):
    v = C.get_environments(P)
    assert v["environments"][0]["name"] == "production" and v["steering"] and v["configured"] and v["live"] is None and v["error"] is None
    monkeypatch.setitem(C.CFG, "steer", False)
    assert not C.get_environments(dict(P, git=None))["configured"] and not C.get_environments(P)["steering"]


@pytest.mark.ui_action("server:deploy")
def test_deploy_starts_the_command_and_the_view_shows_it_running(spawned):
    code, body = C.do_act(dict(action="deploy", env="production", mode="apply"), p=P)
    assert (code, body["msg"]) == (200, "deploy of proj to production started")
    assert spawned[0][2:] == ["deploy", "--project", "proj", "--repo", "o/proj", "--repo-dir", "/clone", "--env", "production", "--apply"]
    assert C.get_environments(P)["run"]["state"] == "running"


@pytest.mark.ui_action("server:deploy/refused")
def test_deploy_refusals(spawned, monkeypatch):
    assert C.do_act(dict(action="deploy", env="test", mode="check"), p=P) == (400, {"error": "no environment 'test' for proj: create it with `ckp env set`"})
    assert C.do_act(dict(action="deploy", env="production", mode="check"), p=P)[0] == 200
    code, body = C.do_act(dict(action="deploy", env="production", mode="apply"), p=P)
    assert code == 400 and "already running" in body["error"] and len(spawned) == 1
    monkeypatch.setitem(C.CFG, "steer", False)
    assert C.do_act(dict(action="deploy", env="production", mode="check"), p=P)[0] == 403
