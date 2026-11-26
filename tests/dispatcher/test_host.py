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


def test_macos_swap_used():
    for raw, n in [("total = 3072.00M  used = 2048.00M  free = 1024.00M  (encrypted)", 2048 * 1024 ** 2),
                   ("total = 16.00G  used = 14.50G  free = 1.50G", int(14.5 * 1024 ** 3)), ("", None), (None, None)]:
        assert host.MacOSHostProbes(fake({"sysctl": raw})).swap_used() == n


def test_macos_free_memory_counts_free_inactive_and_speculative_pages():
    out = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free:   100.\nPages active:  999.\n"
           "Pages inactive:  200.\nPages speculative:   50.\n")
    assert host.MacOSHostProbes(fake({"vm_stat": out})).free_memory() == 350 * 16384
    assert host.MacOSHostProbes(fake({"vm_stat": "garbage"})).free_memory() is None
    assert host.MacOSHostProbes(fake({})).free_memory() is None


def test_unknown_probes_have_no_memory_readings():
    assert (host.UnknownHostProbes().swap_used(), host.UnknownHostProbes().free_memory()) == (None, None)
