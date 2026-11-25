import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import logic as L  # noqa: E402


def state(launched):
    return json.dumps({"workers": {"worker1": {"task_id": "p-1", "launched": launched, "started": True}}})


def test_numeric_launch_time_becomes_an_iso_string():
    w = L.queue_local(state(1795577286.1974359), "")["workers"][0]
    assert isinstance(w["launched"], str) and w["launched"].startswith("2026-11-25T")


def test_string_passes_through_and_unreadable_values_become_none():
    assert L.queue_local(state("2026-11-25T05:00:00"), "")["workers"][0]["launched"] == "2026-11-25T05:00:00"
    for bad in (None, True, [], {}, 1e30):
        assert L.queue_local(state(bad), "")["workers"][0]["launched"] is None
