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
    assert [x["key"] for x in r["days"]] == ["2026-11-20", "2026-11-21"]
    assert r["days"][0]["total"] == 30 and r["days"][0]["input_tokens"] == 3
    assert [x["key"] for x in r["windows"]] == ["2026-11-19 21:00", "2026-11-20 02:00", "2026-11-20 22:00"]
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
    assert r == dict(days=None, windows=None, workers=None, tasks=None)


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
