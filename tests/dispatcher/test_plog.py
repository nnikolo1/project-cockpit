import datetime as dt
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from dispatcher import plog as P  # noqa: E402


class Clock:
    t = dt.datetime(2026, 11, 24, 10, 0, 0)

    def __call__(self):
        return self.t


def lines(log):
    return [json.loads(l) for l in log.path.read_text().splitlines()]


def test_event_failure_and_heartbeat(tmp_path):
    log = P.ProcessingLog(tmp_path / "d", Clock())
    log.event("cockpit-1", "/w/cockpit-1", "wip")
    log.failure("cockpit-1", "/w/cockpit-1", "no start")
    log.heartbeat("t=10:00 running=0 nothing to do")
    a, b, c = lines(log)
    assert a == dict(time="2026-11-24T10:00:00", task="cockpit-1", folder="/w/cockpit-1", state="wip")
    assert (b["state"], b["reason"], b["task"], b["folder"]) == ("failed", "no start", "cockpit-1", "/w/cockpit-1")
    assert c["heartbeat"].startswith("t=10:00")


def test_unknown_failure_reason_refused(tmp_path):
    with pytest.raises(ValueError):
        P.ProcessingLog(tmp_path, Clock()).failure("cockpit-1", "/w", "bored")


def test_refuses_data_folder_inside_git(tmp_path):
    (tmp_path / ".git").mkdir()
    with pytest.raises(ValueError, match="git repository"):
        P.ProcessingLog(tmp_path / "data")


def test_backup_once_a_day(tmp_path):
    clock, sent = Clock(), []
    log = P.ProcessingLog(tmp_path / "d", clock)
    log.heartbeat("x")
    cfg = tmp_path / "gdrive.json"
    cfg.write_text('{"folder": "f"}')
    up = lambda config, path: sent.append((config, path))
    assert log.backup(cfg, up) and sent == [({"folder": "f"}, log.path)]
    assert not log.backup(cfg, up) and len(sent) == 1
    clock.t += dt.timedelta(days=1)
    assert log.backup(cfg, up) and len(sent) == 2


def test_failed_upload_is_retried(tmp_path):
    log = P.ProcessingLog(tmp_path / "d", Clock())
    cfg = tmp_path / "c.json"
    cfg.write_text("{}")

    def boom(c, p):
        raise OSError("down")
    assert log.backup(cfg, boom) is False and log.backup_due()
    assert log.backup(cfg, lambda c, p: None) is True


def test_backup_config_inside_git_refused(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    cfg = repo / "c.json"
    cfg.write_text("{}")
    with pytest.raises(ValueError):
        P.ProcessingLog(tmp_path / "d", Clock()).backup(cfg, lambda c, p: None)
