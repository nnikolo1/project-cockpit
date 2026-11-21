import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import datetime as dt
import json
from bootfill import core as B, job as J, adapters as A
import logic as L

H = 3600
NOW = dt.datetime(2026, 11, 20, 10, 0).timestamp()  # local 10:00, before the summary hour


def task(n, **kw):
    d = dict(key=f"T-{n}", number=n, title=f"title {n}", stage="embryo", labels=[], type="task", cls="nice", prio=None, deadline=None,
             parent=None, blockedBy=[], modDl=None)
    d.update(kw)
    return d


class Hosting:
    def __init__(self, tasks, labels=()):
        self.t, self.l, self.released, self.comments, self.fail = tasks, list(labels), [], [], set()
        self.marked, self.prs = set(), {}  # marked: issues whose worker has assigned them (the fake worker's first action)

    def tasks(self, project):
        return self.t

    def labels(self, project, n):
        return self.l

    def release(self, project, t, comment):
        if t["number"] in self.fail:
            raise RuntimeError("boom")
        self.released.append((project, t["number"], comment))

    def comment(self, project, n, body):
        self.comments.append((project, n, body))

    def assignees(self, project, n):
        return ["m"] if n in self.marked else []

    def find_pr(self, project, branch):
        return self.prs.get(int(branch.rsplit("/", 1)[1]))


class Workers:
    def __init__(self):
        self.started, self.rc, self.fail, self.log = [], {}, set(), None

    def start(self, e, pc):
        if e["number"] in self.fail:
            raise RuntimeError("cannot spawn")
        self.started.append(e["number"])
        return dict(id=e["number"])

    def poll(self, h):
        return self.rc.get(h["id"])


def quota_of(five_used=40, five_in=1.0, week_used=50, week_in=100.0):
    return dict(five=(five_used, NOW + five_in * H), week=(week_used, NOW + week_in * H))


class Quota:
    def __init__(self, q):
        self.q, self.calls = q, 0

    def read(self):
        self.calls += 1
        if isinstance(self.q, Exception):
            raise self.q
        return self.q


class Idle:
    def __init__(self, last=None):
        self.last = last

    def last_write(self):
        return self.last


def make(tmp, tasks, mode="dry-run", q=None, last=None, labels=(), **over):
    tmp.mkdir(exist_ok=True)
    cfg = B.merge_config(B.DEFAULTS, dict(mode=mode, projects=[dict(name="p", repo="o/r", manager="m", dir=str(tmp))], budget_dir=str(tmp / "budget"),
                                          tracking_issue=dict(project="p", number=1), tie_judgement=False, **over))
    notes = []
    h, w, qu = Hosting(tasks, labels), Workers(), Quota(q or quota_of())
    ctx = J.Ctx(cfg, tmp / "data", h, qu, Idle(last), w, None, lambda t, m: notes.append((t, m)))
    (tmp / "data").mkdir(exist_ok=True)
    return ctx, h, w, qu, notes


def contract(ctx):
    return A.read_jsonl(ctx.contract)


def many(n):
    return [task(i) for i in range(2, n + 2)]


def test_dry_run_releases_nothing_but_logs(tmp_path):
    ctx, h, w, qu, _ = make(tmp_path, many(30))
    text = J.poll(ctx, NOW)
    assert h.released == [] and w.started == [] and text.startswith("would release 20")
    c = contract(ctx)
    rel = [x for x in c if x["kind"] == "release"]
    assert len(rel) == 20 and all(x["dry_run"] and x["state"] == "would_release" and x["trigger"] == "five_high" for x in rel)
    assert [x["rank"] for x in rel] == list(range(1, 21)) and c[-1]["kind"] == "poll"
    assert (tmp_path / "data" / "decision-log.jsonl").exists()


