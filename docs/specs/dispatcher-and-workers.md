# Dispatcher and Workers: specification of the unattended-worker spike

Status: draft, owner-authored
Date: 2026-11-23
Source of truth: this file (owner decision, 2026-11-23; the earlier Google Doc is no longer maintained)
Modules: taskqueue

Every requirement below is the owner's own statement or was approved by the owner. The sub-points carry letters because the text refers to them (for example 3i, 4f, 4q).

## Goals and Scope

1. Sorting out the unattended agent sessions: Claude Code running unattended, picking unresolved tasks from a queue while the owner is away from the keyboard, so that his quota is used.
2. This brief is for a spike of the build: a small dispatcher that syncs the unattended workers.
3. This work is part of Project Cockpit.
4. The CI infrastructure is out of scope for the spike.

## Architecture and Requirements

**1. Roles.** The roles are Workers (headless agents) each of which works on one Task, the Dispatcher (a deterministic procedure), the Interactive Session Agent (merges the Workers' results) and the Owner acting via the Cockpit GUI.

**2. Tasks**

- 2a. The states of a task are embryo, released, WIP, unmerged, failed and merged.
- 2b. An embryo created by the Interactive Session Agent counts as "released" once 10 days have passed since its creation; an embryo created by the Owner counts as released at once. A task filed by a scheduled job (the daily upkeep job) is an unreleased embryo that the Owner releases; it does not go through the mop-up agent. Questions and decisions are never released automatically. A task about the Task Queue itself (its Dispatcher, Workers, image, proxy or specification) carries the label `interactive-only` and is never given to a Worker: it is worked outside the queue, by the Interactive Session Agent, as a high-priority task (owner, 2026-11-24). A task filed by an agent because it found a file that contradicts a standing policy counts as released at once; the agent that found the contradiction does not work on that task. The author of every task is recorded as a label: `author:owner`, `author:interactive` or `author:job` (a scheduled job); a task without an author label counts as the Owner's.
- 2c. Only the owner or an interactive agent can change the status of a task once it is "merged" or "failed".
- 2d. Task metadata lives in GitHub, which is its single source of truth. A task is identified as `<repository short name>-<issue number>` (for example `cockpit-44`); the part before the issue number is the task's project key (`cockpit`), the same string in every place that names a project. One Task Queue serves all projects (owner, 2026-11-24); its states WIP, unmerged, failed and merged are the labels `wip`, `unmerged`, `failed` and `merged` on its issue (embryo and released as before).
- 2e. The Interactive Session Agent sets a task to "merged" after merging its branch into main.
- 2f. A Worker may suggest one task per run: it writes the suggestion into its good-bye note (4o) as `suggested_task`. A suggestion is a pre-embryo: the Dispatcher does not file it; the Interactive Session Agent that mops up decides whether to file one or more tasks from it.

**3. Dispatcher**

- 3a. The Dispatcher's function is to assign tasks to Workers. Any "released" tasks can be assigned. The Dispatcher follows the priority and takes into account deadlines and blockers. The ranking is one global ranking across all served projects (owner, 2026-11-24): by the existing rule, with the tasks of the Project cockpit first; the tasks of all other projects are equal among themselves. There is no cap per project.
- 3b. The Dispatcher runs on the host, not in a container.
- 3c. There is one Dispatcher job for all projects. It is started by the host's scheduler (on macOS: launchd) every 2 minutes; this can be changed via "Settings" in the Cockpit UI. At each firing it reads from the settings the list of served projects (6a) and serves only those; a project that is not listed is never touched: no task of it is read, ranked or started, and nothing of it is written. Today the list holds the Project cockpit only; other projects are added by the owner in the settings (owner, 2026-11-24). It can also be disabled there. At each firing it starts at most one Worker, so the Workers are started one by one, whatever project the task belongs to.
- 3d. The Dispatcher puts a git bundle of the current main of the task's repository (the one its issue belongs to) and a task file (the task's title and text) into the Worker's `inbox/` (see 4q).
- 3e. For each Worker whose process has ended, the Dispatcher fetches the Worker's git bundle, reads its good-bye note, and the task becomes "unmerged" if the bundle holds at least one commit and the note's `outcome` is not "gave_up", otherwise "failed" (reasons: no commit in the bundle, or the Worker gave up; a missing or unreadable bundle counts as no commit). A missing or unusable note fails the task. A Worker that gives up may still deliver partial work: its bundle is fetched in any case, so that the partial work is available on `worker/<task-id>`. The fetched branch is created in the task's repository on the host under the name `worker/<task-id>`; the Dispatcher does not push it or open a pull request.
- 3f. As of now, no retries of failed jobs are foreseen; this can be changed via "Settings" in the Cockpit UI. A task whose Worker gave up is never retried automatically.
- 3g. At each Dispatcher firing a heartbeat line records: the time; the Workers running and what the firing did (started a Worker, nothing to do, or skipped, with the reason); the host's memory pressure level, free disk on the data volume, CPU load average and power source (AC or battery); the size of the container tool's data folder, and the container's memory and CPU use against its limits, if the container tool can report them. The line is written to the processing log.
- 3h. The Dispatcher also monitors the quota. Ideally, this shall be from the proxy counts for each Worker. If, for whatever reason, this is not feasible, it maintains running estimates which start from the last interactive session and are incremented with the tokens reported in the good-bye notes. The quota utilisation is just statistics visualised in the UI. At this stage, no decisions are to be based on it.
- 3i. A Worker is a "runaway" and is killed by the Dispatcher if it has run more than 40 minutes since its launch or has made no record for more than 20 minutes. The task is marked "failed" (reasons: over 40 minutes, no activity). Both limits can be changed in the Cockpit UI; the minimum allowed there is 30 minutes for the first and 10 minutes for the second. Time during which the host sleeps (detected as a gap between firings much longer than the interval) does not count towards either limit; the Dispatcher records the sleep in the processing log and the heartbeat.
- 3j. If a Worker has not written "started" within 1 minute of launch, the Dispatcher kills it and marks the task "failed" (reason: no start). This limit can be changed in the Cockpit UI; the minimum allowed there is 1 minute.
- 3k. At each firing the Dispatcher starts the proxy on its host socket if it is not listening, then starts the container if it is not running and starts the relay inside it if it is not running. If any of them cannot be started, it skips the firing and records the reason.
- 3l. The start check (3j) is made at each firing, so a Worker that has not written "started" is killed within one firing interval after the 1-minute limit.
- 3m. The Dispatcher does not tear down the Workers' workspaces. Old task folders are mopped up later.
- 3n. If the free disk space on the data volume is below 10 GiB, the Dispatcher skips the firing and records the reason. A skipped firing starts no new Worker; finished Workers are still collected and runaway Workers are still stopped.
- 3o. The Dispatcher stores the launch time of each Worker. A Worker's last record is the time of its last request through the proxy; if the proxy cannot tell, it is the last-modified time of the agent's record of the session (with Claude Code: its session transcript). The runaway checks (3i) are made at each firing, so a Worker may exceed its limits by up to one firing interval.
- 3p. Every call the Dispatcher makes to the container tool has a time limit. If a call exceeds it, the Dispatcher skips the firing and records the reason.

