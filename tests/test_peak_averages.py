"""Interactive sessions history, quota columns (PC-110): on day, week and month rows the 5-hour value is the mean of the peaks of the vendor
5-hour windows ending in the period, and the weekly value the period's highest weekly reading; synthetic readings only."""
import datetime as dt
import json
import re
import shutil
import subprocess

import pytest

import logic as L
from test_sessions_history import PAGE, TEXT, msg, render

node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def ep(iso):
    return int(dt.datetime.fromisoformat(iso).timestamp())


def rd(ts, five, ends, seven=None):
    """One quota reading at `ts`: 5-hour percentage `five` in the window ending at `ends` (ISO), weekly percentage `seven`."""
    return json.dumps(dict(ts=ts, five_hour=dict(used_percentage=five, resets_at=ep(ends)),
                           seven_day=dict(used_percentage=seven, resets_at=ep("2031-05-26T00:00:00+00:00")) if seven is not None else None))


# The anchor 2031-05-21 15:30 is a Wednesday: 05-19 and 05-20 are day rows, the weeks of 05-12 and 05-05 week rows.
READINGS = [
    # 2031-05-19: three windows ending that day, peaks 10, 20 and 60 (two readings in the last window); mean 30
    rd("2031-05-19T02:00:00+00:00", 10, "2031-05-19T05:00:00+00:00"),
    rd("2031-05-19T06:00:00+00:00", 20, "2031-05-19T10:00:00+00:00"),
    rd("2031-05-19T12:00:00+00:00", 25, "2031-05-19T15:00:00+00:00"),
    rd("2031-05-19T14:00:00+00:00", 60, "2031-05-19T15:00:59+00:00"),   # the reset time wobbles by seconds: the same window
    # read on 05-19 but the window ends on 05-20: it belongs to 05-20, the only window of that day
    rd("2031-05-19T22:00:00+00:00", 45, "2031-05-20T01:00:00+00:00"),
    # the week of 05-12: weekly readings 15, 33, 8 -> its peak is 33; one 5-hour window, peak 12
    rd("2031-05-13T09:00:00+00:00", 12, "2031-05-13T12:00:00+00:00", 15),
    rd("2031-05-14T09:00:00+00:00", None, "2031-05-14T12:00:00+00:00", 33),
    rd("2031-05-16T09:00:00+00:00", None, "2031-05-16T12:00:00+00:00", 8),
    # the week of 05-05 has no readings at all
    rd("2031-05-21T15:30:00+00:00", 5, "2031-05-21T18:00:00+00:00", 9),   # the anchor
]


def rows():
    quota = L.usage_report(None, None, lambda f: None, "\n".join(READINGS))["quota"]
    return {(p["level"], p["key"]): p for p in L.transcript_report(TEXT, quota)["periods"]}


def test_a_day_averages_the_peaks_of_its_windows():
    d = rows()[("day", "2031-05-19")]
    assert (d["five_hour"], d["five_hour_windows"], d["five_hour_high"]) == (30, 3, 60)


def test_a_window_ending_the_next_day_belongs_to_the_next_day_and_one_window_equals_its_peak():
    d = rows()[("day", "2031-05-20")]
    assert (d["five_hour"], d["five_hour_windows"], d["five_hour_high"]) == (45, 1, 45)


def test_a_week_row_shows_its_highest_weekly_reading_and_its_window_mean():
    w = rows()[("week", "2031-05-12")]
    assert w["seven_day"] == 33 and (w["five_hour"], w["five_hour_windows"]) == (12, 1)


def test_a_week_without_readings_has_no_value_not_zero():
    w = rows()[("week", "2031-05-05")]
    assert w["seven_day"] is None and w["five_hour"] is None and w["five_hour_windows"] == 0


def test_hour_rows_keep_their_own_highest_reading():
    assert rows()[("hour", "2031-05-21T11:00")]["five_hour"] is not None   # held from the newest earlier period
    assert "five_hour_windows" not in rows()[("hour", "2031-05-21T11:00")]


def test_page_header_and_honest_limit():
    fn = PAGE[PAGE.index("function sessionCards(T)"):PAGE.index("function fitCard(F)")]
    assert "<th>5-hour peak (avg)</th>" in fn and "no readings" in fn and "only since 2026-11-24" in fn


@node
def test_page_cells_tooltip_and_no_readings():
    quota = L.usage_report(None, None, lambda f: None, "\n".join(READINGS))["quota"]
    # the day row of 05-20 only exists when that day has a message (as it does in real use)
    html = render(L.transcript_report(TEXT + "\n" + msg("2031-05-20T08:00:00+00:00"), quota))
    assert '<td class="mono" title="avg of 3 windows, highest 60 %">30 %</td>' in html
    assert '<td class="mono" title="avg of 1 window, highest 45 %">45 %</td>' in html
    week5 = re.search(r'<tr class="lv-week"><td class="mono">week of 2031-05-05</td>.*?</tr>', html).group(0)
    assert week5.count('<td class="muted">no readings</td>') == 2
