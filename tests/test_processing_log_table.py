"""PC-120: the processing log as a table. Synthetic log lines (no consuming project's data): consecutive idle heartbeats collapse into
one row, the container's JSON becomes sizes and never shows raw, event rows are kept, the page's toggle and filters choose the rows,
unreadable lines are "other" with their text, never an exception. The page part runs the page's own script in node (skipped without node)."""
import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import logic as L  # noqa: E402

STATS = ('[{"blockReadBytes":1024,"memoryUsageBytes":1288490188,"memoryLimitBytes":4294967296,"networkRxBytes":5,'
         '"numProcesses":37,"cpuUsageUsec":99}]')


def at(minute, hour=6):
    return "2026-11-25T%02d:%02d:00+02:00" % (hour, minute)


def hb(t, running="none", did="nothing to do (no released task)", stats=STATS):
    line = (f"{t[:10]} {t[11:19]} | running: {running} | {did} | memory pressure normal | free disk 120.3 GiB | load 1.20 1.00 0.90"
            f" | power AC | container data 512 MiB | proxy off | container {stats}")
    return json.dumps(dict(time=t, heartbeat=line))


def ev(t, task, state, **kw):
    return json.dumps(dict(time=t, task=task, folder="/area/" + task, state=state, **kw))


def rows(*lines):
    return L.log_rows("\n".join(lines))


# ---------- the container's readings ----------
def test_container_json_becomes_sizes_and_a_process_count():
    assert L.container_reading(STATS) == "1.2 GiB of 4 GiB, 37 processes"
    assert L.container_reading('[{"memoryUsageBytes":536870912,"numProcesses":1}]') == "512 MiB, 1 process"


def test_container_json_cut_short_still_gives_what_it_holds_and_never_the_raw_text():
    assert L.container_reading(STATS[:STATS.index("numProcesses")]) == "1.2 GiB of 4 GiB"
    for bad in ('[{"blockReadBytes":1', "{}", "[{", '{"memoryUsageBytes":"x"}'):
        out = L.container_reading(bad)
        assert out == "readings unreadable" and "{" not in out
    assert L.container_reading("not reported") == "not reported" and L.container_reading(None) == "not reported"


def test_heartbeat_parts():
    h = L.parse_heartbeat(json.loads(hb(at(30), "worker1=pc-3, worker2=pc-5", "started worker2 on pc-5"))["heartbeat"])
    assert h["running"] == [("worker1", "pc-3"), ("worker2", "pc-5")] and h["what"] == "started worker2 on pc-5"
    assert h["readings"] == ("running worker1, worker2 · memory pressure normal · free disk 120.3 GiB · load 1.20 1.00 0.90 · power AC"
                             " · proxy off · container 1.2 GiB of 4 GiB, 37 processes")
    assert "memoryUsageBytes" not in h["readings"]


# ---------- the rows ----------
def test_consecutive_idle_heartbeats_collapse_into_one_row_with_the_newest_readings():
    r = rows(*[hb(at(m)) for m in range(30, 55, 2)])
    assert len(r) == 1
    g = r[0]
    assert (g["kind"], g["what"], g["count"], g["time"], g["first"], g["event"]) == ("heartbeat", "nothing to do", 13, at(54), at(30), False)
    assert "container 1.2 GiB of 4 GiB, 37 processes" in g["detail"] and "{" not in g["detail"]
    assert g["lines"][0] == hb(at(54)) and len(g["lines"]) == 13


