import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import cardmeta_churn as ch  # noqa: E402

MAP = {"modules": [{"id": "a"}, {"id": "a/one"}, {"id": "b"}],
       "assign": [{"path": "a/", "module": "a"}, {"path": "a/one/", "module": "a/one"}, {"path": "b/", "module": "b"}]}
UNTIL = datetime(2026, 11, 20, tzinfo=timezone.utc)
T = UNTIL.timestamp()
DAY = 86400


def rec(sha, days_ago, path, add, rem):
    return {"sha": sha, "ts": int(T - days_ago * DAY), "path": path, "added": add, "removed": rem}


def test_parse_log_and_renames_and_binary():
    text = "@@@abc|1000\n3\t1\ta/x.py\n-\t-\timg.png\n2\t0\ta/{old => new}/y.py\n@@@def|2000\n1\t1\told.py => b/z.py\n"
    r = ch.parse_log(text)
    assert [(x["sha"], x["path"], x["added"], x["removed"]) for x in r] == [
        ("abc", "a/x.py", 3, 1), ("abc", "img.png", 0, 0), ("abc", "a/new/y.py", 2, 0), ("def", "b/z.py", 1, 1)]


def test_glob_to_regex():
    assert ch.glob_to_regex("**/*.lock").match("x/y/poetry.lock") and ch.glob_to_regex("docs/screens/**").match("docs/screens/a.png")
    assert not ch.glob_to_regex("*.lock").match("x/y.lock") and ch.glob_to_regex(".coverage").match(".coverage")


def test_windows_rollup_trend_and_check():
    records = [rec("c1", 5, "a/p.py", 10, 2), rec("c2", 5, "a/one/q.py", 4, 0), rec("c3", 40, "a/p.py", 6, 0),
               rec("c4", 3, "tests/test_p.py", 1, 1), rec("c5", 2, "loose.txt", 7, 0), rec("c6", 1, ".coverage", 100, 0)]
    # tests/test_p.py mirrors p.py -> no entry for it in this map, so it is unassigned too
    r = ch.aggregate(records, MAP, [30], UNTIL, [ch.glob_to_regex(".coverage")])
    a = r["modules"]["a"]
    assert a["own"]["30"]["churn"] == 12 and a["total"]["30"]["churn"] == 16 and r["modules"]["a/one"]["total"]["30"]["churn"] == 4
    assert a["own"]["30"]["prev_churn"] == 6 and a["own"]["30"]["trend_pct"] == 100.0
    assert r["modules"]["b"]["own"]["30"]["trend_pct"] is None
    assert r["unassigned"]["30"]["churn"] == 9 and r["unassigned"]["30"]["tests"]["churn"] == 2
    assert r["excluded"] == {"files": 1, "lines": 100}
    assert r["mapping_check"]["30"] == {"raw_lines": 125, "bucket_lines": 25, "excluded_lines": 100, "ok": True}
    assert a["own"]["30"]["source"]["churn"] == 12 and a["own"]["30"]["active_days"] == 1 and a["own"]["30"]["commits"] == 1


def test_size_and_line_counting():
    assert ch.count_code_lines("x.py", "# c\n\nx = 1\n  # d\ny = 2\n") == 2
    assert ch.count_code_lines("x.html", "<p>\n\n</p>\n") == 2 and ch.count_code_lines("x.png", "zzz") is None
    s = ch.size_by_module([("a/p.py", 10), ("a/one/q.py", 5), ("tests/a/test_p.py", 3), ("zzz.py", 99)], MAP)
    assert s["a"] == {"source_lines": 15, "test_lines": 3, "files": 3} and s["a/one"]["source_lines"] == 5
    assert "unassigned" not in s


def test_parse_until_and_trend_helpers():
    assert ch.parse_until("2026-11-20").tzinfo is not None and ch.parse_until("2026-11-20T00:00:00Z").year == 2026
