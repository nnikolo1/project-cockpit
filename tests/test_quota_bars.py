"""PC-112: the percentage bars of the Token Usage page are unambiguous: one shared helper (pctBar) with a fixed track, ticks at
25, 50, 75 and 100 %, a legend, and a fill width that is a pure, non-decreasing function of the value, capped at the full track.
The rendering tests run the page's own code on synthetic readings (skipped without node); the static ones always run."""
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import logic as L  # noqa: E402

PAGE = (ROOT / "web" / "index.html").read_text()
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
QUOTA_TITLE = "Vendor quota used per day (highest reading)"
LEGEND = "share of the quota used (0 to 100 %)"
FILL = re.compile(r'<span class="pbar"[^>]*><i style="width:([0-9.e+-]+)%"></i>')


def run(js_tail):
    esc = re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0)
    fn = PAGE[PAGE.index("const USEF="):PAGE.index("function vRules(){")]
    r = subprocess.run(["node", "-e", esc + fn + "function heatmap(){return ''}" + js_tail], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def quota_html(pairs):
    """the quota card for one reading per day, day i having the (five_hour, seven_day) percentages pairs[i]"""
    reads = [json.dumps({"ts": "2026-%02d-%02dT10:00:00+00:00" % (9 + i // 28, 1 + i % 28),
                         "five_hour": {"used_percentage": f, "resets_at": 1}, "seven_day": {"used_percentage": w, "resets_at": 2}})
             for i, (f, w) in enumerate(pairs)]
    use = L.usage_report("", None, lambda f: None, "\n".join(reads))
    return run("var USE=%s;process.stdout.write(quotaCard(USE.quota))" % json.dumps(use))


def rows(html):
    """[(day, [fill widths of the row])], newest first"""
    return [(re.search(r'<td class="mono">([^<]*)', r).group(1), [float(w) for w in FILL.findall(r)])
            for r in re.findall(r"<tr>(.*?)</tr>", html) if FILL.search(r)]


@node
def test_fill_width_increases_strictly_with_the_value_and_is_capped_above_100():
    vals = [0, 0.5, 1, 13, 13.5, 14, 25, 50, 74.9, 75, 99, 100]
    w = json.loads(run("process.stdout.write(JSON.stringify(%s.map(pctWidth)))" % json.dumps(vals + [100.1, 140, 1e6])))
    assert all(a < b for a, b in zip(w, w[1:len(vals)])) and w[0] == 0
    assert w[len(vals) - 1:] == [100] * 4  # above 100: the full track, never wider


@node
def test_rendered_widths_follow_the_value_in_every_row_and_column():
    pairs = [(v, 100 - v) for v in range(0, 101, 4)] + [(13, 14), (14, 13)]
    got = rows(quota_html(pairs))
    assert len(got) == len(pairs)
    by_value = {}
    for (f, wk), (_, ws) in zip(reversed(pairs), got):  # rows are newest first
        assert len(ws) == 2
        by_value.setdefault(f, set()).add(ws[0])
        by_value.setdefault(wk, set()).add(ws[1])
    assert all(len(s) == 1 for s in by_value.values())  # equal values, equal widths, in every row and both columns
    ordered = [next(iter(by_value[v])) for v in sorted(by_value)]
    assert all(a < b for a, b in zip(ordered, ordered[1:]))  # 13 % is never as long as 14 %


@node
def test_equal_values_give_identical_bars_in_every_row():
    got = rows(quota_html([(13, 13)] * 6 + [(14, 14)] * 3))
    assert {tuple(ws) for _, ws in got} == {(13.0, 13.0), (14.0, 14.0)}


@node
def test_a_value_above_100_is_capped_at_the_full_track_and_ticks_and_legend_render():
    h = quota_html([(130, 100), (100, 250)])
    assert [ws for _, ws in rows(h)] == [[100.0, 100.0], [100.0, 100.0]]
    assert LEGEND in h and h.count("<span class=\"pscale\"") == 2
    for t in (25, 50, 75, 100):
        assert h.count('data-tick="%d"' % t) == 4  # two bars per row, two rows


@node
def test_usage_tables_use_the_same_bar():
    use = {"days": [{"key": "2026-11-0%d" % d, "input_tokens": d, "output_tokens": 0, "cache_creation_input_tokens": 0,
                     "cache_read_input_tokens": 0, "total": d} for d in range(1, 5)]}
    h = run("var USE=%s;process.stdout.write(usageTable('Usage per day',USE.days,NOUSE))" % json.dumps(use))
    assert [w for _, ws in rows(h) for w in ws] == [25.0, 50.0, 75.0, 100.0] and 'data-tick="25"' in h


def test_one_shared_helper_and_no_old_bars_left():
    tables = PAGE[PAGE.index("function usageTable("):PAGE.index("function latestCard(")]
    assert tables.count("pctBar(") == 2 and "pctScale()" in tables and LEGEND in tables
    assert 'class="bar"' not in tables and "width:120px" not in tables
    helper = PAGE[PAGE.index("function pctWidth("):PAGE.index("function usageTable(")]
    assert "Math.min(100" in helper and "min-width" not in helper and "Math.max(" not in helper  # no minimum width
    css = re.search(r"^\.pbar\{.*$", PAGE, re.M).group(0)
    assert "width:200px" in css and "flex:none" in css  # the track does not depend on the table layout
