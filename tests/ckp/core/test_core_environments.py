"""The Environments view and its deploy button (PC-142): the view reads the records of `ckp deploy`; the button starts the command detached
and refuses a second run, an unknown environment or mode. No process is started: `spawn` and `alive` are fakes."""
import json

from ckp import cli
from ckp.core import environments as E
from ckp.store import env as store_env, io, layout, releases

ENV = {"port": 8800, "data_dir": "/d", "label": "com.example.viewer"}


class Spawn:
    def __init__(self):
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        return type("P", (), {"pid": 4242})()


def install(home, *tags):
    for t in tags:
        tree = home / "tree"
        tree.mkdir(exist_ok=True)
        releases.install_release("p", t, tree, home)
        releases.switch_current("p", t, home)


def test_the_view_shows_environments_live_previous_and_the_newest_log_rows(tmp_path):
    store_env.save("p", {"production": ENV}, tmp_path)
    install(tmp_path, "v2026.11.25-1", "v2026.11.26-1")
    for i, proj in enumerate(("p", "other", "p")):
        io.append_jsonl(layout.deploy_log(tmp_path), {"time": f"t{i}", "project": proj, "env": "production", "tag": f"v{i}", "ok": True})
    d = E.view("p", tmp_path)["data"]
    assert d["environments"] == [dict(name="production", **ENV)]
    assert (d["live"], d["previous"], d["releases"]) == ("v2026.11.26-1", "v2026.11.25-1", ["v2026.11.26-1", "v2026.11.25-1"])
    assert [r["time"] for r in d["log"]] == ["t2", "t0"] and d["run"] is None


def test_the_view_of_an_unusable_project_name_is_an_error(tmp_path):
    assert not E.view("../x", tmp_path)["ok"]


def test_start_runs_ckp_deploy_detached_and_the_run_is_followed_until_it_ends(tmp_path):
    store_env.save("p", {"production": ENV}, tmp_path)
    sp, live = Spawn(), {"on": True}
    r = E.start_deploy("p", "o/p", "/clone", "production", "rollback", tmp_path, spawn=sp, alive=lambda pid: live["on"])
    assert r["ok"] and r["msg"] == "rollback of p to production started"
    args, kw = sp.calls[0]
    assert args[1].endswith("bin/ckp") and args[2:] == ["deploy", "--project", "p", "--repo", "o/p", "--repo-dir", "/clone", "--env", "production",
                                                       "--rollback", "--apply"]
    assert kw["start_new_session"] and kw["env"]["CP_HOME"] == str(tmp_path)
    assert E.view("p", tmp_path, alive=lambda pid: True)["data"]["run"]["state"] == "running"
    again = E.start_deploy("p", "o/p", "/clone", "production", "apply", tmp_path, spawn=sp, alive=lambda pid: True)
    assert not again["ok"] and "already running" in again["error"]
    (tmp_path / "deploy-run" / "p.out").write_text(json.dumps({"ok": False, "error": "not ready: tests", "data": {"tag": "v1", "steps": [
        {"step": "stopped", "ok": False, "detail": "not ready: tests"}]}, "dry_run": False}))
    run = E.run_state("p", tmp_path, alive=lambda pid: False)
    assert (run["state"], run["ok"], run["error"], run["tag"], run["mode"]) == ("ended", False, "not ready: tests", "v1", "rollback")


def test_a_run_that_left_no_result_says_so(tmp_path):
    store_env.save("p", {"production": ENV}, tmp_path)
    E.start_deploy("p", "o/p", "/clone", "production", "check", tmp_path, spawn=Spawn(), alive=lambda pid: False)
    run = E.run_state("p", tmp_path, alive=lambda pid: False)
    assert run["state"] == "ended" and not run["ok"] and "without a result" in run["error"]


def test_start_refuses_unknown_mode_environment_and_a_missing_clone(tmp_path):
    store_env.save("p", {"production": ENV}, tmp_path)
    sp = Spawn()
    assert "unknown deploy mode" in E.start_deploy("p", "o/p", "/c", "production", "force", tmp_path, spawn=sp)["error"]
    assert "no environment 'test'" in E.start_deploy("p", "o/p", "/c", "test", "check", tmp_path, spawn=sp)["error"]
    assert "no repository and clone" in E.start_deploy("p", "o/p", None, "production", "check", tmp_path, spawn=sp)["error"]
    assert sp.calls == []


def test_cli_env_show_prints_the_view(capsys):
    store_env.save("p", {"production": ENV})
    assert cli.main(["env", "show", "--project", "p"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["environments"][0]["port"] == 8800


def test_cli_heartbeat_set_run_and_watch(monkeypatch, capsys):
    pings, shown = [], []
    monkeypatch.setattr(cli.notify, "ping", lambda url, ok=True: pings.append(ok) or True)
    monkeypatch.setattr(cli.notify, "desktop", lambda t, x: shown.append(x) or True)
    assert cli.main(["heartbeat", "set", "--job", "daily", "--url", "https://hc.example.invalid/x", "--every-hours", "24"]) == 0
    a = cli.build_parser().parse_args(["heartbeat", "run", "--job", "daily", "--", "false"])
    assert cli.cmd_heartbeat_run(a, call=lambda cmd: 3) == 3 and pings == [False]
    assert cli.main(["watch"]) == 1 and len(shown) == 1 and shown[0].startswith("the last run of daily failed")
    assert cli.main(["heartbeat", "run", "--job", "daily", "--"]) == 1 and pings == [False]   # nothing to run: refused, nothing pinged