def test_contract_lines_validate_against_the_cockpit_parser(tmp_path):
    ctx, *_ = make(tmp_path, many(3), q=quota_of(40, 1.0))
    J.poll(ctx, NOW)
    view = L.decision_view(ctx.contract.read_text())
    assert view["skipped"] == 0 and view["status"] == "ok" and len(view["releases"]) == 3 and len(view["polls"]) == 1
    r = view["releases"][-1]
    assert r["project"] == "p" and r["issue"] and r["title"] and r["reason"] and r["dry_run"] is True and r["time"].endswith("Z")
    p = view["polls"][0]
    assert p["idle"] is True and p["five_hour_used"] == 40 and p["weekly_used"] == 50 and p["five_hour_resets_at"].endswith("Z")
    assert {"name": "five_high", "verdict": "fired"} in p["triggers"] and p["decision"].startswith("would release")
    raw = [json.loads(l) for l in ctx.contract.read_text().splitlines()]
    assert set(raw[0]) == {"kind", "time", "trigger", "rank", "project", "issue", "title", "reason", "state", "dry_run"}
    assert set(raw[-1]) == {"kind", "time", "idle", "idle_reason", "five_hour_used", "five_hour_resets_at", "weekly_used", "weekly_resets_at", "triggers", "decision"}


def test_real_mode_releases_with_trail_and_queues(tmp_path):
    ctx, h, w, *_ = make(tmp_path, many(10), mode="real", q=quota_of(80, 1.0, week_used=90))
    J.poll(ctx, NOW)
    assert len(h.released) == 5 and "boot-filling job (real)" in h.released[0][2] and "five_low" in h.released[0][2]
    assert w.started == [2, 3]  # the default is two workers in parallel, three stay queued
    st = A.read_json(ctx.state_path, {})
    assert len(st["queue"]) == 3 and len(st["running"]) == 2 and st["day_count"] == 5
    states = {r["number"]: r["state"] for r in B.release_list(A.read_jsonl(ctx.decisions))}
    assert states[2] == "running" and states[6] == "queued"


def test_once_per_window_second_poll_skips(tmp_path):
    ctx, h, *_ = make(tmp_path, many(30))
    J.poll(ctx, NOW)
    text = J.poll(ctx, NOW + 1800)
    assert text.startswith("skip") and "already_fired" in text
    assert len([x for x in contract(ctx) if x["kind"] == "release"]) == 20


def test_day_cap_stops_a_later_window(tmp_path):
    ctx, *_ = make(tmp_path, many(100), q=quota_of(40, 1.0, week_used=50, week_in=10), day_cap=30)
    assert "would release 30" in J.poll(ctx, NOW)
    ctx.quota.q = quota_of(40, 1.0 + 5, week_used=50, week_in=10)  # a different five-hour window; weekly already fired
    ctx.quota.q["five"] = (40, NOW + 4 * H + 1 * H)
    text = J.poll(ctx, NOW + 4 * H)
    assert text.startswith("skip") and "day cap 30 reached" in text


def test_kill_switch_flag_and_label(tmp_path):
    ctx, h, w, qu, _ = make(tmp_path, many(3), mode="real")
    (ctx.dir / "PAUSE").write_text("")
    assert "flag file" in J.poll(ctx, NOW) and h.released == [] and qu.calls == 0
    ctx2, h2, *_ = make(tmp_path / "x", many(3), mode="real", labels=["pause"])
    assert "pause" in J.poll(ctx2, NOW) and h2.released == []


def test_not_idle_releases_nothing_and_skips_quota_call(tmp_path):
    ctx, h, w, qu, _ = make(tmp_path, many(3), mode="real", last=NOW - 5 * 60)
    text = J.poll(ctx, NOW)
    assert text == "skip: not idle" and qu.calls == 0 and h.released == []
    assert contract(ctx)[-1]["idle"] is False


def test_quota_feed_failure_is_logged_not_fatal(tmp_path):
    ctx, h, *_ = make(tmp_path, many(3), q=FileNotFoundError("feed gone"))
    text = J.poll(ctx, NOW)
    assert "unreadable" in text and "FileNotFoundError" in text and h.released == []
    p = contract(ctx)[-1]
    assert p["kind"] == "poll" and "five_hour_used" not in p


def test_no_eligible_task(tmp_path):
    ctx, *_ = make(tmp_path, [task(2, labels=["question"]), task(3, stage="released")])
    assert J.poll(ctx, NOW) == "skip: no eligible task"
    assert [x["kind"] for x in contract(ctx)] == ["poll"]


def test_release_failure_is_recorded_and_others_continue(tmp_path):
    ctx, h, *_ = make(tmp_path, many(3), mode="real", q=quota_of(80, 1.0, week_used=95))
    h.fail = {2}
    J.poll(ctx, NOW)
    assert [r[1] for r in h.released] == [3, 4]
    assert [x["state"] for x in contract(ctx) if x["kind"] == "release"][0] == "failed"
    assert 2 not in [r["number"] for r in B.release_list(A.read_jsonl(ctx.decisions))]


