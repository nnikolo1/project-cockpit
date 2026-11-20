import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import datetime as dt
import json
import cockpit as C
import logic as L

FIX = pathlib.Path(__file__).parent / "fixtures" / "budget"
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 20, 10, 40, tzinfo=UTC)
FRESH = dt.datetime(2026, 11, 20, 10, 35, tzinfo=UTC)


def txt(n):
    return (FIX / n).read_text()


def test_parse_ts_epoch_iso_and_junk():
    assert L.parse_ts(1795176000) == dt.datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    assert L.parse_ts("2026-11-24T09:00:00Z") == dt.datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
    assert L.parse_ts("2026-11-24T09:00:00") == dt.datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
    for bad in (None, "", "soon", True, [], 1e30):
        assert L.parse_ts(bad) is None


def test_quota_fresh_reading_has_share_reset_and_age():
    q = L.quota_view(txt("quota.json"), FRESH, NOW)
    assert q["status"] == "ok" and not q["stale"] and q["age_s"] == 300
    five, week = q["windows"]
    assert (five["key"], five["used_pct"], five["left_pct"], five["resets_in_s"], five["expired"]) == ("five_hour", 31.0, 69.0, 4800, False)
    assert week["resets_in_s"] == 3 * 86400 + 22 * 3600 + 20 * 60
    assert five["resets_at"] == "2026-11-20T12:00:00Z"


def test_quota_stale_after_30_minutes_and_expired_window():
    old = NOW - dt.timedelta(minutes=31)
    assert L.quota_view(txt("quota.json"), old, NOW)["stale"]
    assert not L.quota_view(txt("quota.json"), NOW - dt.timedelta(minutes=29), NOW)["stale"]
    late = dt.datetime(2026, 11, 20, 13, 0, tzinfo=UTC)
    w = L.quota_view(txt("quota.json"), late, late)["windows"][0]
    assert w["expired"] and w["resets_in_s"] == 0


def test_quota_unknown_mtime_is_stale_and_percentages_are_clamped():
    q = L.quota_view('{"five_hour": {"used_percentage": 140, "resets_at": null}}', None, NOW)
    assert q["stale"] and q["windows"][0]["used_pct"] == 100.0 and q["windows"][0]["resets_in_s"] is None


def test_quota_missing_and_invalid_never_raise():
    assert L.quota_view(None, None, NOW)["status"] == "missing"
    for bad in ("", "{", "[]", "{}", '{"five_hour": {"used_percentage": "x"}}', '{"five_hour": 3}'):
        q = L.quota_view(bad, FRESH, NOW)
        assert q["status"] == "invalid" and q["windows"] == []


def test_usage_reported_cap_and_derived_cap_are_both_estimates():
    q = L.quota_view(txt("quota.json"), FRESH, NOW)
    u = L.usage_view(txt("usage.json"), None, NOW, q)
    assert u["status"] == "ok" and not u["stale"] and u["age_s"] == 600
    five, week = u["windows"]["five_hour"], u["windows"]["seven_day"]
    assert (five["tokens_cap"], five["cap_source"], five["tokens_left"]) == (1000000, "reported", 690000)
    assert (week["cap_source"], week["tokens_cap"]) == ("derived", round(5550000 / 0.555))


def test_usage_no_cap_without_a_quota_share():
    u = L.usage_view(txt("usage.json"), None, NOW, None)
    w = u["windows"]["seven_day"]
    assert w["tokens_cap"] is None and w["cap_source"] is None and w["tokens_left"] is None


def test_usage_stale_invalid_and_missing():
    assert L.usage_view(txt("usage.json"), None, NOW + dt.timedelta(hours=25))["stale"]
    assert L.usage_view(None, None, NOW)["status"] == "missing"
    for bad in ("{", "[]", '{"schema": 2, "windows": {}}', '{"schema": 1}'):
        assert L.usage_view(bad, None, NOW)["status"] == "invalid"
    assert L.usage_view('{"schema": 1, "windows": {"five_hour": {"tokens_used": "many"}}}', None, NOW)["windows"] == {}


def test_decision_log_newest_first_and_bad_lines_counted():
    d = L.decision_view(txt("decisions.jsonl"))
    assert d["status"] == "ok" and d["skipped"] == 3
    assert [r["rank"] for r in d["releases"]] == [2, 1]
    assert d["releases"][0]["dry_run"] is True and d["releases"][1]["dry_run"] is False
    assert [p["decision"] for p in d["polls"]] == ["release 20", "skip: not idle"]
    p = d["polls"][0]
    assert p["idle"] is True and p["triggers"][0] == {"name": "five_hour_20", "verdict": "fired"} and p["weekly_used"] == 55.5


def test_decision_log_limit_and_missing_fields():
    lines = "\n".join(json.dumps(dict(kind="release", time=f"2026-11-20T10:{i:02d}:00Z", issue=i)) for i in range(5))
    d = L.decision_view(lines, limit=2)
    assert [r["issue"] for r in d["releases"]] == [4, 3]
    assert d["releases"][0]["title"] is None and d["releases"][0]["dry_run"] is False
    assert L.decision_view(None)["status"] == "missing" and L.decision_view("")["releases"] == []


def test_decision_log_free_text_is_capped_and_odd_shapes_tolerated():
    row = dict(kind="release", time="2026-11-20T10:00:00Z", title="x" * 1000, rank="1", triggers="nope")
    r = L.decision_view(json.dumps(row))["releases"][0]
    assert len(r["title"]) == L.TEXT_CAP and r["rank"] is None
    p = L.decision_view(json.dumps(dict(kind="poll", time="2026-11-20T10:00:00Z", triggers=["x", {"name": "a"}], idle="yes")))["polls"][0]
    assert p["triggers"] == [{"name": "a", "verdict": None}] and p["idle"] is None


def test_build_budget_reads_files_and_survives_absent_ones(tmp_path):
    old = {k: C.CFG[k] for k in ("quota_feed", "usage_report", "decision_log")}
    try:
        C.CFG.update(quota_feed=str(FIX / "quota.json"), usage_report=str(FIX / "usage.json"), decision_log=str(FIX / "decisions.jsonl"))
        b = C.build_budget(NOW)
        assert b["quota"]["status"] == "ok" and b["usage"]["status"] == "ok" and len(b["log"]["releases"]) == 2
        C.CFG.update(quota_feed=str(tmp_path / "none"), usage_report=str(tmp_path / "none"), decision_log=str(tmp_path / "none"))
        b = C.build_budget(NOW)
        assert (b["quota"]["status"], b["usage"]["status"], b["log"]["status"]) == ("missing",) * 3
    finally:
        C.CFG.update(old)


def test_quota_stale_limit_is_a_parameter():
    t = NOW - dt.timedelta(minutes=20)
    assert not L.quota_view(txt("quota.json"), t, NOW)["stale"]
    assert L.quota_view(txt("quota.json"), t, NOW, stale_min=15)["stale"]
    assert L.budget_view(txt("quota.json"), t, None, None, None, NOW, stale_min=15)["quota"]["stale"]
