import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import json
import os
import pytest
from bootfill import core as B, job as J, adapters as A, quota_history as Q, quota_backfill as BF
from test_bootfill_job import make, task, quota_of, NOW, H

# 2026-11-20 10:00 local is NOW; transcript timestamps are written as epoch-derived ISO strings


def line(ts, mid, rid="r1", i=1, o=2, cc=3, cr=4, extra=None):
    d = dict(timestamp=B.iso(ts), requestId=rid, message=dict(id=mid, usage=dict(input_tokens=i, output_tokens=o, cache_creation_input_tokens=cc,
                                                                                 cache_read_input_tokens=cr), content="SECRET TEXT"))
    d.update(extra or {})
    return json.dumps(d) + "\n"


def write(p, *lines, mode="w"):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, mode) as f:
        f.writelines(lines)
    return p


def scanner(tmp, own=(), cache=True):
    return Q.TokenScanner(str(tmp / "t" / "**" / "*.jsonl"), own, tmp / "cache.json" if cache else None)


def test_parse_line_reads_usage_only_and_tolerates_junk():
    r = Q.parse_usage_line(line(NOW, "m1").encode())
    assert r[1:] == ("m1", "r1", (1, 2, 3, 4))
    assert "SECRET" not in repr(r)
    for bad in (b'{"usage": ', b'{"message": {"usage": 5}, "timestamp": "x"}', b'{"timestamp": "bad", "message": {"usage": {}}}', b"plain text"):
        assert Q.parse_usage_line(bad) is None


def test_window_boundaries_and_zero_note(tmp_path):
    write(tmp_path / "t/a.jsonl", line(NOW - 5 * H, "a"), line(NOW - 5 * H - 1, "b"), line(NOW, "c"), line(NOW + 1, "d"))
    s = scanner(tmp_path)
    s.scan(NOW - 8 * 86400)
    t = s.window(NOW - 5 * H, NOW)
    assert t["messages"] == 2 and t["total"] == 20 and t["input"] == 2  # start and end both inclusive
    z = s.window(NOW - 100 * H, NOW - 90 * H)
    assert z["total"] == 0 and z["messages"] == 0 and "no transcript" in z["note"]


def test_dedup_by_message_and_request_id_keeps_largest(tmp_path):
    write(tmp_path / "t/a.jsonl", line(NOW - 60, "m", o=1), line(NOW - 50, "m", o=9), line(NOW - 40, "m", rid="r2", o=5))
    write(tmp_path / "t/b.jsonl", line(NOW - 30, "m", o=9))  # the same message copied into another transcript
    s = scanner(tmp_path)
    s.scan(NOW - 86400)
    t = s.window(NOW - H, NOW)
    assert t["messages"] == 2 and t["output"] == 9 + 5


def test_own_folder_counted_separately(tmp_path):
    write(tmp_path / "t/mine/a.jsonl", line(NOW - 60, "m1"))
    write(tmp_path / "t/other/a.jsonl", line(NOW - 60, "m2"))
    s = scanner(tmp_path, own=["*/mine/*"])
    s.scan(NOW - 86400)
    t = s.window(NOW - H, NOW)
    assert t["total"] == 20 and t["own_total"] == 10


def test_incremental_scan_equals_full_scan_and_partial_line(tmp_path):
    f = write(tmp_path / "t/a.jsonl", line(NOW - 300, "m1"), line(NOW - 200, "m2"))
    s1 = scanner(tmp_path)
    s1.scan(NOW - 86400)
    write(f, line(NOW - 100, "m3"), '{"timestamp": "2026-11-2', mode="a")  # partial line still being written
    s1.scan(NOW - 86400)
    assert s1.window(NOW - H, NOW)["messages"] == 3 and s1.stats["read"] == 1
    write(f, 'x"}\n', mode="a")
    s1.scan(NOW - 86400)
    s2 = scanner(tmp_path, cache=False)  # a fresh full scan
    s2.scan(NOW - 86400)
    assert s1.window(0, NOW + 1) == s2.window(0, NOW + 1)
    s3 = scanner(tmp_path)  # a new object over the saved cache reads nothing again
    s3.scan(NOW - 86400)
    assert s3.stats["read"] == 0 and s3.window(0, NOW + 1) == s2.window(0, NOW + 1)


def test_rewritten_shorter_file_is_rescanned_without_double_counting(tmp_path):
    f = write(tmp_path / "t/a.jsonl", line(NOW - 300, "m1"), line(NOW - 200, "m2"))
    s = scanner(tmp_path)
    s.scan(NOW - 86400)
    write(f, line(NOW - 300, "m1"))
    s.scan(NOW - 86400)
    assert s.window(0, NOW)["messages"] == 2  # m2 stays (already counted), m1 is not counted twice


def test_old_events_and_files_dropped_and_cache_has_no_paths(tmp_path):
    write(tmp_path / "t/old.jsonl", line(NOW - 20 * 86400, "o"))
    write(tmp_path / "t/new.jsonl", line(NOW - 60, "n"))
    s = scanner(tmp_path)
    s.scan(NOW - 8 * 86400)
    assert list(s.events) == ["n:r1"]
    assert str(tmp_path) not in (tmp_path / "cache.json").read_text()
    assert "SECRET" not in (tmp_path / "cache.json").read_text()