def test_workers_stop_when_threshold_crossed_and_no_retry(tmp_path):
    ctx, h, w, qu, notes = make(tmp_path, many(10), mode="real", q=quota_of(80, 1.0, week_used=90))
    h.prs[2] = 77
    J.poll(ctx, NOW)
    assert w.started == [2, 3]
    h.marked = {2, 3}  # both fake workers took their first action
    w.rc = {2: 0, 3: 1}  # one finished with a pull request, one failed
    qu.q = quota_of(97, 1.0, week_used=90)  # window nearly spent: nothing new starts
    J.poll(ctx, NOW + 1800)
    st = A.read_json(ctx.state_path, {})
    assert w.started == [2, 3] and len(st["running"]) == 0
    assert not any(e["number"] in (2, 3) for e in st["queue"])  # no retry, ever
    states = {r["number"]: r["state"] for r in B.release_list(A.read_jsonl(ctx.decisions))}
    assert states[2] == "awaiting_acceptance" and states[3] == "needs_you"
    aw = [r for r in A.read_jsonl(ctx.decisions) if r.get("state") == "awaiting_acceptance"][0]
    assert aw["pr"] == 77
    wk = {x["issue"]: x for x in contract(ctx) if x["kind"] == "worker"}
    assert any(x["kind"] == "worker" and x["state"] == "in_progress" and x["issue"] == 2 for x in contract(ctx))
    assert wk[2]["state"] == "awaiting_acceptance" and wk[2]["pr"] == 77 and wk[3]["state"] == "needs_you" and wk[3]["status"] == "NEEDS YOU"
    assert any(t == "NEEDS YOU" and "no retry" in m for t, m in notes)
    qu.q = quota_of(10, 4.0, week_used=90)  # after the reset
    J.poll(ctx, NOW + 3600)
    assert w.started[2:] == [4, 5]  # the queue moves on; the failed task is not started again


def test_exit_zero_without_pull_request_needs_you(tmp_path):
    ctx, h, w, qu, _ = make(tmp_path, many(3), mode="real", q=quota_of(80, 1.0, week_used=90))
    J.poll(ctx, NOW)
    w.rc = {2: 0}
    J.poll(ctx, NOW + 1800)
    assert {r["number"]: r["state"] for r in B.release_list(A.read_jsonl(ctx.decisions))}[2] == "needs_you"


def test_worker_start_failure_is_recorded_and_never_retried(tmp_path):
    ctx, h, w, qu, _ = make(tmp_path, many(3), mode="real", q=quota_of(80, 1.0, week_used=90))
    w.fail = {2}
    J.poll(ctx, NOW)
    assert w.started == [3]
    recs = [r for r in A.read_jsonl(ctx.decisions) if r.get("state") == "start_failed"]
    assert recs and recs[0]["number"] == 2 and "cannot spawn" in recs[0]["note"]
    assert any(x["kind"] == "worker" and x["state"] == "start_failed" for x in contract(ctx))
    w.fail = set()
    J.poll(ctx, NOW + 7200)
    assert 2 not in w.started  # no retry without a requeue record


def test_job_checks_the_workers_mark_and_never_assigns(tmp_path):
    ctx, h, w, *_ = make(tmp_path, many(2), mode="real", q=quota_of(80, 1.0, week_used=90))
    J.poll(ctx, NOW)
    assert not hasattr(h, "assign")  # the hosting client has no write for assignment at all
    h.marked = {2}  # worker 2 marked its issue, worker 3 did not
    J.poll(ctx, NOW + 600)
    J.poll(ctx, NOW + 1200)
    recs = [r for r in A.read_jsonl(ctx.decisions) if r["type"] == "outcome" and r["state"] in ("in_progress", "not_in_progress")]
    assert [(r["number"], r["state"]) for r in recs] == [(2, "in_progress"), (3, "not_in_progress")]  # one record each, no repeats
    assert recs[1]["note"] == "worker did not mark in progress"
    assert [x for x in contract(ctx) if x["kind"] == "worker" and x["state"] == "needs_you" and x["status"] == "NEEDS YOU"]
    assert w.started == [2, 3]  # nothing else: no retry


