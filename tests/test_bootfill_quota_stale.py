"""ADR 0018: stale or missing quota readings are never shown as measured and never replaced by an invented window; tokens come from the
transcripts, an estimate (window tokens / cap) stands in only when a cap is known, otherwise the job is conservative."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import json
import logging
import sys as _sys
from bootfill import core as B, job as J, adapters as A, quota_history as Q
from test_bootfill_job import make, task, quota_of, NOW, H
from test_bootfill_history import line, write, FeedQuota, ctx_with, history

WEEK = NOW + 100 * H


class NoUpdate(FeedQuota):
    """The refresh call runs but the feed does not move (what the headless call does in reality)."""

    def read(self):
        self.reads += 1
        self.last_age, self.advanced = self.age, False
        return self.q


class Moved(FeedQuota):
    def read(self):
        self.reads += 1
        self.last_age, self.advanced = 5.0, True
        return self.q


def parsed(five, week=(50.0, WEEK)):
    return dict(five=five, week=week)


# ---------- core ----------
def test_triggers_unknown_for_a_missing_window_and_never_fire():
    q = parsed(None, None)
    t = B.evaluate_triggers(q, NOW, B.DEFAULTS, {})
    assert {x["name"]: x["verdict"] for x in t} == {"five_low": "unknown", "five_high": "unknown", "weekly": "unknown"}
    assert B.decide(t, B.DEFAULTS, 0)["n"] == 0
    # an estimate without a reset time (no window known to be running) cannot fire either
    t = B.evaluate_triggers(parsed((95.0, None)), NOW, B.DEFAULTS, {})
    assert t[0]["verdict"] == "unknown"


def test_worker_slots_zero_for_unknown_window_and_estimate_used_for_threshold():
    assert B.worker_slots(0, 2, parsed(None), 5) == 0
    assert B.worker_slots(0, 2, parsed((10.0, NOW + H), None), 5) == 0
    assert B.worker_slots(0, 2, parsed((97.0, NOW + H)), 5) == 0  # an estimated 97 percent stops workers like a measured one
    assert B.worker_slots(1, 2, parsed((10.0, NOW + H)), 5) == 1


def test_feed_stale_limit_default_and_override():
    assert B.feed_stale_s(dict(poll_minutes=10)) == 1800 and B.feed_stale_s(dict(poll_minutes=10, feed_stale_minutes=5)) == 300
    assert B.feed_stale_s(B.DEFAULTS) == 1800


def test_poll_line_marks_estimates_and_unknowns():
    est = B.poll_line(NOW, True, "r", parsed((41.0, NOW + 1800)), [], "d", estimated={"five"})
    assert "five_used=~41% est. five_reset_in=0.5h" in est and "week_used=50%" in est and "week_used=~" not in est
    assert "five=unknown" in B.poll_line(NOW, True, "r", parsed(None), [], "d")
    assert "five_used=~95% est. week" in B.poll_line(NOW, True, "r", parsed((95.0, None)), [], "d", estimated={"five"})


def test_contract_poll_keeps_estimates_out_of_the_measured_fields():
    c = B.contract_poll(NOW, True, "r", parsed((41.0, NOW + 1800)), [], "d", estimated={"five"})
    assert "five_hour_used" not in c and c["five_hour_est_used"] == 41.0 and c["weekly_used"] == 50.0
    c = B.contract_poll(NOW, True, "r", parsed(None), [], "d")
    assert not any(k.startswith("five_hour") for k in c)


# ---------- classification and estimate ----------
def test_classify_sources_are_honest():
    p = parsed((40.0, NOW + H))
    fresh = Q.classify(p, 120, 1800, False, None)
    assert fresh["five"]["state"] == "fresh" and fresh["five"]["source"] == "feed" and fresh["five"]["used"] == 40.0
    assert Q.classify(p, 5, 1800, True, True)["five"]["source"] == "refreshed"
    r = Q.classify(p, 100, 1800, True, False)["five"]
    assert r["source"] == "feed" and "refresh did not update the feed" in r["reason"]  # fresh by age, but not by the call
    s = Q.classify(p, 3700, 1800, True, False)["five"]
    assert s["state"] == "stale" and s["source"] == "stale" and s["used"] is None and s["resets_at"] is None
    assert "62 min old" in s["reason"] and "refresh did not update the feed" in s["reason"]
    m = Q.classify(parsed(None), 60, 1800, False, None)
    assert m["five"]["state"] == "missing" and m["five"]["source"] == "missing" and m["week"]["state"] == "fresh"
    u = Q.classify(None, None, 1800, False, None, "boom")
    assert u["five"]["state"] == "unavailable" and u["five"]["reason"] == "refresh failed, boom"


def test_estimate_cap_math_and_clamp():
    good = dict(time=B.iso(NOW - 3600), used=25.0, resets_at=NOW + H, tokens=100, cap=400)
    r = dict(state="stale")
    e = Q.estimate(NOW, r, dict(total=100), good, 5 * H, lambda ts: None)
    assert e["estimated"] and e["est_used"] == 25.0 and e["est_resets_at"] == NOW + H
    assert e["est_basis"]["cap_tokens"] == 400 and e["est_basis"]["cap_age_s"] == 3600 and e["est_basis"]["window_basis"] == "rolling"
    assert Q.estimate(NOW, r, dict(total=900), good, 5 * H, lambda ts: None)["est_used"] == 100.0
    assert Q.estimate(NOW, r, dict(total=1), None, 5 * H, lambda ts: None) == dict(estimated=False, est_note=Q.estimate(NOW, r, dict(total=1), {}, 5 * H, lambda ts: None)["est_note"])
    assert "no cap" in Q.estimate(NOW, r, dict(total=1), None, 5 * H, lambda ts: None)["est_note"]
    assert "no token" in Q.estimate(NOW, r, None, good, 5 * H, lambda ts: None)["est_note"]
    assert Q.estimate(NOW, dict(state="fresh"), dict(total=1), good, 5 * H, lambda ts: None) == dict(estimated=False)
    assert Q.effective(dict(state="stale", estimated=False)) is None


def test_remember_good_needs_a_fresh_reading_with_one_percent_and_tokens():
    good = {}
    fresh = dict(state="fresh", used=20.0, resets_at=NOW + H)
    assert not Q.remember_good(good, "five_hour", NOW, dict(fresh, used=0.5), dict(total=10), NOW - 4 * H, "resets_at")
    assert not Q.remember_good(good, "five_hour", NOW, fresh, dict(total=0), NOW - 4 * H, "resets_at")
    assert not Q.remember_good(good, "five_hour", NOW, dict(fresh, state="stale"), dict(total=10), NOW - 4 * H, "resets_at")
    assert Q.remember_good(good, "five_hour", NOW, fresh, dict(total=50), NOW - 4 * H, "resets_at")
    assert good["five_hour"]["cap"] == 250 and good["five_hour"]["tokens"] == 50 and good["five_hour"]["time"] == B.iso(NOW)


def test_estimate_reset_future_weekly_and_five_hour_roll_forward():
    assert Q.estimate_reset(5 * H, None, NOW, lambda t: None) is None
    assert Q.estimate_reset(5 * H, NOW + 60, NOW, lambda t: None) == NOW + 60
    week = 7 * 86400
    assert Q.estimate_reset(week, NOW - 8 * 86400, NOW, None) == NOW - 8 * 86400 + 2 * week
    first = lambda ts: next((t for t in (NOW - 6 * H, NOW - H) if t >= ts), None)  # a window opened at -6h, the next one at -1h
    assert Q.estimate_reset(5 * H, NOW - 7 * H, NOW, first) == NOW - H + 5 * H
    assert Q.estimate_reset(5 * H, NOW - 1, NOW, lambda t: None) is None  # no use since the reset: no window is running


# ---------- job level ----------
def fresh_then_stale(tmp_path, stale_q=None, age=5000.0, cls=NoUpdate, lines2=(), five_in=1.0):
    """Poll 1: fresh 50 percent, 10 tokens in the window -> cap 20. Poll 2 (+10 min): the feed is stale (or as given)."""
    fq = Moved(quota_of(50, five_in, 50, 100.0))
    ctx = ctx_with(tmp_path, fq, [line(NOW - 3600, "m1")])
    ctx.cfg["quota_history"] = "quota-history.jsonl"
    J.poll(ctx, NOW)
    nq = NoUpdate(stale_q or quota_of(50, five_in, 50, 100.0), age=age)
    ctx.quota = nq
    write(tmp_path / "t/a.jsonl", *lines2, mode="a")
    return ctx, nq


def test_good_reading_is_kept_with_time_and_tokens(tmp_path):
    ctx, _ = fresh_then_stale(tmp_path)
    g = json.loads((ctx.dir / "last-good-quota.json").read_text())["five_hour"]
    assert g["time"] == B.iso(NOW) and g["used"] == 50 and g["tokens"] == 10 and g["cap"] == 20 and g["window_basis"] == "resets_at"
    assert "SECRET" not in json.dumps(g)


def test_stale_feed_is_estimated_from_the_cap_and_drives_the_trigger(tmp_path, caplog):
    # the stale feed still says 50 percent (five_low would fire); the transcripts say 18 tokens of a 20-token cap = 90 percent: no trigger
    ctx, nq = fresh_then_stale(tmp_path, lines2=[line(NOW + 300, "m2", i=8, o=0, cc=0, cr=0)])
    caplog.set_level(logging.INFO, logger="bootfill.poll")
    logging.getLogger("bootfill.poll").propagate = True
    text = J.poll(ctx, NOW + 600)
    w = history(ctx)[-1]["windows"]["five_hour"]
    assert w["source"] == "stale" and w["used_percentage"] is None and w["resets_at"] is None and w["reading_age_s"] == 5000
    assert w["estimated"] is True and w["estimated_percentage"] == 90.0 and w["estimated_resets_at"] == B.iso(NOW + H)
    assert w["estimate_basis"]["cap_tokens"] == 20 and w["estimate_basis"]["window_tokens"] == 18 and w["estimate_basis"]["cap_from"] == B.iso(NOW)
    assert w["window_basis"] == "rolling" and w["window_start"] == B.iso(NOW + 600 - 5 * H) and w["tokens"]["total"] == 18
    assert text.startswith("skip") and "five_low below_threshold" in text and "ESTIMATED 90%" in text
    msg = " ".join(r.getMessage() for r in caplog.records if r.name == "bootfill.poll")
    assert "five_used=~90% est." in msg and "five=stale/83min est. 90%" in msg
    c = [x for x in A.read_jsonl(ctx.contract) if x["kind"] == "poll"][-1]
    assert "five_hour_used" not in c and c["five_hour_est_used"] == 90.0


def test_estimate_below_threshold_lets_the_trigger_fire_and_says_so(tmp_path):
    # poll 1: 3 h to reset (too early, nothing fires). Poll 2, 1.5 h later: 12 of 20 tokens = 60 percent estimated, 1.5 h to reset -> five_low fires
    ctx, nq = fresh_then_stale(tmp_path, lines2=[line(NOW + 300, "m2", i=2, o=0, cc=0, cr=0)], five_in=3.0)
    text = J.poll(ctx, NOW + 1.5 * H)
    assert text.startswith("would release 1 of 5 (five_low)") and "ESTIMATED 60%" in text


def test_missing_five_hour_window_after_reset_is_unknown_not_fresh(tmp_path):
    fq = FeedQuota(parsed(None, (50.0, WEEK)), age=120.0)
    ctx = ctx_with(tmp_path, fq, [line(NOW - 60, "m1")], last=NOW - 60)
    J.poll(ctx, NOW)
    w = history(ctx)[-1]["windows"]["five_hour"]
    assert w["source"] == "missing" and w["used_percentage"] is None and w["window_basis"] == "rolling" and w["tokens"]["messages"] == 1
    assert w["window_start"] == B.iso(NOW - 5 * H) and "estimated" not in w and "no cap" in w["estimate_note"]
    assert history(ctx)[-1]["windows"]["seven_day"]["source"] == "feed"
    assert not (ctx.dir / "last-good-quota.json").exists() or "five_hour" not in json.loads((ctx.dir / "last-good-quota.json").read_text())


def test_no_cap_is_conservative_nothing_fires_and_no_worker_starts(tmp_path):
    ctx, h, w, _, _ = make(tmp_path, [task(i) for i in range(2, 30)], mode="real")
    ctx.quota = NoUpdate(parsed(None, (50.0, WEEK)), age=120.0)
    ctx.scanner = Q.TokenScanner(str(tmp_path / "t/*.jsonl"), (), None)
    write(tmp_path / "t/a.jsonl", line(NOW - 3600, "m1"))
    ctx.excl = {}
    state = dict(queue=[dict(project="p", key="T-2", number=2, title="t", attempt=1, module="", labels=[], paths=[])], running=[], day="x", day_count=0)
    A.write_json(ctx.state_path, state)
    text = J.poll(ctx, NOW)
    assert h.released == [] and w.started == []
    assert text.startswith("skip") and "5-hour window UNKNOWN" in text and "conservative" in text and "no cap known" in text
    assert "five_low unknown" in text
    c = [x for x in A.read_jsonl(ctx.contract) if x["kind"] == "poll"][-1]
    assert not any(k.startswith("five_hour") for k in c) and c["weekly_used"] == 50.0


def test_fully_stale_feed_without_a_cap_is_unknown_for_both_windows(tmp_path):
    ctx = ctx_with(tmp_path, NoUpdate(quota_of(5, 1.0, 5, 100.0), age=9000.0), [line(NOW - 60, "m1")])
    text = J.poll(ctx, NOW)
    r = history(ctx)[-1]["windows"]
    assert r["five_hour"]["source"] == "stale" and r["seven_day"]["source"] == "stale" and r["seven_day"]["window_basis"] == "rolling"
    assert r["seven_day"]["window_start"] == B.iso(NOW - 7 * 86400) and r["five_hour"]["tokens"]["total"] == 10
    assert "weekly window UNKNOWN" in text and "weekly_used" not in str(A.read_jsonl(ctx.contract)[-1])
    assert "refresh did not update the feed" in r["five_hour"]["reason"]


def test_refreshed_only_when_the_feed_really_moved(tmp_path):
    ctx = ctx_with(tmp_path, Moved(quota_of(), age=5.0))
    J.poll(ctx, NOW)
    assert history(ctx)[-1]["windows"]["five_hour"]["source"] == "refreshed"
    ctx2 = ctx_with(tmp_path / "b", NoUpdate(quota_of(), age=100.0))
    J.poll(ctx2, NOW)
    w = history(ctx2)[-1]["windows"]["five_hour"]
    assert w["source"] == "feed" and "refresh did not update the feed" in w["reason"] and w["used_percentage"] == 40


def test_usage_json_carries_the_estimate_label(tmp_path):
    ctx, _ = fresh_then_stale(tmp_path, lines2=[line(NOW + 300, "m2", i=2, o=0, cc=0, cr=0)])
    J.poll(ctx, NOW + 600)
    five = json.loads((tmp_path / "budget" / "usage.json").read_text())["windows"]["five_hour"]
    assert five["estimated"] is True and five["used_percentage"] == 60.0 and five["tokens_cap_estimate"] == 20 and five["tokens_used"] == 12
    assert five["estimate_basis"]["window_basis"] == "rolling" and five["window_basis"] == "rolling"
    import logic as L, datetime as dt
    txt = (tmp_path / "budget" / "usage.json").read_text()
    v = L.usage_view(txt, None, dt.datetime.fromtimestamp(NOW, dt.timezone.utc))
    assert v["status"] == "ok" and v["windows"]["five_hour"]["tokens_cap"] == 20 and v["windows"]["five_hour"]["cap_source"] == "reported"


def test_corrupt_last_good_file_is_ignored(tmp_path):
    ctx = ctx_with(tmp_path, FeedQuota(quota_of(40, 1.0, 50, 100.0)), [line(NOW - 60, "m1")], last=NOW - 60)
    (ctx.dir / "last-good-quota.json").write_text("{not json")
    J.poll(ctx, NOW)
    assert json.loads((ctx.dir / "last-good-quota.json").read_text())["five_hour"]["cap"] == 25


def test_scan_failure_means_no_estimate_and_unknown(tmp_path):
    ctx, _ = fresh_then_stale(tmp_path)

    class Boom:
        def scan(self, k):
            raise ValueError("x")
    ctx.scanner = Boom()
    text = J.poll(ctx, NOW + 600)
    assert "UNKNOWN" in text and "no token count" in text


# ---------- the adapter ----------
def test_quota_adapter_detects_whether_the_refresh_advanced_the_feed(tmp_path):
    feed = tmp_path / "q.json"
    base = dict(five_hour=dict(used_percentage=12, resets_at=NOW + H), seven_day=dict(used_percentage=3, resets_at=NOW + 5 * H))
    feed.write_text(json.dumps(dict(observed_at=NOW - 7200, rate_limits=base)))
    q = A.Quota(str(feed), [_sys.executable, "-c", "pass"])
    q.read()
    assert q.advanced is False and q.last_age > 7000  # age comes from observed_at, not from the file's modification time
    writer = f"import json,time;json.dump(dict(observed_at=time.time(),rate_limits={base!r}),open({str(feed)!r},'w'))"
    q = A.Quota(str(feed), [_sys.executable, "-c", writer])
    q.read()
    assert q.advanced is True and q.last_age < 60
    q = A.Quota(str(feed), None)
    q.read()
    assert q.advanced is False  # no refresh command: nothing was refreshed
    feed.write_text(json.dumps(dict(rate_limits=dict(seven_day=base["seven_day"]))))
    parsed_, _age = A.Quota(str(feed), None).peek()
    assert parsed_["five"] is None


def test_scanner_first_event_at_or_after(tmp_path):
    write(tmp_path / "t/a.jsonl", line(NOW - 100, "a"), line(NOW - 50, "b"))
    sc = Q.TokenScanner(str(tmp_path / "t/*.jsonl"), (), None)
    sc.scan(NOW - 1000)
    assert sc.first_event_at_or_after(NOW - 80) == NOW - 50 and sc.first_event_at_or_after(NOW) is None
