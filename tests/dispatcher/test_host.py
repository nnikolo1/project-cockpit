import sys

from dispatcher import host


def fake(out):
    return lambda *args: out.get(args[0])


def test_macos_memory_pressure_levels():
    for raw, word in [("1", "normal"), ("2", "warn"), ("4", "critical"), ("3", "3"), ("", "unknown"), (None, "unknown")]:
        assert host.MacOSHostProbes(fake({"sysctl": raw})).memory_pressure() == word


def test_macos_power_source():
    for raw, word in [("Now drawing from 'AC Power'", "AC"), ("Now drawing from 'Battery Power'", "battery"),
                      ("", "unknown"), (None, "unknown")]:
        assert host.MacOSHostProbes(fake({"pmset": raw})).power_source() == word


def test_unknown_probes():
    p = host.UnknownHostProbes()
    assert (p.memory_pressure(), p.power_source()) == ("unknown", "unknown")


def test_default_probes_by_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert isinstance(host.default_probes(), host.MacOSHostProbes)
    monkeypatch.setattr(sys, "platform", "linux")
    assert isinstance(host.default_probes(), host.UnknownHostProbes)


def test_keep_awake_prefix_by_platform():
    assert host.MacOSHostProbes().keep_awake_prefix() == ["caffeinate", "-i"]
    assert host.UnknownHostProbes().keep_awake_prefix() == []
