"""Token Usage: every period table lists the most recent period first and has no row for a period without data (owner, 2026-11-24),
rendered by the page's own code on synthetic usage and quota files."""
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import logic as L  # noqa: E402

PAGE = (ROOT / "web" / "index.html").read_text()
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
QUOTA_TITLE = "Vendor quota used per day (highest reading)"


def req(ts, worker="w1", n=10):
    return json.dumps({"ts": ts, "worker": worker, "input_tokens": n, "output_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0})


def reading(ts, five, week):
    return json.dumps({"ts": ts, "five_hour": {"used_percentage": five, "resets_at": 1}, "seven_day": {"used_percentage": week, "resets_at": 2}})


def report(reqs, reads, plog=None):
    return L.usage_report("\n".join(reqs), plog, lambda f: None, "\n".join(reads) if reads is not None else None)


def page(use):
    """{card title: html} of vUsage() for the usage report `use` (heatmap stubbed out: it is not a period table)."""
    esc = re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0)
    fn = PAGE[PAGE.index("const USEF="):PAGE.index("function vRules(){")]
    js = esc + fn + "function heatmap(){return ''}var USE=%s;process.stdout.write(vUsage())" % json.dumps(use)
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return {re.search(r"<h2>(.*?)</h2>", c).group(1): c for c in r.stdout.split('<div class="card">')[1:] if "<h2>" in c}


def keys(html):
    return re.findall(r'<tr(?: class="[^"]*")?><td class="mono">([^<]*)', html)


def test_usage_per_day_and_per_window_newest_first_without_empty_days():
    c = page(report([req("2026-11-01T10:00:00+00:00"), req("2026-11-20T03:00:00+00:00"), req("2026-11-10T12:00:00+00:00")], None))
    assert keys(c["Usage per day"]) == ["2026-11-20", "2026-11-10", "2026-11-01"]
    assert keys(c["Usage per 5-hour window"]) == ["2026-11-20 02:00 (fixed)", "2026-11-10 12:00 (fixed)", "2026-11-01 08:00 (fixed)"]


def test_quota_per_day_newest_first_only_days_with_readings_columns_and_bars_unchanged():
    rep = report([req("2026-11-21T10:00:00+00:00")], [reading("2026-11-02T10:00:00+00:00", 20, 5), reading("2026-11-02T11:00:00+00:00", 40, 7),
                                                      reading("2026-11-20T09:00:00+00:00", 75, 30)])
    h = page(rep)[QUOTA_TITLE]
    assert keys(h) == ["2026-11-20", "2026-11-02"]  # the other 88 days of the 90 have no row
    rows = re.findall(r"<tr>(.*?)</tr>", h)[1:]
    assert re.findall(r'<td class="mono">([^<]*)</td>', rows[0]) == ["2026-11-20", "75 %", "30 %"]
    assert re.findall(r"width:(\d+)%", rows[0]) == ["75", "30"]
    assert re.findall(r'<td class="mono">([^<]*)</td>', rows[1]) == ["2026-11-02", "40 %", "7 %"]
    assert "no quota readings" not in h


def test_quota_per_day_without_readings_has_one_line():
    rep = report([req("2026-11-21T10:00:00+00:00")], [])
    assert len(rep["quota"]["days"]) == 90  # the payload keeps the 90 days (the heatmap uses them); the table drops them
    h = page(rep)[QUOTA_TITLE]
    assert keys(h) == [] and h.count("<tr>") == 2 and "no quota readings in the last 90 days" in h


def test_quota_card_follows_usage_per_day():
    c = list(page(report([req("2026-11-21T10:00:00+00:00")], [reading("2026-11-21T09:00:00+00:00", 1, 1)])))
    assert c.index(QUOTA_TITLE) == c.index("Usage per day") + 1


def test_per_task_lists_the_newest_launch_first():
    plog = "\n".join(json.dumps({"time": t, "task": k, "state": "launched", "folder": k, "worker": "w1"})
                     for k, t in (("t-a", "2026-11-01T10:00:00+00:00"), ("t-c", "2026-11-20T10:00:00+00:00"), ("t-b", "2026-11-10T10:00:00+00:00")))
    h = page(report([req("2026-11-21T10:00:00+00:00")], None, plog))["Per task"]
    assert keys(h) == ["t-c", "t-b", "t-a"]


def test_interactive_periods_newest_first_without_empty_periods():
    T = dict(sources=[], sessions=[], aggregate_days={}, periods=[
        dict(level="day", key="2026-11-23", label="2026-11-23", messages=2, aggregate=0, total=5, five_hour=10, seven_day=3,
             input_tokens=5, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0),
        dict(level="hour", key="2026-11-24T01:00", label="2026-11-24 01:00", messages=0, aggregate=0, total=0, five_hour=10, seven_day=3,
             input_tokens=0, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0),
        dict(level="hour", key="2026-11-24T02:00", label="2026-11-24 02:00", messages=1, aggregate=0, total=7, five_hour=12, seven_day=3,
             input_tokens=7, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0)])
    rep = report([req("2026-11-21T10:00:00+00:00")], None)
    rep["sessions"] = T
    assert keys(page(rep)["Interactive sessions history"]) == ["2026-11-24 02:00", "2026-11-23"]