def test_window_bounds():
    assert Q.window_bounds(NOW + 2 * H, 5 * H, NOW) == (NOW - 3 * H, "resets_at")
    assert Q.window_bounds(NOW - 1, 5 * H, NOW) == (NOW - 5 * H, "rolling")
    assert Q.window_bounds(None, 7 * 86400, NOW) == (NOW - 7 * 86400, "rolling")


class FeedQuota:
    """Quota fake with a feed reader (peek) and a refresh (read)."""

    def __init__(self, q, age=120.0, fail_peek=None, fail_read=None):
        self.q, self.age, self.fail_peek, self.fail_read, self.reads, self.peeks = q, age, fail_peek, fail_read, 0, 0
        self.last_age = 0.0

    def peek(self):
        self.peeks += 1
        if self.fail_peek:
            raise self.fail_peek
        return self.q, self.age

    def read(self):
        self.reads += 1
        if self.fail_read:
            raise self.fail_read
        return self.q


def ctx_with(tmp, quota, lines=(), last=None, **over):
    ctx, h, w, q, notes = make(tmp, [task(2)], last=last, **over)
    ctx.quota = quota
    write(tmp / "t/a.jsonl", *lines)
    ctx.scanner = Q.TokenScanner(str(tmp / "t/*.jsonl"), (), tmp / "data" / "cache.json")
    return ctx


def history(ctx):
    return A.read_jsonl(ctx.dir / "quota-history.jsonl")


def test_not_idle_poll_records_feed_without_refresh(tmp_path):
    fq = FeedQuota(quota_of(40, 1.0, 50, 100.0))
    ctx = ctx_with(tmp_path, fq, [line(NOW - 3600, "m1"), line(NOW - 10 * H, "m2")], last=NOW - 60)
    J.poll(ctx, NOW)
    assert fq.reads == 0 and fq.peeks == 1  # the feed was read as is: no minimal refresh call
    (r,) = history(ctx)
    assert r["idle"] is False and r["kind"] == "quota_snapshot" and r["mode"] == "dry-run"
    five, week = r["windows"]["five_hour"], r["windows"]["seven_day"]
    assert five["used_percentage"] == 40 and five["source"] == "feed" and five["reading_age_s"] == 120
    assert five["window_basis"] == "resets_at" and five["window_start"] == B.iso(NOW + H - 5 * H)
    assert five["tokens"]["messages"] == 1 and five["tokens"]["total"] == 10
    assert week["tokens"]["messages"] == 2 and week["tokens"]["total"] == 20
    assert "SECRET" not in json.dumps(r)


def test_idle_poll_refreshes_and_says_so(tmp_path):
    fq = FeedQuota(quota_of(40, 1.0, 50, 100.0))
    ctx = ctx_with(tmp_path, fq)
    J.poll(ctx, NOW)
    assert fq.reads == 1
    r = history(ctx)[-1]
    assert r["windows"]["five_hour"]["source"] == "refreshed" and r["idle"] is True
    assert r["windows"]["five_hour"]["tokens"]["total"] == 0 and "note" in r["windows"]["five_hour"]["tokens"]


def test_refresh_failure_falls_back_to_the_stale_feed_with_reason(tmp_path):
    fq = FeedQuota(quota_of(), age=5000.0, fail_read=RuntimeError("boom"))
    ctx = ctx_with(tmp_path, fq)
    J.poll(ctx, NOW)
    w = history(ctx)[-1]["windows"]["five_hour"]
    assert w["source"] == "feed" and w["reading_age_s"] == 5000 and "refresh failed" in w["reason"]


def test_feed_unavailable_gives_rolling_tokens_and_reason(tmp_path):
    fq = FeedQuota(None, fail_peek=FileNotFoundError("gone"))
    ctx = ctx_with(tmp_path, fq, [line(NOW - 60, "m1")], last=NOW - 60)
    text = J.poll(ctx, NOW)
    assert text == "skip: not idle"
    w = history(ctx)[-1]["windows"]["five_hour"]
    assert w["source"] == "unavailable" and w["used_percentage"] is None and "FileNotFoundError" in w["reason"]
    assert w["window_basis"] == "rolling" and w["tokens"]["messages"] == 1


def test_feed_window_already_over_uses_rolling(tmp_path):
    fq = FeedQuota(quota_of(five_in=-1.0))
    ctx = ctx_with(tmp_path, fq, last=NOW - 60)
    J.poll(ctx, NOW)
    assert history(ctx)[-1]["windows"]["five_hour"]["window_basis"] == "rolling"


def test_paused_poll_is_recorded_too(tmp_path):
    ctx = ctx_with(tmp_path, FeedQuota(quota_of()))
    (ctx.dir / "PAUSE").write_text("")
    J.poll(ctx, NOW)
    (r,) = history(ctx)
    assert "kill switch" in r["decision"] and r["idle"] is None
    assert r["windows"]["five_hour"]["used_percentage"] == 40