def test_worker_prompt_makes_marking_in_progress_the_first_action():
    p = B.worker_prompt(dict(number=7, title="t"), dict(repo="o/r", manager="m"))
    assert p.startswith("FIRST ACTION") and "gh issue edit 7 -R o/r --add-assignee m" in p
    assert p.index("--add-assignee") < p.index("Read it with gh") and "DRAFT pull request" in p


def test_worked_task_is_never_started_again_without_requeue(tmp_path):
    ctx, h, w, qu, _ = make(tmp_path, many(2), mode="real", q=quota_of(80, 1.0, week_used=90))
    J.poll(ctx, NOW)
    assert w.started == [2, 3]
    w.rc = {2: 0, 3: 0}
    J.poll(ctx, NOW + 1800)
    st = A.read_json(ctx.state_path, {})
    st["queue"].append(dict(project="p", key="T-2", number=2, title="title 2", attempt=1))  # a stray duplicate entry (e.g. old state file)
    A.write_json(ctx.state_path, st)
    J.poll(ctx, NOW + 3600)
    assert w.started == [2, 3]
    assert any(r.get("state") == "skipped_already_worked" and r["number"] == 2 for r in A.read_jsonl(ctx.decisions))
    A.append_jsonl(ctx.decisions, B.requeue_record(NOW + 3700, "p", 2, "manager", "redo after review"))
    J.poll(ctx, NOW + 3800)
    assert w.started == [2, 3, 2] and "resumed" in [r["state"] for r in A.read_jsonl(ctx.decisions) if r.get("number") == 2 and r["type"] == "outcome"]
    J.poll(ctx, NOW + 3900)
    assert w.started == [2, 3, 2]  # the requeue is used up once the worker has started


def test_requeue_record_needs_orchestrator_or_manager_and_a_reason():
    import pytest
    with pytest.raises(ValueError):
        B.requeue_record(NOW, "p", 2, "daemon", "x")
    with pytest.raises(ValueError):
        B.requeue_record(NOW, "p", 2, "manager", " ")
    from bootfill import requeue as R


def test_requeue_tool_writes_the_record(tmp_path):
    from bootfill import requeue as R
    R.main(["--data-dir", str(tmp_path), "--project", "p", "--number", "9", "--by", "orchestrator", "--reason", "PR rejected"])
    (rec,) = A.read_jsonl(tmp_path / "decision-log.jsonl")
    assert rec["type"] == "requeue" and rec["number"] == 9 and rec["by"] == "orchestrator"


def test_guard_raw_released_label_skips_with_record(tmp_path):
    t = task(2, labels=["released"])  # stage embryo although the raw label is there: the policy dropped it as unauthorised
    ctx, h, w, *_ = make(tmp_path, [t, task(3)], mode="real", q=quota_of(80, 1.0, week_used=90))
    J.poll(ctx, NOW)
    assert [x[1] for x in h.released] == [3]
    sk = [r for r in A.read_jsonl(ctx.decisions) if r["type"] == "release_skip"]
    assert sk and sk[0]["number"] == 2 and "released label" in sk[0]["reason"]
    J.poll(ctx, NOW + 7200)
    assert len([r for r in A.read_jsonl(ctx.decisions) if r["type"] == "release_skip"]) == 1  # one record per change, not per poll


def test_guard_history_blocks_rerelease_within_days_then_expires(tmp_path):
    ctx, h, w, qu, _ = make(tmp_path, [task(2)], mode="real", q=quota_of(80, 1.0, week_used=90), rerelease_guard_days=30)
    A.append_jsonl(ctx.decisions, dict(type="release", time=B.iso(NOW - 5 * 86400), project="p", key="T-2", number=2, title="x", mode="real", state="queued"))
    J.poll(ctx, NOW)
    assert h.released == []
    assert any("already released at" in r["reason"] for r in A.read_jsonl(ctx.decisions) if r["type"] == "release_skip")
    A.append_jsonl(ctx.decisions, dict(type="release", time=B.iso(NOW - 40 * 86400), project="p", key="T-3", number=3, title="x", mode="real", state="queued"))
    ctx2, h2, *_ = make(tmp_path / "b", [task(3)], mode="real", q=quota_of(80, 1.0, week_used=90))
    A.append_jsonl(ctx2.decisions, dict(type="release", time=B.iso(NOW - 40 * 86400), project="p", key="T-3", number=3, title="x", mode="real", state="queued"))
    J.poll(ctx2, NOW)
    assert [x[1] for x in h2.released] == [3]  # older than the window: allowed again


