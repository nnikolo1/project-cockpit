import pathlib
import stat
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from ckp.store import env  # noqa: E402


def rec(port, data, label):
    return {"port": port, "data_dir": data, "label": label}


def test_load_of_a_missing_file_is_empty(tmp_path):
    assert env.load("p", tmp_path) == {}


def test_save_and_load_round_trip_with_private_mode(tmp_path):
    envs = {"production": rec(8800, "/d/prod", "L1")}
    env.save("p", envs, tmp_path)
    assert env.load("p", tmp_path) == envs
    assert stat.S_IMODE((tmp_path / "env" / "p.json").stat().st_mode) == 0o600


def test_load_tolerates_a_broken_file(tmp_path):
    (tmp_path / "env").mkdir()
    (tmp_path / "env" / "p.json").write_text("{nope")
    assert env.load("p", tmp_path) == {}


def test_unusable_project_name(tmp_path):
    with pytest.raises(ValueError):
        env.load("../x", tmp_path)


def test_valid_set_has_no_problems():
    assert env.validate({"a": {"prod": rec(8800, "/d/a", "A"), "test": rec(8801, "/d/a-test", "A2")}, "b": {"prod": rec(8900, "/d/b", "B")}}) == []


def test_shared_port_data_folder_and_label_are_reported():
    p = env.validate({"a": {"x": rec(8800, "/d/a", "L")}, "b": {"x": rec(8800, "/d/a", "L")}})
    text = " ".join(p)
    assert "share port 8800" in text and "share the data folder" in text and "share the label" in text


def test_port_out_of_range_or_not_a_number():
    for bad in (80, 1023, 65536, "8800", None):
        assert env.validate({"a": {"x": rec(bad, "/d/a", "L")}}), bad
    assert env.validate({"a": {"x": rec(1024, "/d/a", "L")}}) == [] and env.validate({"a": {"y": rec(65535, "/d/b", "M")}}) == []


def test_nested_data_folders_are_reported_either_way():
    assert env.validate({"a": {"x": rec(8800, "/d/a", "L1"), "y": rec(8801, "/d/a/inner", "L2")}})
    assert env.validate({"a": {"x": rec(8800, "/d/a/inner", "L1"), "y": rec(8801, "/d/a", "L2")}})
    assert env.validate({"a": {"x": rec(8800, "/d/a", "L1"), "y": rec(8801, "/d/ab", "L2")}}) == []
