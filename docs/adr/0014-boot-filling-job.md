# 0014. The boot-filling job releases ranked tasks to use quota that would expire

Status: accepted (owner, 2026-11-20); not built yet (task PC-19)
Date: 2026-11-20

## Context
Assistant quota that is not used before a window resets is lost. The owner wants idle time used to reduce the backlog, without a request for each task.

## Decision
- A daemon polls every 30 minutes and acts only when the assistant is idle (no interactive session has written to its transcript for 10 minutes and none of the job's own workers is running).
- It refreshes the quota reading with one minimal headless call per poll, because the status-line feed is stale while no session is open.
- Triggers, independent and each at most once per window: 5-hour window with more than 10 percent left and less than 2 hours to the reset releases 5 tasks; with more than 50 percent left it releases 20 (the larger wins); weekly window with more than 20 percent left and one day or less to the reset releases 50. These numbers are the owner's defaults and are configuration.
- Ranking: priority label, due date, unblocked before blocked, what a task unblocks, must before nice, then a recorded judgement of value. A task without a priority counts as P2.
- It starts headless assistant sessions to work the released tasks, a small fixed number in parallel (configuration key `workers`, default 2), with the manager identity only; unfinished tasks resume after a quota reset.
- Limits: embryos only; never decisions or questions, anything that sends or withdraws outside the project, schema changes, deletions, spending; caps per run and per day; a kill switch (flag file and pause label); a one-week dry run; a daily summary; a trail comment on every release.
- Every poll, skip, failure and release is logged: a rotated log and an append-only decision log with the ranking inputs and the judgement; the budget view shows the decision log; no tokens and no task text beyond title and number.

## Consequences
- A project opts in with its own exclusion list and quota feed; the project's instruction file records the delegation.
- The job is reversible by the kill switch at any time.
