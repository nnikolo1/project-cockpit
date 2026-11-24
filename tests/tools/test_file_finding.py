import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import file_finding as F  # noqa: E402


def ns(**kw):
    d = dict(repo="o/r", title="T", body="It says X.", policy="P", file=["a.md", "b.py"], filed_by="agent-7", module=None, dry_run=False)
    d.update(kw)
    return argparse.Namespace(**d)


def test_body_has_policy_files_marker_and_origin():
    b = F.build_body("It says X.", "P", ["a.md", "b.py"], "agent-7", "2026-11-24")
    assert "**Policy it contradicts:** P" in b and "- `a.md`" in b and "- `b.py`" in b
    assert "<!-- filed-by: agent-7 -->" in b and "<summary>Origin</summary>" in b and "2026-11-24" in b


def test_marker_is_read_back_by_the_cockpit():
    import logic
    assert logic.parse_filed_by(F.build_body("x", "P", ["a"], "agent-7", "d")) == "agent-7"


def test_labels():
    assert F.labels_for() == ["policy-finding", "author:interactive"]
    assert F.labels_for("taskqueue") == ["policy-finding", "author:interactive", "module:taskqueue"]


def test_files_with_injected_runner():
    calls = []
    url, args = F.file_finding(ns(module="m"), run=lambda a: calls.append(a) or "https://x/issues/1", today="2026-11-24")
    assert url == "https://x/issues/1" and calls == [args]
    assert args[:4] == ["issue", "create", "-R", "o/r"]
    assert [args[i + 1] for i, x in enumerate(args) if x == "--label"] == ["policy-finding", "author:interactive", "module:m"]


def test_dry_run_files_nothing():
    url, args = F.file_finding(ns(dry_run=True), run=lambda a: 1 / 0)
    assert url is None and args[0] == "issue"


def test_body_from_file(tmp_path):
    p = tmp_path / "b.txt"
    p.write_text("from file")
    url, args = F.file_finding(ns(body=f"@{p}", dry_run=True))
    assert "from file" in args[args.index("--body") + 1]
