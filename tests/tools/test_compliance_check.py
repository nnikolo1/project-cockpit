"""The compliance check (PC-108) on two synthetic projects: a compliant one passes; each structural fault fails with its message;
the semantic layer is off by default and logs its cost when on (with an injected model function)."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import compliance_check as K  # noqa: E402
import logic  # noqa: E402

CONS = "# C\n\n## Daily reports (owner, 2026-11-25)\n\nx\n\n## Local policy files: human in the loop (owner, 2026-11-25)\n\ny\n"
DEFS = "# D\n\n## Questions and tasks\n\nz\n"
RELEASE = K.release_of(CONS, DEFS)
BLOCK = "\nManaged text. cockpit:daily-reports cockpit:local-policy-files\n"
POLICY = "# Local policy\n\nDate: 2026-11-25\nSource: owner, session 2026-11-25\n\nTests run before every merge.\n"


def make(root, claude=None, policy=POLICY, manifest=None):
    """A synthetic project: CLAUDE.md with the managed block, one local policy file, the manifest with the block's hash."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".cockpit").mkdir(exist_ok=True)
    (root / "CLAUDE.md").write_text(claude if claude is not None else f"# Rules\n\n{K.BEGIN}{BLOCK}{K.END}\n\n- Local rule.\n")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "policy.md").write_text(policy)
    m = dict(instruction_file="CLAUDE.md", policy_files=["docs/policy.md"], managed_sha256=K.sha256(BLOCK), cockpit_release=RELEASE)
    m.update(manifest or {})
    (root / ".cockpit" / "policies.json").write_text(json.dumps(m))
    return dict(name=root.name, repo=f"o/{root.name}", git=str(root))


def run(p, tmp_path, **kw):
    return K.check(p, tmp_path / "data", CONS, DEFS, RELEASE, "2026-11-25", **kw)


def whats(v):
    return [x["what"] for x in v]


def test_rule_ids_from_headings():
    assert K.rule_ids(CONS, DEFS) == {"daily-reports", "local-policy-files", "questions-and-tasks"}


def test_compliant_project_passes_and_says_so(tmp_path):
    assert run(make(tmp_path / "good"), tmp_path) == []
    rep = (tmp_path / "data" / "good" / K.REPORT_FILE).read_text()
    assert "Compliant" in rep and "2026-11-25" in rep and RELEASE in rep
    assert logic.compliance_summary(rep) == dict(checked="2026-11-25", release=RELEASE, violations=0)


def test_missing_managed_block(tmp_path):
    p = make(tmp_path / "bad", claude="# Rules\n\ncockpit:daily-reports cockpit:local-policy-files\n")
    assert whats(run(p, tmp_path)) == ["managed block missing"]


def test_edited_managed_block(tmp_path):
    p = make(tmp_path / "bad", claude=f"{K.BEGIN}{BLOCK}Sneaked in.\n{K.END}\n")
    assert whats(run(p, tmp_path)) == ["managed block edited (its hash differs from the manifest)"]


def test_outdated_managed_block(tmp_path):
    p = make(tmp_path / "bad", manifest=dict(cockpit_release="old"))
    assert run(p, tmp_path)[0]["what"].startswith("managed block outdated")


def test_undeclared_override(tmp_path):
    p = make(tmp_path / "bad", policy=POLICY + "\nThis overrides the daily report rule.\n")
    v = run(p, tmp_path)
    assert len(v) == 1 and v[0]["file"] == "docs/policy.md" and v[0]["what"].startswith("override without the rule it overrides and the owner's date")
    rep = (tmp_path / "data" / "bad" / K.REPORT_FILE).read_text()
    assert logic.compliance_summary(rep)["violations"] == 1


def test_declared_override_passes_unknown_rule_fails(tmp_path):
    ok = make(tmp_path / "ok", policy=POLICY + "\nOverrides: cockpit:daily-reports (owner, 2026-11-25): weekly here.\n")
    assert run(ok, tmp_path) == []
    bad = make(tmp_path / "bad", policy=POLICY + "\nOverrides: cockpit:no-such-rule (owner, 2026-11-25)\n")
    assert whats(run(bad, tmp_path)) == ["override names an unknown constitutional rule `no-such-rule` (line 8)"]


def test_undated_policy_file_and_missing_source(tmp_path):
    p = make(tmp_path / "bad", policy="# Local policy\n\nTests run.\n")
    assert whats(run(p, tmp_path)) == ["policy file undated (no `Date: YYYY-MM-DD` line)", "policy file without a source line (no `Source:` line)"]


def test_required_rule_marker_missing(tmp_path):
    block = "\nManaged text. cockpit:daily-reports\n"
    p = make(tmp_path / "bad", claude=f"{K.BEGIN}{block}{K.END}\n", manifest=dict(managed_sha256=K.sha256(block)))
    assert whats(run(p, tmp_path)) == ["constitutional rule `local-policy-files` neither restated nor referenced (no marker `cockpit:local-policy-files`)"]


def test_project_files_are_not_edited(tmp_path):
    p = make(tmp_path / "bad", policy="undated\n")
    before = {f: f.read_bytes() for f in (tmp_path / "bad").rglob("*") if f.is_file()}
    run(p, tmp_path)
    assert before == {f: f.read_bytes() for f in (tmp_path / "bad").rglob("*") if f.is_file()}


def test_semantic_layer_off_by_default(tmp_path):
    run(make(tmp_path / "good"), tmp_path)
    assert not (tmp_path / "data" / "good" / K.USAGE_FILE).exists()
    assert "Semantic" not in (tmp_path / "data" / "good" / K.REPORT_FILE).read_text()


def test_semantic_layer_logs_cost_and_lists_candidates(tmp_path):
    calls = []

    def model(prompt, max_tokens):
        calls.append(max_tokens)
        ans = [dict(local="Tests run before every merge.", constitution="x", reason="weakens it")] if "policy.md" in prompt else []
        return json.dumps(ans), dict(input_tokens=300, output_tokens=50)
    assert run(make(tmp_path / "good"), tmp_path, model=model, cap=1000) == []
    d = tmp_path / "data" / "good"
    recs = [json.loads(x) for x in (d / K.USAGE_FILE).read_text().splitlines()]
    assert [r["worker"] for r in recs] == ["compliance-check"] * 2 and calls == [1000, 650]
    assert sum(logic._counts(r)["total"] for r in recs) == 700
    rep = (d / K.REPORT_FILE).read_text()
    assert "not a verdict; 700 tokens" in rep and "weakens it" in rep


def test_semantic_layer_stops_at_the_cap(tmp_path):
    model = lambda prompt, max_tokens: ("[]", dict(input_tokens=max_tokens))
    cands, spent = K.semantic({"a.md": "a", "b.md": "b"}, CONS, model, 100)
    assert cands == [] and spent == 100


def test_findings_filed_once_per_violation(tmp_path):
    p = make(tmp_path / "bad", policy="undated\n")
    calls = []
    existing = K.title("bad", dict(file="docs/policy.md", what="policy file without a source line (no `Source:` line)"))

    def gh(args):
        calls.append(args)
        return json.dumps([existing]) if args[:2] == ["issue", "list"] else "https://x/1"
    run(p, tmp_path, filed_by="agent-1", run=gh)
    created = [a for a in calls if a[:2] == ["issue", "create"]]
    assert len(created) == 1 and "undated" in created[0][created[0].index("--title") + 1]
    assert "policy-finding" in created[0]
