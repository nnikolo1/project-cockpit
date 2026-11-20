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


class Workers:
    def __init__(self):
        self.started, self.rc = [], {}

    def start(self, e, pc):
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


def test_workers_stop_when_threshold_crossed_and_resume(tmp_path):
    ctx, h, w, qu, _ = make(tmp_path, many(10), mode="real", q=quota_of(80, 1.0, week_used=90))
    J.poll(ctx, NOW)
    assert w.started == [2, 3]
    w.rc = {2: 0, 3: 1}  # one finished, one failed
    qu.q = quota_of(97, 1.0, week_used=90)  # window nearly spent: nothing new starts
    J.poll(ctx, NOW + 1800)
    st = A.read_json(ctx.state_path, {})
    assert w.started == [2, 3] and len(st["running"]) == 0
    assert any(e["number"] == 3 and e["attempt"] == 2 for e in st["queue"])  # unfinished goes back to the queue
    states = {r["number"]: r["state"] for r in B.release_list(A.read_jsonl(ctx.decisions))}
    assert states[2] == "finished" and states[3] == "unfinished"
    qu.q = quota_of(10, 4.0, week_used=90)  # after the reset
    J.poll(ctx, NOW + 3600)
    assert w.started[2:] == [4, 5]  # after the reset the queue moves on; the failed one waits at the back for its retry


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
