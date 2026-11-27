# Owner defaults for all projects

Preferences the owner wants as defaults for every project the cockpit shows. When the owner says "this applies to both projects" (or to all projects), the preference lands here in generic wording, in the project's own instruction file, and, where it concerns assistant behaviour, in the owner's user-level instructions. A new project starts from this list; a project may override an item and says so in its own documentation.

## Questions and tasks
- Every open question for the owner is an issue (labels `type:decision` and `question`, an embryo, with the question, the options, a recommendation and the provenance block) in the repository it belongs to; the cockpit lists them with their age; the answer is a comment, the decision is logged, the issue is closed with a link to the record.
- A request for a metadata metric about code or persistence becomes two tasks: one in the cockpit (default preferences, data contract, display) and one in the project (the collector). Both are embryos; the project task is on the project board and links to its counterpart.
- Only the owner releases tasks; an assistant releases or reprioritises only on an explicit request naming the task, unless the owner has approved a standing delegation with stated limits (see the boot-filling job).

## Module cards (display defaults)
- Persistence footprint per module and sub-module: tables (rows, owner), files and folders (inside and outside the repository, personal-data marker), logging (loggers, files, rotation, what is never logged), statistics (records, sizes, input volume per day, week and month), backup and retention policy.
- Code-change metrics for the last 30 and 90 days: commits, files, lines added and removed, net, churn, source/tests/docs split, active days, trend against the previous window; parents show their own files and the total; colour by relative rank of churn (hot to cold, neutral for no change), never colour alone, with an option to colour relative to module size. Activity colours (hot to cold, dormant grey) default to churn relative to module size (owner decision, 2026-11-20); rank of absolute churn is the option.
- Assignment ambiguity: files per sub-module, the module's unsorted files with candidates and reasons, a marker when many files are unsorted, the files that are ambiguous between modules on the overview.
- The files that are ambiguous between sub-modules of a module are listed as notes on that module's card (all of them, each with its candidate sub-modules and the reason), because by the module-map rule they stay in the module itself.

## Module map rules
- One owner per file; a file used by several modules goes to its main user and is shown in every module that uses it, marked as shared.
- A file that cannot be assigned unambiguously to a sub-module belongs to the top-level module (its unsorted part). A file that cannot be assigned unambiguously to a module stays explicitly unassigned with its candidate modules and is reported to the owner.
- A memo or ADR clearly about one module or sub-module stays with it; generic ones (several modules, or the project itself) have a generic home and are listed under every module they name.
- Documents are assigned through a `Modules:` line; superseded documents move to an obsolete-documentation folder with a banner naming the replacement; decision records are never moved.
- Every project has an `Internal` module (owner, 2026-11-20): what keeps the project and its assistants running, as opposed to the product itself. Typical sub-modules: Boot-filling (the job, its tests and footprint), Project metadata (the module map, the footprint declaration, the card-metadata collectors with their tests), DevOps (CI workflows, scheduler templates, install and deploy scripts, runtime settings, hosting-service policies; an existing Infrastructure sub-module is the same thing, DevOps is the template name), Agent policies and skills (standing instructions, skill and agent definitions). Files of such kinds that fit no sub-module stay in `Internal` itself.

## Data and change discipline
- Commit what a person decides; generate what a script derives, into a local folder, never into git; cards show the age of generated data.
- A change to a database schema needs the owner's explicit approval of the exact statement, one approval per change; ordinary data and code changes do not.
- Anything written into a repository is sanitised first (no secrets, personal data, names of private individuals, home paths) and the owner's phrases are corrected for spelling and grammar before they are written; a public repository additionally names no consuming project.
- A short-cut taken to reach a first version gets a dated note: what, why, limits, first step to do it properly.

## Standing automation (when approved by the owner)
- Dispatcher: a deterministic procedure, started by launchd every 2 minutes (changeable in the Cockpit settings, can be disabled), that starts at most one Worker per firing and assigns released tasks to Workers by priority, deadline and blockers. Workers are headless agents running in one container with no network except a host proxy; each works on one task and hands back a git bundle and a good-bye note. The task is then unmerged or failed, and the Interactive Session Agent merges. Quota use is statistics only; no decision is based on it. The full definition is in `docs/specs/dispatcher-and-workers.md`.

## Briefs for background agents

