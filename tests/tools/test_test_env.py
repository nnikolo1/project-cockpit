"""The test environment (PC-84): a throwaway clone of the committed HEAD and a temporary home, a release rehearsal from the
`current` link, a refusal while production has Workers running, and nothing left behind."""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import test_env as T  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]


def repo(tmp_path):
    r = tmp_path / "dev"
    for f in ("ckp/__init__.py", "logic.py", "cockpit.py", "dispatcher/__init__.py", "dispatcher/core.py"):
        (r / f).parent.mkdir(parents=True, exist_ok=True)
        (r / f).write_text("")
    subprocess.run([*GIT, "init", "-q"], cwd=r, check=True)
    subprocess.run([*GIT, "add", "-A"], cwd=r, check=True)
    subprocess.run([*GIT, "commit", "-qm", "one"], cwd=r, check=True)
    return r


def test_clone_takes_the_committed_head_and_the_rehearsal_imports_from_the_current_link(tmp_path):
    dev = repo(tmp_path)
    (dev / "logic.py").write_text("raise SystemExit('uncommitted')")   # not committed: not tested, not released
    tmp = tmp_path / "t"
    tmp.mkdir()
    ok, detail = T.step_clone(dev, tmp)
    assert ok, detail
    assert (tmp / "repo" / "logic.py").read_text() == ""
    ok, detail = T.step_rehearsal(tmp)
    assert ok, detail
    assert (tmp / "home" / "releases" / T.PROJECT / "current").is_symlink()
    T.remove(tmp)
    assert not tmp.exists()


def test_the_tests_run_with_the_test_home_and_name(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_HOME", "/production/home")
    seen = {}

    def run(args, cwd, env):
        seen.update(env=env, args=args)
        return subprocess.CompletedProcess(args, 0)

    assert T.step_tests(tmp_path, run=run)[0]
    assert seen["env"]["CP_HOME"] == str(tmp_path / "home") and seen["env"]["CP_ENV"] == "test"
    assert seen["args"][-1].endswith("tools/merge_gate")


def test_a_failed_step_stops_the_run_and_nothing_is_left_behind(tmp_path, monkeypatch, capsys):
    made = []
    monkeypatch.setattr(T.tempfile, "mkdtemp", lambda prefix: made.append(str(tmp_path / "t")) or (tmp_path / "t").mkdir() or made[0])
    calls = []
    steps = [("a", lambda: calls.append("a") or (False, "red")), ("b", lambda: calls.append("b") or (True, ""))]
    assert T.main([], steps=steps) == 1
    out = capsys.readouterr().out
    assert calls == ["a"] and "FAIL a: red" in out and "test environment: FAIL" in out
    assert not (tmp_path / "t").exists()


def test_a_raising_step_fails_and_keep_keeps_the_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(T.tempfile, "mkdtemp", lambda prefix: (tmp_path / "t").mkdir() or str(tmp_path / "t"))
    assert T.main(["--keep"], steps=[("a", lambda: 1 / 0)]) == 1
    assert "ZeroDivisionError" in capsys.readouterr().out and (tmp_path / "t").exists()


def test_e2e_is_refused_while_production_has_workers_running(tmp_path, capsys):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"workers": {"PC-9": {"task_id": "PC-9"}}}))
    args = ["--e2e-repo", "o/throwaway", "--token-file", "tok", "--prod-state", str(state)]
    assert T.main(args, steps=[("a", lambda: (True, ""))]) == 2
    assert "PC-9" in capsys.readouterr().out
    state.write_text(json.dumps({"workers": {}}))
    assert T.main(args, steps=[("a", lambda: (True, ""))]) == 0


def test_e2e_uses_the_stub_image_and_the_test_home(tmp_path):
    seen = {}

    def run(args, cwd, env):
        seen.update(args=args, env=env)
        return subprocess.CompletedProcess(args, 0)

    a = T.argparse.Namespace(e2e_repo="o/throwaway", image="cockpit-worker:stub", token_file="tok")
    assert T.step_e2e(tmp_path, a, run=run)[0]
    assert "cockpit-worker:stub" in seen["args"] and seen["env"]["CP_ENV"] == "test"
