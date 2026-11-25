"""PC-134: one time convention on the Task Queue page. The server passes an epoch through and gives every ISO time an offset (an old
offset-less processing-log line is the server's local time); the browser shows them all in its zone, "HH:MM" today, else
"YYYY-MM-DD HH:MM", with the UTC value as a tooltip and the zone named once. Tested with a fixed zone offset (UTC+02:00)."""
import datetime as dt
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import logic as L  # noqa: E402

PAGE = (ROOT / "web" / "index.html").read_text()
EPOCH = 1795593541                                   # 2026-11-25T07:59:01Z, the launch of PC-130
NEW, OLD, UTC = "2026-11-25T09:59:01+02:00", "2026-11-25T09:59:01", "2026-11-25T07:59:01Z"


@pytest.fixture
def plus2(monkeypatch):
    """The server machine on a fixed UTC+02:00 (POSIX TZ: the sign is inverted)."""
    monkeypatch.setenv("TZ", "XYZ-2")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def state(launched):
    return json.dumps({"workers": {"worker1": {"task_id": "p-1", "launched": launched, "started": True}}})


def plog(*times):
    return "\n".join(json.dumps(dict(time=t, task="p-1", state="launched")) for t in times)


def test_the_launch_epoch_reaches_the_browser_unconverted():
    assert L.queue_local(state(EPOCH + 0.25), "")["workers"][0]["launched"] == EPOCH + 0.25


def test_unreadable_launch_values_become_none():
    for bad in (None, True, [], {}, 1e30, float("nan"), "soon"):
        assert L.queue_local(state(bad), "")["workers"][0]["launched"] is None


def test_epoch_new_old_and_utc_log_times_are_the_same_instant_with_an_offset(plus2):
    q = L.queue_local(state(EPOCH), plog(OLD, NEW, UTC))
    times = [r["time"] for r in q["log"]]
    assert times[1:] == [NEW, NEW]                     # newest first: NEW, OLD (given the server's offset)
    assert all(re.search(r"[+-]\d\d:\d\d$", t) for t in times)
    assert {dt.datetime.fromisoformat(t).timestamp() for t in times} == {EPOCH}
    assert q["workers"][0]["launched"] == EPOCH


def test_live_events_read_old_and_new_log_lines_alike(plus2):
    for t in (OLD, NEW, UTC):
        assert L.live_events(plog(t), {"p"}) == {1: (EPOCH, "wip")}


# ---------- the page ----------
def body(name):
    i = PAGE.index("function " + name + "(")
    return PAGE[i:PAGE.index("\n}", i)]


def test_the_queue_page_formats_every_time_with_the_shared_function():
    for f in ("vQueueLocal", "ciLine", "bkLine", "paneBar", "drawerTask"):
        b = body(f)
        assert "toLocale" not in b and "toISOString" not in b and ".slice(5)" not in b and "t0(" not in b, f
    assert "launched '+tm(w.launched)" in PAGE and "'+tmSec(r.time)+'" in PAGE
    assert "' started '+tm(c.time)" in PAGE and "tm(x.t)" in PAGE and "'data from '+tm(at)" in PAGE


def test_the_zone_is_named_once_at_the_top_of_the_task_queue():
    assert "(R==='backlog'?'<span class=\"muted\" style=\"font-size:13px\" data-zone>'+zoneNote()+'</span>'" in body("paneBar")
    assert PAGE.count("zoneNote()") == 1


def test_utc_tables_keep_their_label():
    assert PAGE.count("Period (UTC)") >= 2 and "per day (UTC)" in PAGE


node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def run_node(code, tz="Etc/GMT-2"):                   # IANA Etc/GMT-2 is UTC+02:00
    esc = re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0)
    helpers = PAGE[PAGE.index("/* safe-block:start"):PAGE.index("/* safe-block:end */")]
    r = subprocess.run(["node", "-e", esc + helpers + code], capture_output=True, text=True, env={"TZ": tz, "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@node
def test_epoch_offset_time_and_old_offset_less_time_show_the_same_local_clock_time():
    same_day, next_day = (EPOCH + 3600) * 1000, (EPOCH + 86400) * 1000
    out = run_node("console.log(JSON.stringify([%d,'%s','%s','%s'].map(x=>[tmTxt(x,%d),tmTxt(x,%d)])))" % (EPOCH, NEW, OLD, UTC, same_day, next_day))
    assert out == [["09:59", "2026-11-25 09:59"]] * 4


@node
def test_every_time_has_its_utc_value_as_a_tooltip_and_unreadable_values_give_a_question_mark():
    out = run_node("console.log(JSON.stringify([tm(%d,%d),tm('%s',%d)].concat([null,undefined,NaN,1e300,{},[],true,'','soon'].map(x=>tm(x)))))" % (EPOCH, EPOCH * 1000, NEW, EPOCH * 1000))
    assert out[:2] == ['<span class="tm" title="2026-11-25 07:59 UTC">09:59</span>'] * 2
    assert out[2:] == ["?"] * 9


@node
@pytest.mark.parametrize("tz, want", [("Etc/GMT-2", "UTC+02:00"), ("Etc/GMT+5", "UTC-05:00"), ("UTC", "UTC+00:00")])
def test_the_zone_note_names_the_offset(tz, want):
    assert run_node("console.log(JSON.stringify(zoneNote(%d)))" % (EPOCH * 1000), tz) == "times in your local time, " + want