- Every requirement in a research or coding brief given to a background agent is traced to an utterance by the owner, or has been explicitly approved by the owner (owner, 2026-11-23; a default for all projects). It has repeatedly happened that briefs were heavily polluted by requirements invented by an assistant, which led to results heavily distorted compared to what the owner required.

## Authority analysis of tasks

Authority analysis of tasks (owner, 2026-11-20; a default for all projects): every task is analysed IMMEDIATELY when it is formulated or edited (at filing time, before it is reported filed or can be released; never lazily at release time) for (a) privilege escalation (it needs a privilege the actor lacks) and (b) conformance with the existing ADRs and standing rules (it would contravene one). The analysis state is visible per task: not analysed, clear (date and version of the rule set and ADR list checked against), flagged (with a link to its question), or stale (the task text or the ADR set changed since). A task that is not analysed is not releasable, by automation or by the Submit action. A flagged task gets a concrete question filed as a question issue linked as a blocker; the submission form poses that question, the owner answers it there and releases the task personally, resolving the issue in the same step; release is refused while the question is open. Automation (the boot-filling job and its workers) never releases a flagged task. Workers file a question issue only when an existing ADR or standing rule makes the solution impossible or when they need a privilege they lack, never a task; a design choice inside the existing ADRs is theirs to make. The analysis is a duty of the Orchestrator (the role that orchestrates the agents, the boot-filling job being an incarnation of it): it analyses every task as soon as the task exists, whoever filed it; tasks are created only by the Orchestrator and the Personal Assistant, and each analyses a task it formulates in the same turn before reporting it filed; a plain Worker cannot create tasks and has no duty here (owner, 2026-11-20). The same duty applies to the Personal Assistant (owner, 2026-11-20). The Orchestrator may store the analysis result with the task (a marker comment, the label needs-manager (the Manager must resolve it) or needs-pa (the Personal Assistant can resolve it), the linked question issue; never the privileged labels released, priority, deadline, cancelled). Built by the cockpit task PC-35; until then it is applied by hand.

## Substantial coding work is split among sub-agents (standing rule, owner, 2026-11-23)

For any more substantial coding task the assistant attempts to split the work among several sub-agents (disjoint modules or files, each in its own worktree and branch, with an agreed interface between them). The aim is to force clean seams and avoid silo architecture and convoluted code, which the code-quality metrics flagged as weak points. A task that cannot be split is reported as such, with the reason, and then done by one agent.

## The UI may narrow what a parameter can be set to (standing rule, owner, 2026-11-24)

A settings page lets the owner change the behaviour of the system without a code change, but it may restrict the range a parameter can take: not every value that the code accepts is reachable through the UI (for example a minimum for a time limit). The restriction is a rule of the UI, not of the code, so the code accepts any valid value in the settings file. This is part of the overall design policy.

## Who may create tasks, and when a task counts as released (provisional, owner, 2026-11-24)

Accepted for the time being, to resolve the ambiguity about task creation:
- The Interactive Session Agent may create tasks. Such a task counts as released once 10 days have passed since its creation (the Dispatcher computes this; nobody sets a label).
- A Worker may suggest at most one task per run, so whatever it wants to suggest goes into that single suggestion, a pre-embryo in its good-bye note. The Dispatcher does not file it; the Interactive Session Agent that mops up decides whether to file one or more tasks from it.
- The author of every task is recorded (who created it: the owner, an interactive session, or a scheduled job), so that the rules above can be applied and checked.
This replaces the earlier statement that tasks are created only by the Orchestrator and the Personal Assistant. The release privilege itself (the owner releases embryos) is unchanged apart from the 10-day rule above.

## Standing policies: stale components are refreshed; findings are filed, not fixed by the finder (owner, 2026-11-24)

