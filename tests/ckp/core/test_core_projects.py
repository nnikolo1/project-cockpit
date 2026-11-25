import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from ckp.core import projects  # noqa: E402
from ckp.store import env, registry  # noqa: E402


def fake_run(url="git@github.com:o/n.git", inside="true"):
    def run(args):
        if "rev-parse" in args:
            return inside + "\n"
        if "get-url" in args:
            return url + "\n"
        raise AssertionError(args)
    return run


def rec(**kw):
    r = {"name": "alpha", "repo": "o/n", "repo_dir": "/x/alpha", "short": "AL", "first": 5}
    r.update(kw)
    return r


def test_add_creates_folders_env_and_entry(tmp_path):
    res = projects.add_project(rec(), home=tmp_path, run=fake_run())
    assert res["ok"] and res["data"]["created"]
    assert (tmp_path / "alpha").is_dir()
    assert (tmp_path / "task-queue" / "area" / "alpha").is_dir()
    assert env.load("alpha", tmp_path) == {}
    assert (tmp_path / "env" / "alpha.json").exists()
    assert registry.load(tmp_path) == [rec()]


def test_defaults_for_short_and_first(tmp_path):
    res = projects.add_project({"name": "beta", "repo": "o/n", "repo_dir": "/x"}, home=tmp_path, run=fake_run())
    assert res["data"]["project"]["short"] == "BETA" and res["data"]["project"]["first"] == 1


def test_identical_record_is_idempotent(tmp_path):
    projects.add_project(rec(), home=tmp_path, run=fake_run())
    res = projects.add_project(rec(), home=tmp_path, run=fake_run())
    assert res["ok"] and not res["data"]["created"]
    assert len(registry.load(tmp_path)) == 1


def test_different_record_same_name_refused(tmp_path):
    projects.add_project(rec(), home=tmp_path, run=fake_run())
    res = projects.add_project(rec(first=9), home=tmp_path, run=fake_run())
    assert not res["ok"] and "different record" in res["error"]


def test_short_clash_refused_case_insensitive(tmp_path):
    projects.add_project(rec(), home=tmp_path, run=fake_run())
    res = projects.add_project(rec(name="gamma", short="al"), home=tmp_path, run=fake_run())
    assert not res["ok"] and "alpha" in res["error"]
    assert len(registry.load(tmp_path)) == 1


def test_dry_run_changes_nothing(tmp_path):
    res = projects.add_project(rec(), dry_run=True, home=tmp_path, run=fake_run())
    assert res["ok"] and res["dry_run"]
    assert not (tmp_path / "alpha").exists() and registry.load(tmp_path) == []


def test_validation_problems(tmp_path):
    for bad in (rec(name="a/b"), rec(repo="nope"), rec(short="a b"), rec(first=0)):
        assert not projects.add_project(bad, home=tmp_path, run=fake_run())["ok"]


def test_remote_mismatch_and_non_clone_refused(tmp_path):
    assert "not o/n" in projects.add_project(rec(), home=tmp_path, run=fake_run(url="git@github.com:o/other.git"))["error"]
    assert not projects.add_project(rec(), home=tmp_path, run=fake_run(inside="false"))["ok"]

    def boom(args):
        raise OSError("no git")
    assert "not a git clone" in projects.add_project(rec(), home=tmp_path, run=boom)["error"]
    assert registry.load(tmp_path) == []


def test_https_remote_matches(tmp_path):
    assert projects.add_project(rec(), home=tmp_path, run=fake_run(url="https://github.com/o/n"))["ok"]


def test_list_get_remove(tmp_path):
    assert projects.list_projects(tmp_path)["data"]["projects"] == []
    projects.add_project(rec(), home=tmp_path, run=fake_run())
    assert projects.list_projects(tmp_path)["data"]["projects"] == [rec()]
    assert projects.get_project("alpha", tmp_path)["data"]["project"] == rec()
    assert not projects.get_project("zzz", tmp_path)["ok"]
    assert projects.remove_project("alpha", tmp_path)["ok"]
    assert registry.load(tmp_path) == []
    assert (tmp_path / "alpha").is_dir()   # data is never removed
    assert not projects.remove_project("alpha", tmp_path)["ok"]
