"""Heartbeats and the watch (PC-142): a run is recorded and pinged, a missing, failed or late heartbeat and a failed or stale backup are
problems, each notified once a day. Nothing is sent: ping and notify are recorders."""
import datetime as dt
import json

from ckp.adapters import notify
from ckp.core import monitor

NOW = dt.datetime(2026, 11, 26, 12, 0, tzinfo=dt.timezone.utc)
URL = "https://hc.example.invalid/uuid"


class Rec:
    def __init__(self, answer=True):
        self.calls, self.answer = [], answer

    def __call__(self, *a):
        self.calls.append(a)
        return self.answer


def test_a_check_needs_an_https_url_and_is_kept_private(tmp_path):
    assert not monitor.set_check("backup", "http://x", home=tmp_path)["ok"]
    assert not monitor.set_check("backup", URL, every_h=0, home=tmp_path)["ok"]
    assert monitor.set_check("backup", URL, every_h=24, home=tmp_path)["ok"]
    f = tmp_path / "heartbeats.json"
    assert json.loads(f.read_text()) == {"backup": {"url": URL, "every_h": 24}} and f.stat().st_mode & 0o077 == 0


def test_a_beat_is_recorded_and_pinged_only_when_a_check_exists(tmp_path):
    ping = Rec()
    assert monitor.beat("deploy-p", True, ping=ping, home=tmp_path, now=NOW)["data"]["pinged"] is False and ping.calls == []
    monitor.set_check("deploy-p", URL, home=tmp_path)
    assert monitor.beat("deploy-p", False, ping=ping, home=tmp_path, now=NOW)["data"]["pinged"] is True
    assert ping.calls == [(URL, False)]


def test_problems_missing_failed_and_late_heartbeats(tmp_path):
    for job in ("never", "failed", "late", "fine", "unexpected"):
        monitor.set_check(job, URL, every_h=None if job == "unexpected" else 24, home=tmp_path)
    monitor.beat("failed", False, ping=Rec(), home=tmp_path, now=NOW)
    monitor.beat("late", True, ping=Rec(), home=tmp_path, now=NOW - dt.timedelta(hours=26))
    monitor.beat("fine", True, ping=Rec(), home=tmp_path, now=NOW - dt.timedelta(hours=24, minutes=30))
    got = {p["key"]: p["text"] for p in monitor.problems(tmp_path, NOW, projects=[])}
    assert set(got) == {"heartbeat:never", "heartbeat:failed", "heartbeat:late"}
    assert "26 hours" in got["heartbeat:late"]


def test_problems_failed_or_stale_backup_but_not_a_project_without_backups(tmp_path):
    def status(name, **rec):
        (tmp_path / name).mkdir()
        (tmp_path / name / "backup-status.json").write_text(json.dumps(dict(schema=1, **rec)))
    status("bad", time="2026-11-26T03:00:00Z", ok=False, reason="no Drive", last_ok="2026-11-25T03:00:00Z")
    status("old", time="2026-11-24T03:00:00Z", ok=True, last_ok="2026-11-24T03:00:00Z")
    status("good", time="2026-11-26T03:00:00Z", ok=True, last_ok="2026-11-26T03:00:00Z")
    got = monitor.problems(tmp_path, NOW, projects=["bad", "old", "good", "none"])
    assert {p["key"] for p in got} == {"backup:bad", "backup:old"} and any("no Drive" in p["text"] for p in got)


def test_watch_notifies_each_problem_once_a_day_and_again_after_it_was_solved(tmp_path):
    monitor.set_check("daily", URL, every_h=24, home=tmp_path)
    shown = Rec()
    r = monitor.watch(notify=shown, home=tmp_path, now=NOW, projects=[])
    assert not r["ok"] and r["data"]["notified"] == ["heartbeat:daily"] and len(shown.calls) == 1
    assert monitor.watch(notify=shown, home=tmp_path, now=NOW + dt.timedelta(hours=1), projects=[])["data"]["notified"] == []
    assert monitor.watch(notify=shown, home=tmp_path, now=NOW + dt.timedelta(hours=25), projects=[])["data"]["notified"] == ["heartbeat:daily"]
    monitor.beat("daily", True, ping=Rec(), home=tmp_path, now=NOW + dt.timedelta(hours=26))
    assert monitor.watch(notify=shown, home=tmp_path, now=NOW + dt.timedelta(hours=26), projects=[])["ok"]


def test_watch_retries_a_notification_that_could_not_be_shown(tmp_path):
    monitor.set_check("daily", URL, every_h=24, home=tmp_path)
    assert monitor.watch(notify=Rec(False), home=tmp_path, now=NOW, projects=[])["data"]["notified"] == []
    assert monitor.watch(notify=Rec(), home=tmp_path, now=NOW, projects=[])["data"]["notified"] == ["heartbeat:daily"]


class Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ping_gets_the_url_or_its_fail_endpoint_and_never_raises():
    seen = []
    assert notify.ping(URL, True, opener=lambda u, timeout: seen.append(u) or Resp())
    assert notify.ping(URL + "/", False, opener=lambda u, timeout: seen.append(u) or Resp())
    assert seen == [URL, URL + "/fail"]

    def down(u, timeout):
        raise OSError("no network")
    assert notify.ping(URL, opener=down) is False


def test_desktop_notification_per_host_and_a_failing_notifier():
    run = Rec()
    assert notify.desktop('Cockpit', 'backup "p" failed', run=lambda a: run(a), platform="darwin")
    args = run.calls[0][0]
    assert args[:2] == ["osascript", "-e"] and '\\"p\\"' in args[2]
    assert notify.desktop("t", "x", run=lambda a: run(a), platform="linux") and run.calls[1][0] == ["notify-send", "t", "x"]

    def broken(a):
        raise FileNotFoundError("notify-send")
    assert notify.desktop("t", "x", run=broken, platform="linux") is False