- **Refresh within 24 hours (owner, 2026-11-24).** The components of the project metadata, incl. code metrics, token usage, etc, are to be refreshed within 24 hours: whatever is older than that without a reason counts as stale and is refreshed, by the daily job where mechanical, otherwise by a task.
- **Refresh, not only report.** When a component of the project is found to be stale (the generated card data, the code snapshot behind the card lines and the Quality view (a project with none configured is reported as a gap), the module map, the footprint declarations, the Workflow drawing, the screenshots and the like; the README is not part of the check, owner, 2026-11-24), it is refreshed. A staleness report is filed in the repository, but the report is not the goal: the refresh is. What is mechanical is refreshed by the daily job; everything else becomes a task. The daily job also spots gaps (a module metric that shows no data, such as missing test coverage or a missing snapshot) and files them as tasks (owner, 2026-11-24). A gap the owner reports is covered by a generalized, project-agnostic test in the same change that fixes it, never only fixed or only tested for one project (owner, 2026-11-24).
- **Separation of powers.** If at any time an agent finds that a file (documentation or code) contradicts a standing policy, it may file a task, and that task counts as released at once. The agent that found the contradiction is not allowed to work on that task itself: the finder files, someone else works. The separation of the filing from the working is a check and balance, like the separation of the Dispatcher, the Workers and the owner.

## Production: all tasks go through the Task Queue (owner, 2026-11-24)
- All tasks are submitted via the Task Queue: filed as issues, released by the owner, worked by the Dispatcher's Workers (up to 6 at once; 10 is de-prioritised); the assistant files, analyses and merges and does not start its own background agents for new work unless the owner says so.
- Tasks about the Task Queue itself (owner, 2026-11-25, replaces the rule of 2026-11-24): the label `interactive-only` was removed and can no longer be set; a bug or task about the Task Queue goes through the queue like every other task.
- The Project cockpit is the top-priority project unless the owner says otherwise: it is a meta-project that provides the bedrock for all projects, so its tasks rank before other projects' tasks in the shared Task Queue and its work comes first; all other projects are equal unless the owner says otherwise (owner, 2026-11-24).
- The host operating system does not dictate the architecture: do not jump through hoops to fit one operating system or one version of it; borrow over build, and choose the host that suits the borrowed parts (owner, 2026-11-24).
- Structure policy (standing rule, owner, 2026-11-24): when more than 30 percent of a module's files are unsorted (kept in the module because they fit no sub-module unambiguously), that is a policy violation: the daily job finds it and files a policy-finding task (released at once; the finder does not work on it) whose work is an analysis of the module's structure that writes suggestions (sub-modules, assignments) for the owner; it never changes the module map itself.

## Objective: a Swiss Army knife, in a nutshell and opinionated (owner, 2026-11-24)

The Cockpit is a pocket-size Swiss Army knife for agent-assisted software development, in a nutshell and opinionated: several tools combined (continuous integration, quality gates, a queue, an agent manager, an artefact store, policy checks), each at a minimal level, as plain files and small commands, no heavy framework. A project that adopts the Cockpit gets this stance as its default; where a choice has to be made, the Cockpit makes it and says so.

## Policy findings may be released by the interactive agent (owner, 2026-11-24)

A policy violation identified by the interactive agent may be filed and released by it as a task, to be worked by a background agent (a Worker); the finder itself never works the task, so the separation of powers holds. The release applies only to tasks labelled policy-finding that carry the policy sentence and the evidence, with a trail comment naming the policy (owner, 2026-11-24; amends the policy-finding rule of the same day).

## The Interactive Agent merges unmerged tasks (owner, 2026-11-24, constitutional amendment)

The Interactive Agent has the authority to merge tasks in the state `unmerged`, and does so unprompted. Before merging it verifies (1) that the commit matches the task brief and (2) that it obeys all policies (tests passing included). If both hold, it merges. If no policy is violated and no test fails but the commit does not meet the brief fully, it merges and files a task meant to finish the job. If a policy is violated or a test fails, it does not merge: it formulates a new follow-up task whose work is to remove the existing issues (owner, 2026-11-24, constitutional amendment). The Interactive Agent may delegate the merge to a background agent by supplying it with this brief; for that merge the background agent holds the privileges of the Interactive Agent, so it can merge and formulate tasks; in this way the Interactive Agent can parallelise the merging (owner, 2026-11-24). Merging is parallelised whenever conflicts can be avoided (branches that touch disjoint or cleanly combinable files go to separate merge agents at once; the others are merged in turn) (owner, 2026-11-24, standing policy).

## Daily reports are a constitutional policy (owner, 2026-11-25)

