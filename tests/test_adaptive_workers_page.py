"""PC-95: the threshold settings on the Settings page, and the effective number of Workers next to the ceiling on the Task Queue page."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import json
import re
import shutil
import subprocess
import pytest
import logic as L
from dispatcher.core import DEFAULTS

PAGE = (pathlib.Path(__file__).parent.parent / "web" / "index.html").read_text()
THRESHOLDS = ("hold_pressure_level", "max_swap_gib", "min_free_memory_gib", "min_free_disk_gib", "max_container_memory_percent")


def test_every_threshold_is_a_setting_with_a_default_a_label_and_a_page_row():
    for k in THRESHOLDS:
        assert isinstance(DEFAULTS[k], int) and k in L.SETTING_LABELS and f"['{k}'," in PAGE


@pytest.mark.parametrize("key,val,word", [("min_free_disk_gib", 9, "at least 10"), ("hold_pressure_level", 5, "at most 4"),
                                          ("max_container_memory_percent", 101, "at most 100"), ("max_swap_gib", 0, "at least 1")])
def test_the_page_narrows_the_threshold_ranges(key, val, word):
    with pytest.raises(ValueError, match=word):
        L.validate_settings({**DEFAULTS, key: val}, DEFAULTS, 6)
    assert L.validate_settings({**DEFAULTS, "max_swap_gib": "4"}, DEFAULTS, 6)["max_swap_gib"] == 4


def state(cap):
    return json.dumps({"workers": {}, "capacity": cap})


def test_the_queue_gets_the_dispatchers_last_decision():
    held = {"ceiling": 3, "effective": 1, "held": "swap 14 GiB above 8 GiB", "readings": "pressure normal, swap 14 GiB", "at": 1_800_000_000}
    assert L.queue_local(state(held), None)["capacity"] == held
    assert L.queue_local(state(dict(held, held=None)), None)["capacity"]["held"] is None
    for bad in (None, "x", {"ceiling": 3}, {"ceiling": True, "effective": 1}):
        assert L.queue_local(state(bad), None)["capacity"] is None


def test_the_queue_card_shows_the_capacity_line():
    i = PAGE.index("function vQueueLocal(")
    assert "+capLine(L.capacity)+" in PAGE[i:PAGE.index("\n}", i)]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_cap_line_shows_effective_ceiling_and_the_reason_when_held():
    esc = re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0)
    helpers = PAGE[PAGE.index("/* safe-block:start"):PAGE.index("/* safe-block:end */")]
    code = ("console.log(JSON.stringify([capLine({effective:1,ceiling:3,held:'swap 14 GiB above 8 GiB',readings:'swap 14 GiB',at:null}),"
            "capLine({effective:3,ceiling:3,held:null,readings:null,at:null}),capLine(null)]))")
    r = subprocess.run(["node", "-e", esc + helpers + code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    held, free, none = json.loads(r.stdout)
    assert "<b>1</b> of ceiling 3" in held and "held: swap 14 GiB above 8 GiB" in held
    assert "<b>3</b> of ceiling 3" in free and "held" not in free and "not yet decided" in none
