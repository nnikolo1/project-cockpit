"""Lock files (fcntl.flock): shared for readers of a deployment, exclusive for the deploy itself."""
import contextlib
import fcntl
import logging
import os
import pathlib
import time

from ckp.store import layout

log = logging.getLogger(__name__)


class Busy(Exception):
    """The lock could not be taken within the wait."""


@contextlib.contextmanager
def _lock(path, mode, wait_s):
    path = pathlib.Path(path) if path else layout.deploy_lock()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + wait_s
    try:
        while True:
            try:
                fcntl.flock(fd, mode | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    log.warning("lock %s busy after %ss", path, wait_s)
                    raise Busy(f"{path} is locked") from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the lock


def shared(path=None, wait_s=0):
    return _lock(path, fcntl.LOCK_SH, wait_s)


def exclusive(path=None, wait_s=30):
    return _lock(path, fcntl.LOCK_EX, wait_s)
