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


def test_report_groups_by_day_window_and_worker_and_skips_bad_lines():
    r = L.usage_report(USAGE, PLOG, lambda f: None)
    assert len(r["days"]) == 90 and r["days"][-1]["key"] == "2026-11-21" and r["days"][0]["key"] == "2026-08-24"
    d = {x["key"]: x for x in r["days"]}
    assert d["2026-11-20"]["total"] == 30 and d["2026-11-20"]["input_tokens"] == 3 and d["2026-11-19"]["total"] == 0
    assert [x["key"] for x in r["windows"]] == ["2026-11-19 21:00", "2026-11-20 02:00", "2026-11-20 22:00"]
    assert {x["kind"] for x in r["windows"]} == {"fixed"} and r["quota"] is None
    assert r["windows"][0]["total"] == 10 and r["windows"][1]["total"] == 20
    assert {x["key"]: x["total"] for x in r["workers"]} == {"w1": 30, "w2": 10}


def test_tasks_compare_proxy_sum_with_goodbye_tokens():
    r = L.usage_report(USAGE, PLOG, lambda f: 25 if f.endswith("T-1") else None)
    t1, t2 = r["tasks"]
    assert (t1["proxy"], t1["note"], t1["diff"]) == (30, 25, 5)
    assert (t2["proxy"], t2["note"], t2["diff"]) == (0, None, None)
    assert not t1["overlap"] and not t2["overlap"]


def test_missing_files_are_no_data():
    r = L.usage_report(None, None, lambda f: None)
    assert r == dict(days=None, windows=None, workers=None, tasks=None, quota=None)


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


def test_all_projects_table_sums_each_project(tmp_path):
    for n, ts in (("one", "2026-11-20T01:00:00+00:00"), ("two", "2026-11-22T01:00:00+00:00")):
        (tmp_path / n).mkdir()
        (tmp_path / n / "proxy-usage.jsonl").write_text(u(ts, "w1") + "\n" + u(ts, "w2"))
    (tmp_path / "one" / "processing").mkdir()
    (tmp_path / "one" / "processing" / "processing.jsonl").write_text(p("2026-11-20T01:00:00+00:00", "T-1", "launched"))
    projs = {n: dict(name=n, repo="o/" + n) for n in ("one", "two", "none")}
    r = C.get_usage(projs["one"], data_dir=tmp_path, projects=projs)
    assert r["summary"]["total"] == 20 and r["summary"]["tasks"] == 1 and r["summary"]["last_day"] == "2026-11-20"
    rows = {x["name"]: x for x in r["projects"]}
    assert rows["two"]["total"] == 20 and rows["two"]["tasks"] == 0 and rows["two"]["last_day"] == "2026-11-22"
    assert rows["none"] == dict(name="none")


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
        ] + [("hour", "2026-11-25T%02d:00" % h) for h in range(9)]  # every hour of the day up to the newest reading's, none still to come
    assert ps[0]["readings"] == 2 and ps[0]["five_hour"] == dict(first=11, last=12, low=11, high=12)
    hr = {p["key"]: p for p in ps if p["level"] == "hour"}
    assert hr["2026-11-25T05:00"]["readings"] == 2 and hr["2026-11-25T05:00"]["five_hour"]["first"] == 18 and hr["2026-11-25T05:00"]["five_hour"]["last"] == 19
    assert hr["2026-11-25T03:00"]["readings"] == 0 and hr["2026-11-25T03:00"]["five_hour"] == dict(first=17, last=17, low=17, high=17)  # carried from the day before
    assert hr["2026-11-25T06:00"]["five_hour"]["last"] == 19 and hr["2026-11-25T08:00"]["readings"] == 1
    assert "2026-11-25T09:00" not in hr


def test_week_crossing_a_month_start_has_no_week_row():
    end = int(L._epoch("2026-12-01T08:30:00+00:00"))
    qt = "\n".join([q("2026-11-30T10:00:00+00:00", 10, 30, end), q("2026-12-02T10:00:00+00:00", 20, 31, end)])
    ps = L._quota(qt, None)[1]["periods"]
    assert [(p["level"], p["key"]) for p in ps if p["level"] != "hour"] == [("day", "2026-11-30")]
    assert [p["key"][-5:] for p in ps if p["level"] == "hour"] == ["%02d:00" % h for h in range(11)]
