import os
import pathlib
import stat
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from ckp.store import releases  # noqa: E402


@pytest.fixture
def home(tmp_path):
    yield tmp_path / "home"
    for dp, _dn, _fn in os.walk(tmp_path):  # release folders are read-only: make them removable
        os.chmod(dp, 0o755)


@pytest.fixture
def tree(tmp_path):
    t = tmp_path / "tree"
    (t / "sub").mkdir(parents=True)
    (t / "a.txt").write_text("a")
    (t / "sub" / "b.txt").write_text("b")
    return t


def test_release_path_layout_and_unusable_names(home):
    assert releases.release_path("p", "v2026.11.25-1", home) == home / "releases" / "p" / "v2026.11.25-1"
    with pytest.raises(ValueError):
        releases.release_path("p", "../x", home)
    with pytest.raises(ValueError):
        releases.release_path("a/b", "v2026.11.25-1", home)


def test_install_copies_and_makes_read_only(home, tree):
    dest = releases.install_release("p", "v2026.11.25-1", tree, home)
    assert (dest / "sub" / "b.txt").read_text() == "b"
    assert not os.access(dest / "a.txt", os.W_OK) and not os.access(dest, os.W_OK)
    assert not (dest / "a.txt").stat().st_mode & stat.S_IWUSR


def test_install_refuses_an_existing_tag(home, tree):
    releases.install_release("p", "v2026.11.25-1", tree, home)
    with pytest.raises(FileExistsError):
        releases.install_release("p", "v2026.11.25-1", tree, home)


def test_failed_install_leaves_no_partial_folder(home, tmp_path):
    with pytest.raises(OSError):
        releases.install_release("p", "v2026.11.25-1", tmp_path / "missing", home)
    assert releases.list_releases("p", home) == []
    assert not [x for x in (home / "releases" / "p").iterdir()]


def test_list_is_newest_first_by_date_and_counter(home, tree):
    for t in ("v2026.11.25-2", "v2026.11.25-10", "v2026.11.24-3", "v2026.12.01-1"):
        releases.install_release("p", t, tree, home)
    (home / "releases" / "p" / "notatag").mkdir()
    assert releases.list_releases("p", home) == ["v2026.12.01-1", "v2026.11.25-10", "v2026.11.25-2", "v2026.11.24-3"]
    assert releases.list_releases("none", home) == []


def test_switch_remembers_previous_and_refuses_unknown_tag(home, tree):
    for t in ("v2026.11.25-1", "v2026.11.25-2"):
        releases.install_release("p", t, tree, home)
    assert releases.current("p", home) is None and releases.previous("p", home) is None
    releases.switch_current("p", "v2026.11.25-1", home)
    assert releases.current("p", home) == "v2026.11.25-1" and releases.previous("p", home) is None
    releases.switch_current("p", "v2026.11.25-2", home)
    assert releases.current("p", home) == "v2026.11.25-2" and releases.previous("p", home) == "v2026.11.25-1"
    assert (home / "releases" / "p" / "current" / "a.txt").read_text() == "a"
    with pytest.raises(LookupError):
        releases.switch_current("p", "v2026.11.25-9", home)
    assert releases.current("p", home) == "v2026.11.25-2"


def test_switching_to_the_same_tag_keeps_previous(home, tree):
    for t in ("v2026.11.25-1", "v2026.11.25-2"):
        releases.install_release("p", t, tree, home)
        releases.switch_current("p", t, home)
    releases.switch_current("p", "v2026.11.25-2", home)
    assert releases.previous("p", home) == "v2026.11.25-1"


def test_rollback_returns_previous_and_fails_without_one(home, tree):
    for t in ("v2026.11.25-1", "v2026.11.25-2"):
        releases.install_release("p", t, tree, home)
    releases.switch_current("p", "v2026.11.25-1", home)
    with pytest.raises(LookupError):
        releases.rollback("p", home)
    releases.switch_current("p", "v2026.11.25-2", home)
    assert releases.rollback("p", home) == "v2026.11.25-1"
    assert releases.current("p", home) == "v2026.11.25-1"
