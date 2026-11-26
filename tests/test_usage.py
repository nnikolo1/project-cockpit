import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import json
import cockpit as C
import logic as L


def u(ts, worker, i=1, o=2, cw=3, cr=4):
    return json.dumps(dict(ts=ts, worker=worker, input_tokens=i, output_tokens=o, cache_creation_input_tokens=cw, cache_read_input_tokens=cr))


def p(time, task, state):
    return json.dumps(dict(time=time, task=task, folder=f"/area/{task}", state=state))


USAGE = "\n".join([u("2026-11-20T01:00:00+00:00", "w1"), u("2026-11-20T04:59:00+00:00", "w2"), u("2026-11-20T05:00:00+00:00", "w1"), "garbage",
                   u("2026-11-21T00:10:00+00:00", "w1")])
PLOG = "\n".join([p("2026-11-20T00:50:00+00:00", "T-1", "launched"), p("2026-11-20T05:00:00+00:00", "T-1", "done"), "{broken",
                  p("2026-11-21T00:00:00+00:00", "T-2", "launched")])


def test_report_groups_by_day_and_window_and_skips_bad_lines():
    r = L.usage_report(USAGE, PLOG, lambda f: None)
    assert [x["key"] for x in r["days"]] == ["2026-11-21", "2026-11-20"]
    d = {x["key"]: x for x in r["days"]}
    assert d["2026-11-20"]["total"] == 30 and d["2026-11-20"]["input_tokens"] == 3
    assert [x["key"] for x in r["windows"]] == ["2026-11-20 22:00", "2026-11-20 02:00", "2026-11-19 21:00"]
    assert {x["kind"] for x in r["windows"]} == {"fixed"} and r["quota"] is None
    assert r["windows"][2]["total"] == 10 and r["windows"][1]["total"] == 20
    assert "workers" not in r  # no per-Worker part in the payload (PC-83)


def test_tasks_compare_proxy_sum_with_goodbye_tokens():
    r = L.usage_report(USAGE, PLOG, lambda f: 25 if f.endswith("T-1") else None)
    t1, t2 = r["tasks"]
    assert (t1["proxy"], t1["note"], t1["diff"]) == (30, 25, 5)
    assert (t2["proxy"], t2["note"], t2["diff"]) == (0, None, None)
    assert not t1["overlap"] and not t2["overlap"]


def test_missing_files_are_no_data():
    r = L.usage_report(None, None, lambda f: None)
    assert r == dict(days=None, windows=None, tasks=None, quota=None)


def test_overlapping_tasks_are_marked():
    log = "\n".join([p("2026-11-20T01:00:00+00:00", "A", "launched"), p("2026-11-20T03:00:00+00:00", "B", "launched"),
                     p("2026-11-20T04:00:00+00:00", "A", "done")])
    assert all(t["overlap"] for t in L.usage_report(USAGE, log, lambda f: None)["tasks"])


def test_get_usage_reads_the_project_folder(tmp_path):
    d = tmp_path / "proj"
    (d / "processing").mkdir(parents=True)
    (d / "proxy-usage.jsonl").write_text(USAGE)
    (d / "processing" / "processing.jsonl").write_text(PLOG)
    (d / "area" / "T-1" / "outbox").mkdir(parents=True)
    (d / "area" / "T-1" / "outbox" / "goodbye.json").write_text(json.dumps(dict(tokens=25)))
    (d / "area" / "T-2" / "outbox").mkdir(parents=True)
    (d / "area" / "T-2" / "outbox" / "goodbye.json").write_text("not json")
    r = C.get_usage(dict(name="proj", repo="o/r"), data_dir=tmp_path)
    assert [t["note"] for t in r["tasks"]] == [25, None]
    assert C.get_usage(dict(name="none", repo="o/r"), data_dir=tmp_path)["days"] is None


def test_task_with_a_worker_name_counts_only_that_worker_and_is_not_overlap():
    log = "\n".join([json.dumps(dict(time="2026-11-20T00:50:00+00:00", task="A", folder="/a/A", state="launched", worker="w1")),
                     p("2026-11-20T01:00:00+00:00", "B", "launched"), p("2026-11-20T05:00:00+00:00", "A", "done")])
    a, b = L.usage_report(USAGE, log, lambda f: None)["tasks"]
    assert a["worker"] == "w1" and a["proxy"] == 20 and a["overlap"] is False   # w1: 01:00 and 05:00, not w2's 04:59
    assert b["worker"] is None and b["overlap"] is True


def q(ts, five, week, resets):
    return json.dumps({"ts": ts, "five_hour": {"used_percentage": five, "resets_at": resets}, "seven_day": {"used_percentage": week, "resets_at": 0}, "source": "t"})


