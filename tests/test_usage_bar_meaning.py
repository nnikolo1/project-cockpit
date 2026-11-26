"""PC-105: the bar of the per-day and per-5-hour-window usage tables says what it depicts. A row the vendor's quota readings cover shows the
5-hour quota percentage as text and as the bar width; a row without keeps the relative bar, labelled "share of the largest row".
Synthetic rows only. The rendering tests run the page's own code (skipped without node); the static ones always run."""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

PAGE = (pathlib.Path(__file__).parent.parent / "web" / "index.html").read_text()
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
HEADER = "Quota used, else share of the largest row"
CELL = re.compile(r'<td class="ubar" data-bar="(quota|share)"><span class="pbar(?: rel)?"[^>]*><i style="width:([0-9.e+-]+)%"></i>.*?'
                  r'<span class="(?:mono|muted)">([^<]*)</span></td>')


def run(js_tail):
    esc = re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0)
    fn = PAGE[PAGE.index("const USEF="):PAGE.index("function vRules(){")]
    r = subprocess.run(["node", "-e", esc + fn + js_tail], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def row(key, total, **kw):
    return dict(key=key, input_tokens=total, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0, total=total, **kw)


def cells(html):
    """[(row key, bar kind, fill width, text next to the bar)] in table order"""
    out = []
    for tr in re.findall(r"<tr>(.*?)</tr>", html):
        m = CELL.search(tr)
        if m:
            out.append((re.search(r'<td class="mono">([^<]*)', tr).group(1), m.group(1), float(m.group(2)), m.group(3)))
    return out


@node
def test_day_rows_show_the_reading_where_there_is_one_else_the_relative_share():
    days = [row("2026-03-04", 400), row("2026-03-03", 100), row("2026-03-02", 200)]
    quota = {"days": [{"key": "2026-03-04", "five_hour": 62, "seven_day": 10}, {"key": "2026-03-02", "five_hour": None, "seven_day": 5}]}
    h = run("process.stdout.write(usageTable('Usage per day',%s,NOUSE,dayQuota(%s),'x'))" % (json.dumps(days), json.dumps(quota)))
    assert HEADER in h and "share of the largest row" in h
    assert cells(h) == [("2026-03-04", "quota", 62.0, "5-hour quota 62 %"),         # the reading, not 100 % for the largest row
                        ("2026-03-03", "share", 25.0, "share of the largest row 25 %"),
                        ("2026-03-02", "share", 50.0, "share of the largest row 50 %")]   # a weekly reading alone is no 5-hour reading


@node
def test_window_rows_match_the_vendor_window_by_its_start():
    end = 1772546400  # 2026-03-03 14:00 UTC; the window starts 09:00
    wins = [row("2026-03-03 09:00 (vendor)", 900, kind="vendor", start="2026-03-03 09:00"),
            row("2026-03-03 05:00 (fixed)", 450, kind="fixed", start="2026-03-03 05:00"),
            row("2026-03-02 09:00 (vendor)", 300, kind="vendor", start="2026-03-02 09:00")]
    quota = {"windows": [{"end": end, "peak": 13.5}]}
    h = run("process.stdout.write(usageTable('Usage per 5-hour window',%s,NOUSE,windowQuota(%s),'x'))" % (json.dumps(wins), json.dumps(quota)))
    assert HEADER in h
    assert cells(h) == [("2026-03-03 09:00 (vendor)", "quota", 13.5, "5-hour quota 13.5 %"),
                        ("2026-03-03 05:00 (fixed)", "share", 50.0, "share of the largest row 50 %"),
                        ("2026-03-02 09:00 (vendor)", "share", 300 / 9, "share of the largest row 33 %")]


@node
def test_without_readings_every_row_keeps_the_relative_bar():
    h = run("process.stdout.write(usageTable('Usage per day',%s,NOUSE,dayQuota(null),'x'))" % json.dumps([row("2026-03-02", 1), row("2026-03-01", 4)]))
    assert [(k, w) for _, k, w, _ in cells(h)] == [("share", 25.0), ("share", 100.0)]


def test_the_label_and_both_tables_pass_their_readings():
    body = PAGE[PAGE.index("function usageTable("):PAGE.index("function quotaCard(")]
    assert HEADER in body and "share of the largest row" in body and "5-hour quota" in body and "data-bar=\"quota\"" in body
    assert "usageTable('Usage per day',USE.days,NOUSE,dayQuota(USE.quota)," in PAGE
    assert re.search(r"usageTable\('Usage per 5-hour window',.*?,NOUSE,windowQuota\(USE\.quota\),", PAGE)
    assert re.search(r"^\.pbar\.rel i\{", PAGE.replace("}.pbar.rel", "}\n.pbar.rel"), re.M)  # the relative bar looks different
