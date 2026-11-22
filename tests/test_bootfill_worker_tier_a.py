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
                 "Bash(gh issue view *)", "Bash(gh issue comment *)", "Bash(gh issue edit * --add-assignee *)"):
        assert need in allow, need
    for banned in ("Bash(gh *)", "Bash(git *)", "Bash(git push *)", "Bash(gh api *)", "Bash(sqlite3 *)", "Bash(curl *)", "Bash", "Bash(*)"):
        assert banned not in allow, banned  # no broad grants
    assert not any("push" in a or "pr create" in a or "Bash(gh pr" in a for a in allow)  # ADR 0020: the job pushes and opens the pull request
    assert "git push" not in " ".join(cmd[:cmd.index("--disallowedTools")]) and "pr create" not in " ".join(cmd[:cmd.index("--disallowedTools")])
    for need in ("Bash(git push *)", "Bash(gh pr *)", "Bash(git remote *)", "Bash(gh api *)", "Bash(gh issue close *)",
                 "Bash(gh issue edit *--add-label*)", "Bash(sqlite3 *)", "Bash(rm -rf *)", "Bash(sudo *)", "Bash(curl *)", "Bash(wget *)", "Bash(pip *)", "Bash(ln *)"):
        assert need in deny, need
    assert not set(allow) & set(deny)  # a deny rule beats an allow rule, so an overlap would be a dead entry
    assert "{" not in cmd[cmd.index("--allowedTools") + 1] and "{" not in cmd[cmd.index("--disallowedTools") + 1]


def test_branch_prefix_placeholder_is_rendered_in_both_lists():
    cfg = dict(EXAMPLE["worker"], branch_prefix="auto/", command=["c", "{prompt}", "--allowedTools", "Bash(git switch {branch_prefix}*)"], disallowed=["Bash(x {branch_prefix}*)"])
    cmd = B.worker_command(cfg, "x")
    assert split(cmd, "--allowedTools") == ["Bash(git switch auto/*)"] and split(cmd, "--disallowedTools") == ["Bash(x auto/*)"]


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
    for word in ("never by deletion", "IF NOT EXISTS", "question issue", "Never merge, never push, never open a pull request", "belong to the task"):
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


# ---- the job pushes the branch and opens the draft pull request (ADR 0020), tested with fakes
import test_bootfill_job as TJ
from bootfill import job as J


def finish(tmp_path, ahead, existing_pr=None, pr_fail=False):
    ctx, h, w, qu, notes = TJ.make(tmp_path, TJ.many(2), mode="real", q=TJ.quota_of(80, 1.0, week_used=90))
    J.poll(ctx, TJ.NOW)
    h.marked = {2, 3}
    w.ahead = {2: ahead}
    h.pr_fail = pr_fail
    if existing_pr:
        h.prs[2] = existing_pr
    w.rc = {2: 0, 3: 1}
    J.poll(ctx, TJ.NOW + 1800)
    recs = [r for r in A.read_jsonl(ctx.decisions) if r.get("number") == 2]
    return h, w, recs


def test_job_pushes_and_opens_a_draft_pr_after_commits(tmp_path):
    h, w, recs = finish(tmp_path, ahead=2)
    assert w.pushed == [2] and h.opened[0][0] == "boot/2"
    aw = [r for r in recs if r.get("state") == "awaiting_acceptance"]
    assert aw and aw[0]["pr"] == 502


def test_no_new_commit_means_no_push_and_no_pr_recorded_plainly(tmp_path):
    h, w, recs = finish(tmp_path, ahead=0)
    assert w.pushed == [] and not getattr(h, "opened", [])
    nm = [r for r in recs if r.get("state") == "needs_you"]
    assert nm and "committed nothing" in json.dumps(nm[0])


def test_existing_pr_is_reused_without_push(tmp_path):
    h, w, recs = finish(tmp_path, ahead=2, existing_pr=77)
    assert w.pushed == [] and [r for r in recs if r.get("state") == "awaiting_acceptance"][0]["pr"] == 77


def test_pr_failure_after_commits_is_needs_you(tmp_path):
    h, w, recs = finish(tmp_path, ahead=1, pr_fail=True)
    assert w.pushed == [2] and "could not push or open" in json.dumps([r for r in recs if r.get("state") == "needs_you"][0])


def test_push_target_guard():
    B.check_push_target("boot/7", "boot/", "main")
    for bad in ("main", "boot/7:main", "feature/x", "-f", "boot/"[:0] + "x"):
        try:
            B.check_push_target(bad, "boot/", "main")
            raise AssertionError(bad)
        except RuntimeError:
            pass
    try:
        B.check_push_target("boot/main", "boot/", "boot/main")
        raise AssertionError
    except RuntimeError:
        pass
    assert B.default_branch(lambda c: "origin/trunk\n") == "trunk"
    def boom(c):
        raise RuntimeError("x")
    assert B.default_branch(boom) == "main"


def test_real_push_and_commit_count_in_a_temp_repo(tmp_path):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    def git(*a, cwd=None):
        subprocess.run(["git", *a], check=True, env=env, capture_output=True, cwd=cwd)
    remote = tmp_path / "remote.git"
    git("init", "-q", "--bare", str(remote))
    repo = tmp_path / "repo"
    git("init", "-q", "-b", "main", str(repo))
    git("-C", str(repo), "remote", "add", "origin", str(remote))
    git("-C", str(repo), "commit", "-q", "--allow-empty", "-m", "i")
    wt = tmp_path / "repo-wt-boot-7"
    git("-C", str(repo), "worktree", "add", "-q", str(wt), "-b", "boot/7")
    w = A.Workers(dict(command=["true"]), tmp_path / "d")
    pc = dict(dir=str(repo))
    assert w.commits_ahead(ENTRY, pc) == 0
    git("-C", str(wt), "commit", "-q", "--allow-empty", "-m", "work")
    assert w.commits_ahead(ENTRY, pc) == 1
    assert w.push(ENTRY, pc) == "boot/7"
    out = subprocess.run(["git", "-C", str(remote), "branch", "--list"], capture_output=True, text=True).stdout
    assert "boot/7" in out