def test_below_threshold_and_scan_failure_still_record(tmp_path, caplog):
    ctx = ctx_with(tmp_path, FeedQuota(quota_of(95, 4.0, 95, 100.0)))

    class Boom:
        def scan(self, k):
            raise ValueError("x")
    ctx.scanner = Boom()
    J.poll(ctx, NOW)
    r = history(ctx)[-1]
    assert r["windows"]["five_hour"]["tokens"] is None and "scan failed" in r["windows"]["five_hour"]["tokens_note"]
    assert r["windows"]["five_hour"]["used_percentage"] == 95


def test_poll_line_shows_readings_and_tokens_when_skipped(tmp_path, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="bootfill.poll")
    ctx = ctx_with(tmp_path, FeedQuota(quota_of(40, 1.0, 50, 100.0)), [line(NOW - 60, "m1")], last=NOW - 60)
    ctx.cfg["quota_history"] = "quota-history.jsonl"
    import logging as L
    L.getLogger("bootfill.poll").propagate = True
    J.poll(ctx, NOW)
    msg = " ".join(r.getMessage() for r in caplog.records if r.name == "bootfill.poll")
    assert "n/a" not in msg.split("|")[1] and "five_used=40%" in msg and "five=feed/2min" in msg and "five_tokens=10 (1 msg, own 0)" in msg


def test_usage_json_written_atomically_and_readable_by_the_cockpit(tmp_path):
    import logic as L
    ctx = ctx_with(tmp_path, FeedQuota(quota_of(40, 1.0, 50, 100.0)), [line(NOW - 60, "m1")], last=NOW - 60)
    J.poll(ctx, NOW)
    path = tmp_path / "budget" / "usage.json"
    raw = json.loads(path.read_text())
    assert raw["schema"] == 1 and raw["windows"]["five_hour"]["tokens_used"] == 10 and raw["windows"]["five_hour"]["messages"] == 1
    assert not list(path.parent.glob("*.tmp"))
    view = L.usage_view(path.read_text(), None, __import__('datetime').datetime.fromtimestamp(NOW, __import__('datetime').timezone.utc))  # the cockpit's own reader accepts the extended file
    assert view["status"] == "ok" and view["windows"]["five_hour"]["tokens_used"] == 10 and view["windows"]["seven_day"]["tokens_used"] == 10
    ctx.cfg["usage_report"] = ""
    path.unlink()
    J.poll(ctx, NOW + 600)
    assert not path.exists()


def test_history_off_and_prune_once_a_day(tmp_path):
    ctx = ctx_with(tmp_path, FeedQuota(quota_of()), last=NOW - 60, quota_history_days=10)
    hp = ctx.dir / "quota-history.jsonl"
    A.append_jsonl(hp, dict(time=B.iso(NOW - 11 * 86400), kind="quota_snapshot"))
    A.append_jsonl(hp, dict(time=B.iso(NOW - 5 * 86400), kind="quota_snapshot"))
    hp.open("a").write("not json\n")
    J.poll(ctx, NOW)
    times = [r["time"] for r in A.read_jsonl(hp)]
    assert len(times) == 2 and B.iso(NOW - 11 * 86400) not in times
    A.append_jsonl(hp, dict(time=B.iso(NOW - 30 * 86400)))
    J.poll(ctx, NOW + 600)  # same day: not pruned again
    assert B.iso(NOW - 30 * 86400) in [r["time"] for r in A.read_jsonl(hp)]
    ctx.cfg["quota_history"] = None
    n = len(A.read_jsonl(hp))
    J.poll(ctx, NOW + 1200)
    assert len(A.read_jsonl(hp)) == n


def test_quota_peek_reads_feed_without_running_refresh(tmp_path):
    feed = tmp_path / "q.json"
    feed.write_text(json.dumps(dict(five_hour=dict(used_percentage=12, resets_at=NOW + H), seven_day=dict(used_percentage=3, resets_at=NOW + 5 * H))))
    q = A.Quota(str(feed), ["definitely-not-a-command"])
    parsed, age = q.peek()
    assert parsed["five"][0] == 12 and age >= 0


def test_backfill_buckets_and_guards(tmp_path):
    base = NOW - NOW % 3600
    write(tmp_path / "t/a.jsonl", line(base + 10, "a"), line(base + 3500, "b"), line(base + 3700, "c"), line(base - 10 * 86400, "old"))
    out = tmp_path / "out" / "h.jsonl"
    n = BF.run(str(tmp_path / "t/*.jsonl"), out, base - 3600, own=["*/a.jsonl"])
    rows = A.read_jsonl(out)
    assert n == 2 and [r["messages"] for r in rows] == [2, 1]
    assert rows[0]["reconstructed"] is True and rows[0]["percentages"] is None and rows[0]["total"] == 20 and rows[0]["own_total"] == 20
    with pytest.raises(ValueError):
        BF.run(str(tmp_path / "t/*.jsonl"), out, base)  # exists
    with pytest.raises(ValueError):
        BF.run(str(tmp_path / "t/*.jsonl"), BF.REPO / "x.jsonl", base)  # inside the repository
    assert BF.parse_date("2026-11-01") == B.parse_time("2026-11-01T00:00:00Z")