Every project the Cockpit serves has a daily session report (owner, 2026-11-25, constitutional policy): a dated, terse, factual log of what happened that day, in the project's report folder (default `docs/session_reports/session-report-YYYY.MM.DD.md`), opened at the start of a session, appended to at idle moments, and closed out completely when the date changes. The day's token statistics, as granular as the data allows (totals, per hour, per source, per model, per session, the Workers' proxy usage, the vendor quota readings, the age of each input), are generated by a script, never typed by hand, into the project's data folder (`daily-token-stats/YYYY-MM-DD.md`) and the report names that file; generated and processing data never goes into a project's git repository (owner, 2026-11-25), so the report holds the narrative and the pointer, not the numbers. The policy harness checks it: a day with activity and no report, or a report without its token statistics, is a violation that files a task.

## Local policy files are never overwritten automatically (owner, 2026-11-25)

Local policy files are never overwritten automatically (owner, 2026-11-25): violations the compliance check finds in a project's local policy markdown files are highlighted in the Cockpit UI, and resolving them is a human-in-the-loop procedure; the check, the daily job and every agent only read these files, a fix is the owner's own act, and a Worker may at most prepare a proposed text.

## Supported platform: the installed Python (owner, 2026-11-25, standing policy)

Only the Python version installed on the owner's machine is supported and tested (owner, 2026-11-25, standing policy): code is developed and tested on that one version only, there is no compatibility matrix across versions or operating systems, and the documents name that version. When the installed version changes, the documents and the test setup follow it in the same change. The installer does not refuse another version: it finds the latest Python installed on the machine, uses it, and warns that it is not the tested one (today the tested version is Python 3.14) (owner, 2026-11-25).

## Blocked tasks are not assigned (owner, 2026-11-25, constitutional amendment)

A released task that is blocked by a task which is not yet merged is never assigned to a Worker (owner, 2026-11-25, constitutional amendment): a task's blockers are its relations "blocked by"; the task counts as blocked until every blocker is merged (a blocker that is open in any other state, failed or cancelled keeps it blocked until it is merged or the owner removes the relation); the Dispatcher skips blocked tasks when it chooses, and the Cockpit shows them as blocked. A blocked task may be released: release states the owner's decision, the blockers state the order.

## Every UI action has a test (owner, 2026-11-25)

Every action the UI offers is covered by a test (owner, 2026-11-25): each server action and each page action has at least one test that exercises it (the happy path and the refusal), a test enumerates the actions the code defines and fails for an action no test names, and a new action ships with its tests in the same change; an action without a test is a policy violation that the policy harness reports.

## Generated data stays out of git (owner, 2026-11-25)

Data that is derived or produced by processing (reports of checks, statistics, snapshots, logs, caches) belongs to the Cockpit's data folder, not to a project's git repository; a repository holds what a person decided and wrote. A generated file is written into a repository only when the owner has decided so for that file.

## Design talk is not a decision (owner, 2026-11-25, standing rule)

When the owner reflects on a design ("I am not sure", "I feel", "I think", thinking aloud), the assistant answers the discussion, gives a reasoned view and options, and logs the exchange in the feedback log as a design discussion; it writes no decision record, no constitution or policy text and no task comment, files no task and changes no task until the owner says it is decided ("decided", "file it", or an explicit instruction); when it is unclear whether a remark is a decision, it asks once (owner, 2026-11-25, standing rule).

## Turns end with a clear status (constitutional amendment, owner, 2026-11-25)

Every turn ends in a way that makes it abundantly clear whether the assistant is about to work on anything: either it states plainly that it is idle and waits for the owner (and for what), or it states what it is doing next and then does it. Every turn also reports the number of sub-agents currently running and any intention to launch one (owner, 2026-11-25, constitutional amendment; applies to all projects at all times).


## Labels are the owner's (standing rule, owner, 2026-11-25)

Never apply a label that the owner has not proposed himself: not a new label, not an existing one the owner did not name for that purpose (owner, 2026-11-25, standing rule; applies to all projects at all times). Occasion: the assistant had applied the label `interactive-only` on its own initiative; it was removed and can no longer be set.


## No underground scripts (standing rule, owner, 2026-11-27)

Any helper script the assistant creates ad hoc becomes part of the git repository of the project it serves: no script lives only in a temporary or scratch folder; there are no throw-away scripts either: every script is recorded in the repository before it runs, even a one-off, and the usual rules apply to it (tests where it decides something, the code checklist, the module map) (owner, 2026-11-27, standing rule; applies to all projects at all times). Occasion: the housekeeping scripts of a merge run existed only in /tmp, could not be reviewed or tested, and one of them closed three failed tasks as merged.
