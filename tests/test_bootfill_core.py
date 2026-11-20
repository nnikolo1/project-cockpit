import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest
from bootfill import core as B

CFG = B.merge_config(B.DEFAULTS, {})
H = 3600
NOW = 1_800_000_000.0


def task(n, **kw):
    d = dict(key=f"T-{n}", number=n, title=f"title {n}", stage="embryo", labels=[], type="task", cls="nice", prio=None, deadline=None,
             parent=None, blockedBy=[], modDl=None)
    d.update(kw)
    return d


def quota(five_used=50, five_in=1.0, week_used=50, week_in=100.0):
    return dict(five=(five_used, NOW + five_in * H), week=(week_used, NOW + week_in * H))


def verdicts(q, fired=None):
    return {t["name"]: t["verdict"] for t in B.evaluate_triggers(q, NOW, CFG, fired or {})}


# ---- feed ----
def test_parse_feed_epoch_and_iso():
    f = B.parse_feed({"five_hour": {"used_percentage": 40, "resets_at": 1000}, "seven_day": {"used_percentage": "10", "resets_at": "2026-01-01T00:00:00Z"}})
    assert f["five"] == (40.0, 1000.0) and f["week"][0] == 10.0 and f["week"][1] == 1767225600.0


@pytest.mark.parametrize("obj", [{}, {"five_hour": {"used_percentage": 1}}, {"five_hour": {"used_percentage": 101, "resets_at": 1}, "seven_day": {"used_percentage": 1, "resets_at": 1}},
                                 {"five_hour": {"used_percentage": "x", "resets_at": 1}, "seven_day": {}}])
def test_parse_feed_rejects_broken(obj):
    with pytest.raises(ValueError):
        B.parse_feed(obj)


# ---- triggers ----
def test_five_both_hold():
    v = verdicts(quota(40, 1.0, week_used=95))
    assert v["five_low"] == v["five_high"] == "fire" and v["weekly"] == "below_threshold"


def test_five_only_low():
    v = verdicts(quota(70, 1.0, week_used=95))
    assert v["five_low"] == "fire" and v["five_high"] == "below_threshold"


def test_thresholds_are_strict():
    assert verdicts(quota(90, 1.0, week_used=95))["five_low"] == "below_threshold"  # exactly 10 percent left is not more than 10
    assert verdicts(quota(50, 1.0, week_used=95))["five_high"] == "below_threshold"


def test_too_early_and_stale():
    assert verdicts(quota(10, 2.0, week_used=95))["five_low"] == "too_early"  # needs less than 2 h
    assert verdicts(quota(10, 1.99, week_used=95))["five_low"] == "fire"
    assert verdicts(quota(10, -1.0, week_used=95))["five_low"] == "stale_reset"


def test_weekly_boundaries():
    assert verdicts(quota(99, 3, week_used=79, week_in=24.0))["weekly"] == "fire"  # one day or less
    assert verdicts(quota(99, 3, week_used=79, week_in=24.1))["weekly"] == "too_early"
    assert verdicts(quota(99, 3, week_used=80, week_in=10))["weekly"] == "below_threshold"


def test_once_per_window():
    q = quota(40, 1.0)
    key = B.window_key(q["five"][1])
    v = verdicts(q, {"five_high": key, "five_low": key})
    assert v["five_high"] == v["five_low"] == "already_fired"
    assert verdicts(q, {"five_high": "other-window"})["five_high"] == "fire"  # a new window re-arms


def test_window_key_tolerates_jitter():
    assert B.window_key(1000.0) == B.window_key(1100.0)


# ---- decide ----
def dec(q, released=0, fired=None, cfg=CFG):
    return B.decide(B.evaluate_triggers(q, NOW, cfg, fired or {}), cfg, released)


def test_larger_five_hour_wins_and_marks_both():
    d = dec(quota(40, 1.0, week_used=95))
    assert d["n"] == 20 and d["fired"] == ["five_high"] and set(d["mark"]) == {"five_high", "five_low"}


def test_low_only_releases_five():
    d = dec(quota(80, 1.0, week_used=95))
    assert d["n"] == 5 and d["fired"] == ["five_low"]


