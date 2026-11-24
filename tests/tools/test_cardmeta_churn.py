import pathlib
import sys
from datetime import datetime, timedelta, timezone

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
    assert r["excluded"] == {"files": 1, "lines": 100, "by_reason": {"generated": {"files": 1, "lines": 100}, "deleted files": {"files": 0, "lines": 0}}}
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


def _git(repo, *args, env=None):
    import subprocess
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True, env=env)


def _commit(repo, when, path, text):
    import os
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    _git(repo, "add", "-A")
    stamp = when.isoformat()
    _git(repo, "commit", "-m", path, env={**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})


def test_weekly_series_from_a_temporary_repo(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    # until = Sunday 2026-11-22 12:00 UTC; the week is Mon 2026-11-16, the 28-day window starts Mon 2026-10-26 + 12h
    until = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
    at = lambda d, h=9: datetime(2026, 11, d, h, tzinfo=timezone.utc) if d >= 1 else None  # noqa: E731
    _commit(tmp_path, datetime(2026, 10, 28, 9, tzinfo=timezone.utc), "a/p.py", "1\n2\n3\n")
    _commit(tmp_path, datetime(2026, 11, 3, 9, tzinfo=timezone.utc), "a/one/q.py", "1\n")
    _commit(tmp_path, datetime(2026, 11, 10, 9, tzinfo=timezone.utc), "tests/a/test_p.py", "1\n2\n")
    _commit(tmp_path, datetime(2026, 11, 10, 10, tzinfo=timezone.utc), "docs/a.md", "x\n")
    _commit(tmp_path, at(18), "b/z.py", "1\n2\n")
    _commit(tmp_path, at(18, 10), "loose.txt", "1\n")
    _commit(tmp_path, datetime(2025, 1, 6, 9, tzinfo=timezone.utc), "a/old.py", "1\n")  # older than 52 weeks
    mmap = dict(MAP, assign=MAP["assign"] + [{"path": "tests/a/", "module": "a"}])
    recs = ch.parse_log(ch.git_log(tmp_path, until - timedelta(days=ch.WEEKS * 7 + 400), until))
    r = ch.aggregate(recs, mmap, [28], until, [])
    a = r["modules"]["a"]["series"]["weekly"]
    assert len(a["own"]) == 52 and a["own"][-1]["w"] == "2026-11-16" and a["own"][-2]["w"] == "2026-11-09"
    wk = {e["w"]: e for e in a["own"]}
    assert wk["2026-10-26"] == {"w": "2026-10-26", "commits": 1, "added": 3, "removed": 0, "tests": 0, "docs": 0}
    assert wk["2026-11-09"]["tests"] == 2 and wk["2026-11-09"]["added"] == 2 and wk["2026-11-09"]["commits"] == 1
    assert wk["2026-11-02"]["added"] == 0                      # a/one/q.py is a sub-module file: not in own
    assert {e["w"]: e for e in a["total"]}["2026-11-02"]["added"] == 1
    assert sum(e["added"] for e in a["own"]) == 5 and "2025-01-06" not in wk
    assert r["modules"]["a/one"]["series"]["weekly"]["own"] == r["modules"]["a/one"]["series"]["weekly"]["total"]
    docs = r["unassigned"]["series"]["weekly"]["own"]
    assert {e["w"]: e for e in docs}["2026-11-09"]["docs"] == 1 and docs[-1]["added"] == 1
    # weekly sums over the 28-day window (4 whole weeks) equal the window totals, for every module and the unassigned entry
    entries = [(r["modules"][m]["own"]["28"], r["modules"][m]["series"]["weekly"]["own"]) for m in r["modules"]]
    entries += [(r["modules"][m]["total"]["28"], r["modules"][m]["series"]["weekly"]["total"]) for m in r["modules"]]
    entries.append((r["unassigned"]["28"], r["unassigned"]["series"]["weekly"]["own"]))
    for win, weeks in entries:
        last4 = weeks[-4:]
        assert sum(e["added"] for e in last4) == win["added"] and sum(e["removed"] for e in last4) == win["removed"]
        assert sum(e["tests"] for e in last4) == win["tests"]["churn"] and sum(e["docs"] for e in last4) == win["docs"]["churn"]
        assert sum(e["commits"] for e in last4) == win["commits"]
    assert all(c["ok"] for c in r["mapping_check"].values())


def test_week_starts_are_mondays_and_end_with_the_current_week():
    w = ch.week_starts(datetime(2026, 11, 22, 23, tzinfo=timezone.utc))
    assert len(w) == 52 and w[-1] == "2026-11-16" and w[0] == "2025-11-24"


def test_deleted_files_are_excluded_not_unassigned(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    until = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
    _commit(tmp_path, datetime(2026, 11, 10, 9, tzinfo=timezone.utc), "a/keep.py", "1\n2\n")
    _commit(tmp_path, datetime(2026, 11, 11, 9, tzinfo=timezone.utc), "gone/job.py", "1\n2\n3\n")
    _commit(tmp_path, datetime(2026, 11, 12, 9, tzinfo=timezone.utc), "a/gone.py", "1\n")
    _git(tmp_path, "rm", "-q", "gone/job.py", "a/gone.py")
    _commit(tmp_path, datetime(2026, 11, 13, 9, tzinfo=timezone.utc), "loose.txt", "x\n")
    recs = ch.parse_log(ch.git_log(tmp_path, until - timedelta(days=500), until))
    existing = set(ch.mp.tracked_files(tmp_path))
    r = ch.aggregate(recs, MAP, [28], until, [], existing)
    assert r["modules"]["a"]["own"]["28"]["churn"] == 2 and r["modules"]["a"]["own"]["28"]["files"] == 1
    assert r["unassigned"]["28"]["churn"] == 1 and r["unassigned"]["28"]["files"] == 1
    # job.py: +3 -3 (added and deleted), gone.py: +1 -1 -> 8 lines, 2 files
    assert r["excluded"]["by_reason"]["deleted files"] == {"files": 2, "lines": 8} and r["excluded"]["lines"] == 8
    c = r["mapping_check"]["28"]
    assert c["ok"] and c["raw_lines"] == 2 + 8 + 1 and c["bucket_lines"] == 3 and c["excluded_lines"] == 8
    weeks = r["unassigned"]["series"]["weekly"]["own"]
    assert sum(e["added"] + e["removed"] for e in weeks[-4:]) == r["unassigned"]["28"]["churn"] == 1
    a = r["modules"]["a"]["series"]["weekly"]["own"]
    assert sum(e["added"] + e["removed"] for e in a[-4:]) == 2
    # without the rule (existing=None) the deleted files fall to a module / unassigned as before
    assert ch.aggregate(recs, MAP, [28], until, [])["unassigned"]["28"]["churn"] == 1 + 6


def test_default_card_window_counts_only_the_last_10_days_with_the_trend_against_the_10_before(tmp_path):
    """Synthetic history: commits 5, 15 and 40 days old. The default run writes the card window (logic.CARD_CHURN_DAYS = 10) first,
    keeps 30 and 90; the 10-day window holds only the 5-day commit and compares with days 10..20 (the 15-day commit)."""
    import json
    import subprocess
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    import logic as L
    assert L.CARD_CHURN_DAYS == 10 and ch.DEFAULT_DAYS[0] == L.CARD_CHURN_DAYS and {30, 90} <= set(ch.DEFAULT_DAYS)
    repo, out = tmp_path / "repo", tmp_path / "out"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    until = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
    _commit(repo, until - timedelta(days=40), "a/p.py", "1\n2\n3\n4\n")  # +4
    _commit(repo, until - timedelta(days=15), "a/p.py", "1\n2\n")        # -2
    _commit(repo, until - timedelta(days=5), "a/p.py", "1\n2\n3\n")      # +1
    (tmp_path / "map.json").write_text(json.dumps(MAP))
    assert ch.main(["--repo", str(repo), "--map", str(tmp_path / "map.json"), "--project", "demo", "--out-dir", str(out),
                    "--until", until.isoformat()]) == 0
    d = json.loads((out / "churn.json").read_text())
    assert d["windows"] == [10, 30, 90]
    w10, w30 = d["modules"]["a"]["total"]["10"], d["modules"]["a"]["total"]["30"]
    assert (w10["commits"], w10["added"], w10["removed"], w10["net"], w10["churn"], w10["active_days"]) == (1, 1, 0, 1, 1, 1)
    assert w10["source"]["churn"] == 1 and w10["files"] == 1
    assert w10["prev_churn"] == 2 and w10["trend_pct"] == -50.0
    assert w30["commits"] == 2 and w30["prev_churn"] == 4                # 30 days: the 5- and 15-day commits, before: the 40-day one
    assert d["modules"]["a"]["total"]["90"]["commits"] == 3
