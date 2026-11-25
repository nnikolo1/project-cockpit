import json
import os
import pathlib
import stat
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from ckp.store import io  # noqa: E402


def test_atomic_write_creates_folders_and_leaves_no_temporary_file(tmp_path):
    f = tmp_path / "a" / "b" / "x.json"
    io.atomic_write_json(f, {"k": 1})
    assert json.loads(f.read_text()) == {"k": 1} and [p.name for p in f.parent.iterdir()] == ["x.json"]


def test_a_failed_write_keeps_the_old_file_and_removes_the_temporary_one(tmp_path):
    f = tmp_path / "x.json"
    io.atomic_write_json(f, {"old": True})
    with pytest.raises(TypeError):
        io.atomic_write_json(f, {"bad": object()})
    assert json.loads(f.read_text()) == {"old": True} and [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_mode_is_applied_to_a_new_file(tmp_path):
    f = tmp_path / "secret.json"
    io.atomic_write_json(f, {}, mode=0o600)
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600


def test_default_encoder_writes_values_json_cannot(tmp_path):
    import datetime as dt
    f = tmp_path / "d.json"
    io.atomic_write_json(f, {"d": dt.date(2026, 11, 25)}, default=lambda o: o.isoformat())
    assert json.loads(f.read_text()) == {"d": "2026-11-25"}


def test_reads_are_tolerant(tmp_path):
    assert io.read_json(tmp_path / "none.json", "dflt") == "dflt" and io.read_text(tmp_path / "none") is None
    (tmp_path / "bad.json").write_text("{not json")
    assert io.read_json(tmp_path / "bad.json", []) == []


def test_jsonl_append_and_read_skip_bad_lines(tmp_path):
    f = tmp_path / "log" / "x.jsonl"
    io.append_jsonl(f, {"a": 1})
    io.append_jsonl(f, {"b": 2})
    with open(f, "a") as fh:
        fh.write("garbage\n[1, 2]\n")
    assert io.read_jsonl(f) == [{"a": 1}, {"b": 2}] and io.read_jsonl(tmp_path / "none.jsonl") == []
