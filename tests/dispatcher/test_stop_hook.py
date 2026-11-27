import io
import json
import re
import subprocess
import pathlib

import pytest

from dispatcher import runtime as rt
from dispatcher.image import stop_hook as hook

GOOD = {"outcome": "done", "summary": "Did the work."}


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


@pytest.fixture
def world(tmp_path):
    """A clone with origin/main, an outbox, and a helper to commit on worker/T-1."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "-b", "main", cwd=origin)
    git("commit", "--allow-empty", "-m", "base", cwd=origin)
    clone = tmp_path / "clone"
    git("clone", str(origin), str(clone), cwd=tmp_path)
    git("checkout", "-b", "worker/T-1", cwd=clone)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    env = {"WORKER_TASK_ID": "T-1", "WORKER_OUTBOX": str(outbox)}
    return clone, outbox, env


def run(clone, env, **event):
    err = io.StringIO()
    import contextlib
    with contextlib.redirect_stderr(err):
        rc = hook.main(io.StringIO(json.dumps(dict(event, cwd=str(clone)))), env)
    return rc, err.getvalue()


def commit(clone):
    git("commit", "--allow-empty", "-m", "work", cwd=clone)


def test_all_good_ends(world):
    clone, outbox, env = world
    (outbox / "goodbye.json").write_text(json.dumps(GOOD))
    (outbox / "result.bundle").write_text("x")
    commit(clone)
    assert run(clone, env) == (0, "")


def test_missing_note_refuses_then_lets_go(world):
    clone, outbox, env = world
    rc, msg = run(clone, env)
    assert rc == 2 and "goodbye.json is missing" in msg
    assert run(clone, env, stop_hook_active=True)[0] == 0


def test_unreadable_and_invalid_note(world):
    clone, outbox, env = world
    (outbox / "goodbye.json").write_text("{nope")
    assert "not readable" in run(clone, env)[1]
    (outbox / "goodbye.json").write_text(json.dumps({"outcome": "maybe"}))
    rc, msg = run(clone, env)
    assert rc == 2 and "outcome" in msg and "summary" in msg


def test_done_needs_commit_and_bundle(world):
    clone, outbox, env = world
    (outbox / "goodbye.json").write_text(json.dumps(GOOD))
    rc, msg = run(clone, env)
    assert rc == 2 and "no commit beyond main" in msg and "result.bundle is missing" in msg
    commit(clone)
    msg = run(clone, env)[1]
    assert "no commit" not in msg and "result.bundle is missing" in msg


def test_gave_up_needs_only_the_note(world):
    clone, outbox, env = world
    (outbox / "goodbye.json").write_text(json.dumps({"outcome": "gave_up", "summary": "Could not."}))
    assert run(clone, env) == (0, "")


def test_not_a_dispatcher_run_is_ignored(world):
    clone, _, _ = world
    assert run(clone, {})[0] == 0


def test_launch_passes_task_and_outbox():
    cmd = " ".join(_launch_cmd())
    assert "WORKER_TASK_ID=T-5" in cmd and "WORKER_OUTBOX=/area/T-5/outbox" in cmd


def _launch_cmd():
    from tests.dispatcher.test_runtime import make
    import tempfile
    r, _ = make(pathlib.Path(tempfile.mkdtemp()))
    return r.launch("worker1", "T-5")[1]


def test_containerfile_wires_the_hook():
    text = (pathlib.Path(__file__).resolve().parents[2] / "dispatcher" / "image" / "Containerfile").read_text()
    s = json.loads(re.search(r"^ARG WORKER_SETTINGS='(.*)'$", text, re.M).group(1))
    cmd = s["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert cmd == "python3 /opt/hook/stop_hook.py"
    assert "COPY dispatcher/image/stop_hook.py dispatcher/note.py /opt/hook/" in text
    assert "skills/" in text and "SKILL.md" in text


def transcript(tmp_path):
    def line(kind, mid, **u):
        return json.dumps({"type": kind, "message": {"id": mid, "usage": u}})
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join([
        line("user", "u1", input_tokens=999),
        line("assistant", "m1", input_tokens=1, output_tokens=2, cache_creation_input_tokens=3,
             cache_read_input_tokens=4),
        line("assistant", "m1", input_tokens=1, output_tokens=2, cache_creation_input_tokens=3,
             cache_read_input_tokens=4),  # same message, second content block
        line("assistant", "m2", input_tokens=10, output_tokens=20),
        "not json",
    ]))
    return p


def test_tokens_written_over_workers_value(world, tmp_path):
    clone, outbox, env = world
    (outbox / "goodbye.json").write_text(json.dumps(dict(GOOD, tokens=5)))
    (outbox / "result.bundle").write_text("x")
    commit(clone)
    assert run(clone, env, transcript_path=str(transcript(tmp_path)))[0] == 0
    assert json.loads((outbox / "goodbye.json").read_text()) == dict(GOOD, tokens=40)


def test_refused_stop_leaves_note_unchanged(world, tmp_path):
    clone, outbox, env = world
    (outbox / "goodbye.json").write_text(json.dumps(GOOD))
    assert run(clone, env, transcript_path=str(transcript(tmp_path)))[0] == 2
    assert json.loads((outbox / "goodbye.json").read_text()) == GOOD


def test_second_stop_also_stamps(world, tmp_path):
    clone, outbox, env = world
    (outbox / "goodbye.json").write_text(json.dumps(GOOD))
    assert run(clone, env, stop_hook_active=True, transcript_path=str(transcript(tmp_path)))[0] == 0
    assert json.loads((outbox / "goodbye.json").read_text())["tokens"] == 40


def test_unreadable_transcript_keeps_value_and_warns(world, tmp_path, caplog):
    clone, outbox, env = world
    (outbox / "goodbye.json").write_text(json.dumps(dict(GOOD, tokens=5)))
    (outbox / "result.bundle").write_text("x")
    commit(clone)
    assert run(clone, env, transcript_path=str(tmp_path / "missing.jsonl"))[0] == 0
    assert json.loads((outbox / "goodbye.json").read_text())["tokens"] == 5
    assert "tokens not written" in caplog.text
