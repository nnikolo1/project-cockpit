"""The commands the live launchd jobs run must still start after the modules moved into ckp/ (refactoring, 2026-11-27).

An import test cannot show this: `python3 -m dispatcher.core` runs the old file as `__main__`, and only a real subprocess proves that the shim
reaches the moved module's main(). Each command is run with --help, which exits 0 and prints the usage without touching any service.
"""
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

COMMANDS = [
    ["-m", "dispatcher.core", "--help"],
    ["-m", "dispatcher.quota", "--help"],
    ["-m", "dispatcher.proxy", "--help"],
    ["-m", "dispatcher.launchd", "render", "--help"],
    ["-m", "ckp", "--help"],
    ["cockpit.py", "--help"],
]


@pytest.mark.parametrize("args", COMMANDS, ids=[" ".join(a) for a in COMMANDS])
def test_a_job_command_starts_and_prints_its_usage(args):
    r = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"{args}: exit {r.returncode}: {r.stderr[-300:]}"
    assert "usage" in (r.stdout + r.stderr).lower(), f"{args}: no usage text"