def test_quota_history_gives_vendor_windows_and_daily_maxima():
    # vendor window 2026-11-20 03:30-08:30 UTC; requests at 04:59 and 05:00 fall in it, 01:00 does not (fixed block)
    end = int(L._epoch("2026-11-20T08:30:00+00:00"))
    qt = "\n".join([q("2026-11-20T04:00:00+00:00", 10, 30, end), q("2026-11-20T06:00:00+00:00", 40, 35, end + 3), "bad",
                     q("2026-11-21T00:30:00+00:00", 5, 36, end + 18000)])
    r = L.usage_report(USAGE, PLOG, lambda f: None, qt)
    assert {(x["key"], x["kind"]): x["total"] for x in r["windows"]}[("2026-11-20 03:30", "vendor")] == 20
    assert {x["kind"] for x in r["windows"]} == {"fixed", "vendor"}
    d = {x["key"]: x for x in r["quota"]["days"]}
    assert len(r["quota"]["days"]) == 90 and d["2026-11-20"]["five_hour"] == 40 and d["2026-11-20"]["seven_day"] == 35
    assert d["2026-11-19"]["five_hour"] is None
    h = {x["key"]: x for x in r["quota"]["periods"]}
    assert list(h) == ["2026-11-20", "2026-11-21T00:00"]  # the last reading is the anchor: Friday 21st 00:30; the 20th is an earlier day of its week
    assert h["2026-11-20"]["five_hour"] == dict(first=10, last=40, low=10, high=40) and h["2026-11-20"]["readings"] == 2
    assert h["2026-11-21T00:00"]["reset5"] == "2026-11-20T13:30+00:00"


def test_get_usage_reads_the_quota_file(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "proxy-usage.jsonl").write_text(USAGE)
    assert C.get_usage(dict(name="proj", repo="o/r"), data_dir=tmp_path)["quota"] is None
    (d / "quota-history.jsonl").write_text(q("2026-11-21T00:30:00+00:00", 5, 36, 1))
    assert C.get_usage(dict(name="proj", repo="o/r"), data_dir=tmp_path)["quota"]["periods"][0]["seven_day"]["last"] == 36


def test_per_project_monthly_table_reads_every_data_folder(tmp_path):
    for n, ts in (("one", "2026-11-20T01:00:00+00:00"), ("two", "2026-10-22T01:00:00+00:00")):
        (tmp_path / n).mkdir()
        (tmp_path / n / "proxy-usage.jsonl").write_text(u(ts, "w1") + "\n" + u(ts, "w2"))
    (tmp_path / "one" / "processing").mkdir()
    (tmp_path / "one" / "processing" / "processing.jsonl").write_text(p("2026-11-20T01:00:00+00:00", "T-1", "launched"))
    tr = json.dumps(dict(ts="2026-11-21T01:00:00+00:00", source="-home-x-src-two", session="s", agent=None, message="m", input_tokens=7))
    (tmp_path / "one" / "transcript-usage.jsonl").write_text(tr)
    (tmp_path / "two" / "transcript-usage.jsonl").write_text(tr)   # the same message in two folders counts once
    projs = {n: dict(name=n, repo="o/" + n) for n in ("one", "two", "none")}
    projs["two"]["git"] = "/home/x/src/two"
    projs["none"]["start_month"] = "2026-11"
    r = C.get_usage(projs["one"], data_dir=tmp_path, projects=projs, today=L.dt.date(2026, 11, 25))
    m = r["monthly"]
    assert m["months"] == ["2026-11", "2026-10", "2026-09"]
    rows = {x["name"]: {y["month"]: y for y in x["rows"]} for x in m["projects"]}
    assert rows["one"]["2026-11"]["unattended"] == 20 and rows["one"]["2026-11"]["tasks"] == 1 and rows["one"]["2026-11"]["interactive"] == 0
    assert rows["two"]["2026-11"]["interactive"] == 7 and rows["two"]["2026-10"]["unattended"] == 20 and rows["two"]["2026-10"]["last_day"] == "2026-10-22"
    assert rows["none"]["2026-11"]["total"] == 0 and rows["none"]["2026-10"]["state"] == "before_start"


