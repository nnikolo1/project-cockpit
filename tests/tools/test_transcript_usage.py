import json
import logging
import os
import stat

from tools import transcript_usage as tu


def line(mid, ts="2026-01-02T03:04:05.123Z", usage=None, model="m-1", text="SECRET TEXT"):
    msg = {"id": mid, "model": model, "content": [{"type": "text", "text": text}]}
    if usage is not None:
        msg["usage"] = usage
    return json.dumps({"type": "assistant", "timestamp": ts, "cwd": "/secret/path", "message": msg})


def write(folder, name, lines):
    (folder / name).write_text("\n".join(lines) + "\n")


def out_lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_duplicate_message_counted_once(tmp_path):
    src = tmp_path / "s"; src.mkdir()
    write(src, "sess1.jsonl", [line("a", usage={"input_tokens": 1, "output_tokens": 2}),
                               line("a", usage={"input_tokens": 1, "output_tokens": 9})])
    out = tmp_path / "o.jsonl"
    stats = tu.collect({"L": str(src)}, str(out))
    rows = out_lines(out)
    assert len(rows) == 1 and stats["messages"] == 1
    assert rows[0]["output_tokens"] == 9 and rows[0]["session"] == "sess1" and rows[0]["source"] == "L"
    assert rows[0]["ts"] == "2026-01-02T03:04:05+00:00" and rows[0]["model"] == "m-1"


def test_missing_counts_are_zero(tmp_path):
    src = tmp_path / "s"; src.mkdir()
    write(src, "x.jsonl", [line("a", usage={"output_tokens": 5}), line("b", usage={}, model=None)])
    out = tmp_path / "o.jsonl"
    tu.collect({"L": str(src)}, str(out))
    a, b = out_lines(out)
    assert a["input_tokens"] == 0 and a["cache_read_input_tokens"] == 0 and a["output_tokens"] == 5
    assert b["model"] is None and b["output_tokens"] == 0


def test_unreadable_lines_skipped_and_counted(tmp_path):
    src = tmp_path / "s"; src.mkdir()
    write(src, "x.jsonl", ["not json", json.dumps({"message": {"id": "q", "usage": {}}}),
                           line("a", usage={"input_tokens": 1}), json.dumps({"type": "user"})])
    out = tmp_path / "o.jsonl"
    stats = tu.collect({"L": str(src)}, str(out))
    assert stats["skipped"] == 2 and stats["messages"] == 1


def test_no_text_or_path_in_output(tmp_path):
    src = tmp_path / "s"; src.mkdir()
    write(src, "x.jsonl", [line("a", usage={"input_tokens": 1})])
    out = tmp_path / "o.jsonl"
    tu.collect({"L": str(src)}, str(out))
    body = out.read_text()
    assert "SECRET" not in body and "/secret" not in body
    assert set(out_lines(out)[0]) == {"ts", "kind", "source", "session", "agent", "message", "model", *tu.COUNTS}


def test_atomic_rewrite_mode_and_no_leftover(tmp_path):
    src = tmp_path / "s"; src.mkdir()
    write(src, "x.jsonl", [line("a", usage={"input_tokens": 1})])
    out = tmp_path / "o.jsonl"
    out.write_text("old\n")
    tu.collect({"L": str(src)}, str(out))
    tu.collect({"L": str(src)}, str(out))
    assert len(out_lines(out)) == 1
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert sorted(os.listdir(tmp_path)) == ["o.jsonl", "s"]


