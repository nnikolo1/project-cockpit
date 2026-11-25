import datetime
import subprocess

import pytest

from ckp.adapters import git

DAY = datetime.date(2026, 11, 25)


def sh(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    sh(r, "config", "user.email", "t@example.invalid")
    sh(r, "config", "user.name", "T")
    (r / "a.txt").write_text("a")
    (r / "sub").mkdir()
    (r / "sub" / "b.txt").write_text("b")
    sh(r, "add", ".")
    sh(r, "commit", "-q", "-m", "one")
    return r


def commit(repo, name, text):
    (repo / name).write_text(text)
    sh(repo, "add", ".")
    sh(repo, "commit", "-q", "-m", name)
    return sh(repo, "rev-parse", "HEAD")


def test_next_tag_counts_per_date(repo):
    sha = sh(repo, "rev-parse", "HEAD")
    assert git.next_tag(repo, DAY) == "v2026.11.25-1"
    git.tag(repo, "v2026.11.25-1", sha)
    git.tag(repo, "v2026.11.24-7", sha)
    assert git.next_tag(repo, DAY) == "v2026.11.25-2"
    git.tag(repo, "v2026.11.25-2", sha)
    git.tag(repo, "v2026.11.25-10", sha)
    assert git.next_tag(repo, DAY) == "v2026.11.25-11"


def test_tag_refuses_existing_tag(repo):
    sha = sh(repo, "rev-parse", "HEAD")
    git.tag(repo, "v2026.11.25-1", sha)
    with pytest.raises(subprocess.CalledProcessError):
        git.tag(repo, "v2026.11.25-1", sha)


def test_archive_unpacks_the_tag_not_the_working_tree(repo, tmp_path):
    git.tag(repo, "v2026.11.25-1", sh(repo, "rev-parse", "HEAD"))
    (repo / "later.txt").write_text("x")  # not in the tag
    out = git.archive(repo, "v2026.11.25-1", tmp_path / "out")
    assert out == tmp_path / "out"
    assert (out / "sub" / "b.txt").read_text() == "b"
    assert not (out / "later.txt").exists()


def test_archive_of_unknown_tag_fails(repo, tmp_path):
    with pytest.raises(subprocess.CalledProcessError):
        git.archive(repo, "v1999.01.01-1", tmp_path / "out")


def test_changed_paths(repo):
    git.tag(repo, "v2026.11.25-1", sh(repo, "rev-parse", "HEAD"))
    commit(repo, "c.txt", "c")
    (repo / "sub" / "b.txt").write_text("b2")
    sha = commit(repo, "d.txt", "d")
    git.tag(repo, "v2026.11.25-2", sha)
    assert sorted(git.changed_paths(repo, "v2026.11.25-1", "v2026.11.25-2")) == ["c.txt", "d.txt", "sub/b.txt"]
    assert git.changed_paths(repo, "v2026.11.25-2", "v2026.11.25-2") == []


def test_changed_paths_without_old_tag_lists_everything(repo):
    git.tag(repo, "v2026.11.25-1", sh(repo, "rev-parse", "HEAD"))
    assert sorted(git.changed_paths(repo, None, "v2026.11.25-1")) == ["a.txt", "sub/b.txt"]


def test_injected_run_receives_a_git_command():
    seen = []
    git.next_tag("/r", DAY, run=lambda a: seen.append(a) or "")
    assert seen == [["git", "-C", "/r", "tag", "--list", "v2026.11.25-*"]]


def test_push_tag_runs_git_push_origin_tag():
    calls = []
    git.push_tag("/r", "v2026.11.25-1", run=lambda args: calls.append(list(args)) or "")
    assert calls == [["git", "-C", "/r", "push", "origin", "v2026.11.25-1"]]
