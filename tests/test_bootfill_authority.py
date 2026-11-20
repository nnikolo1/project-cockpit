import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import json
import logging
import pytest
from bootfill import core as B, job as J, adapters as A
import logic as L
from test_bootfill_job import NOW, H, task, make, quota_of, contract

CFG = B.merge_config(B.DEFAULTS, {})
EXCL = B.parse_exclusions(None)


def assess(t, others=(), cfg=CFG, excl=EXCL):
    ts = [t, *others]
    return B.authority_assess(t, cfg, excl, {x["key"]: x for x in ts}, {x["number"]: x for x in ts})


def decide(t, judge=None, others=(), cache=None, budget=None, cfg=CFG):
    ts = [t, *others]
    return B.authority_decide(t, cfg, EXCL, {x["key"]: x for x in ts}, {x["number"]: x for x in ts}, judge, cache, NOW, budget)


# ---- signals and verdicts ----
def test_clean_task_is_none():
    a = assess(task(5, title="Add a retry to the fetch loop", desc="Cover with a unit test."))
    assert a["verdict"] == "none" and a["signals"] == []


@pytest.mark.parametrize("kw,kind", [
    (dict(labels=["needs-approval"]), "label"),
    (dict(desc="This needs approval before anything is merged."), "strong"),
    (dict(desc="The owner decides which option wins."), "strong"),
    (dict(title="Rotate the API key", desc="Issue a new token and share it."), "strong"),
    (dict(desc="Run the DDL migration"), "strong"),
    (dict(desc="Edit constitution.md to add a rule"), "path"),
    (dict(desc="see #9", blockedBy=[]), "question"),
])
def test_each_signal_gives_needs_authority(kw, kind):
    q = task(9, labels=["question"], title="Which database?")
    a = assess(task(5, **kw), others=[q])
    assert a["verdict"] == "needs_authority" and any(s.startswith(kind + ":") for s in a["signals"])


def test_question_via_blocker_and_closed_question_does_not_count():
    q = task(9, key="T-9", labels=["question"])
    assert "question" in " ".join(assess(task(5, blockedBy=["T-9"]), [q])["signals"])
    q["stage"] = "completed"
    assert assess(task(5, blockedBy=["T-9"], desc="see #9"), [q])["verdict"] == "none"


def test_weak_signal_is_maybe_and_thresholds_are_configurable():
    t = task(5, desc="Update the deployment notes for production.")
    assert assess(t)["verdict"] == "maybe"
    cfg = B.merge_config(B.DEFAULTS, dict(authority_maybe_score=2))
    assert assess(t, cfg=cfg)["verdict"] == "none"
    cfg = B.merge_config(B.DEFAULTS, dict(authority_skip_score=1))
    assert assess(t, cfg=cfg)["verdict"] == "needs_authority"


def test_project_exclusion_file_extends_labels_patterns_and_paths():
    ex = B.parse_exclusions(dict(authority_labels=["legal-review"], authority_patterns=[r"\bvendor X\b"], protected_paths=["infra/*"]))
    for kw in (dict(labels=["legal-review"]), dict(desc="talk to vendor X"), dict(desc="edit infra/main.tf")):
        assert assess(task(5, **kw), excl=ex)["verdict"] == "needs_authority"


def test_redact_and_excerpt_cap():
    t = "token=abcd1234 mail me@x.org /Users/bob/x ghp_" + "a" * 20 + " " + "z" * 40
    r = B.redact(t)
    assert "abcd1234" not in r and "me@x.org" not in r and "/Users/bob" not in r and "ghp_a" not in r and "zzzz" not in r
    assert len(B.excerpt("word " * 500, 50)) == 50
    p = B.authority_prompt(task(5, desc="x" * 5000 + " token=hunter2"), ["weak: deploy"], 100)
    assert "hunter2" not in p and len(p) < 1600


# ---- judge ----
def test_judge_decides_maybe_and_caches():
    t = task(5, desc="Update the deployment notes for production.")
    calls = []
    judge = lambda tk, sig: (calls.append(1), ("none", "docs only"))[1]
    cache = {}
    d = decide(t, judge, cache=cache)
    assert d["release"] and d["judged"] and d["reason"] == "docs only"
    d2 = decide(t, judge, cache=cache)
    assert d2["cached"] and d2["release"] and len(calls) == 1
    t2 = dict(t, desc="Update the deployment notes for production. Also credentials.")
    decide(t2, judge, cache=cache)
    assert len(calls) == 2  # changed text: judged again