def test_weekly_adds_to_five_hour():
    d = dec(quota(40, 1.0, week_used=50, week_in=10))
    assert d["n"] == 60 and d["fired"] == ["five_high", "weekly"]  # 20 + 50 = 70 cut to the day cap 60
    assert d["want"] == 70 and "cut to 60" in d["reasons"][0]


def test_weekly_alone_and_cap():
    assert dec(quota(99, 3, week_used=50, week_in=10))["n"] == 50
    assert dec(quota(99, 3, week_used=50, week_in=10), released=30)["n"] == 30


def test_day_cap_reached_releases_nothing_and_marks_nothing():
    d = dec(quota(40, 1.0), released=60)
    assert d["n"] == 0 and d["mark"] == [] and "day cap 60 reached" in d["reasons"][0]


def test_no_trigger():
    d = dec(quota(99, 3, week_used=99))
    assert d["n"] == 0 and d["reasons"] == ["no trigger fires"]


def test_batch_triggers_labels_slots():
    ts = B.evaluate_triggers(quota(40, 1.0, week_used=50, week_in=10), NOW, CFG, {})
    s = B.batch_triggers(ts, 60)
    assert s[:20] == ["five_high"] * 20 and s[20:] == ["weekly"] * 40
    assert B.batch_triggers(ts, 3) == ["five_high"] * 3


def test_mark_fired_and_roll_day():
    ts = B.evaluate_triggers(quota(40, 1.0), NOW, CFG, {})
    st = B.mark_fired({}, B.decide(ts, CFG, 0), ts)
    assert st["fired"]["five_high"] == st["fired"]["five_low"] == B.window_key(NOW + H)
    assert not any(t["verdict"] == "fire" for t in B.evaluate_triggers(quota(40, 1.0), NOW, CFG, st["fired"]))
    s = B.roll_day({"day": "d1", "day_count": 7}, "d1")
    assert s["day_count"] == 7
    assert B.roll_day(s, "d2") == {"day": "d2", "day_count": 0}


# ---- idle, kill switch ----
def test_idle():
    assert B.idle_verdict(NOW, None, 10)[0]
    assert B.idle_verdict(NOW, NOW - 11 * 60, 10)[0]
    ok, why = B.idle_verdict(NOW, NOW - 3 * 60, 10)
    assert not ok and why == "other Claude Code agent active: last write 3 min ago"


def test_own_workers_never_block_idleness():
    ok, why = B.idle_verdict(NOW, None, 10, own_running=2)
    assert ok and why == "only own Claude Code agents (2 own worker(s) running)"
    assert B.idle_verdict(NOW, None, 10)[1] == "no other Claude Code agent active"


def test_future_transcript_time_is_clamped_not_negative():
    ok, why = B.idle_verdict(NOW, NOW + 45, 10)
    assert not ok and why == "other Claude Code agent active: last write 0 min ago"


def test_other_headless_process_blocks_and_fallback_is_stated():
    ok, why = B.idle_verdict(NOW, None, 10, other_procs=[7])
    assert not ok and "1 headless Claude Code process" in why
    ok, why = B.idle_verdict(NOW, None, 10, process_check=False)
    assert ok and "process check unavailable: transcript signal only" in why


PS = """  1     0 /sbin/launchd
 100     1 python3 -m bootfill.job --once
 101   100 claude -p ok --model haiku
 200     1 sh -c claude -p work
 201   200 /usr/local/bin/claude -p work --allowedTools Read
 300     1 claude
 301     1 /opt/bin/claude --print triage
 302     1 vim claude -p
garbage line
"""


def test_parse_ps_skips_malformed_lines():
    procs = B.parse_ps(PS)
    assert (101, 100, "claude -p ok --model haiku") in procs and len(procs) == 8


def test_other_agent_processes_excludes_own_children_and_interactive_sessions():
    procs = B.parse_ps(PS)
    assert B.other_agent_processes(procs, [100, 200]) == [301]  # own refresh call and own worker excluded; pid 300 is interactive (judged by transcript); 302 is not an assistant
    assert B.other_agent_processes(procs, [100]) == [201, 301]
    assert B.other_agent_processes(procs, [100, 200], names=["other"]) == []