def test_guard_dry_run_history_does_not_block(tmp_path):
    ctx, h, *_ = make(tmp_path, [task(2)], mode="real", q=quota_of(80, 1.0, week_used=90))
    A.append_jsonl(ctx.decisions, dict(type="release", time=B.iso(NOW - 3600), project="p", key="T-2", number=2, title="x", mode="dry-run", state="would_release"))
    J.poll(ctx, NOW)
    assert [x[1] for x in h.released] == [2]


def test_gh_release_never_unlabels_a_task_that_carries_the_label(monkeypatch):
    import cockpit as C
    calls = []
    monkeypatch.setattr(C, "run_gh", lambda args, *a, **k: calls.append(args) or "{}")
    monkeypatch.setattr(C, "gh_user", lambda: "m")
    monkeypatch.setattr(C, "execute", lambda ops, run=None: calls.extend(ops))
    g = A.Gh([dict(name="p", repo="o/r", manager="m")])
    monkeypatch.setattr(g, "_cockpit", lambda: C)
    flagged = dict(key="T-1", number=1, labels=["released"], bad={"released": "unknown"})
    import pytest
    with pytest.raises(RuntimeError, match="already carries"):
        g.release("p", flagged, "trail")
    assert calls == []  # not even a comment: no edit, no remove-label
    g.release("p", dict(key="T-2", number=2, labels=[], bad={}), "trail")
    assert not any("--remove-label" in c for c in calls) and any("--add-label" in c for c in calls)


def test_gh_reads_assignees_and_pr_only(monkeypatch):
    import cockpit as C
    calls = []
    monkeypatch.setattr(C, "run_gh", lambda args, *a, **k: calls.append(args) or "[]")
    g = A.Gh([dict(name="p", repo="o/r", manager="m")])
    monkeypatch.setattr(g, "_cockpit", lambda: C)
    assert g.assignees("p", 5) == [] and g.find_pr("p", "boot/5") is None
    assert not hasattr(g, "assign") and not any("edit" in c for c in calls)


def test_daily_summary_once_per_day_and_notification(tmp_path):
    late = dt.datetime(2026, 11, 20, 21, 0).timestamp()
    ctx, h, w, qu, notes = make(tmp_path, many(3), q=quota_of(99, 3.0, week_used=99))
    J.poll(ctx, late)
    J.poll(ctx, late + 1800)
    assert len(h.comments) == 1 and h.comments[0][:2] == ("p", 1) and len(notes) == 1


def test_tie_judgement_reasoning_stored(tmp_path):
    ctx, h, *_ = make(tmp_path, many(8), q=quota_of(80, 1.0, week_used=95))
    ctx.cfg["tie_judgement"] = True
    ctx.judge = lambda ts: (["T-9", "T-8"], "nine matters most")
    J.poll(ctx, NOW)
    recs = [r for r in A.read_jsonl(ctx.decisions) if r["type"] == "release"]
    assert any(r["judgement"] == "nine matters most" for r in recs)
    assert any("judgement: nine matters most" in x["reason"] for x in contract(ctx) if x["kind"] == "release")


def test_jsonl_helpers_and_state_by_mode(tmp_path):
    A.append_jsonl(tmp_path / "a.jsonl", {"x": 1})
    A.append_jsonl(tmp_path / "a.jsonl", {"x": 2})
    assert A.read_jsonl(tmp_path / "a.jsonl") == [{"x": 1}, {"x": 2}] and A.read_jsonl(tmp_path / "none") == []
    A.write_json(tmp_path / "s.json", {"a": 1})
    assert A.read_json(tmp_path / "s.json", {}) == {"a": 1} and A.read_json(tmp_path / "nope", 5) == 5
    assert make(tmp_path / "a", [])[0].state_path.name == "state-dry-run.json"


