"""Token Usage, heatmap "Tokens per day" (PC-106): the interactive transcripts' day totals count, the sources of a day add up, and a day
no source covers is "no data", not zero; generalized, on synthetic proxy, transcript and statistics-cache files."""
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import cockpit as C  # noqa: E402
import logic as L  # noqa: E402

PAGE = (ROOT / "web" / "index.html").read_text()


def msg(ts, n=10, kind="interactive"):
    return json.dumps(dict(ts=ts, source="src-a", session="s1", kind=kind, input_tokens=n, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0))


def agg(day, n):
    return json.dumps(dict(day=day, model="m-1", tokens=n, messages=1, sessions=1, source="stats-cache"))


def req(ts, n=5):
    return json.dumps(dict(ts=ts, worker="w1", input_tokens=n, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0))


TRANSCRIPTS = "\n".join([msg("2026-10-08T09:00:00+00:00", 100), msg("2026-10-08T15:00:00+00:00", 20), msg("2026-10-10T09:00:00+00:00", 7),
                         msg("2026-10-10T10:00:00+00:00", 1000, kind="headless"), msg("2026-11-20T10:00:00+00:00", 30)])
AGGREGATES = "\n".join([agg("2026-09-10", 400), agg("2026-09-12", 50), agg("2026-10-08", 999)])


def heat(usage=None, transcripts=TRANSCRIPTS, aggregates=AGGREGATES):
    rep = L.usage_report(usage, None, lambda f: None)
    return L.heat_days(rep["days"], L.transcript_report(transcripts, None, aggregates))


def test_transcript_only_days_appear_with_the_history_day_sums():
    h = heat()
    assert h["2026-10-08"] == dict(total=120, sources=dict(transcripts=120))  # older than the history's day rows, still per day
    assert h["2026-10-10"]["total"] == 7  # headless records are not interactive
    assert h["2026-11-20"]["total"] == 30


def test_a_day_no_source_covers_is_no_data_but_a_covered_day_without_use_is_zero():
    h = heat()
    assert "2026-09-20" not in h and "2026-10-07" not in h and "2026-11-21" not in h  # the gap before the transcripts, and after the newest record
    assert h["2026-10-09"] == dict(total=0, sources=dict(transcripts=0))  # inside the transcripts' span: zero, not no data
    assert h["2026-09-10"] == {"total": 400, "sources": {"statistics cache": 400}}


def test_days_with_several_sources_add_up_and_exact_transcripts_win_over_the_aggregate():
    h = heat("\n".join([req("2026-11-20T01:00:00+00:00", 5), req("2026-09-12T01:00:00+00:00", 3)]))
    assert h["2026-11-20"] == dict(total=35, sources=dict(proxy=5, transcripts=30))
    assert h["2026-09-12"] == {"total": 53, "sources": {"proxy": 3, "statistics cache": 50}}
    assert h["2026-10-08"]["sources"] == dict(transcripts=120)  # the aggregate of a day with per-message records is ignored


def test_no_files_no_heat_values():
    assert L.heat_days(None, None) == {}


def test_get_usage_sends_the_heatmap_values(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "transcript-usage.jsonl").write_text(TRANSCRIPTS)
    (d / "stats-cache-usage.jsonl").write_text(AGGREGATES)
    p = dict(name="proj", repo="o/proj")
    h = C.get_usage(p, tmp_path, {"proj": p})["heatmap"]
    assert h["2026-10-08"]["total"] == 120 and h["2026-09-10"]["total"] == 400 and "2026-09-20" not in h


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_page_marks_no_data_cells_and_names_the_sources():
    esc = re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0)
    hm = PAGE[PAGE.index("function shade("):PAGE.index("function vTimeline(){")]
    fn = PAGE[PAGE.index("const USEF="):PAGE.index("function vRules(){")]
    use = dict(heatmap=heat("\n".join([req("2026-11-20T01:00:00+00:00", 5)])), quota=None, sessions=None)
    js = esc + hm + fn + "var TODAY='2026-11-26',USE=%s;process.stdout.write(vUsage())" % json.dumps(use)
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    tips = dict((t.split(" on ")[1].split(":")[0].split("\n")[0], (c, t)) for c, t in re.findall(r'<i class="c ([^"]+)" title="([^"]*)"', r.stdout))
    assert tips["2026-09-20"][0] == "nd" and tips["2026-09-20"][1].startswith("no data on 2026-09-20")
    assert tips["2026-10-09"][0] == "l0" and tips["2026-10-09"][1].startswith("0 tokens")
    assert "proxy: 5" in tips["2026-11-20"][1] and "transcripts: 30" in tips["2026-11-20"][1]
    assert "statistics cache (daily aggregate, no split): 400" in tips["2026-09-10"][1]
    assert '<i class="c nd"></i> no data' in r.stdout  # the legend tells the two apart
