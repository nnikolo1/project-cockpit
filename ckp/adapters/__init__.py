"""Adapters of the Cockpit (PC-84): every external call goes through one injected function `run(args) -> str`."""
import logging
import subprocess

log = logging.getLogger("ckp.adapters")


def run(args, timeout=120):
    """The real runner: the command's stdout; a failure raises `subprocess.CalledProcessError` (stderr in its `stderr`)."""
    return subprocess.run(list(args), check=True, capture_output=True, text=True, timeout=timeout).stdout
