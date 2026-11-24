"""3g: the host readings that depend on the operating system (memory pressure, power source)."""
import abc
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
    def power_source(self) -> str:
        """"AC", "battery" or "unknown"."""


class MacOSHostProbes(HostProbes):
    def __init__(self, cmd=_cmd):
        self.cmd = cmd                  # (*args) -> stdout text or None; tests inject it

    def memory_pressure(self) -> str:
        lvl = self.cmd("sysctl", "-n", "kern.memorystatus_vm_pressure_level")
        return {"1": "normal", "2": "warn", "4": "critical"}.get(lvl, lvl or "unknown")

    def power_source(self) -> str:
        batt = self.cmd("pmset", "-g", "batt") or ""
        return "AC" if "AC Power" in batt else ("battery" if "Battery Power" in batt else "unknown")


class UnknownHostProbes(HostProbes):
    def memory_pressure(self) -> str:
        return "unknown"

    def power_source(self) -> str:
        return "unknown"


def default_probes() -> HostProbes:
    return MacOSHostProbes() if sys.platform == "darwin" else UnknownHostProbes()
