"""Quota estimates (PC-111): the quota percentage of a window without readings, from its token counts, calibrated per window kind on the
windows with readings of the same plan period; synthetic readings and usage, no project data."""
import datetime as dt
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import cockpit as C  # noqa: E402
import logic as L  # noqa: E402

PAGE = (ROOT / "web" / "index.html").read_text()


def ep(s):
    return int(dt.datetime.fromisoformat(s + "+00:00").timestamp())


R1, R2 = ep("2026-11-10T08:00:00"), ep("2026-11-24T08:00:00")   # known weekly resets; the week ending 2026-11-17 has no reading


def reading(ts, seven=None, r7=None, five=None, r5=None):
    rec = {"ts": ts + "+00:00"}
    if seven is not None:
        rec["seven_day"] = {"used_percentage": seven, "resets_at": r7}
    if five is not None:
        rec["five_hour"] = {"used_percentage": five, "resets_at": r5}
    return json.dumps(rec)


QUOTA = "\n".join([reading("2026-11-09T10:00:00", 10, R1),
                   reading("2026-11-09T12:00:00", 20, R1, 10, ep("2026-11-09T13:00:00")),
                   reading("2026-11-23T12:00:00", 30, R2 + 40)])        # the reset time wobbles by seconds


def tok(ts, n):
    return json.dumps({"ts": ts + "+00:00", "input_tokens": n})


PROXY = "\n".join([tok("2026-11-09T09:00:00", 1000),   # before the peak readings: calibrates both kinds
                   tok("2026-11-09T15:00:00", 500),    # after the weekly peak: counted, does not calibrate
                   tok("2026-11-12T10:00:00", 2000)])  # the week without readings
TRANSCRIPT = "\n".join([tok("2026-11-23T10:00:00", 3000), tok("2026-10-25T10:00:00", 700)])


def week(rep, start):
    return next(r for r in rep["seven_day"]["rows"] if r["start"].startswith(start))


def test_weekly_calibration_and_estimate_of_a_week_without_readings():
    rep = L.quota_estimates(QUOTA, PROXY, TRANSCRIPT)
    cal = rep["seven_day"]["calibration"]
    assert cal["factor"] == 80 and cal["readings"] == 3 and cal["windows"] == 2       # (1000 + 3000) tokens / (20 + 30) percent
    assert (cal["first"], cal["last"]) == ("2026-11-03", "2026-11-24")
    w = week(rep, "2026-11-10T08:00")
    assert w["state"] == "estimate" and w["percent"] == 25.0 and w["tokens"] == 2000 and w["kind"] == "extrapolated"
    assert "80 tokens per percent" in w["note"] and "3 readings" in w["note"] and "2 weekly windows" in w["note"]
    assert "2026-11-03 to 2026-11-24" in w["note"]
    assert "extrapolated by whole 7-day steps" in w["note"] and "2026-11-24 08:00" in w["note"]


def test_a_measured_value_overrides_the_estimate():
    w = week(L.quota_estimates(QUOTA, PROXY, TRANSCRIPT), "2026-11-03T08:00")
    assert w["state"] == "measured" and w["percent"] == 20 and w["tokens"] == 1500 and w["readings"] == 2 and w["kind"] == "vendor"
    assert w["note"].startswith("Measured")


def test_weekly_windows_follow_the_vendor_resets_not_calendar_weeks():
    rows = L.quota_estimates(QUOTA, PROXY, TRANSCRIPT)["seven_day"]["rows"]
    assert [r["start"][:16] for r in rows] == ["2026-10-20T08:00", "2026-10-27T08:00", "2026-11-03T08:00", "2026-11-10T08:00",
                                               "2026-11-17T08:00"]
    assert rows[-1]["end"] == "2026-11-24T08:00+00:00" and rows[-1]["kind"] == "vendor"
    assert all(a["end"] == b["start"] for a, b in zip(rows, rows[1:]))            # contiguous: no sliver from the wobbling reset
    assert all(dt.datetime.fromisoformat(r["start"]).weekday() == 1 for r in rows)   # a Tuesday 08:00, not a Monday


def test_no_estimate_before_the_plan_period():
    rep = L.quota_estimates(QUOTA, PROXY, TRANSCRIPT)
    for start in ("2026-10-20", "2026-10-27"):   # October, and the window that straddles the plan start
        w = week(rep, start)
        assert w["state"] == "other plan" and w["percent"] is None and "other plan" in w["note"].lower()


def test_no_estimate_across_a_plan_change():
    rep = L.quota_estimates(QUOTA, PROXY, TRANSCRIPT, {"quota_plan_start": "2026-11-15"})
    assert rep["seven_day"]["calibration"]["factor"] == 100 and rep["seven_day"]["calibration"]["readings"] == 1   # only the newer week
    assert week(rep, "2026-11-10")["state"] == "other plan"
    assert week(rep, "2026-11-03")["state"] == "measured"                           # a reading is shown whatever the plan


