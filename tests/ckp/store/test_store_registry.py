import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from ckp.store import registry  # noqa: E402


def test_missing_file_is_empty(tmp_path):
    assert registry.load(tmp_path) == []


def test_round_trip(tmp_path):
    registry.save([{"name": "a"}], tmp_path)
    assert registry.load(tmp_path) == [{"name": "a"}]


def test_broken_or_foreign_content_is_tolerated(tmp_path):
    (tmp_path / "projects.json").write_text("{nope")
    assert registry.load(tmp_path) == []
    (tmp_path / "projects.json").write_text('{"a": 1}')
    assert registry.load(tmp_path) == []
    (tmp_path / "projects.json").write_text('[{"name": "a"}, 3]')
    assert registry.load(tmp_path) == [{"name": "a"}]


def test_home_defaults_to_cp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_HOME", str(tmp_path))
    registry.save([{"name": "x"}])
    assert (tmp_path / "projects.json").exists()
