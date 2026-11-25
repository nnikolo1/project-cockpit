import json
import subprocess
import urllib.error

from ckp.adapters import process


class Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_health_ok():
    seen = {}

    def opener(url, timeout):
        seen.update(url=url, timeout=timeout)
        return Resp(200)

    ok, detail = process.health(8800, "/ping", timeout=2, opener=opener)
    assert ok and "200" in detail
    assert seen == {"url": "http://127.0.0.1:8800/ping", "timeout": 2}


def test_health_http_error_is_a_failed_check():
    def opener(url, timeout):
        raise urllib.error.HTTPError(url, 503, "down", {}, None)

    ok, detail = process.health(8800, opener=opener)
    assert not ok and "503" in detail


def test_health_connection_refused_is_a_failed_check():
    def opener(url, timeout):
        raise urllib.error.URLError("refused")

    ok, detail = process.health(8800, opener=opener)
    assert not ok and "refused" in detail


def test_health_timeout_is_a_failed_check():
    def opener(url, timeout):
        raise TimeoutError("timed out")

    ok, detail = process.health(8800, opener=opener)
    assert not ok and "timed out" in detail


def test_restart_service_boots_out_pauses_then_bootstraps(tmp_path):
    events = []
    plist = tmp_path / "x.plist"
    ok, detail = process.restart_service("com.example.svc", plist=plist, uid=501, pause_s=4,
                                         run=lambda a: events.append(a) or "", sleep=lambda s: events.append(("sleep", s)))
    assert ok
    assert events == [["launchctl", "bootout", "gui/501/com.example.svc"], ("sleep", 4),
                      ["launchctl", "bootstrap", "gui/501", str(plist)]]


def test_restart_service_tolerates_bootout_of_unloaded_job():
    def run(args):
        if args[1] == "bootout":
            raise subprocess.CalledProcessError(3, args)
        return ""

    ok, _ = process.restart_service("l", plist="/p", uid=1, run=run, sleep=lambda s: None)
    assert ok


def test_restart_service_reports_bootstrap_failure():
    def run(args):
        if args[1] == "bootstrap":
            raise subprocess.CalledProcessError(5, args, stderr="Input/output error")
        return ""

    ok, detail = process.restart_service("l", plist="/p", uid=1, run=run, sleep=lambda s: None)
    assert not ok and "Input/output error" in detail


def test_running_workers(tmp_path):
    f = tmp_path / "state.json"
    f.write_text(json.dumps({"workers": {"a": {"task_id": "PC-1"}, "b": {"task_id": "IN-2"}}}))
    assert process.running_workers(f) == ["PC-1", "IN-2"]


def test_running_workers_empty_and_unreadable(tmp_path):
    f = tmp_path / "state.json"
    assert process.running_workers(f) == []  # absent
    f.write_text("not json")
    assert process.running_workers(f) == []
    f.write_text(json.dumps({"workers": {}}))
    assert process.running_workers(f) == []
    f.write_text(json.dumps({"other": 1}))
    assert process.running_workers(f) == []