def test_web_and_desktop_processes_are_ignored_only_claude_code_counts():
    ex = B.DEFAULTS["process_exclude"]
    procs = B.parse_ps("""
 10     1 /Applications/Claude.app/Contents/MacOS/Claude --print
 11    10 /Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper -p
 12     1 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --print
 13     1 /usr/local/bin/claude -p background work
 14     1 /Applications/Claude.app/Contents/MacOS/claude -p inside an app bundle
""")
    assert B.other_agent_processes(procs, [], ["claude"], ex) == [13]
    assert B.DEFAULTS["transcript_glob"].startswith("~/.claude/projects/")


def test_encode_cwd_and_transcript_exclusion():
    assert B.encode_cwd("/tmp/a.b/data_dir/headless") == "-tmp-a-b-data-dir-headless"
    pats = ["*-wt-boot-*", "*-tmp-a-b-data-dir-headless*"]
    assert B.transcript_excluded("/h/.claude/projects/-tmp-a-b-data-dir-headless/x.jsonl", pats)
    assert B.transcript_excluded("/h/.claude/projects/-code-proj-wt-boot-12/x.jsonl", pats)
    assert not B.transcript_excluded("/h/.claude/projects/-code-proj/x.jsonl", pats)


def test_stale_minutes_is_three_polls():
    assert B.stale_minutes(10) == 30 and B.stale_minutes(0) == 3


# ---- conflict-aware choice ----
CFG = B.merge_config(B.DEFAULTS, {})


def ent(n, module="", project="p", labels=(), paths=(), key=None):
    return dict(project=project, key=key or f"T-{n}", number=n, title=f"title {n}", module=module, labels=list(labels), paths=list(paths), attempt=1)


def test_task_paths_finds_files_and_folders_only():
    t = "Fix cockpit.py and docs/adr/0014.md; see https://x.org/a/b and version 1.2.3, also web/ folder"
    assert B.task_paths(t) == ["cockpit.py", "docs/adr/0014.md", "web"]
    assert B.entry_meta(dict(module="m", labels=["a"], title="x", desc="edit bootfill/core.py"))["paths"] == ["bootfill/core.py"]


def test_pair_conflict_signals():
    a = ent(1, "kit/a")
    assert B.pair_conflict(a, ent(2, "kit/a"), CFG)["score"] == 4          # same project + same module
    assert B.pair_conflict(a, ent(2, "kit/b"), CFG)["score"] == 3          # same top-level module
    assert B.pair_conflict(a, ent(2, "web"), CFG)["score"] == 1            # different modules
    assert B.pair_conflict(a, ent(2, "kit/a", project="q"), CFG)["score"] == 0
    assert B.pair_conflict(ent(1, "m1", paths=["a/b"]), ent(2, "m2", paths=["a/b/c.py"]), CFG)["score"] >= 11  # folder prefix
    assert B.pair_conflict(ent(1, "m1", labels=["shared-files"]), ent(2, "m2", labels=["shared-files"]), CFG)["score"] == 7
    u = B.pair_conflict(ent(1, ""), ent(2, "unassigned"), CFG)
    assert u["unclear"] and u["score"] == 1
    assert not B.pair_conflict(a, ent(2, "web"), CFG)["unclear"]


def test_choose_nothing_running_takes_head():
    i, rec = B.choose_next([ent(1, "a"), ent(2, "a")], [], CFG)
    assert i == 0 and rec["skipped"] == [] and "nothing running" in rec["reason"]


def test_choose_skips_conflicting_head_for_independent_task():
    q = [ent(1, "kit"), ent(2, "kit"), ent(3, "web")]
    i, rec = B.choose_next(q, [ent(9, "kit")], CFG)
    assert i == 2 and rec["skipped"] == ["p/T-1", "p/T-2"] and "same module kit" in " ".join(rec["signals"]["p/T-1"])
    assert rec["chosen"] == "p/T-3" and rec["active"] == ["T-9"]


def test_choose_other_project_is_independent():
    i, _ = B.choose_next([ent(1, "kit"), ent(2, "kit", project="q")], [ent(9, "kit")], CFG)
    assert i == 1