def test_two_sources_sorted_and_only_top_level_jsonl(tmp_path, caplog):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    (b / "sub").mkdir()
    write(a, "s1.jsonl", [line("m1", ts="2026-01-02T00:00:02Z", usage={"input_tokens": 1})])
    write(b, "s2.jsonl", [line("m2", ts="2026-01-02T00:00:01Z", usage={"input_tokens": 2})])
    write(b / "sub", "s3.jsonl", [line("m3", usage={"input_tokens": 3})])
    write(b, "notes.txt", [line("m4", usage={"input_tokens": 4})])
    out = tmp_path / "o.jsonl"
    with caplog.at_level(logging.WARNING):
        stats = tu.collect({"A": str(a), "B": str(b), "C": str(tmp_path / "missing")}, str(out))
    rows = out_lines(out)
    assert [(r["source"], r["message"]) for r in rows] == [("B", "m2"), ("A", "m1")]
    assert stats["sources"] == 2 and stats["files"] == 2
    assert "folder missing" in caplog.text


def test_main_parses_sources(tmp_path):
    src = tmp_path / "s"; src.mkdir()
    write(src, "x.jsonl", [line("a", usage={"input_tokens": 1})])
    out = tmp_path / "o.jsonl"
    tu.main(["--source", f"L={src}", "--out", str(out)])
    assert len(out_lines(out)) == 1


def test_subagent_transcripts_belong_to_the_parent_session(tmp_path):
    src = tmp_path / "src"
    (src / "s1" / "subagents").mkdir(parents=True)
    write(src, "s1.jsonl", [line("m1", ts="2026-01-02T00:00:01Z", usage={"input_tokens": 1})])
    write(src / "s1" / "subagents", "agent-a1.jsonl", [line("m2", ts="2026-01-02T00:00:02Z", usage={"input_tokens": 5})])
    write(src / "s1" / "subagents", "notes.txt", [line("m3", usage={"input_tokens": 9})])
    out = tmp_path / "o.jsonl"
    stats = tu.collect({"A": str(src)}, str(out))
    assert [(r["session"], r["agent"], r["input_tokens"]) for r in out_lines(out)] == [("s1", None, 1), ("s1", "agent-a1", 5)]
    assert stats["files"] == 2


def test_records_of_purged_transcripts_are_kept_and_repeated_labels_merge(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    write(a, "s1.jsonl", [line("m1", ts="2026-01-02T00:00:01Z", usage={"input_tokens": 1})])
    out = tmp_path / "o.jsonl"
    tu.collect([("L", str(a)), ("L", str(b))], str(out))
    (a / "s1.jsonl").unlink()  # Claude Code deletes the old transcript
    write(b, "s2.jsonl", [line("m2", ts="2026-01-02T00:00:02Z", usage={"input_tokens": 2})])
    stats = tu.collect([("L", str(a)), ("L", str(b))], str(out))
    assert [(r["message"], r["input_tokens"]) for r in out_lines(out)] == [("m1", 1), ("m2", 2)]
    assert stats["kept"] == 1 and stats["messages"] == 2
    tu.collect([("L", str(b))], str(out))  # run again: nothing doubles
    assert len(out_lines(out)) == 2


def test_the_collector_writes_the_kind_of_each_source(tmp_path):
    folders = {n: tmp_path / n for n in ("i", "h", "d", "e")}
    for n, f in folders.items():
        f.mkdir()
        write(f, "s.jsonl", [line("m-" + n, usage={"input_tokens": 1})])
    out = tmp_path / "o.jsonl"
    tu.main(["--source", f"interactive:Lab={folders['i']}", "--source", f"headless:other={folders['h']}",
             "--source", f"headless-old={folders['d']}", "--source", f"plain={folders['e']}", "--out", str(out)])
    assert {(r["source"], r["kind"]) for r in out_lines(out)} == {("Lab", "interactive"), ("other", "headless"), ("headless-old", "headless"),
                                                                   ("plain", "interactive")}
    tu.collect([("headless", "X", str(folders["i"])), ("Y", str(folders["e"]))], str(out))
    assert {(r["source"], r["kind"]) for r in out_lines(out) if r["source"] in ("X", "Y")} == {("X", "headless"), ("Y", "interactive")}


def test_an_unknown_kind_is_refused(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        tu.main(["--source", f"batch:L={tmp_path}", "--out", str(tmp_path / "o.jsonl")])
