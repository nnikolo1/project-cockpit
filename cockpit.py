#!/usr/bin/env python3
"""Project cockpit v1: the entry shim. The viewer lives in ckp.server (HTTP plumbing) and ckp.api (what it offers); this file starts it with the
same command line (`python3 cockpit.py --projects FILE ...`, the launchd job) and lets the code that still says `import cockpit` find
the names that moved. Deleted in the final cleanup slice (docs/contracts/ckp-architecture.md, rule 2)."""
import importlib
import sys
import types

from ckp.server.startup import main

_MODULES = ("ckp.api.context", "ckp.api.gh", "ckp.api.cache", "ckp.api.fetch", "ckp.api.settings", "ckp.api.usage", "ckp.api.code", "ckp.api.state",
            "ckp.api.queue", "ckp.api.health", "ckp.api.actions", "ckp.api.pages", "ckp.server.handler", "ckp.server.routes", "ckp.server.startup")


def __getattr__(name):
    """A name that moved: read from the module that holds it now (the first of _MODULES that has it)."""
    for m in _MODULES:
        v = vars(importlib.import_module(m))
        if name in v:
            return v[name]
    raise AttributeError(f"module 'cockpit' has no attribute {name!r}")


class _Shim(types.ModuleType):
    def __setattr__(self, name, value):
        # a write here would not reach the code that moved: patch the module that holds the name instead
        if name not in ("__spec__", "__loader__", "__cached__", "__file__", "__builtins__"):
            raise AttributeError(f"cockpit.{name} cannot be set: set it on the ckp.api / ckp.server module that holds it")
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _Shim

if __name__ == "__main__":
    main()
