import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from dispatcher import launchd, layout  # noqa: E402


def test_queue_layout_follows_the_contract():
    p = layout.queue("/root")
    assert p["state"] == pathlib.Path("/root/dispatcher-state.json") and p["plog"] == pathlib.Path("/root/processing/processing.jsonl")
    assert p["area"] == pathlib.Path("/root/area") and p["usage"].name == "proxy-usage.jsonl" and p["quota"].name == "quota-history.jsonl"
    assert p["socket"].name == "proxy.sock"


def test_a_project_gets_its_own_area_synopsis_and_memory():
    p = layout.queue("/root", "alpha")
    assert p["area"] == pathlib.Path("/root/area/alpha") and p["synopsis"] == pathlib.Path("/root/synopsis/alpha.md")
    assert p["memory"] == pathlib.Path("/root/memory/alpha") and p["state"] == pathlib.Path("/root/dispatcher-state.json")


@pytest.mark.parametrize("bad", ["a/b", "..", ".", "a\\b"])
def test_an_unusable_project_name_is_refused(bad):
    with pytest.raises(ValueError):
        layout.queue("/root", bad)


def test_per_project_layout_is_today_s_folder_per_project():
    p = layout.per_project("/data", "beta")
    assert p["dir"] == pathlib.Path("/data/beta") and p["plog"] == pathlib.Path("/data/beta/processing/processing.jsonl")
    assert p["settings"].name == "dispatcher-settings.json" and p["area"] == pathlib.Path("/data/beta/area")


def test_launchd_uses_the_layout_module_and_the_same_defaults():
    assert launchd.QUEUE_ROOT == layout.QUEUE_ROOT and launchd.DATA_DIR == layout.DATA_DIR
    assert launchd.paths("x", "/d") == layout.per_project("/d", "x") and launchd.queue_paths("/r") == layout.queue("/r")
