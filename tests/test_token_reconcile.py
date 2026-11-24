"""tools/token_reconcile.py and the card "How these numbers fit", on synthetic data folders (project-agnostic)."""
import datetime as dt
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import cockpit as C  # noqa: E402
import token_reconcile as R  # noqa: E402

PLUS2 = R.tzinfo("+02:00")
UTC = dt.timezone.utc


def ep(s):
    return int(dt.datetime.fromisoformat(s).timestamp())


def msg(ts, mid, session="s1", agent=None, model="m1", i=10, o=20, cw=30, cr=40):
    return dict(ts=ts, source="src", session=session, agent=agent, message=mid, model=model, input_tokens=i, output_tokens=o,
                cache_creation_input_tokens=cw, cache_read_input_tokens=cr)


def req(ts, worker="w1", i=10, o=20, cw=30, cr=40):
    return dict(ts=ts, worker=worker, input_tokens=i, output_tokens=o, cache_creation_input_tokens=cw, cache_read_input_tokens=cr)


def reading(ts, five, reset5):
    return {"ts": ts, "five_hour": {"used_percentage": five, "resets_at": reset5}, "seven_day": {"used_percentage": 1, "resets_at": ep("2026-11-30T00:00:00+00:00")}}


def write(d, name, recs):
    p = d / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")


def cache_file(d, days):
    """A statistics cache: days = {day: (messages, sessions, {model: tokens})}."""
    c = dict(dailyActivity=[dict(date=k, messageCount=v[0], sessionCount=v[1]) for k, v in days.items()],
             dailyModelTokens=[dict(date=k, tokensByModel=v[2]) for k, v in days.items()])
    (d / "stats-cache.json").write_text(json.dumps(c))
    return d / "stats-cache.json"


def ten_messages(day="2026-11-10"):
    """Ten messages of 100 tokens (60 without cache reads, 30 input + output) at noon UTC of `day`, two sessions."""
    return [msg(f"{day}T12:{i:02d}:00+00:00", f"{day}-{i}", session=f"s{i % 2}") for i in range(10)]


def rep(tmp_path, cache=None, tz=PLUS2):
    return R.reconcile(R.load(tmp_path, cache), tz)


def test_two_sources_that_agree(tmp_path):
    write(tmp_path, "transcript-usage.jsonl", ten_messages())
    r = rep(tmp_path, cache_file(tmp_path, {"2026-11-10": (10, 2, {"m1": 1000})}))
    c = r["cache_vs_transcripts"]
    assert c["definition"] == "all" and c["fit"]["all"] == dict(days=1, agree=1, differ=0, median_ratio=1.0)
    d = c["days"][0]
    assert d["tokens"] == dict(a=1000, b=1000, diff=0, ratio=1.0) and d["messages"]["diff"] == 0 and d["sessions"]["b"] == 2
    assert d["models"]["m1"]["ratio"] == 1.0
    assert "agree within 3 percent on 1 of 1 days" in r["sentences"][0]


def test_two_sources_that_differ_and_the_token_definition_that_fits(tmp_path):
    write(tmp_path, "transcript-usage.jsonl", ten_messages("2026-11-10") + ten_messages("2026-11-11"))
    r = rep(tmp_path, cache_file(tmp_path, {"2026-11-10": (10, 2, {"m1": 300}), "2026-11-11": (12, 2, {"m1": 900})}))
    c = r["cache_vs_transcripts"]
    assert c["definition"] == "io" and c["fit"]["io"]["agree"] == 1 and c["fit"]["io"]["differ"] == 1
    assert c["days"][1]["tokens"] == dict(a=900, b=300, diff=600, ratio=3.0) and c["days"][1]["messages"]["diff"] == 2
    assert "on 1 days they differ by more than 20 percent" in r["sentences"][0]
    assert next(x for x in r["reasons"] if x["key"] == "definition")["status"] == "observed"


def test_backfill_file_alone_has_no_overlap_and_says_so(tmp_path):
    write(tmp_path, "transcript-usage.jsonl", ten_messages("2026-11-10"))
    write(tmp_path, "stats-cache-usage.jsonl", [dict(day="2026-11-01", model="m1", tokens=500, messages=5, sessions=1, source="stats-cache")])
    r = rep(tmp_path)
    assert r["cache_vs_transcripts"]["days"] == [] and r["cache_vs_transcripts"]["cache_only"] == ["2026-11-01"]
    assert "cannot be compared" in r["sentences"][0] and "--cache" in r["sentences"][0]
    pruned = next(x for x in r["reasons"] if x["key"] == "pruned")
    assert pruned["status"] == "observed" and "1 days" in pruned["evidence"]


