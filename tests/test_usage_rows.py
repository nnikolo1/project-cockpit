"""The per-day and per-window tables of the Token Usage page list only periods with usage (PC-80). Synthetic proxy data, no project names."""
import sys, pathlib, json, re
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import logic as L

HTML = (pathlib.Path(__file__).parent.parent / "web" / "index.html").read_text()


def u(ts, n=1, worker="w"):
    return json.dumps(dict(ts=ts, worker=worker, input_tokens=n, output_tokens=n, cache_creation_input_tokens=n, cache_read_input_tokens=n))


def rep(*lines):
    return L.usage_report("\n".join(lines), None, lambda f: None)


def test_a_month_with_two_active_days_yields_two_rows_newest_first():
    r = rep(u("2026-03-03T10:00:00Z", 1), u("2026-03-03T11:00:00Z", 1), u("2026-03-17T08:00:00Z", 5))
    assert [(d["key"], d["total"]) for d in r["days"]] == [("2026-03-17", 20), ("2026-03-03", 8)]


def test_a_day_with_zero_in_every_count_has_no_row():
    r = rep(u("2026-03-02T10:00:00Z", 0), u("2026-03-03T10:00:00Z", 2))
    assert [d["key"] for d in r["days"]] == ["2026-03-03"]
    assert [w["key"] for w in r["windows"]] == ["2026-03-03 10:00"]
    assert [w["key"] for w in r["workers"]] == ["w"]            # the per-Worker table stays as it is


def test_no_usage_at_all_gives_empty_lists_and_the_empty_line():
    for r in (rep(""), rep(u("2026-03-02T10:00:00Z", 0))):
        assert r["days"] == [] and r["windows"] == []
    assert "const NOUSE='no usage in the last 90 days'" in HTML
    assert "usageTable('Usage per day',USE.days,NOUSE)" in HTML and re.search(r"usageTable\('Usage per 5-hour window',.*?,NOUSE\)", HTML)
    assert L.usage_report(None, None, lambda f: None)["days"] is None   # a missing file is still "no data"


def test_five_hour_windows_list_only_windows_with_usage_newest_first():
    r = rep(u("2026-03-01T01:00:00Z", 1), u("2026-03-01T06:00:00Z", 0), u("2026-03-02T12:00:00Z", 3))
    assert [(w["key"], w["total"]) for w in r["windows"]] == [("2026-03-02 09:00", 12), ("2026-02-28 22:00", 4)]   # fixed epoch blocks


def test_only_the_last_90_days_are_listed_and_the_summary_keeps_the_last_day():
    r = rep(u("2025-11-01T00:00:00Z", 9), u("2026-03-01T00:00:00Z", 1))
    assert [d["key"] for d in r["days"]] == ["2026-03-01"]
    r["tasks"] = None
    assert L.usage_summary(r)["last_day"] == "2026-03-01" and L.usage_summary(r)["total"] == 4


def test_bar_is_scaled_to_the_highest_row_listed():
    body = HTML[HTML.index("function usageTable("):HTML.index("function quotaCard(")]
    assert "const mx=Math.max(1,...rows.map(r=>r.total));" in body and "100*r.total/mx" in body
    r = rep(u("2026-03-01T00:00:00Z", 1), u("2026-03-05T00:00:00Z", 4))
    mx = max(d["total"] for d in r["days"])
    assert [100 * d["total"] / mx for d in r["days"]] == [100, 25]