def test_judge_says_needs_authority():
    d = decide(task(5, desc="Update production."), lambda t, s: ("needs_authority", "touches prod"))
    assert not d["release"] and d["verdict"] == "needs_authority"


def test_judge_failure_no_judge_and_budget_all_skip_and_say_so():
    t = task(5, desc="Update production notes.")

    def boom(tk, s):
        raise RuntimeError("down")
    d = decide(t, boom)
    assert not d["release"] and "FAILED (RuntimeError)" in d["failure"]
    d = decide(t, None)
    assert not d["release"] and "no authority judge" in d["failure"]
    d = decide(t, lambda t, s: ("none", "ok"), budget=[0])
    assert not d["release"] and "budget" in d["failure"]
    assert "FAILED" in B.needs_you_text("p", 5, "t", decide(t, boom))


def test_parse_authority():
    assert B.parse_authority('x {"verdict": "NONE", "reason": "fine\\n ok"} y') == ("none", "fine ok")
    for bad in ("nothing", '{"verdict": "maybe"}'):
        with pytest.raises(ValueError):
            B.parse_authority(bad)


# ---- wording ----
def test_text_has_status_why_todo_signals():
    d = decide(task(5, labels=["needs-approval"]))
    txt = B.needs_you_text("p", 5, "A title", d)
    assert txt.startswith("NEEDS YOU: p #5 A title (NOT released). Why: ") and " To release it: " in txt and "Signals: label: needs-approval" in txt
    assert "released anyway" in B.needs_you_text("p", 5, "A title", d, "flagged")
    for soft in ("unfinished",):
        assert soft not in txt


# ---- job behaviour ----
class Judge:
    def __init__(self, ans=("none", "fine")):
        self.ans, self.calls = ans, []

    def __call__(self, t, s):
        self.calls.append(t["number"])
        if isinstance(self.ans, Exception):
            raise self.ans
        return self.ans


def tasks_mix():
    return [task(2), task(3, labels=["needs-approval"]), task(4, desc="Update production notes."), task(5), task(6, labels=["type:decision"])]


def test_skip_mode_keeps_embryos_records_and_unaffected_caps(tmp_path):
    ctx, h, *_ = make(tmp_path, tasks_mix() + [task(i) for i in range(7, 12)], mode="real", q=quota_of(80, 1.0, week_used=90))
    ctx.authority_judge = Judge(("needs_authority", "prod"))
    J.poll(ctx, NOW)
    rel = [n for _, n, _ in h.released]
    assert 3 not in rel and 4 not in rel and len(rel) == 5  # cap still 5: replaced by the next eligible ones
    recs = [r for r in A.read_jsonl(ctx.decisions) if r["type"] == "authority_skip"]
    assert {r["number"] for r in recs} == {3, 4}
    r = recs[0]
    assert set(r) >= {"time", "project", "number", "title", "verdict", "signals", "reason", "status", "why", "todo", "text", "action"} and r["status"] == "NEEDS YOU" and r["action"] == "skipped"
    c = [x for x in contract(ctx) if x["kind"] == "authority_skip"]
    assert len(c) == 2 and c[0]["status"] == "NEEDS YOU"


def test_three_places_agree_record_poll_line_and_summary(tmp_path, caplog):
    late = __import__("datetime").datetime(2026, 11, 20, 21, 0).timestamp()
    ctx, h, *_ = make(tmp_path, tasks_mix(), q=quota_of(80, 12.0, week_used=90))
    ctx.authority_judge = Judge(("needs_authority", "prod"))
    with caplog.at_level(logging.INFO, logger="bootfill.poll"):
        text = J.poll(ctx, late)
    rec = [r for r in A.read_jsonl(ctx.decisions) if r["type"] == "authority_skip" and r["number"] == 3][0]
    assert rec["text"] in [m for m in caplog.messages]  # poll log carries the record's text verbatim
    assert "NEEDS YOU: 2 task(s) NOT released" in text and "#3" in text and "#4" in text
    summary = h.comments[0][2]
    assert "- " + rec["text"] in summary and summary.count("NEEDS YOU") >= 3
    assert any(x["kind"] == "authority_skip" and rec["text"][:300] == x["reason"] for x in contract(ctx))


