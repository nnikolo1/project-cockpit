"""Shared test setup: the staggered refresh slots (cockpit.STAGGER_*) are off unless a test turns them on, as if the service had run long."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import cockpit as C


@pytest.fixture(autouse=True)
def no_stagger(monkeypatch):
    monkeypatch.setattr(C, "STAGGER_JITTER_S", 0)
    monkeypatch.setattr(C, "_started", 0)
    C._slots.clear()
    yield
    C._slots.clear()
