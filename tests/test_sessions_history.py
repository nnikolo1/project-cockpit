"""The Interactive sessions history card (Token Usage page): rows from transcript_report and their rendering, on synthetic data."""
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
LEVELS = ("hour", "day", "week", "month")
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def msg(ts, session="s1", source="src-a"):
    return json.dumps(dict(ts=ts, source=source, session=session, message="x", model=None, input_tokens=1, output_tokens=2,
                           cache_creation_input_tokens=3, cache_read_input_tokens=4))


def quota(*ts):
    return L.usage_report(None, None, lambda f: None, "\n".join(json.dumps(dict(ts=t, five_hour=dict(used_percentage=10, resets_at=1),
                                                                                  seven_day=dict(used_percentage=20, resets_at=1))) for t in ts))["quota"]


# A synthetic history: the data starts mid-month (first month M), the anchor is two months later, today has messages in two hours only.
FIRST, ANCHOR = "2031-03-17T09:00:00+00:00", "2031-05-21T15:30:00+00:00"   # 2031-05-21 is a Wednesday
TEXT = "\n".join(msg(t) for t in (FIRST, "2031-04-02T10:00:00+00:00", "2031-05-06T10:00:00+00:00", "2031-05-19T08:00:00+00:00",
                                  "2031-05-21T03:10:00+00:00", "2031-05-21T03:40:00+00:00", "2031-05-21T11:00:00+00:00"))


def history(text=TEXT, q=None, agg=None):
    return L.transcript_report(text, q if q is not None else quota(ANCHOR), agg)["periods"]


def test_no_row_before_the_first_month_with_data():
    rows = history()
    assert min(r["key"][:7] for r in rows) == FIRST[:7]
    assert ("month", FIRST[:7]) in {(r["level"], r["key"]) for r in rows}


def test_aggregate_days_count_as_data_for_the_first_month():
    agg = json.dumps(dict(day="2031-03-01", tokens=5, source="stats-cache"))
    early = "\n".join(l for l in TEXT.split("\n") if FIRST not in l)
    assert min(r["key"][:7] for r in history(early, agg=agg)) == "2031-03"
    assert min(r["key"][:7] for r in history(early)) == "2031-04"


def test_no_hour_row_without_messages():
    hours = [r for r in history() if r["level"] == "hour"]
    assert [r["key"][-5:] for r in hours] == ["03:00", "11:00"] and all(r["messages"] for r in hours)


def test_an_empty_current_day_has_no_hour_rows_but_keeps_the_rest():
    rows = history("\n".join(l for l in TEXT.split("\n") if "2031-05-21" not in l))
    assert rows and not [r for r in rows if r["level"] == "hour"]


def test_no_data_gives_no_rows():
    assert L.transcript_report("", quota(ANCHOR))["periods"] == []


# --- the page ---

def test_tokens_for_every_level_in_both_themes():
    blocks = re.findall(r":root(?:\{|:not\(\[data-theme=\"light\"\]\)\{|\[data-theme=\"dark\"\]\{)(.*?)\}", PAGE, re.S)
    assert len(blocks) == 3
    for b in blocks:
        for lv in LEVELS:
            assert f"--lv-{lv}:" in b and f"--lv-{lv}-tint:" in b
    for lv in LEVELS:
        assert f"tr.lv-{lv} td{{background:var(--lv-{lv}-tint)}}" in PAGE and f"border-left:4px solid var(--lv-{lv})" in PAGE


def render(sessions):
    fn = re.search(r"^function recentRows\(.*$", PAGE, re.M).group(0) + PAGE[PAGE.index("function sessionCards(T)"):PAGE.index("function projectsCard()")]
    esc = re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0)
    usef = re.search(r"^const USEF=.*$", PAGE, re.M).group(0)
    r = subprocess.run(["node", "-e", esc + usef + fn + "process.stdout.write(sessionCards(" + json.dumps(sessions) + "))"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


@node
def test_card_title_legend_and_one_class_per_level():
    rep = L.transcript_report(TEXT, quota(ANCHOR))
    html = render(rep)
    card = html[:html.index("Interactive sessions with the most tokens")]
    assert "<h2>Interactive sessions history</h2>" in card and "<h2>Interactive sessions per period</h2>" not in card
    assert all(f'class="lvkey lv-{lv}"' in card and f"</i>{lv}</span>" in card for lv in LEVELS)
    rows = re.findall(r'<tr class="lv-(\w+)"><td[^>]*>.*?</td><td>(\w+)</td>', card)
    assert len(rows) == len([p for p in rep["periods"] if p["messages"] > 0 or p["total"] > 0]) and all(cls == per for cls, per in rows)  # periods with data only (PC-82); the class matches the word in Per
    assert {cls for cls, _ in rows} == set(LEVELS)


@node
def test_empty_current_day_says_no_sessions_today():
    html = render(L.transcript_report("\n".join(l for l in TEXT.split("\n") if "2031-05-21" not in l), quota(ANCHOR)))
    assert '<tr class="lv-hour"><td class="muted" colspan="10">no sessions today</td></tr>' in html


@node
def test_other_cards_unchanged():
    html = render(L.transcript_report(TEXT + "\n" + msg(ANCHOR, source="src-b"), quota(ANCHOR)))
    rest = html[html.index("<h2>Interactive sessions per source</h2>") - 18:]
    assert "<h2>Interactive sessions with the most tokens</h2>" in rest and "lv-" not in rest
    assert render(None) == '<div class="card"><h2>Interactive sessions</h2><span class="muted">no data</span></div>'
