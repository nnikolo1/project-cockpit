import json
import os
import stat

import pytest

from tools import stats_cache_usage as sc


def cache(activity=(), tokens=(), **extra):
    return dict(version=1, dailyActivity=[dict(date=d, messageCount=m, sessionCount=s, toolCallCount=9) for d, m, s in activity],
                dailyModelTokens=[dict(date=d, tokensByModel=by) for d, by in tokens], longestSession={"sessionId": "SECRET"}, **extra)


def put(path, obj):
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj))
    return str(path)


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


C = cache(activity=[("2026-01-01", 5, 1), ("2026-01-02", 7, 2), ("2026-01-03", 3, 1)],
          tokens=[("2026-01-02", {"m-b": 20, "m-a": 10}), ("2026-01-03", {"m-a": 4}), ("2026-01-04", {"m-a": 1, "m-x": "bad"})])


def test_one_line_per_day_and_model_numbers_only(tmp_path):
    out = tmp_path / "agg.jsonl"
    assert sc.run(put(tmp_path / "c.json", C), str(out)) == 5
    rows = lines(out)
    assert [(r["day"], r["model"], r["tokens"]) for r in rows] == [("2026-01-01", None, None), ("2026-01-02", "m-a", 10), ("2026-01-02", "m-b", 20),
                                                                     ("2026-01-03", "m-a", 4), ("2026-01-04", "m-a", 1)]
    assert rows[1]["messages"] == rows[2]["messages"] == 7 and rows[1]["sessions"] == 2 and rows[4]["messages"] is None
    assert {r["source"] for r in rows} == {"stats-cache"} and all(set(r) == {"day", "model", "tokens", "messages", "sessions", "source"} for r in rows)
    assert "SECRET" not in out.read_text() and stat.S_IMODE(os.stat(out).st_mode) == 0o600


def test_days_with_exact_transcript_data_are_left_out(tmp_path):
    exact = tmp_path / "t.jsonl"
    exact.write_text(json.dumps(dict(ts="2026-01-02T23:00:00+00:00", session="s")) + "\nbroken\n")
    out = tmp_path / "agg.jsonl"
    sc.run(put(tmp_path / "c.json", C), str(out), [str(exact), str(tmp_path / "missing.jsonl")])
    assert [r["day"] for r in lines(out)] == ["2026-01-01", "2026-01-03", "2026-01-04"]


@pytest.mark.parametrize("content", ["{not json", "[]", json.dumps(dict(version=1)), json.dumps(dict(dailyActivity=3))])
def test_malformed_or_missing_cache_leaves_the_output_untouched(tmp_path, content):
    out = tmp_path / "agg.jsonl"
    out.write_text("earlier\n")
    assert sc.main(["--cache", put(tmp_path / "c.json", content), "--out", str(out)]) == 1
    assert sc.main(["--cache", str(tmp_path / "none.json"), "--out", str(out)]) == 1
    assert out.read_text() == "earlier\n" and [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_repeat_runs_rewrite_the_file(tmp_path):
    out, c = tmp_path / "agg.jsonl", tmp_path / "c.json"
    sc.main(["--cache", put(c, C), "--out", str(out)])
    first = out.read_text()
    sc.main(["--cache", str(c), "--out", str(out)])
    assert out.read_text() == first
    sc.main(["--cache", put(c, cache(tokens=[("2026-02-01", {"m": 3})])), "--out", str(out)])
    assert [(r["day"], r["tokens"]) for r in lines(out)] == [("2026-02-01", 3)]