def test_window_with_a_known_ratio_and_the_cache_read_weight(tmp_path):
    """Two 5-hour windows; tokens per point equal only when cache reads count zero: the best weight is 0."""
    e1, e2 = ep("2026-11-10T15:00:00+00:00"), ep("2026-11-10T20:00:00+00:00")
    write(tmp_path, "quota-history.jsonl", [reading("2026-11-10T10:30:00+00:00", 5, e1), reading("2026-11-10T14:00:00+00:00", 10, e1 + 30),
                                            reading("2026-11-10T16:00:00+00:00", 20, e2)])
    write(tmp_path, "transcript-usage.jsonl", [msg("2026-11-09T08:00:00+00:00", "early"), msg("2026-11-10T10:05:00+00:00", "a", i=500, o=500, cw=0, cr=1000)]
          + [msg("2026-11-10T16:30:00+00:00", "b", i=1000, o=1000, cw=0, cr=0)])
    q = rep(tmp_path)["quota_vs_tokens"]["five_hour"]
    assert [(w["pct"], w["tokens"], w["per_point"], w["partial"]) for w in q["windows"]] == [(10, 2000, 200, False), (20, 2000, 100, False)]
    assert q["weights"]["0.0"]["cv"] == 0 and q["weights"]["0.0"]["median"] == 100 and q["best_weight"] == "0.0"
    assert q["spread"]["low"] == 100 and q["spread"]["high"] == 200


def test_window_before_the_transcripts_is_partial_and_quota_without_tokens_is_observed(tmp_path):
    e1, e2 = ep("2026-11-10T05:00:00+00:00"), ep("2026-11-10T20:00:00+00:00")
    write(tmp_path, "quota-history.jsonl", [reading("2026-11-10T04:00:00+00:00", 30, e1), reading("2026-11-10T16:00:00+00:00", 7, e2)])
    write(tmp_path, "transcript-usage.jsonl", [msg("2026-11-10T06:00:00+00:00", "a")])
    r = rep(tmp_path)
    w = r["quota_vs_tokens"]["five_hour"]["windows"]
    assert w[0]["partial"] and not w[1]["partial"] and w[1]["tokens"] == 0
    dev = next(x for x in r["reasons"] if x["key"] == "devices")
    assert dev["status"] == "observed" and dev["evidence"].startswith("1 5-hour windows")


def test_task_note_against_the_proxy_sum(tmp_path):
    write(tmp_path, "proxy-usage.jsonl", [req("2026-11-10T10:00:00+00:00"), req("2026-11-10T10:30:00+00:00"), req("2026-11-10T13:00:00+00:00", "w2")])
    write(tmp_path, "processing/processing.jsonl", [dict(time="2026-11-10T09:59:00+00:00", task="T-1", folder="/x/T-1", state="launched", worker="w1"),
                                                     dict(time="2026-11-10T11:00:00+00:00", task="T-1", folder="/x/T-1", state="done")])
    write(tmp_path, "area/T-1/outbox/goodbye.json", [dict(outcome="done", tokens=150)])
    r = rep(tmp_path)
    assert r["tasks_vs_notes"] == [dict(task="T-1", start="2026-11-10T09:59:00+00:00", worker="w1", overlap=False, a=200, b=150, diff=50, ratio=1.3333)]
    assert "0 of 1 good-bye notes are within 10 percent" in " ".join(r["sentences"])
    assert next(x for x in r["coverage"] if x["source"] == "notes")["records"] == 1


