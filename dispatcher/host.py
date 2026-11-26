"""3g: the host readings that depend on the operating system (memory pressure, swap in use, free memory, power source)."""
import abc
import re
import subprocess
import sys


def _cmd(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


class HostProbes(abc.ABC):
    @abc.abstractmethod
    def memory_pressure(self) -> str:
        """A short word for the memory pressure, or "unknown"."""

    @abc.abstractmethod
    def swap_used(self):
        """Bytes of swap in use, or None if unknown."""

    @abc.abstractmethod
    def free_memory(self):
        """Bytes the host can hand out without swapping (free, inactive and speculative pages), or None if unknown."""

    @abc.abstractmethod
    def power_source(self) -> str:
        """"AC", "battery" or "unknown"."""

    @abc.abstractmethod
    def keep_awake_prefix(self) -> list:
        """The command words that keep the host from sleeping while the command after them runs; [] if there are none."""


class MacOSHostProbes(HostProbes):
    def __init__(self, cmd=_cmd):
        self.cmd = cmd                  # (*args) -> stdout text or None; tests inject it

    def memory_pressure(self) -> str:
        lvl = self.cmd("sysctl", "-n", "kern.memorystatus_vm_pressure_level")
        return {"1": "normal", "2": "warn", "4": "critical"}.get(lvl, lvl or "unknown")

    def swap_used(self):
        # "total = 2048.00M  used = 1024.25M  free = 1023.75M  (encrypted)"
        m = re.search(r"used = ([\d.]+)([KMG])", self.cmd("sysctl", "-n", "vm.swapusage") or "")
        return int(float(m.group(1)) * 1024 ** ("KMG".index(m.group(2)) + 1)) if m else None

    def free_memory(self):
        out = self.cmd("vm_stat") or ""
        page = re.search(r"page size of (\d+) bytes", out)
        counts = [re.search(rf"Pages {k}:\s+(\d+)", out) for k in ("free", "inactive", "speculative")]
        return int(page.group(1)) * sum(int(c.group(1)) for c in counts) if page and all(counts) else None

    def power_source(self) -> str:
        batt =self.cmd("pmset", "-g", "batt") or ""
        return "AC" if "AC Power" in batt else ("battery" if "Battery Power" in batt else "unknown")

    def keep_awake_prefix(self) -> list:
        return ["caffeinate", "-i"]


class UnknownHostProbes(HostProbes):
    def memory_pressure(self) -> str:
        return "unknown"

    def swap_used(self):
        return None

    def free_memory(self):
        return None

    def power_source(self) -> str:
        return "unknown"

    def keep_awake_prefix(self) -> list:
        return []


def default_probes() -> HostProbes:
    return MacOSHostProbes() if sys.platform == "darwin" else UnknownHostProbes()