def test_weak_and_firm():
    assert week(L.quota_estimates(QUOTA, PROXY, TRANSCRIPT), "2026-11-10")["weak"] is True       # 3 readings, below the default 20
    assert "Weak: fewer than 20 readings" in week(L.quota_estimates(QUOTA, PROXY, TRANSCRIPT), "2026-11-10")["note"]
    w = week(L.quota_estimates(QUOTA, PROXY, TRANSCRIPT, {"quota_weak_readings": 3}), "2026-11-10")
    assert w["weak"] is False and "Weak" not in w["note"]


def test_no_calibration_yet_shows_no_number():
    rep = L.quota_estimates(None, PROXY, TRANSCRIPT)
    assert rep["seven_day"]["calibration"] is None and rep["five_hour"]["calibration"] is None
    rows = rep["seven_day"]["rows"] + rep["five_hour"]["rows"]
    assert rows and all(r["percent"] is None for r in rows)
    assert {r["state"] for r in rows} == {"none", "other plan"} and all(r["kind"] == "fixed" for r in rows)
    assert "No calibration yet" in next(r for r in rows if r["state"] == "none")["note"]
    assert L.quota_estimates(None, None, None) is None


def test_five_hour_windows_have_their_own_calibration():
    rep = L.quota_estimates(QUOTA, PROXY, TRANSCRIPT)
    cal = rep["five_hour"]["calibration"]
    assert cal["factor"] == 100 and cal["readings"] == 1 and cal["weak"] is True                  # 1000 tokens / 10 percent
    rows = {r["start"][:16]: r for r in rep["five_hour"]["rows"]}
    assert rows["2026-11-09T08:00"]["state"] == "measured" and rows["2026-11-09T08:00"]["percent"] == 10
    est = [r for r in rows.values() if r["state"] == "estimate"]
    assert sorted(r["percent"] for r in est) == [5.0, 20.0, 30.0] and all(r["kind"] == "fixed" and "fixed 5-hour block" in r["note"] for r in est)
    assert [r["state"] for r in rows.values() if r["start"] < "2026-11"] == ["other plan"]


def test_vendor_windows_extrapolate_and_keep_a_shifted_rest():
    d = 86400
    w = L.vendor_windows([10 * d, 27 * d], 7 * d, 0, 30 * d)
    assert [(s // d, e // d, b and b // d) for s, e, b in w] == [(-4, 3, 10), (3, 10, None), (10, 13, 27), (13, 20, 27), (20, 27, None), (27, 34, 27)]
    assert L.vendor_windows([], 7 * d, 0, 30 * d) == []
    assert L.vendor_windows([10 * d, 12 * d], 7 * d, 0, 0, extrapolate=False) == [(3 * d, 10 * d, None), (10 * d, 12 * d, None)]


def test_quota_settings_are_validated():
    assert L.validate_quota_settings({}) == L.QUOTA_SETTING_DEFAULTS == {"quota_plan_start": "2026-11-01", "quota_weak_readings": 20}
    assert L.validate_quota_settings({"quota_plan_start": "2027-01-01", "quota_weak_readings": "5"}) == {"quota_plan_start": "2027-01-01", "quota_weak_readings": 5}
    for bad in ({"quota_plan_start": "November"}, {"quota_weak_readings": 0}, {"quota_weak_readings": "2.5"}, {"quota_weak_readings": True}):
        with pytest.raises(ValueError):
            L.validate_quota_settings(bad)
        assert L.validate_quota_settings(bad, strict=False) == L.QUOTA_SETTING_DEFAULTS
    assert L.validate_card_settings({"churn_colour": "size", "quota_weak_readings": 7})["quota_weak_readings"] == 7


def test_get_usage_serves_the_estimates_with_the_project_settings(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "quota-history.jsonl").write_text(QUOTA)
    (d / "proxy-usage.jsonl").write_text(PROXY)
    (d / "transcript-usage.jsonl").write_text(TRANSCRIPT)
    (d / "cockpit-settings.json").write_text(json.dumps({"churn_colour": "size", "quota_weak_readings": 2}))
    p = dict(name="proj", repo="o/r")
    est = C.get_usage(p, tmp_path, {"proj": p})["estimates"]
    assert est["weak_readings"] == 2 and est["seven_day"]["calibration"]["weak"] is False
    assert week(est, "2026-11-10")["percent"] == 25.0


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_page_marks_estimates_and_names_their_basis():
    est = L.quota_estimates(QUOTA, PROXY, TRANSCRIPT)
    code = "\n".join([re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0), re.search(r"^function recentRows\(rows,has\).*$", PAGE, re.M).group(0),
                      PAGE[PAGE.index("function estimateCard(E){"):PAGE.index("function sessionCards(T){")],
                      "process.stdout.write(estimateCard(%s))" % json.dumps(est)])
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "<i>25 % est.</i>" in r.stdout and "weak" in r.stdout and "80 tokens per percent" in r.stdout
    assert "no readings, other plan" in r.stdout and ">20 %</td>" in r.stdout
    none = subprocess.run(["node", "-e", code.replace(json.dumps(est), json.dumps(L.quota_estimates(None, PROXY, None)))], capture_output=True, text=True)
    assert "no calibration yet" in none.stdout and "est." not in none.stdout.replace("“est.”", "")