# ---- idle = no other agent; own transcripts excluded ----
def test_own_running_workers_do_not_block_the_poll(tmp_path):
    ctx, h, w, qu, _ = make(tmp_path, many(10), mode="real", q=quota_of(80, 1.0, week_used=90))
    J.poll(ctx, NOW)
    assert len(w.started) == 2
    text = J.poll(ctx, NOW + 600)  # both workers still running: the job is idle-eligible, but the cap keeps it from starting more
    st = A.read_json(ctx.state_path, {})
    assert text != "skip: not idle" and len(st["running"]) == 2 and len(w.started) == 2
    assert "only own Claude Code agents (2 own worker(s) running)" in contract(ctx)[-1]["idle_reason"]


def test_other_headless_process_blocks_and_ps_failure_falls_back(tmp_path):
    ctx, h, *_ = make(tmp_path, many(3), mode="real")
    ctx.procs = lambda: [(500, 1, "claude -p something")]
    assert J.poll(ctx, NOW) == "skip: not idle" and "other Claude Code agent active" in contract(ctx)[-1]["idle_reason"]
    ctx2, *_ = make(tmp_path / "b", many(3))

    def broken():
        raise RuntimeError("ps missing")
    ctx2.procs = broken
    J.poll(ctx2, NOW)
    assert "process check unavailable" in contract(ctx2)[-1]["idle_reason"] and contract(ctx2)[-1]["idle"] is True


def test_own_worker_process_tree_is_not_another_agent(tmp_path):
    ctx, h, w, *_ = make(tmp_path, many(10), mode="real", q=quota_of(80, 1.0, week_used=90))
    ctx.procs = lambda: []
    J.poll(ctx, NOW)
    st = A.read_json(ctx.state_path, {})
    st["running"][0]["handle"]["pid"] = 700
    A.write_json(ctx.state_path, st)
    ctx.procs = lambda: [(701, 700, "claude -p work"), (800, 1, "claude -p foreign")]
    J.poll(ctx, NOW + 600)
    assert "1 headless Claude Code process" in contract(ctx)[-1]["idle_reason"]  # only the foreign one counts


def test_idle_adapter_ignores_excluded_transcripts(tmp_path):
    import os
    own, other = tmp_path / "-x-data-headless", tmp_path / "-y-proj"
    wt = tmp_path / "-y-proj-wt-boot-5"
    for d, age in ((own, 1), (wt, 2), (other, 600)):
        d.mkdir()
        f = d / "s.jsonl"
        f.write_text("{}")
        os.utime(f, (NOW - age, NOW - age))
    pats = ["*-wt-boot-*", "*-x-data-headless*"]
    idle = A.Idle(str(tmp_path / "*" / "*.jsonl"), pats)
    assert idle.last_write() == NOW - 600  # own refresh and own worker transcripts do not count
    assert A.Idle(str(tmp_path / "*" / "*.jsonl")).last_write() == NOW - 1
    assert A.Idle(str(tmp_path / "nothing" / "*.jsonl"), pats).last_write() is None


