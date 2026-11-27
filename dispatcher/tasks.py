"""Shim: the code moved to ckp.queue.tasks (refactoring slice 2). This module IS that module (same object, so patches and private names work)."""
import sys

from ckp.queue import tasks as _impl

if __name__ == "__main__":
    pass
else:
    sys.modules[__name__] = _impl