def test_periods_are_graded_hour_day_week_month():
    end = int(L._epoch("2026-11-25T08:30:00+00:00"))
    at = ["2026-08-31T10:00:00+00:00",   # older than the two months before the current one (September, October): dropped
          "2026-10-05T10:00:00+00:00", "2026-10-28T10:00:00+00:00",   # October: one month row (September has none)
          "2026-11-03T10:00:00+00:00", "2026-11-04T10:00:00+00:00",   # week of 2026-11-02 (Monday), earlier week of November
          "2026-11-09T10:00:00+00:00",                                # week of 2026-11-09
          "2026-11-23T10:00:00+00:00", "2026-11-24T09:00:00+00:00",   # Monday and Tuesday of the current week: day rows
                    "2026-11-25T05:10:00+00:00", "2026-11-25T05:40:00+00:00", "2026-11-25T08:20:00+00:00"]  # hour rows: the current day (Wednesday 25th) per hour
    qt = "\n".join(q(ts, 10 + i, 30, end) for i, ts in enumerate(at))
    ps = L._quota(qt, None)[1]["periods"]
    assert [(p["level"], p["key"]) for p in ps] == [
        ("month", "2026-10"), ("week", "2026-11-02"), ("week", "2026-11-09"), ("day", "2026-11-23"), ("day", "2026-11-24"),
        ("hour", "2026-11-25T05:00"), ("hour", "2026-11-25T08:00")]  # only the hours of the current day that have readings
    assert ps[0]["readings"] == 2 and ps[0]["five_hour"] == dict(first=11, last=12, low=11, high=12)
    hr = {p["key"]: p for p in ps if p["level"] == "hour"}
    assert hr["2026-11-25T05:00"]["readings"] == 2 and hr["2026-11-25T05:00"]["five_hour"]["first"] == 18 and hr["2026-11-25T05:00"]["five_hour"]["last"] == 19
    assert hr["2026-11-25T08:00"]["readings"] == 1 and hr["2026-11-25T08:00"]["five_hour"] == dict(first=20, last=20, low=20, high=20)
    assert all(p["readings"] > 0 for p in ps)  # an hour without readings has no row, nothing is carried forward into it


def test_week_crossing_a_month_start_has_no_week_row():
    end = int(L._epoch("2026-12-01T08:30:00+00:00"))
    qt = "\n".join([q("2026-11-30T10:00:00+00:00", 10, 30, end), q("2026-12-02T10:00:00+00:00", 20, 31, end)])
    ps = L._quota(qt, None)[1]["periods"]
    assert [(p["level"], p["key"]) for p in ps if p["level"] != "hour"] == [("day", "2026-11-30")]
    assert [p["key"] for p in ps if p["level"] == "hour"] == ["2026-12-02T10:00"]


def m(ts, session, source="src-a", i=1, o=2, cw=3, cr=4):
    return json.dumps(dict(ts=ts, source=source, session=session, message="x", model=None, input_tokens=i, output_tokens=o,
                           cache_creation_input_tokens=cw, cache_read_input_tokens=cr))


QUOTA = "\n".join(json.dumps(dict(ts=t, five_hour=dict(used_percentage=a, resets_at=1763650000), seven_day=dict(used_percentage=b, resets_at=1764000000)))
                  for t, a, b in [("2026-11-20T10:00:00+00:00", 10, 20), ("2026-11-20T10:30:00+00:00", 40, 22), ("2026-11-20T12:10:00+00:00", 5, 25)])


def test_transcript_missing_file_is_no_data():
    assert L.transcript_report(None) is None
    assert L.transcript_report("") == dict(periods=[], headless=[], sources=[], sessions=[], aggregate_days={}, transcript_days={}, transcript_span=None)


def test_transcript_periods_follow_the_quota_grading_with_peaks():
    quota = L.usage_report(None, None, lambda f: None, QUOTA)["quota"]
    text = "\n".join([m("2026-11-20T10:05:00+00:00", "s1"), m("2026-11-20T10:50:00+00:00", "s1"), m("2026-11-19T08:00:00+00:00", "s2", source="src-b"), "bad"])
    r = L.transcript_report(text, quota)
    rows = {(p["level"], p["key"]): p for p in r["periods"]}
    h10 = rows[("hour", "2026-11-20T10:00")]
    assert (h10["messages"], h10["input_tokens"], h10["total"], h10["five_hour"], h10["seven_day"]) == (2, 2, 20, 40, 22)
    assert ("hour", "2026-11-20T12:00") not in rows and ("hour", "2026-11-20T13:00") not in rows   # an hour without messages has no row
    assert rows[("day", "2026-11-19")]["total"] == 10 and rows[("day", "2026-11-19")]["five_hour"] is None
    assert [p["label"] for p in r["periods"]][-1] == "2026-11-20 10:00"


