import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from ckp.store import layout  # noqa: E402


def test_home_is_cp_home_or_the_default(monkeypatch, tmp_path):
    monkeypatch.delenv("CP_HOME", raising=False)
    assert layout.home() == layout.DATA_DIR
    monkeypatch.setenv("CP_HOME", str(tmp_path / "h"))
    assert layout.home() == tmp_path / "h" and layout.project_dir("alpha") == tmp_path / "h" / "alpha"
    assert layout.cache_dir(3) == tmp_path / "h" / "cache" / "s3"


def test_explicit_data_dir_wins_over_cp_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CP_HOME", str(tmp_path / "h"))
    assert layout.project_dir("a", tmp_path / "d") == tmp_path / "d" / "a" and layout.cache_dir(1, tmp_path / "d") == tmp_path / "d" / "cache" / "s1"


def test_queue_layout_follows_the_contract():
    p = layout.queue("/root")
    assert p["state"] == pathlib.Path("/root/dispatcher-state.json") and p["plog"] == pathlib.Path("/root/processing/processing.jsonl")
    assert p["area"] == pathlib.Path("/root/area") and p["usage"].name == "proxy-usage.jsonl" and p["quota"].name == "quota-history.jsonl"


def test_a_project_gets_its_own_area_synopsis_and_memory():
    p = layout.queue("/root", "alpha")
    assert p["area"] == pathlib.Path("/root/area/alpha") and p["synopsis"] == pathlib.Path("/root/synopsis/alpha.md")
    assert p["memory"] == pathlib.Path("/root/memory/alpha") and p["state"] == pathlib.Path("/root/dispatcher-state.json")


@pytest.mark.parametrize("bad", ["a/b", "..", ".", "a\\b"])
def test_an_unusable_project_name_is_refused(bad):
    with pytest.raises(ValueError):
        layout.queue("/root", bad)


def test_per_project_layout_is_the_folder_per_project():
    p = layout.per_project("/data", "beta")
    assert p["dir"] == pathlib.Path("/data/beta") and p["plog"] == pathlib.Path("/data/beta/processing/processing.jsonl")
    assert p["settings"].name == "dispatcher-settings.json" and p["area"] == pathlib.Path("/data/beta/area")
