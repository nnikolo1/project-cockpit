"""Shared test setup: the staggered refresh slots (cockpit.STAGGER_*) are off unless a test turns them on, as if the service had run long (PC-18);
and no test writes into the owner's real data folder (the action log of PC-132 is written by every action)."""
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


@pytest.fixture(autouse=True)
def _private_data_dir(tmp_path, monkeypatch):
    if "cockpit" in sys.modules:
        monkeypatch.setattr(sys.modules["cockpit"], "DATA_DIR", tmp_path / "cockpit-data")
        monkeypatch.setattr(sys.modules["cockpit"], "QUEUE_ROOT", tmp_path / "queue-root")   # not migrated unless a test makes it so (PC-67)


def pytest_configure(config):
    config.addinivalue_line("markers", "ui_action(*ids): the UI actions a test exercises (server:x, page:x, form:x; /refused: its refusal); "
                                       "tools/ui_actions.py reads them, tests/test_ui_actions.py fails for an action no test names")
