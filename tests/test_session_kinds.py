"""Interactive and headless sessions apart on the Token Usage page (PC-77), on synthetic transcript-usage data."""
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import logic as L

PAGE = (ROOT / "web" / "index.html").read_text()
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
DAY = "2031-05-21T"
HEADLESS_TITLE = "<h2>Headless Worker sessions (before the proxy)</h2>"


def msg(hour, source, kind=None, session="s1", n=1):
    rec = dict(ts=f"{DAY}{hour:02d}:00:00+00:00", source=source, session=session, message=f"{source}-{hour}", model=None,
               input_tokens=n, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    if kind:
        rec["kind"] = kind
    return json.dumps(rec)


def report(*lines):
    return L.transcript_report("\n".join(lines))


def hour_total(rows, hour):
    return next((r["input_tokens"] for r in rows if r["level"] == "hour" and r["key"] == f"{DAY}{hour:02d}:00"), 0)


def test_a_record_without_a_kind_follows_its_label():
    assert L.record_kind(dict(source="proj-a")) == "interactive"
    assert L.record_kind(dict(source="headless-worker")) == "headless"
    assert L.record_kind(dict(source="headless-worker", kind="interactive")) == "interactive"
    assert L.record_kind(dict(source="proj-a", kind="headless")) == "headless"
    r = report(msg(1, "proj-a", n=2), msg(2, "headless-worker", n=5))
    assert [s["key"] for s in r["sources"]] == ["proj-a"] and [s["source"] for s in r["sessions"]] == ["proj-a"]
    assert hour_total(r["periods"], 1) == 2 and hour_total(r["periods"], 2) == 0
    assert hour_total(r["headless"], 2) == 5 and hour_total(r["headless"], 1) == 0


def test_mixed_kinds_in_one_file():
    r = report(msg(1, "a", "interactive", n=1), msg(2, "b", "headless", n=10), msg(3, "headless-x", n=100), msg(4, "c", n=1000))
    assert {s["key"] for s in r["sources"]} == {"a", "c"} and sum(s["total"] for s in r["sources"]) == 1001
    assert sum(p["input_tokens"] for p in r["periods"] if p["level"] == "hour") == 1001
    assert sum(p["input_tokens"] for p in r["headless"] if p["level"] == "hour") == 110
    assert {p["level"] for p in r["headless"]} >= {"hour", "day"}


def test_no_headless_records_give_no_headless_rows():
    assert report(msg(1, "a"), msg(2, "b", "interactive"))["headless"] == []


# --- the page ---

def render(sessions):
    fn = re.search(r"^function recentRows\(.*$", PAGE, re.M).group(0) + PAGE[PAGE.index("function sessionCards(T)"):PAGE.index("function projectsCard()")]
    esc = re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0)
    usef = re.search(r"^const USEF=.*$", PAGE, re.M).group(0)
    r = subprocess.run(["node", "-e", esc + usef + fn + "process.stdout.write(sessionCards(" + json.dumps(sessions) + "))"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def titles(html):
    return re.findall(r"<h2>(.*?)</h2>", html)


@node
def test_titles_and_the_headless_card_with_the_same_columns_and_levels():
    html = render(report(msg(1, "a"), msg(2, "b"), msg(3, "headless-worker"), msg(4, "w", "headless")))
    assert titles(html) == ["Interactive sessions history", "Headless Worker sessions (before the proxy)", "Interactive sessions per source",
                            "Interactive sessions with the most tokens"]
    cards = html.split('<div class="card">')[1:]
    head = lambda c: re.search(r"<thead>(.*?)</thead>", c).group(1)
    assert head(cards[0]) == head(cards[1])
    rows = re.findall(r'<tr class="lv-(\w+)"><td[^>]*>.*?</td><td>(\w+)</td>', cards[1])
    assert rows and all(c == p for c, p in rows) and {"hour", "day"} <= {c for c, _ in rows}
    assert "headless" not in cards[2] and "headless" not in cards[3]


@node
def test_no_headless_card_without_headless_records():
    html = render(report(msg(1, "a"), msg(2, "b", "interactive")))
    assert HEADLESS_TITLE not in html and "<h2>Interactive sessions per source</h2>" in html


@node
def test_a_single_interactive_source_has_no_source_card_but_keeps_the_session_card():
    html = render(report(msg(1, "a"), msg(2, "a", session="s2"), msg(3, "headless-worker")))
    assert titles(html) == ["Interactive sessions history", "Headless Worker sessions (before the proxy)", "Interactive sessions with the most tokens"]