def test_choose_head_wins_when_no_conflict_or_window_too_small():
    assert B.choose_next([ent(1, "web"), ent(2, "kit")], [ent(9, "kit")], CFG)[0] == 0
    q = [ent(1, "kit"), ent(2, "kit"), ent(3, "web")]
    cfg = dict(CFG, conflict_window=2)
    i, rec = B.choose_next(q, [ent(9, "kit")], cfg)
    assert i == 0 and "least conflicting" in rec["reason"]  # outside the window is never considered


def test_choose_starvation_limit_forces_the_passed_over_task():
    q = [ent(1, "kit"), ent(2, "web")]
    i, rec = B.choose_next(q, [ent(9, "kit")], CFG, {"p/T-1": 3})
    assert i == 0 and "starvation" in rec["reason"]
    assert B.choose_next(q, [ent(9, "kit")], CFG, {"p/T-1": 2})[0] == 1


def test_choose_all_conflict_takes_least_conflicting_then_rank():
    q = [ent(1, "kit/a", paths=["x/y.py"]), ent(2, "kit/a"), ent(3, "kit/b")]
    i, _ = B.choose_next(q, [ent(9, "kit/a", paths=["x/y.py"])], CFG)
    assert i == 2  # 3 (same top-level only) is the least conflicting


def test_choose_unclear_uses_judge_with_reasoning_and_falls_back():
    q = [ent(1, ""), ent(2, ""), ent(3, "")]
    seen = []

    def judge(cands, running):
        seen.append(([c["key"] for c in cands], [r["key"] for r in running]))
        return ["p/T-3", "p/T-1"], "T-3 looks unrelated"
    i, rec = B.choose_next(q, [ent(9, "")], CFG, judge=judge)
    assert i == 2 and rec["judgement"] == "T-3 looks unrelated" and seen == [(["p/T-1", "p/T-2", "p/T-3"], ["T-9"])]

    def broken(c, r):
        raise RuntimeError("down")
    i, rec = B.choose_next(q, [ent(9, "")], CFG, judge=broken)
    assert i == 0 and "judgement unavailable" in rec["reason"]
    assert B.choose_next(q, [ent(9, "")], dict(CFG, conflict_judgement=False), judge=judge)[0] == 0


def test_judge_not_asked_when_signals_are_clear():
    def judge(c, r):
        raise AssertionError("must not be called")
    assert B.choose_next([ent(1, "kit"), ent(2, "web")], [ent(9, "kit")], CFG, judge=judge)[0] == 1


def test_update_skips_counts_once_per_poll_and_resets_chosen():
    rec = dict(chosen="p/T-3", skipped=["p/T-1", "p/T-2"])
    s = B.update_skips({"p/T-3": 2}, rec)
    assert s == {"p/T-1": 1, "p/T-2": 1}
    assert B.update_skips(s, rec, already={"p/T-1"}) == {"p/T-1": 1, "p/T-2": 2}


def test_conflict_prompt_has_keys_and_titles_only():
    p = B.conflict_prompt([dict(key="A", title="ta")], [dict(key="B", title="tb")])
    assert "- A: ta" in p and "- B: tb" in p and "order" in p


def test_kill_switch():
    assert B.kill_switch(True, [], "pause") == "kill switch: flag file"
    assert "label" in B.kill_switch(False, ["x", "pause"], "pause")
    assert B.kill_switch(False, ["x"], "pause") is None and B.kill_switch(False, None) is None


# ---- eligibility ----
EX = B.parse_exclusions(None)


@pytest.mark.parametrize("kw,ok", [
    (dict(), True),
    (dict(stage="released"), False),
    (dict(labels=["question"]), False),
    (dict(labels=["type:decision"]), False),
    (dict(type="decision"), False),
    (dict(labels=["pause"]), False),
    (dict(title="Submit the form"), False),
    (dict(title="Drop old table"), False),
    (dict(title="Add schema migration"), False),
    (dict(title="Refactor parser"), True),
])
def test_eligibility_defaults(kw, ok):
    assert B.eligibility(task(1, **kw), CFG, EX)[0] is ok


def test_eligibility_project_exclusions_and_tracking():
    ex = B.parse_exclusions('{"labels": ["no-auto"], "numbers": [9], "title_patterns": ["^Legal"]}')
    assert not B.eligibility(task(2, labels=["no-auto"]), CFG, ex)[0]
    assert B.eligibility(task(9), CFG, ex)[1] == "excluded by number"
    assert not B.eligibility(task(3, title="Legal review"), CFG, ex)[0]
    assert B.eligibility(task(5), CFG, EX, tracking_number=5)[1] == "tracking issue"