**4. Workers**

- 4a. Up to 6 Workers run at once, in total over all projects: one container and one Worker pool are shared by all projects (owner, 2026-11-24) (owner, 2026-11-24, raised from 3 for production; a further raise to 10 is de-prioritised until the image is rebuilt for it); this can be changed via "Settings" in the Cockpit UI, up to the number of `workerN` users defined in the image.
- 4b. All Workers run in one container, at any time.
- 4c. The Workers run in one long-lived container with an init process, no network interface, a fixed memory (4-6 GB) and CPU limit, and one volume for the worker area; the container runtime sits behind one interface in the Dispatcher so that it can be replaced. The current runtime is Apple's native macOS `container` tool, using plain `container run` (not `container machine`) with `--init`, `--network none`, `--memory`, `--cpus`, and no `--ssh`.
- 4d. The proxy runs as long as there is a released task that is not yet WIP, or a Worker is running (a running Worker needs it). The Dispatcher starts it when that is the case and stops it when it is not; while it runs it keeps the host from sleeping (on macOS: it is wrapped in `caffeinate -i`).
- 4e. The image has no sudo; the Dispatcher starts each Worker through the runtime as user `workerN` (with Apple's tool: `container exec --user workerN`), using a `workerN` that no running Worker is using, under an in-guest `timeout` of twice the runaway limit (80 minutes by default) as a backstop if the Dispatcher is down. The runtime also tells whether a Worker's process has ended and kills it (3e, 3i, 3j).
- 4f. A host Unix socket (its path shorter than 104 bytes) is made reachable inside the container by the runtime (with Apple's tool: mounted with `-v`, relayed over vsock); inside, a tiny relay exposes it on `127.0.0.1`, and the Workers' agent points its model-API base URL there (with Claude Code: `ANTHROPIC_BASE_URL`).
- 4g. On the host, a small reverse proxy listens only on that socket, forwards only the model API's message requests (with Anthropic's API: `POST /v1/messages` and `count_tokens`) to the model API, strips any client credential and adds the credentials corresponding to the owner's subscription; each Worker holds its own dummy token, so that the proxy can tell the Workers apart.
- 4h. The token usage can be inferred from the proxy, and this shall be done.
- 4i. The Workers' environment switches off the agent's non-essential traffic and gives each Worker its own configuration folder; with Claude Code: `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1`, `skipWebFetchPreflight: true` or the WebFetch tool denied, `ENABLE_CLAUDEAI_MCP_SERVERS=false`, `CLAUDE_CODE_DISABLE_ARTIFACT=1`, `CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL=1`, and a per-Worker `CLAUDE_CONFIG_DIR`.
- 4j. The Workers run as unprivileged users.
- 4k. Each Worker works in its own separate clone, without hardlinks.
- 4l. The Workers clone into a folder named after the task (`<task-id>/clone`) on the container's own disk or a named volume, so that clones of different tasks never overwrite each other; the bind mounts only hand work in and results out.
- 4m. The agent's CLI, python, pytest and git are baked into the image.
- 4n. The Worker confirms that it has picked a task up by writing the file `outbox/started` (its content is ignored); the Dispatcher picks the file up and sets the status of the task to "WIP".
- 4o. The Worker composes a good-bye note, `outbox/goodbye.json`, a JSON object. Mandatory fields: `outcome` ("done" or "gave_up": the Worker's own assessment; "gave_up" fails the task, any other value is not used by the Dispatcher, and the Interactive Session Agent that merges takes it into account but is not limited to it) and `summary` (the work done, up to 100 words and 400 characters; mandatory whatever the outcome). Optional fields: `any_assumptions` (assumptions not stated in the task brief, the skill and agent files or the codebase), `any_limitations_or_caveats` (limitations or caveats not stated there), `any_open_questions`, each of the same size as the summary, `suggested_task` (a task the Worker suggests, text of the same size as the summary; see 2f), and `tokens` (a whole number, the tokens consumed: the sum of the input, output, cache-creation and cache-read tokens; written by the end check (4t) from the agent's record of the session (with Claude Code: the session transcript), not by the Worker, so that it can be compared with the proxy's count).
- 4p. The Worker writes a git bundle of its branch `worker/<task-id>` into `outbox/result.bundle`.
- 4q. Each task has a folder `<worker area>/<task-id>/` with two subfolders: (1) `inbox/`: `main.bundle` (the current main of the task's repository) and `task.md` (the task's title and text), written by the Dispatcher; (2) `outbox/`: `started`, `goodbye.json` and `result.bundle`, written by the Worker.
- 4r. The Dispatcher launches each Worker with a fixed instruction: read `inbox/task.md`; work in your own clone; write `outbox/started` first; at the end write `outbox/goodbye.json` and `outbox/result.bundle`.
- 4s. A Worker's only way out of the container is the proxy socket. Before it starts a Worker, the Dispatcher checks that a connection from the Worker's user to an outside address (for example 1.1.1.1 port 443) fails; if it succeeds, the Dispatcher skips the firing and records the reason.
- 4t. The note is both a skill and an end check. The Workers' skill (with Claude Code: a skill in each Worker's configuration folder) states what the note contains and when it is written. An end check, a script baked into the image (with Claude Code: a Stop hook), checks before a Worker may end: the note is a JSON object; `outcome` and `summary` are present; the fields are of the right type and size; and, unless the outcome is "gave_up", the clone holds at least one commit beyond main and `outbox/result.bundle` exists. If a check fails, the end check refuses the stop once and tells the Worker what is missing; if the check fails again the Worker ends, and the Dispatcher fails the task by 3e.

**5. Processing log**

- 5a. Each significant change of state related to Workers and the Dispatcher is recorded in the processing log; each entry names the task, its project (the project key, 2d) and its task folder, and carries its time as ISO local time with the offset (`2026-11-25T09:59:01+02:00`; entries before PC-134 have no offset and are the host's local time).
- 5b. Every failed task records its reason in the processing log: no start, over 40 minutes, no activity, no note, an unusable note, no commit in the bundle, or the Worker gave up.
- 5c. The processing log is stored where the processing occurs, on the owner's host (currently a MacBook). The processing data of all projects lives in one place, one data root (owner, 2026-11-24, constitutional): the one processing log, the Dispatcher's state, the proxy socket and usage file, the quota history and the Workers' area, with a subfolder per project where the interface contract (`docs/contracts/queue-interfaces.md`) says so (the task folders of 4q lie under `area/<project>/`). The root is an argument of the Dispatcher; its default is `~/.local/share/project-cockpit/task-queue`.
- 5d. The processing log is not part of any git repository.
- 5e. The processing log is backed up on GDrive once a day. The access details are stored in a configuration file which is not part of the git repository.

**6. Settings**

- 6a. Settings (the Dispatcher's interval and on/off switch, the list of served projects (3c), retries, the number of Workers and the three limits of 3i and 3j) are stored in a configuration file in the Cockpit's local data folder, outside git.
- 6b. The Dispatcher reads the file at each firing.

**7. Open points of the one queue** (not decided; not answered here)

- 7a. Open: whether and how Worker kinds differ (PC-69).
- 7b. Open: whether a project may have its own image recipe, and so its own container (PC-71); until decided, one image and one container serve all projects.
- 7c. Open: whether a Worker is bound to one project (PC-72).
- 7d. Open: a Linux machine as host (PC-73).
- 7e. Open: the project field of an entry that belongs to no task (the heartbeat, 3g).
- 7f. Open: where the settings file lives once there is one data root (6a says: the Cockpit's local data folder, outside git).

## Approach

1. The first step was done on 2026-11-23 with Apple's `container` tool 1.7.1 on macOS 15.3.1, a macOS version the tool does not support (it supports macOS 26): a container started with no network interface reaches the host proxy through the socket relay (4f). The Workers have no other way out, so no egress policy is needed beyond 4s. A replacement runtime, or the same tool on macOS 26, repeats this check before use.
2. If feasible, this would be a major re-work of the codebase of Project Cockpit.
3. Eliminate anything from the codebase which contradicts a specification set in this document.
4. When implementing, follow the philosophy of minimalism ("less is more").
5. If you find that important functionality or edge cases are left out in this spec, please note them.
6. If you believe that these are of critical importance, interrupt the work and seek input from the owner. Do not proactively implement code that is not based on the requirements here or explicitly approved by the owner.