def test_own_call_directory_is_passed_to_the_headless_calls(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(A, "run", lambda cmd, timeout=300, cwd=None: seen.append(cwd) or '{"order": ["a/T-1"], "reasoning": "r"}')
    feed = tmp_path / "q.json"
    feed.write_text(json.dumps(dict(five_hour=dict(used_percentage=1, resets_at=NOW), seven_day=dict(used_percentage=1, resets_at=NOW))))
    A.Quota(str(feed), ["x"], cwd="/own").read()
    A.Judge(["x", "{prompt}"], cwd="/own")([dict(key="a/T-1", title="t")], [dict(key="T-9", title="u")])
    A.Judge(["x", "{prompt}"], cwd="/own")([dict(key="a/T-1", title="t")])
    assert seen == ["/own"] * 3


# ---- conflict-aware worker choice in the poll ----
def test_worker_choice_skips_conflict_and_is_logged(tmp_path):
    tasks = [task(2, module="kit"), task(3, module="kit"), task(4, module="kit"), task(5, module="web")]
    ctx, h, w, *_ = make(tmp_path, tasks, mode="real", q=quota_of(80, 1.0, week_used=90))
    J.poll(ctx, NOW)
    assert w.started == [2, 5]  # 2 first (nothing running), then 5 skips ahead of the same-module 3 and 4
    st = A.read_json(ctx.state_path, {})
    assert st["skips"] == {"p/T-3": 1, "p/T-4": 1}
    rec = [r for r in A.read_jsonl(ctx.decisions) if r["type"] == "conflict_choice"]
    assert len(rec) == 1 and rec[0]["chosen"] == "p/T-5" and rec[0]["skipped"] == ["p/T-3", "p/T-4"]
    assert "same module kit" in " ".join(rec[0]["signals"]["p/T-3"]) and rec[0]["reason"]


def test_worker_choice_starvation_limit_across_polls(tmp_path):
    tasks = [task(2, module="kit"), task(3, module="kit"), task(4, module="web"), task(5, module="web2")]
    ctx, h, w, qu, _ = make(tmp_path, tasks, mode="real", q=quota_of(80, 1.0, week_used=90), workers=1, conflict_max_skips=1)
    J.poll(ctx, NOW)  # starts 2
    w.rc = {2: 0}
    J.poll(ctx, NOW + 600)  # 2 finished: nothing running, rank order -> 3
    assert w.started == [2, 3]
    w.rc = {2: 0, 3: None}
    st = A.read_json(ctx.state_path, {})
    assert st["running"][0]["entry"]["number"] == 3


def test_entries_remember_module_labels_and_paths(tmp_path):
    ctx, *_ = make(tmp_path, [task(2, module="kit", labels=["shared-files"], desc="edit docs/x.md")], mode="real", q=quota_of(80, 1.0, week_used=90))
    J.poll(ctx, NOW)
    e = A.read_json(ctx.state_path, {})["running"][0]["entry"]
    assert e["module"] == "kit" and e["labels"] == ["shared-files"] and e["paths"] == ["docs/x.md"]


# ---- plist helper ----
def test_render_plist_uses_poll_minutes_and_escapes():
    from bootfill import render_plist as R
    tpl = R.TEMPLATE.read_text()
    out = R.render(tpl, "/c/a&b.json", "/d", "/k", 10)
    assert "<integer>600</integer>" in out and "a&amp;b.json" in out and "POLL_SECONDS" not in out and "CONFIG_PATH" not in out
    assert "<integer>120</integer>" in R.render(tpl, "c", "d", "k", 2)
    import pytest
    with pytest.raises(ValueError):
        R.render(tpl, "c", "d", "k", 0)


def test_render_plist_cli_reads_config(tmp_path, capsys):
    from bootfill import render_plist as R
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps(dict(poll_minutes=15)))
    R.main(["--config", str(cfg), "--data-dir", str(tmp_path), "--checkout", str(tmp_path), "--out", str(tmp_path / "o.plist")])
    assert "<integer>900</integer>" in (tmp_path / "o.plist").read_text() and "--quota-stale-min 45" in capsys.readouterr().err
    cfg.write_text("{}")
    R.main(["--config", str(cfg), "--data-dir", str(tmp_path), "--checkout", str(tmp_path), "--out", str(tmp_path / "o.plist")])
    assert "<integer>600</integer>" in (tmp_path / "o.plist").read_text()


class FakeKeepAwake:
    def __init__(self):
        self.held = []

    def hold(self, seconds):
        self.held.append(seconds)


def test_keep_awake_is_held_while_armed_not_when_dry_or_paused(tmp_path):
    ctx, h, w, *_ = make(tmp_path, many(1), mode="real", q=quota_of(80, 1.0, week_used=90))
    ctx.keep_awake = FakeKeepAwake()
    J.poll(ctx, NOW)
    assert ctx.keep_awake.held == [2 * 10 * 60]  # twice the default poll interval of 10 minutes
    ctx.cfg["keep_awake"] = False
    J.poll(ctx, NOW + 600)
    assert ctx.keep_awake.held == [1200]  # switched off: no new hold
    dctx, *_ = make(tmp_path / "dry", many(1), mode="dry-run", q=quota_of(80, 1.0, week_used=90))
    dctx.keep_awake = FakeKeepAwake()
    J.poll(dctx, NOW)
    assert dctx.keep_awake.held == []  # dry run does not need the machine awake


def test_keep_awake_failure_never_stops_the_poll(tmp_path):
    class Broken:
        def hold(self, seconds):
            raise FileNotFoundError("caffeinate")
    ctx, *_ = make(tmp_path, many(1), mode="real", q=quota_of(80, 1.0, week_used=90))
    ctx.keep_awake = Broken()
    assert isinstance(J.poll(ctx, NOW), str)  # the poll still returns its decision text

