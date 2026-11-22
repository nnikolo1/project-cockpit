import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import json
import os
import subprocess
from bootfill import core as B, adapters as A

EXAMPLE = json.loads((pathlib.Path(__file__).parent.parent / "bootfill" / "config.example.json").read_text())
ENTRY = dict(number=7, title="t", project="p", key="T-7")
PROJ = dict(repo="o/r", manager="m")


def split(cmd, flag):
    return cmd[cmd.index(flag) + 1].split(",")


def test_final_command_line_carries_the_allow_and_deny_lists():
    cmd = B.worker_command(EXAMPLE["worker"], "PROMPT")
    assert cmd[:3] == ["claude", "-p", "PROMPT"]
    allow, deny = split(cmd, "--allowedTools"), split(cmd, "--disallowedTools")
    for need in ("Read", "Edit", "Write", "Glob", "Grep", "Bash(git status *)", "Bash(git commit *)", "Bash(git switch *)", "Bash(python3 *)", "Bash(pytest *)",
                 "Bash(git push origin boot/*)",  # {branch_prefix} is rendered
                 "Bash(gh issue view *)", "Bash(gh issue comment *)", "Bash(gh issue edit * --add-assignee *)", "Bash(gh pr create *--draft*)"):
        assert need in allow, need
    for banned in ("Bash(gh *)", "Bash(git *)", "Bash(git push *)", "Bash(gh api *)", "Bash(sqlite3 *)", "Bash(curl *)", "Bash", "Bash(*)"):
        assert banned not in allow, banned  # no broad grants
    for need in ("Bash(git push *--force*)", "Bash(git push * main*)", "Bash(git push *:**)", "Bash(gh api *)", "Bash(gh pr merge *)", "Bash(gh issue close *)",
                 "Bash(gh issue edit *--add-label*)", "Bash(sqlite3 *)", "Bash(rm -rf *)", "Bash(sudo *)", "Bash(curl *)", "Bash(wget *)", "Bash(pip *)", "Bash(ln *)"):
        assert need in deny, need
    assert not set(allow) & set(deny)  # a deny rule beats an allow rule, so an overlap would be a dead entry
    assert "{" not in cmd[cmd.index("--allowedTools") + 1] and "{" not in cmd[cmd.index("--disallowedTools") + 1]


def test_branch_prefix_follows_the_config():
    cfg = dict(EXAMPLE["worker"], branch_prefix="auto/")
    assert "Bash(git push origin auto/*)" in split(B.worker_command(cfg, "x"), "--allowedTools")


def test_config_override_replaces_the_lists():
    cfg = dict(EXAMPLE["worker"], command=["claude", "-p", "{prompt}", "--allowedTools", "Read"], disallowed=["Bash(rm *)"])
    cmd = B.worker_command(cfg, "x")
    assert split(cmd, "--allowedTools") == ["Read"] and split(cmd, "--disallowedTools") == ["Bash(rm *)"]
    cmd = B.worker_command(dict(cfg, disallowed=[]), "x")
    assert "--disallowedTools" not in cmd  # an empty list renders nothing


def test_worker_rules_block_follows_the_first_action_and_override_replaces_it():
    p = B.worker_prompt(ENTRY, PROJ)
    assert p.startswith("FIRST ACTION") and B.DEFAULT_WORKER_RULES in p
    assert p.index("--add-assignee") < p.index("RULES") < p.index("Then work on issue")
    for word in ("never by deletion", "IF NOT EXISTS", "question issue", "Never merge", "belong to the task"):
        assert word in p
    q = B.worker_prompt(ENTRY, PROJ, "MY RULES")
    assert "MY RULES" in q and "RULES (the repository" not in q and q.startswith("FIRST ACTION")
    assert "RULES" not in B.worker_prompt(ENTRY, PROJ, "")  # empty value: no block
    assert "A. B." in B.worker_prompt(ENTRY, PROJ, ["A.", "B."])


def make_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    for c in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "i"]):
        subprocess.run(["git", "-C", str(repo)] + c, check=True, env=env, capture_output=True)
    return repo, tmp_path / "repo-wt-boot-7"


def test_symlinks_block_the_start_and_nothing_is_changed(tmp_path):
    repo, wt = make_worktree(tmp_path)
    cfg = dict(EXAMPLE["worker"], command=["true"])
    w = A.Workers(cfg, tmp_path / "data")
    sub = tmp_path / "repo-wt-boot-7"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", str(sub), "-b", "boot/7"], check=True, capture_output=True)
    (sub / "pkg").mkdir()
    os.symlink(tmp_path / "some.db", sub / "pkg" / "data.db")  # one level down, pointing outside the worktree
    before = sorted(os.listdir(sub))
    try:
        w.start(ENTRY, dict(PROJ, dir=str(repo)))
        raise AssertionError("worker must not start")
    except RuntimeError as ex:
        assert "data.db" in str(ex) and "worker NOT started" in str(ex)
    assert sorted(os.listdir(sub)) == before and os.path.islink(sub / "pkg" / "data.db")  # nothing removed
    assert not list((tmp_path / "data" / "workers").glob("*.rc"))


def test_top_level_link_found_and_deeper_links_and_git_ignored(tmp_path):
    os.symlink("/x", tmp_path / "top")
    (tmp_path / "a" / "b").mkdir(parents=True)
    os.symlink("/y", tmp_path / "a" / "b" / "deep")  # two levels down: out of scope
    os.symlink("/z", tmp_path / ".git")
    assert B.find_symlinks(tmp_path) == [("top", "/x")]
    assert B.find_symlinks(tmp_path / "a") == [("b/deep", "/y")]  # relative to the directory given


def test_clean_worktree_starts_with_the_role_marker_and_env_override_replaces_it(tmp_path):
    repo, wt = make_worktree(tmp_path)
    out = tmp_path / "env.txt"
    cfg = dict(EXAMPLE["worker"], command=["sh", "-c", f"echo $BOOTFILL_ROLE:$EXTRA > {out}"], disallowed=[])
    h = A.Workers(cfg, tmp_path / "d1").start(ENTRY, dict(PROJ, dir=str(repo)))
    import time
    for _ in range(100):
        if os.path.exists(h["rc"]):
            break
        time.sleep(0.05)
    assert out.read_text().strip() == "worker:"
    h = A.Workers(cfg, tmp_path / "d2", env={"EXTRA": "1"}).start(ENTRY, dict(PROJ, dir=str(repo)))
    for _ in range(100):
        if os.path.exists(h["rc"]):
            break
        time.sleep(0.05)
    assert out.read_text().strip() == ":1"  # the override replaces the default mapping
