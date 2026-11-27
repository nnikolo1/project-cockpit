"""Shim: the code moved to ckp.queue.core (refactoring slice 2). This module IS that module (same object, so patches and private names work)."""
import sys

from ckp.queue import core as _impl

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
