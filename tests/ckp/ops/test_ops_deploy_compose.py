"""The three parts composed: a real temporary git repository and a real release store in a temporary home, with the hosting service and the
service manager faked: a whole deploy, a refusal, a held release and a rollback."""
import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from ckp import cli  # noqa: E402
from ckp.adapters.deploy import GitDeployAdapter  # noqa: E402
from ckp.core import deploy as core  # noqa: E402
from ckp.store import env as store_env, releases  # noqa: E402


class FakeProcess:
    def __init__(self, healthy=True, workers=()):
        self.healthy, self.workers, self.restarts = healthy, list(workers), []

    def restart_service(self, label, **kw):
        self.restarts.append(label)
        return True, "restarted"

    def health(self, port, path="/", **kw):
        return (self.healthy, "ok" if self.healthy else "no answer")

    def running_workers(self, state_file):
        return list(self.workers)


class FakeGithub:
    def __init__(self):
        self.records = []

    def record_deployment(self, repo, tag, environment, state, description, **kw):
        self.records.append((tag, environment, state))


def sh(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    sh(r, "init", "-b", "main")
    sh(r, "config", "user.email", "t@t")
    sh(r, "config", "user.name", "t")
    for f in ("ckp/__init__.py", "logic.py", "cockpit.py", "dispatcher/__init__.py", "dispatcher/core.py"):
        (r / f).parent.mkdir(parents=True, exist_ok=True)
        (r / f).write_text("")
    sh(r, "add", "-A")
    sh(r, "commit", "-m", "one")
    sh(r, "tag", "v2026.11.25-1")
    (r / "web.txt").write_text("two")
    sh(r, "add", "-A")
    sh(r, "commit", "-m", "two")
    sh(r, "tag", "v2026.11.25-2")
    return r


def adapter(repo, tmp_path, process, github, gate=True):
    # the working clone has no remote: HEAD is compared with itself through a fake ref
    ad = GitDeployAdapter("p", "o/p", repo, "production", home=tmp_path / "home", process=process, github=github, tests_green=gate,
                          health_wait_s=0, sleep=lambda s: None)
    ad._sha = lambda ref: sh(repo, "rev-parse", ref.replace("origin/main", "HEAD"))
    return ad


ENV = {"port": 8800, "data_dir": "x", "label": "com.example.job"}


def test_a_whole_deploy_installs_switches_restarts_checks_and_records(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(releases, "_root", lambda p, home=None: tmp_path / "home" / "releases" / p)
    pr, gh = FakeProcess(), FakeGithub()
    r = core.deploy("p", "v2026.11.25-1", ENV, adapter(repo, tmp_path, pr, gh), dry_run=False)
    assert r["ok"], r
    assert releases.current("p", tmp_path / "home") == "v2026.11.25-1" and pr.restarts == ["com.example.job"]
    assert gh.records == [("v2026.11.25-1", "production", "success")]


def test_a_dry_run_changes_nothing(repo, tmp_path):
    pr, gh = FakeProcess(), FakeGithub()
    r = core.deploy("p", "v2026.11.25-1", ENV, adapter(repo, tmp_path, pr, gh), dry_run=True)
    assert r["ok"] and r["dry_run"] and pr.restarts == [] and gh.records == []
    assert not (tmp_path / "home" / "releases").exists()


def test_not_ready_lists_the_unmet_conditions(repo, tmp_path):
    r = core.deploy("p", "v2026.11.25-1", ENV, adapter(repo, tmp_path, FakeProcess(), FakeGithub(), gate=False), dry_run=False)
    assert not r["ok"] and "tests" in r["error"] and "not ready" in r["error"]


def test_a_failed_health_check_rolls_back_to_the_previous_release(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(releases, "_root", lambda p, home=None: tmp_path / "home" / "releases" / p)
    home = tmp_path / "home"
    assert core.deploy("p", "v2026.11.25-1", ENV, adapter(repo, tmp_path, FakeProcess(), FakeGithub()), dry_run=False)["ok"]
    pr, gh = FakeProcess(healthy=False), FakeGithub()
    r = core.deploy("p", "v2026.11.25-2", ENV, adapter(repo, tmp_path, pr, gh), dry_run=False)
    assert not r["ok"] and "rolled back" in r["error"]
    assert releases.current("p", home) == "v2026.11.25-1" and gh.records[-1][2] == "failure" and len(pr.restarts) == 2   # the failed switch and the restart after the rollback


def test_a_release_that_touches_the_proxy_is_held_while_a_worker_runs(repo, tmp_path):
    (repo / "dispatcher").mkdir(exist_ok=True)
    (repo / "dispatcher" / "proxy.py").write_text("x")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-m", "proxy")
    sh(repo, "tag", "v2026.11.25-3")
    r = core.deploy("p", "v2026.11.25-3", ENV, adapter(repo, tmp_path, FakeProcess(workers=["p-9"]), FakeGithub()), dry_run=False)
    assert not r["ok"] and "p-9" in r["error"]


def test_cli_env_set_and_a_deploy_dry_run(tmp_path, monkeypatch, capsys, repo):
    monkeypatch.setenv("CP_HOME", str(tmp_path / "cphome"))
    assert cli.main(["env", "set", "--project", "p", "--env", "production", "--port", "8800", "--label", "com.example.job",
                     "--data-dir", str(tmp_path / "d")]) == 0
    assert store_env.load("p")["production"]["port"] == 8800
    capsys.readouterr()
    args = cli.build_parser().parse_args(["deploy", "--project", "p", "--repo", "o/p", "--repo-dir", str(repo), "--tag", "v2026.11.25-2"])
    assert cli.cmd_deploy(args, adapter=adapter(repo, tmp_path, FakeProcess(), FakeGithub())) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["dry_run"] and out["data"]["tag"] == "v2026.11.25-2"


def test_cli_refuses_an_unknown_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CP_HOME", str(tmp_path / "cphome"))
    assert cli.main(["deploy", "--project", "p", "--repo", "o/p", "--repo-dir", str(tmp_path), "--tag", "v2026.11.25-1"]) == 1
    assert "no environment" in json.loads(capsys.readouterr().out)["error"]


def test_untracked_files_do_not_make_the_clone_dirty_but_changed_tracked_files_do(repo, tmp_path):
    ad = adapter(repo, tmp_path, FakeProcess(), FakeGithub())
    (repo / ".DS_Store").write_text("x")
    assert ad.facts("p", "v2026.11.25-2", ENV)["clean"] is True
    (repo / "web.txt").write_text("changed")
    assert ad.facts("p", "v2026.11.25-2", ENV)["clean"] is False


def test_a_dry_run_plans_a_tag_that_does_not_exist_yet_against_head(repo, tmp_path):
    ad = adapter(repo, tmp_path, FakeProcess(), FakeGithub())
    r = core.deploy("p", "v2026.11.26-1", ENV, ad, dry_run=True)
    assert r["ok"] and r["dry_run"] and r["data"]["plan"]["mode"] == "hot"


def test_cli_prints_a_failure_as_a_result(tmp_path, monkeypatch, capsys, repo):
    monkeypatch.setenv("CP_HOME", str(tmp_path / "cphome"))
    store_env.save("p", {"production": ENV})

    class Boom:
        def facts(self, *a):
            raise RuntimeError("boom")

    args = cli.build_parser().parse_args(["deploy", "--project", "p", "--repo", "o/p", "--repo-dir", str(repo), "--tag", "v2026.11.25-2"])
    assert cli.cmd_deploy(args, adapter=Boom()) == 1
    assert "RuntimeError: boom" in json.loads(capsys.readouterr().out)["error"]


def test_a_refused_apply_leaves_no_tag_behind(tmp_path, monkeypatch, capsys, repo):
    monkeypatch.setenv("CP_HOME", str(tmp_path / "cphome"))
    store_env.save("p", {"production": ENV})
    (repo / "web.txt").write_text("changed but not committed")   # a dirty clone is refused by the readiness check
    tags_before = subprocess.run(["git", "-C", str(repo), "tag"], capture_output=True, text=True).stdout
    args = cli.build_parser().parse_args(["deploy", "--project", "p", "--repo", "o/p", "--repo-dir", str(repo), "--apply"])
    assert cli.cmd_deploy(args, adapter=adapter(repo, tmp_path, FakeProcess(), FakeGithub())) == 1
    assert subprocess.run(["git", "-C", str(repo), "tag"], capture_output=True, text=True).stdout == tags_before


def _apply(repo, tmp_path, gate, *extra):
    args = cli.build_parser().parse_args(["deploy", "--project", "p", "--repo", "o/p", "--repo-dir", str(repo), "--apply", *extra])
    return cli.cmd_deploy(args, adapter=adapter(repo, tmp_path, FakeProcess(), FakeGithub(), gate=gate))


def test_a_rollback_is_the_same_command_with_the_previous_tag_and_every_apply_is_logged(tmp_path, monkeypatch, capsys, repo):
    home = tmp_path / "home"
    monkeypatch.setenv("CP_HOME", str(home))
    store_env.save("p", {"production": ENV})
    assert _apply(repo, tmp_path, True, "--tag", "v2026.11.25-1") == 0 and _apply(repo, tmp_path, True, "--tag", "v2026.11.25-2") == 0
    capsys.readouterr()
    assert _apply(repo, tmp_path, False, "--rollback") == 0   # the gate is red now: a rollback does not need it
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["data"]["installed"] and releases.current("p", home) == "v2026.11.25-1"
    assert "fetch" not in [s["step"] for s in out["data"]["steps"]]
    assert _apply(repo, tmp_path, False, "--tag", "v2026.11.25-2") == 0 and releases.current("p", home) == "v2026.11.25-2"
    assert _apply(repo, tmp_path, False) == 1   # a new tag needs the green gate; the refusal is logged too
    lines = [json.loads(line) for line in (home / "deploy-log.jsonl").read_text().splitlines()]
    assert [(r["tag"], r["rollback"], r["ok"], r["env"]) for r in lines[:4]] == [
        ("v2026.11.25-1", False, True, "production"), ("v2026.11.25-2", False, True, "production"),
        ("v2026.11.25-1", True, True, "production"), ("v2026.11.25-2", False, True, "production")]
    assert not lines[4]["ok"] and "tests" in lines[4]["error"]


def test_rollback_without_a_previous_release_or_with_a_tag_is_refused(tmp_path, monkeypatch, capsys, repo):
    monkeypatch.setenv("CP_HOME", str(tmp_path / "home"))
    store_env.save("p", {"production": ENV})
    args = ["deploy", "--project", "p", "--repo", "o/p", "--repo-dir", str(repo), "--rollback"]
    assert cli.main(args) == 1 and "no previous release" in json.loads(capsys.readouterr().out)["error"]
    assert cli.main(args + ["--tag", "v2026.11.25-1"]) == 1 and "exclude" in json.loads(capsys.readouterr().out)["error"]


def test_a_dry_run_writes_no_log_line(tmp_path, monkeypatch, capsys, repo):
    monkeypatch.setenv("CP_HOME", str(tmp_path / "home"))
    store_env.save("p", {"production": ENV})
    args = cli.build_parser().parse_args(["deploy", "--project", "p", "--repo", "o/p", "--repo-dir", str(repo), "--tag", "v2026.11.25-2"])
    assert cli.cmd_deploy(args, adapter=adapter(repo, tmp_path, FakeProcess(), FakeGithub())) == 0
    assert not (tmp_path / "home" / "deploy-log.jsonl").exists()
