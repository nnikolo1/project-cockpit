"""Shim: the code moved to ckp.queue.quota (refactoring slice 2). This module IS that module (same object, so patches and private names work)."""
import sys

from ckp.queue import quota as _impl

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
