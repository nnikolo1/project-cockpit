# 0019. Boot-filling job: a task is released once and worked once, and its state is explainable in one sentence

Status: Superseded by docs/specs/dispatcher-and-workers.md (2026-11-24)
Earlier status: proposed (builder); the owner confirms or changes
Date: 2026-11-21
Modules: internal/bootfilling, internal/cockpit
Amends: [0014](0014-boot-filling-job.md), [0015](0015-boot-filling-implementation-choices.md)

## Context
In the first live night every trigger window released the same early tasks again (four batches of 20, 77 worker runs; each re-release showed on GitHub as `released` unlabeled and labeled again), and the cockpit header jumped from 0 to 30 unauthorised changes.

Verified root cause (read-only, live repository, 2026-11-21): the cockpit fetched issues in GraphQL pages of 100 with a nested `timelineItems(last:100)`. GitHub does not fail on a big page: it silently thins the nested timeline. The same issue, same query, only the page size varying: 2 to 30 issues per page gave all 13 label events of issue 1; 50 gave 7; 100 gave 5 (all of them the oldest, the `released` events missing). The cockpit then held 533 events (1036 with pages of 20). A protected label without an event has the actor `unknown`, so `logic.policy()` flagged it unauthorised, dropped it from the effective labels, `logic.stage()` returned `embryo`, the job found the task eligible and its release path (`_edit`) removed the flagged label and added it again. 30 issues were flagged, all `released`, all actors `unknown`; with pages of 20 none is.

## Decisions
1. **Cockpit fetch.** Pages of `PAGE` = 20 issues (30 verified complete, 50 not). Because the thinning is silent, `repair_events` additionally fetches an issue's own timeline (10 issues per query, cached per issue and `updatedAt`) whenever a protected label has no `labeled` event as its latest one. A label that still has no event stays `unknown` and flagged; a failed fetch is logged and leaves the label flagged. A label with a real event by someone else is a genuine unauthorised change and is not refetched. Limit: a thinned page that still shows an older event could name an older actor; the small pages are what prevents that.
2. **Release guard in the job, independent of the cockpit's policy.** A task is not released when its raw labels contain `released` or `cancelled`, when the decision log holds a real (not dry-run) release of it within `rerelease_guard_days` (default 30), or when it is queued or running. The skip is written once per change as a `release_skip` line (decision log) and a warning (poll log): "already released at <time>".
3. **No relabeling.** `Gh.release` refuses a task whose raw labels contain `released` or `cancelled`, so the cockpit's remove-then-add path is never taken for it. The job never removes an assignment or a label.
4. **Worked at most once.** A task that already has a worker outcome in the decision log (started, finished, failed, could not start) is never started again, without expiry, unless a `requeue` record written by the Orchestrator or the Manager is newer than its last start (`python3 -m bootfill.requeue`, writes the record; `--by` states who decided, phase 1 does not authenticate it). Duplicate queue entries are dropped, each drop recorded as `skipped_already_worked`. Retries are gone: `max_attempts` stays in the config for old files and has no effect.
5. **The worker marks the issue in progress.** The worker's first action (first sentence of its prompt, `core.worker_prompt`) is to assign the issue to the Manager login, which is what `logic.stage()` reads as `in_progress`. The job only checks: at its next poll it looks at the issue's assignees; marked: one `in_progress` record; not marked: one record "worker did not mark in progress" (NEEDS YOU, decision log, poll log, `worker` contract line) and nothing else (no assignment, no retry). The job's guards do not depend on that action.
6. **Acceptance.** When the worker exits 0 and a pull request from its branch exists, the state is awaiting acceptance (record with the PR number). The Orchestrator or the Manager reviews and merges it (the issue closes: completed) or rejects it (closes the PR; the task goes back to them for a decision, never to the queue). Exit not 0, or exit 0 without a pull request: needs you. A worker that cannot be started: nothing changes on the issue, `start_failed` is recorded, no retry.
7. **Budget contract.** A compatible extension line `{"kind": "worker", "state": "in_progress" | "awaiting_acceptance" | "needs_you" | "start_failed", "pr": N, ...}`; `decision_view` ignores it (older readers count nothing new as unreadable).

## States and transitions
| State | What sets it | Who may move it on |
|---|---|---|
| embryo | the task is filed | the Manager (release), or the job (a release, once) |
| released | the `released` label by the Manager login (job or cockpit) | the job starts one worker; never released again |
| in progress | the worker, as its first action (assignee = Manager login) | the worker (opens a draft PR); the job only records it |
| awaiting acceptance | the job records worker exit 0 plus an open pull request | the Orchestrator or the Manager: merge (completed) or reject |
| needs you | the job records a failed worker, no pull request, a worker that did not mark in progress, or a worker that could not start | the Orchestrator or the Manager decide; a redo only by a `requeue` record |
| completed | the issue closes (PR merged) | nobody |

The job never removes a label or an assignment and never reverses a state by itself.

## Consequences
One extra GraphQL call per 10 repaired issues in the bad case; 9 pages instead of 2 for 167 issues. Old worker queue entries in an existing state file are filtered by the never-twice rule.