# ---- ranking ----
def ranked(tasks):
    by = {t["key"]: t for t in tasks}
    return B.rank(tasks, by, B.unblock_counts(tasks))


def order(rows):
    return [r["task"]["number"] for r in rows]


def test_rank_order_each_criterion():
    assert order(ranked([task(1, prio=3), task(2), task(3, prio=1)])) == [3, 2, 1]  # P1, none = P2, P3
    assert order(ranked([task(1, deadline="2026-12-31"), task(2, deadline="2026-12-01"), task(3)])) == [2, 1, 3]
    assert order(ranked([task(1, blockedBy=["T-9"]), task(2), task(9, prio=3)]))[:2] == [2, 1]  # P3 blocker ranks last, blocked 1 still after 2
    assert order(ranked([task(1), task(2), task(3, blockedBy=["T-1"]), task(4, blockedBy=["T-1"]), task(5, blockedBy=["T-2"])]))[:2] == [1, 2]
    assert order(ranked([task(1), task(2, cls="must")])) == [2, 1]


def test_priority_beats_deadline_beats_blocked():
    rows = ranked([task(1, deadline="2026-01-01"), task(2, prio=1), task(3, blockedBy=["T-9"], deadline="2026-01-01"), task(9, stage="released")])
    assert order(rows) == [2, 1, 3, 9][:3] + [9] or order(rows)[:3] == [2, 1, 3]


def test_inherited_deadline():
    parent = task(1, deadline="2026-12-01")
    child = task(2, parent="T-1")
    grand = task(3, parent="T-2")
    by = {t["key"]: t for t in (parent, child, grand)}
    assert B.inherited_deadline(grand, by) == ("2026-12-01", "parent T-1")
    assert B.inherited_deadline(task(4, modDl=("2027-01-01", "alpha")), by) == ("2027-01-01", "module alpha")
    assert B.inherited_deadline(task(5), by) == (None, "none")
    loop = task(6, parent="T-7"), task(7, parent="T-6")
    assert B.inherited_deadline(loop[0], {t["key"]: t for t in loop}) == (None, "none")  # no endless loop


def test_blocked_only_while_blocker_open():
    done = task(9, stage="completed")
    by = {t["key"]: t for t in (done, task(1, blockedBy=["T-9"]))}
    assert B.rank_inputs(by["T-1"], by, {})["blocked"] is False
    by["T-9"]["stage"] = "embryo"
    assert B.rank_inputs(by["T-1"], by, {})["blocked"] is True


def test_unblock_counts_ignore_closed():
    ts = [task(1), task(2, blockedBy=["T-1"]), task(3, blockedBy=["T-1"], stage="completed")]
    assert B.unblock_counts(ts) == {"T-1": 1}


# ---- select and tie judgement ----
def test_select_without_tie_at_cutoff_makes_no_judge_call():
    rows = ranked([task(1, prio=1), task(2), task(3)])
    out = B.select(rows, 1, judge=lambda ts: pytest.fail("judge must not be called"))
    assert [r["task"]["number"] for r in out] == [1] and out[0]["judgement"] is None and out[0]["rank"] == 1


def test_select_judges_only_the_straddling_group():
    rows = ranked([task(1, prio=1), task(2), task(3), task(4)])
    seen = []

    def judge(ts):
        seen.append([t["number"] for t in ts])
        return ["T-4", "T-3", "T-2"], "four is most valuable"
    out = B.select(rows, 2, judge)
    assert seen == [[2, 3, 4]] and [r["task"]["number"] for r in out] == [1, 4]
    assert out[1]["judgement"] == "four is most valuable" and out[0]["judgement"] is None and [r["rank"] for r in out] == [1, 2]


def test_select_judge_failure_falls_back_to_number_order():
    def judge(ts):
        raise TimeoutError("slow")
    out = B.select(ranked([task(3), task(1), task(2)]), 2, judge)
    assert [r["task"]["number"] for r in out] == [1, 2] and "TimeoutError" in out[0]["judgement"]


