# 0014. The boot-filling job releases ranked tasks to use quota that would expire

Status: Superseded by docs/specs/dispatcher-and-workers.md (2026-11-24)
Earlier status: accepted (owner, 2026-11-20); built (task PC-19); poll interval, idle definition and conflict-aware choice amended 2026-11-20
Date: 2026-11-20

## Context
Assistant quota that is not used before a window resets is lost. The owner wants idle time used to reduce the backlog, without a request for each task.

## Decision
- A daemon polls every 10 minutes (configuration key `poll_minutes`; owner, 2026-11-20) and acts only when the assistant is idle: no Claude Code agent other than the job's own is active. Activity is a Claude Code transcript written within the idle window (`idle_minutes`, default 10) or a headless Claude Code process the job did not start; an open but silent interactive session is not running once its transcript is older than the window. Claude Web, the desktop chat app and the browser never count. The job's own agents (quota refresh, judge, workers) never count either, however recent their transcripts are, and own workers running do not block a poll; only the worker cap limits how many are started.
- When a worker slot frees up, the next task is the first in rank order that is least likely to get in the way of the workers already running or chosen (same module, shared paths or labels; see ADR 0015); rank stays primary and a task is passed over at most `conflict_max_skips` times.
- It refreshes the quota reading with one minimal headless call per poll, because the status-line feed is stale while no session is open.
- Triggers, independent and each at most once per window: 5-hour window with more than 10 percent left and less than 2 hours to the reset releases 5 tasks; with more than 50 percent left it releases 20 (the larger wins); weekly window with more than 20 percent left and one day or less to the reset releases 50. These numbers are the owner's defaults and are configuration.
- Ranking: priority label, due date, unblocked before blocked, what a task unblocks, must before nice, then a recorded judgement of value. A task without a priority counts as P2.
- It starts headless assistant sessions to work the released tasks, a small fixed number in parallel (configuration key `workers`, default 2), with the manager identity only; unfinished tasks resume after a quota reset.
- Limits: embryos only; never decisions or questions, anything that sends or withdraws outside the project, schema changes, deletions, spending; caps per run and per day; a kill switch (flag file and pause label); an optional dry-run mode (not a requirement); a daily summary; a trail comment on every release.
- Every poll, skip, failure and release is logged: a rotated log and an append-only decision log with the ranking inputs and the judgement; the budget view shows the decision log; no tokens and no task text beyond title and number.

## Consequences
- A project opts in with its own exclusion list and quota feed; the project's instruction file records the delegation.
- The job is reversible by the kill switch at any time.

## Amendment 2026-11-20 (owner, question PC-36)
The boot-filling job, as an incarnation of the Orchestrator, may store the result of the authority analysis with the task: a marker comment, the label `needs-manager` (the Manager must resolve it) or `needs-pa` (the Personal Assistant can resolve it) when the task is flagged, and the linked question issue (labels `type:decision` and `question`). It still never sets `released`, a priority label, a deadline label or `cancelled`. These writes are part of the delegation limits and are logged like every other action of the job.

