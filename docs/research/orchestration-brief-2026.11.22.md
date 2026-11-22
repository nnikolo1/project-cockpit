# Research brief: orchestration of unattended assistant work

Status: brief for two independent studies, 2026-11-22. Generic wording on purpose (public repository).
Modules: docs

## 1. Why this study exists

The owner is a solo developer who builds software with AI coding assistants, across several projects, now and in the future. The Project cockpit (CP) exists to make that way of working manageable, and the question in this brief is a cornerstone of it: how work is scheduled, run, limited and integrated when the assistants run without the owner present. The answer decides the design of CP, of the first project that uses it, and of every later project, because CP is also the template new projects start from.

## 2. What the Project cockpit is for

- A thin, local client. It shows, for each project, the tasks (hosting-service issues), the open questions waiting for the owner, the rules in force, the state of the unattended work and metadata about the code (activity, persistence footprint, ambiguity). It holds no project data of its own; a project supplies its data through generic contracts (files and the hosting service). Its own state is a cache that can be rebuilt.
- The template for all of the owner's assistant-supported projects: generic agent policies, skills, instruction-file sections and tooling live in CP; a project starts from them and adds its own.
- Principles the owner has set and that any answer must respect: adopt over build (use what the community already solved); no single source of truth (every fact has exactly one owner and is never copied; a disagreement is reported, not auto-corrected); fail clearly (every dead end is a visible state with a reason and a named action, never silent); every state and transition explainable in one plain sentence; least privilege by role; the owner keeps authority over what matters (releases, schema changes, deletions, approvals) and is told, not surprised.

## 3. The goal: unattended execution

The point of the exercise is to use the assistants' usage quota (a 5-hour window and a weekly window, both expiring) and the owner's absence (nights, away from the keyboard) to reduce a backlog of tasks automatically, safely and visibly. A background job (the Orchestrator, running every few minutes on the owner's laptop, which can sleep) checks whether any other assistant session is active and whether quota is about to expire; if so it releases ranked tasks and starts workers, within limits the owner delegated. The owner returns to a clear state of play: what was released, what is in progress, what is waiting for acceptance, what dead-ended and needs a human, and why.

Unattended means there is nobody to answer a prompt, approve a tool call or notice a mistake. Therefore: workers run with the narrowest rights; expected dead ends (a decision, a privilege, a conflicting rule) are an accepted limitation and must stop cleanly and be shown plainly; results are integrated later by a privileged agent or the owner (the manager and janitor pattern), which can take days.

## 4. Roles and states

- Roles, a strict ordering of rights: the Manager (the owner); the Personal Assistant (does what the owner asks, including formulating tasks); the Orchestrator (releases within the delegation, analyses tasks, files question issues; the background job is an incarnation of it); the Worker (the narrowest: takes up one task, marks it in progress as its first action, commits in its own git worktree; no push, no pull requests, nothing privileged).
- States: released (cleared; a queue entry; constrains nothing) and in progress (from the worker's start until a privileged agent has accepted or rejected the result; it holds resources and limits the choice of other tasks).
- Static clearance (is this task allowed to be worked autonomously at all: privilege, conformance with standing decisions) is judged once, before release; dynamic checks (rank, quota, interference, back-pressure, idle state) are judged at every start. This split is a recommendation under discussion, not settled.

## 5. The problem to solve, as needs

1. Compute sets of tasks that may run at the same time without interfering. Conflict only if one writes what the other reads or writes (resource keys with read or write modes: files, folders, database tables, configuration entries). The read and write sets are only estimated from the task text before the run. The policy must not be too restrictive. Interference is evaluated against the in-progress set only.
2. Never run a task twice; no automatic retries; idempotent starts.
3. Back-pressure: a limit on unintegrated results, so that production does not outrun integration.
4. Lazy execution: materialise only what is ranked and needed.
5. A small concurrency limit (a few workers) and an externally supplied budget gate (the assistants' quota, evaluated every few minutes; the reading can be stale and is then estimated).
6. Human approval nodes, which may wait for days.
7. The hosting service owns the task state; a small local journal of the job's own actions is acceptable; no mirror of hosting data.
8. Minimal infrastructure and maintenance: one machine that may sleep, tens of tasks, no cluster, no always-on server if avoidable, permissive licence.
9. A status display that stays a thin client.
10. Failing clearly.

## 6. A failure that motivates the study

A hand-built scheduler (a queue in a file, guards in code, labels as state, a poll every 10 minutes) released and ran the same tasks four times in one night: the hosting service silently returned incomplete timeline data for large queries, labels looked unauthorised and were dropped, and finished tasks looked unreleased again. 77 worker runs, unintegrated pull requests piled up, quota was burned. Earlier the same night an unattended worker wrote rows into a real database through a symlink. The lesson the owner draws: the scheduling, state, idempotency and concurrency parts are generic and should be adopted from existing frameworks, not hand-built; the owner believes the problem is a computation graph with lazy execution, a problem handled by many orchestration frameworks and, in the AI-agent world, by agent frameworks.

What stays specific to this problem and will remain custom in any case: the quota and idle logic, the authority analysis of tasks (privilege and standing decisions), and the worker harness (tool lists, rules, worktrees, the keep-awake step).

## 6a. Pending design questions the study must inform

- Whether flow state should live in a small local journal (and what exactly it may hold) or stay in labels and the hosting service.
- A salvo computation: building sets of combinable tasks, with a permissive notion of interference.
- Where control is exercised: before a task is released, or before it is set to in progress.
- Related, not part of this study: identity separation on the hosting service, database roles, and an operating-system sandbox for workers.

## 7. What each study delivers

One report file: (a) a fit-gap table of the serious candidates against needs 1 to 10; (b) a recommendation: a general workflow engine, an agent-specific framework, or concepts plus a very small custom scheduler on top of a library, with reasons and the smallest adoption path; (c) what the current custom job would be replaced by and what must stay custom; (d) the effect on the three pending design questions; (e) risks and open questions, including how the recommendation could be wrong; (f) sources.

Method: find the prior art yourself and do not limit yourself to well-known names (non-exhaustive starting points: general workflow and orchestration engines, build systems and dataflow schedulers, AI-agent and multi-agent frameworks, tools that run parallel coding-agent sessions in git worktrees and how they decide what may run together or predict file conflicts, research and write-ups on dependency-graph planning for LLM agents); cite sources with URLs and dates; mark anything unverified; never invent features or versions; challenge the premise where warranted; say plainly where a candidate would be over-engineering for tens of tasks and how it behaves on a laptop that sleeps.

## 8. How the decision will be taken

Two studies run independently and do not see each other's work. A third, isolated reviewer compares them and attacks the points they agree on. The owner decides. A spike then runs three real tasks end to end on the chosen option without custom guards to test whether it removes the failure classes above (a task run twice, silent loss of state, no back-pressure). The decision becomes an architecture decision record of CP and part of the template.
