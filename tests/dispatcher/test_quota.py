import json
import logging
import os
import stat

from dispatcher import quota


def snap(path, five=10, seven=20, observed=1_700_000_000):
    path.write_text(json.dumps({"observed_at": observed, "rate_limits": {
        "five_hour": {"used_percentage": five, "resets_at": 1_700_003_600},
        "seven_day": {"used_percentage": seven, "resets_at": 1_700_500_000}}}))


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_appends_only_on_change(tmp_path):
    s, h = tmp_path / "s.json", tmp_path / "h.jsonl"
    snap(s)
    assert quota.collect(s, h) is True
    assert quota.collect(s, h) is False
    snap(s, five=11, observed=1_700_000_030)
    assert quota.collect(s, h) is True
    rec = lines(h)
    assert len(rec) == 2 and rec[0]["source"] == "snapshot"
    assert rec[0]["ts"] == "2023-11-14T22:13:20+00:00"
    assert rec[0]["five_hour"] == {"used_percentage": 10, "resets_at": 1_700_003_600}
    assert rec[1]["five_hour"]["used_percentage"] == 11 and rec[1]["seven_day"]["used_percentage"] == 20


def test_missing_or_broken_snapshot_appends_nothing_and_warns(tmp_path, caplog):
    h = tmp_path / "h.jsonl"
    with caplog.at_level(logging.WARNING, logger="quota"):
        assert quota.collect(tmp_path / "none.json", h) is False
        (tmp_path / "bad.json").write_text("{not json")
        assert quota.collect(tmp_path / "bad.json", h) is False
        (tmp_path / "odd.json").write_text("{}")
        assert quota.collect(tmp_path / "odd.json", h) is False
    assert not h.exists() and len(caplog.records) == 3


def test_history_is_mode_0600_and_never_rewritten(tmp_path):
    s, h = tmp_path / "s.json", tmp_path / "h.jsonl"
    snap(s)
    quota.collect(s, h)
    assert stat.S_IMODE(os.stat(h).st_mode) == 0o600
    first = h.read_text()
    snap(s, seven=21)
    quota.collect(s, h)
    assert h.read_text().startswith(first)


def test_command_line_does_one_reading(tmp_path):
    s, h = tmp_path / "s.json", tmp_path / "h.jsonl"
    snap(s)
    quota.main(["--snapshot", str(s), "--history", str(h)])
    assert len(lines(h)) == 1
