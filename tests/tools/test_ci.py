"""CI automation: the merge gate on a green and a red tree, the state of the last run of main, the red-main finding filed once."""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import logic as L  # noqa: E402
import ci_main as C  # noqa: E402

GATE = ROOT / "tools" / "merge_gate"
GREEN = "def test_ok():\n    assert True\n"
RED = "def test_broken():\n    assert 1 == 2\n"


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


def tree(tmp_path, test_text):
    """A tiny repository with one test file, committed on main."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_one.py").write_text(test_text)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "one")
    return repo


def gate(repo, tmp_path, *args, node=True):
    """Run the gate in `repo`; a stub `node` on PATH stands in for node (the gate only checks that it is there)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "node"
    if node:
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    elif stub.exists():
        stub.unlink()
    path = os.pathsep.join([str(bin_dir)] + [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and not (pathlib.Path(p) / "node").exists()])
    r = subprocess.run([sys.executable, str(GATE), *args], cwd=repo, capture_output=True, text=True, env=dict(os.environ, PATH=path))
    return r.returncode, r.stdout.strip().splitlines()[-1]


def test_gate_passes_on_a_green_tree(tmp_path):
    assert gate(tree(tmp_path, GREEN), tmp_path) == (0, "merge gate: PASS")


def test_gate_fails_on_a_red_tree(tmp_path):
    code, last = gate(tree(tmp_path, RED), tmp_path)
    assert code == 1 and last.startswith("merge gate: FAIL")


def test_gate_fails_without_node(tmp_path):
    code, last = gate(tree(tmp_path, GREEN), tmp_path, node=False)
    assert code == 1 and "node not found" in last


def test_gate_tests_a_branch_merged_into_head_and_leaves_the_tree_alone(tmp_path):
    repo = tree(tmp_path, GREEN)
    git(repo, "checkout", "-q", "-b", "worker/x-1")
    (repo / "tests" / "test_two.py").write_text(RED)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "red")
    git(repo, "checkout", "-q", "-b", "worker/x-2", "main")
    (repo / "tests" / "test_three.py").write_text(GREEN)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "green")
    git(repo, "checkout", "-q", "main")
    assert gate(repo, tmp_path, "worker/x-2") == (0, "merge gate: PASS")
    code, last = gate(repo, tmp_path, "worker/x-1")
    assert code == 1 and last.startswith("merge gate: FAIL")
    assert sorted(p.name for p in (repo / "tests").iterdir() if p.suffix == ".py") == ["test_one.py"]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_task_queue_page_shows_a_red_main_as_a_warning():
    page = (ROOT / "web" / "index.html").read_text()
    local = L.gate_result(json.dumps(dict(state="red", reason="pytest exited with 1", sha="abc", branch="main", time="2026-11-25T10:00:00Z")))
    states = [L.ci_run(runs(conclusion=c)) for c in ("failure", "success")] + [local]
    code = "\n".join(["const tmTxt=t=>'HH:MM',tm=tmTxt;", re.search(r"^function esc\(s\).*$", page, re.M).group(0), page[page.index("function ciLine(c){"):page.index("function vQueueLocal(){")],
                      "process.stdout.write(JSON.stringify(%s.map(ciLine)))" % json.dumps(states)])
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    red, green, local = json.loads(r.stdout)
    assert 'class="warnline"' in red and "CI on main: failed (failure)" in red and "https://x/runs/7" in red
    assert 'class="muted"' in green and "CI on main: passed" in green
    assert 'class="warnline"' in local and "CI on main: failed (pytest exited with 1), local gate" in local and "<a " not in local


def runs(status="completed", conclusion="failure"):
    return json.dumps([dict(databaseId=7, status=status, conclusion=conclusion, headSha="abc", createdAt="2026-11-24T08:00:00Z", url="https://x/runs/7")])


def test_state_of_the_last_run_of_main():
    assert L.ci_run(runs(conclusion="success"))["state"] == "green"
    assert L.ci_run(runs())["state"] == "red" and L.ci_run(runs(conclusion="timed_out"))["state"] == "red"
    assert L.ci_run(runs(status="in_progress", conclusion=""))["state"] == "running"
    assert L.ci_run(runs(conclusion="cancelled"))["state"] == "other"
    for empty in ("[]", "", None, "not json", "{}"):
        assert L.ci_run(empty)["state"] == "none"


class FakeGitHub:
    """A gh stand-in: run list answers the given runs; issue list/create act on an in-memory list of open issues."""
    def __init__(self, run_json):
        self.run_json, self.issues, self.created = run_json, [], []

    def __call__(self, args):
        if args[:2] == ["run", "list"]:
            assert "main" == args[args.index("--branch") + 1]
            return self.run_json
        if args[:2] == ["issue", "list"]:
            return json.dumps(self.issues)
        if args[:2] == ["issue", "create"]:
            self.created.append(args)
            url = f"https://x/issues/{len(self.created)}"
            self.issues.append(dict(title=args[args.index("--title") + 1], url=url))
            return url
        raise AssertionError(args)


def test_red_main_finding_is_filed_once_as_a_released_job_finding():
    gh = FakeGitHub(runs())
    state = C.last_run("o/r", run=gh)
    assert C.file_red_main("o/r", state, "job", run=gh) == "https://x/issues/1"
    assert C.file_red_main("o/r", C.last_run("o/r", run=gh), "job", run=gh) is None  # still red the next day: deduplicated
    assert len(gh.created) == 1
    args = gh.created[0]
    labels = [args[i + 1] for i, x in enumerate(args) if x == "--label"]
    assert labels == ["policy-finding", "author:job"]
    assert L.is_finding(labels)  # released at once (spec 2b)
    body = args[args.index("--body") + 1]
    assert "https://x/runs/7" in body and L.parse_filed_by(body) == "job"


def test_a_new_finding_after_the_old_one_is_closed():
    gh = FakeGitHub(runs())
    C.file_red_main("o/r", C.last_run("o/r", run=gh), "job", run=gh)
    gh.issues.clear()  # the finding was worked and closed; main went red again
    assert C.file_red_main("o/r", C.last_run("o/r", run=gh), "job", run=gh) == "https://x/issues/2"


def test_main_files_only_when_red(monkeypatch, capsys):
    for conclusion, code, filed in (("success", 0, 0), ("failure", 2, 1)):
        gh = FakeGitHub(runs(conclusion=conclusion))
        monkeypatch.setattr(C.ff, "gh_run", gh)
        assert C.main(["--repo", "o/r", "--file-task"]) == code
        assert len(gh.created) == filed and json.loads(capsys.readouterr().out)["state"] == ("red" if filed else "green")
