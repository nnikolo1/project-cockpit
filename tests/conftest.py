"""Shared test guard: no test writes into the owner's real data folder (the action log of PC-132 is written by every action)."""
import sys

import pytest


@pytest.fixture(autouse=True)
def _private_data_dir(tmp_path, monkeypatch):
    if "cockpit" in sys.modules:
        monkeypatch.setattr(sys.modules["cockpit"], "DATA_DIR", tmp_path / "cockpit-data")