def test_transcript_hours_without_readings_keep_the_held_peak():
    """The quota rows list only the hours with readings; the sessions card still has a row for every hour of the current day and shows
    the last value before an empty hour (unchanged by the 'Today's readings' change)."""
    quota = L.usage_report(None, None, lambda f: None, QUOTA)["quota"]
    assert [p["key"] for p in quota["periods"]] == ["2026-11-20T10:00", "2026-11-20T12:00"]
    rows = {(p["level"], p["key"]): p for p in L.transcript_report(m("2026-11-20T11:05:00+00:00", "s1"), quota)["periods"]}
    assert (rows[("hour", "2026-11-20T11:00")]["messages"], rows[("hour", "2026-11-20T11:00")]["five_hour"], rows[("hour", "2026-11-20T11:00")]["seven_day"]) == (1, 40, 22)
    assert ("hour", "2026-11-20T09:00") not in rows and ("hour", "2026-11-20T12:00") not in rows  # hours without messages are not listed (PC-76)


def test_held_peaks_start_from_the_newest_earlier_period():
    periods = [dict(level="day", key="2026-11-19", five_hour=dict(first=3, last=7, low=3, high=9), seven_day={}),
               dict(level="hour", key="2026-11-20T02:00", five_hour=dict(first=8, last=8, low=8, high=8), seven_day=dict(first=1, last=1, low=1, high=1))]
    h = L._held_peaks(periods)
    assert h[("hour", "2026-11-20T00:00")] == dict(five_hour=7, seven_day=None) and h[("hour", "2026-11-20T02:00")] == dict(five_hour=8, seven_day=1)
    assert h[("day", "2026-11-19")] == dict(five_hour=9, seven_day=None) and ("hour", "2026-11-20T03:00") not in h


def test_transcript_sources_and_sessions_rank_by_total_and_cap():
    lines = [m("2026-11-20T10:00:00+00:00", "s1"), m("2026-11-20T10:01:00+00:00", "s1", source="src-b", i=1000)]
    lines += [m("2026-11-20T11:00:00+00:00", f"n{i}", source="src-c") for i in range(25)] + [m("2026-05-01T00:00:00+00:00", "old", source="src-old")]
    r = L.transcript_report("\n".join(lines))
    assert r["sources"][0]["key"] == "src-b" and r["sources"][0]["total"] == 1009 and "src-old" not in {s["key"] for s in r["sources"]}
    assert len(r["sessions"]) == 20 and r["sessions"][0]["key"] == "s1" and r["sessions"][0]["source"] == "src-b"
    assert r["sessions"][0]["messages"] == 1


def test_get_usage_reads_the_transcript_file(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "transcript-usage.jsonl").write_text(m("2026-11-20T10:00:00+00:00", "s1"))
    p = dict(name="proj", repo="o/proj")
    assert C.get_usage(p, tmp_path, {"proj": p})["sessions"]["sources"][0]["total"] == 10
    assert C.get_usage(p, tmp_path / "none", {"proj": p})["sessions"] is None


def a(day, tokens, model="m-1"):
    return json.dumps(dict(day=day, model=model, tokens=tokens, messages=4, sessions=1, source="stats-cache"))


def test_daily_aggregates_count_in_the_period_total_only_and_exact_days_win():
    quota = L.usage_report(None, None, lambda f: None, QUOTA)["quota"]
    text = m("2026-11-19T08:00:00+00:00", "s2")
    agg = "\n".join([a("2026-10-05", 100), a("2026-10-05", 50, "m-2"), a("2026-11-19", 999), a("2026-11-03", None), "bad",
                     json.dumps(dict(day="2026-10-06", tokens=7, source="other"))])
    r = L.transcript_report(text, quota, agg)
    rows = {(p["level"], p["key"]): p for p in r["periods"]}
    oct_ = rows[("month", "2026-10")]
    assert (oct_["total"], oct_["aggregate"], oct_["messages"], oct_["input_tokens"]) == (150, 150, 0, 0)
    assert rows[("day", "2026-11-19")]["total"] == 10 and rows[("day", "2026-11-19")]["aggregate"] == 0   # the exact data wins
    assert r["aggregate_days"] == {"2026-10-05": 150}
    assert [s["total"] for s in r["sources"]] == [10] and [s["total"] for s in r["sessions"]] == [10]


def test_aggregates_alone_are_shown_and_neither_file_is_no_data(tmp_path):
    r = L.transcript_report(None, None, a("2026-10-05", 100))
    assert r["aggregate_days"] == {"2026-10-05": 100} and r["sources"] == [] and sum(p["aggregate"] for p in r["periods"]) == 100
    assert L.transcript_report(None, None, None) is None
    d = tmp_path / "proj"
    d.mkdir()
    (d / "stats-cache-usage.jsonl").write_text(a("2026-10-05", 100))
    p = dict(name="proj", repo="o/proj")
    assert C.get_usage(p, tmp_path, {"proj": p})["sessions"]["aggregate_days"] == {"2026-10-05": 100}
