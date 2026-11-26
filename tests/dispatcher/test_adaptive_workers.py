"""PC-95: the number of Workers goes up only while the host readings allow it, never beyond the ceiling, and a reading never kills."""
import pytest

from dispatcher import core
from dispatcher import runtime as rt
from tests.dispatcher.test_core import World, T

G = core.GIB
LOW = {"pressure": "normal", "swap": 2 * G, "free_memory": 6 * G, "container": (1 * G, 6 * G)}
HIGH = {   # each one reading over its default threshold, the others low
    "pressure": ({"pressure": "warn"}, "pressure warn at or above warn"),
    "swap": ({"swap": 14 * G}, "swap 14 GiB above 8 GiB"),
    "free_memory": ({"free_memory": G // 2}, "free memory 0.5 GiB below 2 GiB"),
    "free_disk": ({}, "free disk 5 GiB below 10 GiB"),
    "container": ({"container": (int(5.7 * G), 6 * G)}, "container memory 95 % of its limit above 85 %"),
}


def world(tmp_path, readings, n_tasks=10, **settings):
    w = World(tmp_path, [T(f"x-{i}") for i in range(n_tasks)], **settings)
    w.runtime.outbox = {f"x-{i}": (True, None, "no note") for i in range(n_tasks)}   # every Worker writes `started` at once
    w.mtime = w.now                                                                     # and is active
    d = w.dispatcher()
    d.probes.resources = lambda: dict(readings)
    w.fire = d.fire
    return w


def fire_running(w, times):
    out = []
    for _ in range(times):
        w.alive_pids = {4242}    # every Worker started so far keeps running
        out.append(w.fire())
    return out


def test_starts_one_per_firing_up_to_the_ceiling_when_the_readings_are_low(tmp_path):
    w = world(tmp_path, LOW, workers=3)
    did = fire_running(w, 6)
    assert [x.split(":")[0] for x in did[:3]] == ["started worker1 on x-0", "started worker2 on x-1", "started worker3 on x-2"]
    assert all("all Workers busy" in x for x in did[3:])
    assert len(w.runtime.launched) == 3
    assert core.load_state(w.state)["capacity"] | {"at": 0} == {
        "ceiling": 3, "effective": 3, "held": None, "at": 0,
        "readings": "pressure normal, swap 2 GiB, free memory 6 GiB, free disk 50 GiB, container 1 GiB of 6 GiB"}


@pytest.mark.parametrize("key", HIGH)
def test_holds_when_any_one_reading_is_over_its_threshold(tmp_path, key):
    over, why = HIGH[key]
    w = world(tmp_path, LOW, workers=4)
    fire_running(w, 1)                                # one Worker runs
    d = w.dispatcher()
    d.probes.resources = lambda: {**LOW, **over}
    if key == "free_disk":
        w.free = 5 * G
    w.alive_pids = {4242}
    assert d.fire() == f"held: {why}"
    assert len(w.runtime.launched) == 1 and w.killed == []
    assert why in w.plog.beats[-1]                    # the processing log carries the reading and its threshold
    cap = core.load_state(w.state)["capacity"]
    assert (cap["effective"], cap["ceiling"], cap["held"]) == (1, 4, why)


def test_never_exceeds_the_ceiling(tmp_path):
    for ceiling in (1, 2, 5):
        (tmp_path / str(ceiling)).mkdir()
        w = world(tmp_path / str(ceiling), LOW, workers=ceiling)
        fire_running(w, 10)
        assert len(w.runtime.launched) == ceiling


def test_a_reading_never_kills_a_running_worker(tmp_path):
    w = world(tmp_path, LOW, workers=3)
    fire_running(w, 2)
    d = w.dispatcher()
    d.probes.resources = lambda: {"pressure": "critical", "swap": 30 * G, "free_memory": 0, "container": (6 * G, 6 * G)}
    w.free = 1 * G
    for _ in range(5):
        w.alive_pids = {4242}
        w.now += 30
        assert d.fire().startswith("held: pressure critical")
    assert w.killed == [] and w.plog.failures == [] and "failed" not in str(w.tasks.states) and set(core.load_state(w.state)["workers"]) == {"worker1", "worker2"}


def test_resumes_when_the_readings_drop(tmp_path):
    readings = {**LOW, "swap": 14 * G}
    w = world(tmp_path, readings, workers=3)
    d = w.dispatcher()
    d.probes.resources = lambda: dict(readings)
    assert d.fire().startswith("held: swap 14 GiB")
    readings["swap"] = 2 * G
    started = d.fire()
    assert started.startswith("started worker1 on x-0: pressure normal, swap 2 GiB")
    assert started in w.plog.beats[-1]


def test_unknown_readings_never_hold():
    ok, seen, why = core.admission({"free_disk": 50 * G}, core.DEFAULTS)
    assert ok and why == ""
    assert seen == "pressure unknown, swap unknown, free memory unknown, free disk 50 GiB, container memory unknown"


def test_a_reading_at_its_threshold_does_not_hold_and_thresholds_are_settings():
    at = {"pressure": "normal", "swap": 8 * G, "free_memory": 2 * G, "free_disk": 10 * G, "container": (85, 100)}
    assert core.admission(at, core.DEFAULTS)[0]
    tight = dict(core.DEFAULTS, hold_pressure_level=1, max_swap_gib=1, min_free_memory_gib=3, min_free_disk_gib=20,
                 max_container_memory_percent=50)
    ok, _, why = core.admission(at, tight)
    assert not ok and why == ("pressure normal at or above normal; swap 8 GiB above 1 GiB; free memory 2 GiB below 3 GiB; "
                              "free disk 10 GiB below 20 GiB; container memory 85 % of its limit above 50 %")
    assert core.admission({"pressure": "critical"}, dict(core.DEFAULTS, hold_pressure_level=3))[2] == "pressure critical at or above level 3"


def test_threshold_settings_load_with_defaults(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"max_swap_gib": 4, "min_free_memory_gib": "x"}')
    s = core.load_settings(str(p))
    assert s["max_swap_gib"] == 4 and s["min_free_memory_gib"] == 2 and s["min_free_disk_gib"] == 10


def test_resource_readings_and_container_memory(tmp_path):
    class Host:
        memory_pressure, swap_used, free_memory = (lambda self: "warn"), (lambda self: 3), (lambda self: 4)

    class Runtime:
        memory = staticmethod(lambda: (1, 2))
    assert core.resource_readings(Runtime(), Host()) == {"pressure": "warn", "swap": 3, "free_memory": 4, "container": (1, 2)}
    r = rt.Runtime(rt.Config(area=str(tmp_path), socket="/s"))
    for raw, want in [('[{"memoryUsageBytes": 100, "memoryLimitBytes": 400, "numProcesses": 3}]', (100, 400)),
                      ('{"memoryUsageBytes": 100}', None), (None, None), ('{"memoryUsageBytes": 1, "memoryLimitBytes": 0}', None)]:
        r.stats = lambda raw=raw: raw
        assert r.memory() == want
