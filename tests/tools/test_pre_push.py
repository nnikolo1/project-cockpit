"""The pre-push hook (PC-129), generalized over any clone: it blocks a push on a red gate and lets a green one through (the gate
is injected), it is installed idempotently and never overwrites another hook, and the gate's log feeds the red-main report."""
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import logic as L  # noqa: E402
import ci_main as C  # noqa: E402
import pre_push as P  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]


def git(cwd, *args):
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def clone_pair(tmp_path):
    """A bare remote and a clone of it with one commit on main."""
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(tmp_path, "init", "-q", str(work))
    (work / "a.txt").write_text("a\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "one")
    git(work, "remote", "add", "origin", str(remote))
    return remote, work


def push(work, exit_code):
    """Push with the installed hook, the gate replaced by a command that exits `exit_code`."""
    gate = f"{sys.executable} -c 'import sys; sys.exit({exit_code})'"
    return subprocess.run([*GIT, "push", "-q", "origin", "main"], cwd=work, capture_output=True, text=True, env=dict(os.environ, MERGE_GATE=gate))


def remote_head(remote):
    r = subprocess.run(["git", "rev-parse", "--verify", "-q", "refs/heads/main"], cwd=remote, capture_output=True, text=True)
    return r.stdout.strip() or None


def test_hook_blocks_a_push_on_a_red_gate_and_passes_on_a_green_one(tmp_path):
    remote, work = clone_pair(tmp_path)
    assert P.main(["--install", "--repo", str(work)]) == 0
    r = push(work, 1)
    assert r.returncode != 0 and "push refused" in r.stderr and remote_head(remote) is None
    r = push(work, 0)
    assert r.returncode == 0, r.stderr
    assert remote_head(remote) == git(work, "rev-parse", "HEAD").strip()


def test_installing_twice_changes_nothing_and_says_so(tmp_path, caplog):
    _, work = clone_pair(tmp_path)
    hook = work / ".git" / "hooks" / "pre-push"
    ok, msg = P.install(work)
    assert ok and msg.startswith("installed") and os.access(hook, os.X_OK)
    before = (hook.read_bytes(), hook.stat().st_mtime_ns)
    ok, msg = P.install(work)
    assert ok and msg.startswith("already installed")
    assert (hook.read_bytes(), hook.stat().st_mtime_ns) == before


def test_another_hook_is_never_overwritten(tmp_path):
    _, work = clone_pair(tmp_path)
    hook = work / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 0\n")
    assert P.main(["--install", "--repo", str(work)]) == 1
    assert hook.read_text() == "#!/bin/sh\nexit 0\n"


def test_install_outside_a_clone_is_refused(tmp_path):
    assert P.main(["--install", "--repo", str(tmp_path)]) == 1


def gate_log(**kw):
    return json.dumps(dict(dict(state="red", reason="pytest exited with 1", sha="abc", branch="main", merged=None, time="2026-11-25T10:00:00Z"), **kw))


def hosted(conclusion="failure", at="2026-11-24T08:00:00Z"):
    return L.ci_run(json.dumps([dict(databaseId=7, status="completed", conclusion=conclusion, headSha="def", createdAt=at, url="https://x/runs/7")]))


def test_gate_log_counts_only_for_a_plain_run_on_main():
    assert L.gate_result(gate_log())["state"] == "red" and L.gate_result(gate_log())["source"] == "local gate"
    assert L.gate_result(gate_log(state="green", reason=None))["conclusion"] == "success"
    for other in (gate_log(branch="feature"), gate_log(merged="worker/x-1"), gate_log(state="maybe"), "", None, "not json", "[]"):
        assert L.gate_result(other) is None


def test_the_report_reads_whichever_result_exists_the_newer_when_both_do():
    local = L.gate_result(gate_log(state="green", reason=None))
    assert L.ci_latest(hosted(), local)["state"] == "green"                                  # local is newer
    assert L.ci_latest(hosted(at="2026-11-26T00:00:00Z"), local)["state"] == "red"          # hosted is newer
    assert L.ci_latest(None, local) is local and L.ci_latest(hosted(), None)["url"] == "https://x/runs/7"
    assert L.ci_latest(dict(state="unknown", error="offline"), local) is local
    assert L.ci_latest(L.ci_run("[]"), None)["state"] == "none"
    assert "local merge gate" in L.red_main_body(L.gate_result(gate_log())) and "https://x/runs/7" in L.red_main_body(hosted())


def test_merge_gate_logs_its_result_in_the_git_folder_and_ci_main_reads_it(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_one.py").write_text("def test_broken():\n    assert 1 == 2\n")
    git(repo.parent, "init", "-q", str(repo))
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "one")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "node").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "node").chmod(0o755)
    env = dict(os.environ, PATH=os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]))
    subprocess.run([sys.executable, str(ROOT / "tools" / "merge_gate")], cwd=repo, capture_output=True, env=env)
    rec = json.loads((repo / ".git" / L.GATE_LOG).read_text())
    assert rec["state"] == "red" and rec["branch"] == "main" and rec["merged"] is None and not (repo / L.GATE_LOG).exists()
    def offline(args):
        raise OSError("GitHub unavailable")
    state = C.last_run("o/r", run=offline, repo_dir=repo)  # GitHub down: the local result still reports the red main
    assert state["state"] == "red" and state["source"] == "local gate" and state["sha"] == git(repo, "rev-parse", "HEAD").strip()