def test_select_edge_sizes():
    assert B.select(ranked([task(1)]), 5) and len(B.select(ranked([task(1)]), 5)) == 1
    assert B.select([], 5) == [] and B.select(ranked([task(1)]), 0) == []


def test_parse_judgement():
    order, why = B.parse_judgement('noise {"order": ["B", "X"], "reasoning": "r"} tail', ["A", "B"])
    assert order == ["B", "A"] and why == "r"
    for bad in ("nothing", '{"order": ["X"]}'):
        with pytest.raises(ValueError):
            B.parse_judgement(bad, ["A"])
    assert "A: title" in B.judge_prompt([dict(key="A", title="title")])


# ---- records ----
ROW = dict(task=task(7), inputs=dict(priority="P2", deadline=None, deadline_source="none", blocked=False, unblocks=0, cls="nice"), rank=3, judgement=None)


def test_release_record_shape_has_no_task_text():
    r = B.release_record(NOW, "widgets", ROW, "five_high", B.DRY, "run1")
    assert set(r) == {"type", "time", "run", "project", "key", "number", "title", "rank", "trigger", "mode", "inputs", "judgement", "state"}
    assert r["state"] == "would_release" and r["time"].endswith("Z") and r["title"] == "title 7"
    assert B.release_record(NOW, "w", ROW, "weekly", B.REAL, "r")["state"] == "queued"


def test_release_list_follows_outcomes_and_counts():
    L = [B.release_record(NOW, "w", ROW, "weekly", B.REAL, "r"),
         B.release_record(NOW, "w", dict(ROW, task=task(8), rank=4), "five_low", B.REAL, "r"),
         B.outcome_record(NOW + 5, "w", "T-7", 7, "running"), B.outcome_record(NOW + 9, "w", "T-7", 7, "finished"),
         B.outcome_record(NOW, "w", "T-99", 99, "finished")]  # orphan outcome is ignored
    rows = B.release_list(L)
    assert [(r["number"], r["state"], r["trigger"], r["rank"], r["project"]) for r in rows] == [(7, "finished", "weekly", 3, "w"), (8, "queued", "five_low", 4, "w")]
    assert set(rows[0]) == {"project", "number", "key", "title", "rank", "trigger", "time", "mode", "state"}
    assert B.counts_per_trigger(rows) == {"weekly": {"finished": 1}, "five_low": {"queued": 1}}


def test_poll_line_and_summary():
    ts = B.evaluate_triggers(quota(40, 1.0), NOW, CFG, {})
    line = B.poll_line(NOW, True, "idle", quota(40, 1.0), ts, "would release 20")
    for part in ("idle=yes", "five_used=40%", "five_reset_in=1.0h", "week_used=50%", "five_high=fire", "decision: would release 20"):
        assert part in line
    assert "five=n/a" in B.poll_line(NOW, False, "busy", None, [], "skip")
    day = B.iso(NOW)[:10]
    s = B.summary_text(B.release_list([B.release_record(NOW, "w", ROW, "weekly", B.DRY, "r")]), day, B.DRY)
    assert "1 task(s) would be released" in s and "w #7 title 7 | rank 3 | weekly" in s


def test_worker_slots():
    q = quota(50, week_used=50)
    assert B.worker_slots(1, 3, q, 5) == 2 and B.worker_slots(3, 3, q, 5) == 0
    assert B.worker_slots(0, 3, quota(95), 5) == 0  # five-hour window at the stop threshold
    assert B.worker_slots(0, 3, quota(10, week_used=96), 5) == 0


def test_merge_config():
    c = B.merge_config(B.DEFAULTS, {"five_hour": {"low_payload": 7}, "day_cap": 10})
    assert c["five_hour"]["low_payload"] == 7 and c["five_hour"]["high_payload"] == 20 and c["day_cap"] == 10 and c["mode"] == "dry-run"


def test_workers_default_is_two_and_unusable_values_fall_back():
    assert B.DEFAULTS["workers"] == 2
    q = quota(10)
    assert B.worker_slots(0, B.DEFAULTS["workers"], q, 5) == 2
    assert B.worker_slots(0, 0, q, 5) == 0 and B.worker_slots(0, "x", q, 5) == 2 and B.worker_slots(0, None, q, 5) == 2
