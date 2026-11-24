import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "e2e"))
import stub_claude as stub  # noqa: E402
from dispatcher import note as good_bye  # noqa: E402

TID = "r-7"


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def world(tmp_path, monkeypatch):
    clone = tmp_path / "clone"
    clone.mkdir()
    git("init", "-b", "main", cwd=clone)
    git("commit", "--allow-empty", "-m", "base", cwd=clone)
    (tmp_path / "inbox").mkdir()
    (tmp_path / "outbox").mkdir()
    monkeypatch.chdir(clone)
    return tmp_path


def play(world, mode):
    stub.run(mode, TID, str(world / "outbox"), str(world / "config"))
    return world / "outbox"


def test_mode_is_read_from_the_marker_line(tmp_path):
    f = tmp_path / "task.md"
    f.write_text("# case success\n\nMODE=success\n")
    assert stub.read_mode(str(f)) == "success"
    f.write_text("# nothing\n")
    with pytest.raises(ValueError):
        stub.read_mode(str(f))


def test_success_commits_bundles_and_writes_a_valid_note(world):
    out = play(world, "success")
    assert (out / "started").exists() and (out / "result.bundle").exists()
    n = json.loads((out / "goodbye.json").read_text())
    assert n["outcome"] == "done" and good_bye.validate(n) == []
    heads = subprocess.run(["git", "bundle", "list-heads", str(out / "result.bundle")], capture_output=True, text=True).stdout
    assert f"refs/heads/worker/{TID}" in heads


def test_gaveup_is_success_with_the_outcome_gave_up(world):
    out = play(world, "gaveup")
    n = json.loads((out / "goodbye.json").read_text())
    assert n["outcome"] == "gave_up" and good_bye.validate(n) == [] and (out / "result.bundle").exists()


def test_nocommit_has_a_note_but_no_bundle(world):
    out = play(world, "nocommit")
    assert (out / "started").exists() and not (out / "result.bundle").exists()
    assert good_bye.validate(json.loads((out / "goodbye.json").read_text())) == []


def test_nonote_has_a_bundle_but_no_note(world):
    out = play(world, "nonote")
    assert (out / "result.bundle").exists() and not (out / "goodbye.json").exists()


def test_badnote_has_a_note_that_is_not_json(world):
    out = play(world, "badnote")
    assert (out / "result.bundle").exists()
    with pytest.raises(ValueError):
        json.loads((out / "goodbye.json").read_text())


def test_silent_modes_sleep(world, monkeypatch):
    class Stop(Exception):
        pass

    def sleep(_):
        raise Stop
    monkeypatch.setattr(stub.time, "sleep", sleep)
    for mode, started in (("nostart", False), ("noactivity", True), ("overtime", True)):
        out = world / "outbox" / mode
        out.mkdir()
        with pytest.raises(Stop):
            stub.run(mode, TID, str(out), str(world / "config"))
        assert (out / "started").exists() == started
    assert list((world / "config" / "projects").rglob("*.jsonl"))   # overtime looks active to the Dispatcher
