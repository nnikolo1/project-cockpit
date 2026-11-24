import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import e2e_failure_paths as e2e  # noqa: E402


def test_every_case_passes_with_its_expected_outcome():
    for mode, (label, reason) in e2e.EXPECTED.items():
        assert e2e.judge(mode, label, reason, True) == []


def test_wrong_label_reason_or_missing_branch_fails():
    assert e2e.judge("nonote", "failed", "unusable note", False)
    assert e2e.judge("nonote", "unmerged", "no note", False)
    assert e2e.judge("success", None, None, False)          # not finished
    assert e2e.judge("gaveup", "failed", "the Worker gave up", False)
    assert e2e.judge("gaveup", "failed", "the Worker gave up", True) == []


def test_final_label_ignores_other_labels():
    assert e2e.final_label({"wip", "bug"}) is None
    assert e2e.final_label({"failed", "x"}) == "failed"


def test_failure_reasons_takes_the_last_failed_entry(tmp_path):
    p = tmp_path / "processing.jsonl"
    p.write_text("\n".join([json.dumps({"task": "r-1", "state": "launched"}),
                            json.dumps({"task": "r-1", "state": "failed", "reason": "no start"}),
                            "garbage",
                            json.dumps({"task": "r-2", "state": "unmerged"}),
                            json.dumps({"heartbeat": "x"})]))
    assert e2e.failure_reasons(p) == {"r-1": "no start"}
    assert e2e.failure_reasons(tmp_path / "missing") == {}
