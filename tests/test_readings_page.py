"""Token Usage: "Today's readings" (the hours of today with readings) and "Earlier readings" (the graded rows before), on synthetic quota files."""
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
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def q(ts, five, week):
    return json.dumps({"ts": ts, "five_hour": {"used_percentage": five, "resets_at": 1}, "seven_day": {"used_percentage": week, "resets_at": 2}})


def quota(*ts):
    return L.usage_report(None, None, lambda f: None, "\n".join(q(t, 10 + i, 30) for i, t in enumerate(ts)))["quota"]


def cards(qrep, today):
    """The HTML of latestCard() for the quota report, split per card: {title: html}."""
    esc = re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0)
    esc += re.search(r"^function recentRows\(rows,has\).*$", PAGE, re.M).group(0)
    fn = PAGE[PAGE.index("function latestCard(q,today){"):PAGE.index("function sessionCards(T){")]
    r = subprocess.run(["node", "-e", esc + fn + "process.stdout.write(latestCard(%s,%s))" % (json.dumps(qrep), json.dumps(today))],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return {re.search(r"<h2>(.*?)</h2>", c).group(1): c for c in r.stdout.split('<div class="card">')[1:]}


def rows(html):
    return re.findall(r'<tr><td class="mono">([^<]*)</td><td>(\w+)</td>', html)


def test_page_has_both_cards_and_keeps_the_others():
    assert "Today’s readings" in PAGE and "Earlier readings" in PAGE and "Latest readings" not in PAGE
    assert "no readings today" in PAGE and "One row per hour of today (UTC) that has recorded readings" in PAGE
    v = PAGE[PAGE.index("function vUsage(){"):PAGE.index("function vRules(){")]
    for part in ("latestCard(USE.quota)", "sessionCards(USE.sessions)", "heatmap(dv", "projectsCard()", "usageTable('Usage per day'",
                 "quotaCard(USE.quota)", "<h2>Per task</h2>"):
        assert part in v, part
    assert "Usage per Worker" not in PAGE and "USE.workers" not in PAGE  # the per-Worker card is gone (PC-83)


def test_payload_has_no_rows_for_hours_without_readings():
    ps = quota("2026-11-24T02:10:00+00:00", "2026-11-24T07:20:00+00:00", "2026-11-24T07:40:00+00:00")["periods"]
    assert [(p["level"], p["key"], p["readings"]) for p in ps] == [("hour", "2026-11-24T02:00", 1), ("hour", "2026-11-24T07:00", 2)]


@node
def test_today_lists_only_hours_with_readings_newest_first():
    c = cards(quota("2026-11-23T09:00:00+00:00", "2026-11-24T02:10:00+00:00", "2026-11-24T07:20:00+00:00"), "2026-11-24")
    assert list(c) == ["Today’s readings", "Earlier readings"]
    assert rows(c["Today’s readings"]) == [("2026-11-24 07:00", "hour"), ("2026-11-24 02:00", "hour")]
    assert rows(c["Earlier readings"]) == [("2026-11-23", "day")]


@node
def test_today_without_readings_shows_the_empty_state_and_earlier_levels_stay():
    qr = quota("2026-10-05T10:00:00+00:00", "2026-11-03T10:00:00+00:00", "2026-11-17T10:00:00+00:00", "2026-11-23T09:00:00+00:00")
    c = cards(qr, "2026-11-24")  # the newest reading is from yesterday: none today
    assert "no readings today" in c["Today’s readings"] and rows(c["Today’s readings"]) == []
    levels = [lv for _, lv in rows(c["Earlier readings"])]
    assert "month" in levels and "week" in levels and "hour" in levels  # yesterday's hours stay, in the earlier card
    c = cards(dict(qr, periods=[]), "2026-11-24")
    assert "no readings today" in c["Today’s readings"] and "no data" in c["Earlier readings"]


@node
def test_no_quota_file_is_no_data():
    assert "no data" in cards(None, "2026-11-24")["Today’s readings"]