def test_local_day_against_utc_day(tmp_path):
    """23:30 UTC is the next day at +02:00: per UTC day and per local day the token moves; the cache (local day) matches by local day."""
    write(tmp_path, "transcript-usage.jsonl", [msg("2026-11-10T23:30:00+00:00", "a"), msg("2026-11-10T12:00:00+00:00", "b")])
    r = rep(tmp_path, cache_file(tmp_path, {"2026-11-10": (1, 1, {"m1": 100}), "2026-11-11": (1, 1, {"m1": 100})}))
    b = {d["day"]: d for d in r["day_boundaries"]["days"]}
    assert (b["2026-11-10"]["transcripts_utc"], b["2026-11-10"]["transcripts_local"]) == (200, 100)
    assert (b["2026-11-11"]["transcripts_utc"], b["2026-11-11"]["transcripts_local"]) == (0, 100)
    assert r["day_boundaries"]["moved"] == 100 and r["day_boundaries"]["changed"] == 2
    assert r["cache_vs_transcripts"]["fit"]["all"]["agree"] == 2 and r["cache_vs_transcripts"]["fit_utc"]["all"]["agree"] == 0
    assert next(x for x in r["reasons"] if x["key"] == "day")["status"] == "observed"
    assert any("moves 100 tokens between days on 2 days" in s for s in r["sentences"])
    assert not rep(tmp_path, tz=UTC)["day_boundaries"]["changed"]


def test_double_counted_message_and_subagents(tmp_path):
    write(tmp_path, "transcript-usage.jsonl", [msg("2026-11-10T12:00:00+00:00", "a", session="s1"), msg("2026-11-10T13:00:00+00:00", "a", session="s2"),
                                               msg("2026-11-10T14:00:00+00:00", "b", agent="ag1")])
    by = {x["key"]: x for x in rep(tmp_path)["reasons"]}
    assert by["double"]["status"] == "observed" and "1 message ids" in by["double"]["evidence"] and "100 tokens" in by["double"]["evidence"]
    assert by["subagents"]["status"] == "observed" and by["subagents"]["evidence"].startswith("1 sub-agent messages")


def test_missing_sources_say_so_and_nothing_fails(tmp_path):
    r = rep(tmp_path, cache=tmp_path / "absent.json")
    assert [x["present"] for x in r["coverage"]] == [False] * 5 and all(x["not_counted"] for x in r["coverage"])
    assert {x["status"] for x in r["reasons"]} == {"possible"} and len(r["reasons"]) >= 6
    assert r["tasks_vs_notes"] == [] and r["day_boundaries"]["days"] == [] and r["quota_vs_tokens"]["five_hour"]["windows"] == []
    assert "Coverage" in R.text(r) and json.dumps(r)


def test_command_line_prints_text_and_json(tmp_path, capsys):
    write(tmp_path, "transcript-usage.jsonl", ten_messages())
    assert R.main(["--data-dir", str(tmp_path), "--tz", "+02:00", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["coverage"][2]["records"] == 10
    assert R.main(["--data-dir", str(tmp_path), "--tz", "UTC"]) == 0
    assert "6 Known reasons for mismatch" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        R.main(["--data-dir", str(tmp_path), "--tz", "+2x"])


def test_server_adds_the_card_from_the_same_files(tmp_path):
    d = tmp_path / "proj"
    write(d, "transcript-usage.jsonl", ten_messages())
    cache = cache_file(tmp_path, {"2026-11-10": (10, 2, {"m1": 1000})})
    p = dict(name="proj", repo="o/r", stats_cache=str(cache))
    card = C.get_usage(p, tmp_path, {"proj": p})["reconcile"]
    assert set(card) == {"coverage", "sentences", "reasons"} and "agree within 3 percent on 1 of 1 days" in card["sentences"][0]
    assert C.get_usage(dict(name="none", repo="o/r"), tmp_path, {"none": dict(name="none", repo="o/r")})["reconcile"]["coverage"][0]["present"] is False


def test_projects_file_takes_the_statistics_cache_path():
    (p,), _ = C.projects_from_file(dict(projects=[dict(name="a", repo="o/a", stats_cache="~/c.json")]))
    assert p["stats_cache"].endswith("/c.json") and not p["stats_cache"].startswith("~")


PAGE = (ROOT / "web" / "index.html").read_text()


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_page_card_shows_coverage_sentences_and_open_reasons(tmp_path):
    write(tmp_path, "transcript-usage.jsonl", ten_messages())
    card = R.card(rep(tmp_path))
    esc = re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0)
    fn = PAGE[PAGE.index("function fitCard(F){"):PAGE.index("function projectsCard(){")]
    out = subprocess.run(["node", "-e", esc + fn + "process.stdout.write(fitCard(%s)+'|'+fitCard(null))" % json.dumps(card)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    html, none = out.stdout.split("|")
    assert "How these numbers fit" in html and "Interactive transcripts" in html and html.count("no data") == 4
    assert "cannot be compared" in html and "Open: possible" in html and "no data" in none
