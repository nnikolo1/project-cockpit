# 0015. Boot-filling job: implementation choices beyond ADR 0014

Status: proposed (builder, task PC-19); the owner confirms or changes
Date: 2026-11-20
Modules: kit

## Decisions
- One poll is one short process started by launchd every 30 minutes (`--once`); no resident loop. State lives in `state-<mode>.json`, so the dry run and the real mode never share fired-window or day counters.
- A window is identified by its `resets_at` (rounded to 10 minutes). A trigger fires at most once per window. When the larger 5-hour trigger wins it also marks the smaller one as fired; nothing is marked when the day cap leaves 0 tasks, so a later poll can still fire.
- The kill-switch label `pause` is read from the tracking issue (the same issue that receives the daily summary); the flag file is `PAUSE` in the data folder.
- Eligibility is opt-out with conservative defaults: labels `type:decision` and `question`, a title pattern for send, submit, withdraw, delete, drop, spend, purchase, schema, DDL, plus a per-project file (labels, numbers, title patterns). Over-excluding is the safe error; all are configuration.
- Tasks of all configured projects are ranked together; ties inside the group that straddles the cutoff are the only ones sent to the model judgement (titles and keys only); a failed judgement keeps the task-number order and records the failure as the reasoning.
- Workers are detached `sh` processes that write an exit-code file; a later poll reaps them. A non-zero exit puts the task back into the queue once (`max_attempts`, default 2) and the retry is logged as `resumed`. Worker start stops when either window has `worker_stop_left` percent or less.
- Two decision logs: the contract file `decisions.jsonl` in the budget folder (docs/contracts/budget-panel.md, poll and release lines) and the job's own richer `decision-log.jsonl` (ranking inputs, outcomes per task). The budget list of released tasks is `core.release_list` over the richer log.
- The release uses the cockpit's own label edit (`cockpit._edit`, `cockpit.execute`) and refuses when `gh` is not logged in as the Manager.

## Limits
- The "no network except the hosting CLI" rule for workers is not enforced by stdlib code; the tool allowlist is. An operating-system sandbox is an open point.