def test_decision_log_not_repeated_every_poll(tmp_path):
    ctx, *_ = make(tmp_path, tasks_mix(), q=quota_of(80, 1.0, week_used=90))
    J.poll(ctx, NOW)
    J.poll(ctx, NOW + 7 * H)  # new window: assessed again, unchanged text
    assert len([r for r in A.read_jsonl(ctx.decisions) if r["type"] == "authority_skip" and r["number"] == 3]) == 1


def test_judge_failure_in_job_skips_and_says_so(tmp_path):
    ctx, h, *_ = make(tmp_path, [task(4, desc="Update production notes.")], mode="real", q=quota_of(80, 1.0, week_used=90))
    ctx.authority_judge = Judge(RuntimeError("x"))
    text = J.poll(ctx, NOW)
    assert h.released == [] and "NEEDS YOU" in text
    rec = [r for r in A.read_jsonl(ctx.decisions) if r["type"] == "authority_skip"][0]
    assert "FAILED" in rec["text"] and rec["failure"]


def test_flag_mode_releases_and_marks(tmp_path):
    ctx, h, *_ = make(tmp_path, [task(3, labels=["needs-approval"]), task(2)], mode="real", q=quota_of(80, 1.0, week_used=90), authority_mode="flag")
    J.poll(ctx, NOW)
    assert sorted(n for _, n, _ in h.released) == [2, 3]
    flagged = [c for _, n, c in h.released if n == 3][0]
    assert "NEEDS YOU" in flagged and "released anyway" in flagged
    assert "NEEDS YOU" not in [c for _, n, c in h.released if n == 2][0]
    rel = [r for r in A.read_jsonl(ctx.decisions) if r["type"] == "release" and r["number"] == 3][0]
    assert rel["authority"]["status"] == "NEEDS YOU"
    assert [r for r in A.read_jsonl(ctx.decisions) if r["type"] == "authority_skip"][0]["action"] == "flagged"


def test_clean_poll_has_no_authority_text_and_summary_unchanged(tmp_path):
    ctx, h, *_ = make(tmp_path, [task(2)], q=quota_of(80, 1.0, week_used=90))
    assert "NEEDS YOU" not in J.poll(ctx, NOW)
    assert B.authority_needs_text([]) == ""


# ---- contract compatibility ----
def test_decision_view_ignores_authority_kind_and_still_counts_unknown():
    rec = B.authority_skip_record(NOW, "p", task(3), decide(task(3, labels=["needs-approval"])), "skip")
    lines = [json.dumps(B.contract_authority(rec)), json.dumps(dict(kind="future_kind", time="2026-11-20T10:00:00Z")), "not json"]
    v = L.decision_view("\n".join(lines))
    assert v["status"] == "ok" and v["releases"] == [] and v["polls"] == [] and v["skipped"] == 2


def test_config_example_keys_are_known():
    cfg = json.load(open(pathlib.Path(__file__).parent.parent / "bootfill" / "config.example.json"))
    assert cfg["authority_mode"] in ("skip", "flag")
    assert set(k for k in cfg if k.startswith("authority_")) <= set(B.DEFAULTS)


def test_every_why_and_todo_sentence_is_plain_and_distinct():
    q = task(9, labels=["question"])
    cases = [dict(desc="see #9"), dict(desc="edit constitution.md"), dict(labels=["needs-approval"]), dict(desc="the owner decides"), dict(desc="Update production.")]
    seen = set()
    for kw in cases:
        d = decide(task(5, **kw), others=[q])
        why, todo = B.needs_you_sentences(d)
        assert why.endswith(".") and todo.endswith(".") and "NEEDS YOU" not in why
        seen.add(why)
    assert len(seen) == 5
    why, todo = B.needs_you_sentences(dict(signals=["weak: x"], verdict="needs_authority", judged=True, reason="touches prod", failure=""))
    assert "judge found" in why and "touches prod" in why