def test_a_group_keeps_at_most_its_newest_original_lines():
    g = rows(*[hb(at(m % 60, 6 + m // 60)) for m in range(L.GROUP_LINES + 10)])[0]
    assert g["count"] == L.GROUP_LINES + 10 and len(g["lines"]) == L.GROUP_LINES


def test_an_event_breaks_a_run_and_heartbeats_with_a_worker_are_their_own_rows():
    r = rows(hb(at(30)), hb(at(32)), ev(at(33), "pc-3", "launched", worker="worker1"), hb(at(34), "worker1=pc-3", "nothing to do (all Workers busy)"),
             hb(at(36), "worker1=pc-3", "nothing to do (all Workers busy)"), ev(at(37), "pc-3", "unmerged"), hb(at(38)), hb(at(40)))
    assert [(x["kind"], x["count"]) for x in r] == [("heartbeat", 2), ("unmerged", 1), ("heartbeat", 1), ("heartbeat", 1), ("launched", 1), ("heartbeat", 2)]
    assert (r[2]["task"], r[2]["worker"]) == ("pc-3", "worker1")
    assert r[4]["worker"] == "worker1" and r[4]["event"] and not r[4]["alert"]


def test_event_rows_are_kept_with_reasons_and_failures_and_reconciliations_highlighted():
    r = rows(ev(at(1), "pc-1", "wip"), ev(at(2), "pc-1", "failed", reason="no note"), ev(at(3), "-", "proxy started"),
             ev(at(4), "pc-2", "wip with no Worker -> unmerged"), ev(at(5), "pc-4", "merged"),
             json.dumps(dict(time=at(6), event="sleep", start=1, end=2, minutes=42)))
    got = [(x["kind"], x["task"], x["detail"], x["alert"], x["event"]) for x in r]
    assert got == [("sleep", "", "the host slept or the Dispatcher was down for 42 minutes", False, True),
                   ("merged", "pc-4", "", False, True),
                   ("unmerged", "pc-2", "wip with no Worker -> unmerged", True, True),
                   ("other", "", "proxy started", False, True),
                   ("failed", "pc-1", "no note", True, True),
                   ("wip", "pc-1", "", False, True)]


def test_a_skip_worth_seeing_is_an_event_the_interval_skip_is_not():
    r = rows(hb(at(1), did="skipped: egress check failed for worker1"), hb(at(2), did="skipped: interval"))
    assert [(x["event"], x["alert"]) for x in r] == [(False, False), (True, True)]


@pytest.mark.parametrize("line", ["{broken", "[1, 2]", '"text"', "null", '{"time": 5}', '{"heartbeat": 7}', '{"state": ["x"], "task": 3}',
                                  '{"time": "soon", "event": "sleep"}', "\x00�"])
def test_unreadable_lines_are_other_with_their_text_never_an_exception(line):
    r = rows(hb(at(1)), line)
    assert len(r) == 2 and r[0]["lines"] == [line]
    if r[0]["kind"] == "other":
        assert r[0]["detail"] and r[0]["event"]


def test_unreadable_line_shows_its_text():
    assert [(x["kind"], x["detail"], x["time"]) for x in rows("{broken")] == [("other", "{broken", None)]


def test_each_class_keeps_up_to_the_limit_newest_first():
    lines = []
    for m in range(30):
        lines += [ev(at(m), "pc-%d" % m, "wip"), hb(at(m), "worker1=pc-1")]
    r = L.log_rows("\n".join(lines), limit=5)
    assert [x["task"] for x in r if x["event"]] == ["pc-29", "pc-28", "pc-27", "pc-26", "pc-25"]
    assert sum(not x["event"] for x in r) == 5
    assert L.log_rows(None) == [] and L.log_rows("") == []


def test_the_queue_serves_the_rows():
    q = L.queue_local(None, "\n".join([hb(at(1)), hb(at(2)), ev(at(3), "pc-1", "launched", worker="worker1")]))
    assert [x["kind"] for x in q["log"]] == ["launched", "heartbeat"] and q["log"][1]["count"] == 2


# ---------- the page (node) ----------
PAGE = (ROOT / "web" / "index.html").read_text()
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def test_the_page_draws_the_log_as_a_table_with_the_five_columns():
    assert "<th>Time</th><th>Kind</th><th>Task</th><th>Worker</th><th>Detail</th>" in PAGE and "QLOG_LIMITS=[15,50,200]" in PAGE
    assert "block('Processing log',()=>vQlog(L.log))" in PAGE


@pytest.fixture
def run_node(monkeypatch):
    """The page's own script with a fake browser (tests/test_ui_actions_page.py), in the zone UTC+02:00 of the synthetic times."""
    import test_ui_actions_page as P
    monkeypatch.setenv("TZ", "Etc/GMT-2")
    return P.run


def page_rows():
    return json.dumps(rows(hb(at(30)), hb(at(32)), ev(at(33), "pc-3", "launched", worker="worker1"), hb(at(34), "worker1=pc-3", "nothing to do (all Workers busy)"),
                           ev(at(35), "pc-2", "failed", reason="no note"), "{broken"))


@node
def test_page_events_by_default_the_toggle_and_the_filters(run_node):
    out = run_node("const R=%s,F=(o)=>qlogPick(R,Object.assign({beats:false,task:'',worker:'',n:15},o)).map(r=>r.kind);"
                   "return [F({}),F({beats:true}),F({beats:true,task:'pc-3'}),F({beats:true,worker:'worker1'}),F({task:'pc-9'}),F({beats:true,n:2})];" % page_rows())
    assert out == [["other", "failed", "launched"], ["other", "failed", "heartbeat", "launched", "heartbeat"], ["heartbeat", "launched"],
                   ["heartbeat", "launched"], [], ["other", "failed"]]


@node
def test_page_table_has_date_headings_clock_times_tags_highlights_and_no_raw_json(run_node):
    h = run_node("UI.qlog.beats=true;return vQlog(%s);" % page_rows())
    assert '<tr class="qday"><td colspan="5">2026-11-25</td></tr>' in h and "Unknown date" in h
    assert ">06:35:00</span>" in h and 'class="chip qk-failed">failed' in h and 'class="qrow qalert"' in h
    assert "nothing to do, 2 heartbeats, " in h and "1.2 GiB of 4 GiB, 37 processes" in h and "memoryUsageBytes" not in h


@node
@pytest.mark.ui_action("page:qlogbeats", "page:qlogrow", "form:qlog")
def test_page_toggle_expand_and_filter_form():
    from test_ui_actions_page import quiet, run
    out = quiet(run("await __click({act:'qlogbeats'});const a=UI.qlog.beats;await __click({act:'qlogrow',k:'x'});const b=!!UI.qlogRows.x;"
                    "await __click({act:'qlogrow',k:'x'});await __submit('qlog',null,{task:'pc-3',worker:'',n:'50'});const c=UI.qlog;"
                    "await __submit('qlog',null,{task:'',worker:'worker1',n:'999'});"
                    "return Object.assign(__out(),{v:[a,b,!!UI.qlogRows.x,c.task,c.n,UI.qlog.worker,UI.qlog.n]});"))
    assert out["v"] == [True, True, False, "pc-3", 50, "worker1", 15]


@node
def test_page_expanded_row_shows_the_original_line(run_node):
    h = run_node("const R=%s;UI.qlogRows[qlogKey(R[1])]=true;return vQlog(R);" % page_rows())
    assert '<tr class="qfull"><td colspan="5"><pre class="mono">' in h and "no note" in h and 'aria-expanded="true"' in h
