# Dispatcher and Workers: specification of the unattended-worker spike

Status: draft, owner-authored
Date: 2026-11-23
Modules: internal/bootfilling

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
- 2b. At the moment all embryos (apart from "questions" and "decisions") are regarded as "released" but this may change in the future. A tentative idea is to have the Owner or an interactive agent session release them.
- 2c. Only the owner or an interactive agent can change the status of a task once it is "merged" or "failed".
- 2d. Task metadata lives in GitHub, which is its single source of truth.
- 2e. The Interactive Session Agent sets a task to "merged" after merging its branch into main.

**3. Dispatcher**

- 3a. The Dispatcher's function is to assign tasks to Workers. Any "released" tasks can be assigned. The Dispatcher follows the priority and takes into account deadlines and blockers.
- 3b. The Dispatcher runs on the host, not in a container.
- 3c. The Dispatcher is started by launchd every 2 minutes; this can be changed via "Settings" in the Cockpit UI. It can also be disabled there. At each firing it starts at most one Worker, so the Workers are started one by one.
- 3d. The Dispatcher puts a git bundle of the current main of the task's repository (the one its issue belongs to) and a task file (the task's title and text) into the Worker's `inbox/` (see 4p).
- 3e. For each Worker whose process has ended, the Dispatcher fetches the Worker's git bundle, reads its good-bye note and decides whether the Worker was successful: the task becomes "unmerged" if the outcome is "done" and the bundle holds at least one commit, otherwise "failed". The fetched branch is created in the task's repository on the host under the name `worker/<task-id>`; the Dispatcher does not push it or open a pull request.
- 3f. As of now, no retries of failed jobs are foreseen; this can be changed via "Settings" in the Cockpit UI.
- 3g. At each Dispatcher firing a heartbeat line records: the time; the Workers running and what the firing did (started a Worker, nothing to do, or skipped, with the reason); the host's memory pressure level, free disk on the data volume, CPU load average and power source (AC or battery); and the container's memory and CPU use against its limits, if the container tool can report them. The line is written to the processing log.
- 3h. The Dispatcher also monitors the quota. Ideally, this shall be from the proxy counts for each Worker. If, for whatever reason, this is not feasible, it maintains running estimates which start from the last interactive session and are incremented with the tokens reported in the good-bye notes. The quota utilisation is just statistics visualised in the UI. At this stage, no decisions are to be based on it.
- 3i. A Worker is a "runaway" and is killed by the Dispatcher if it has run more than 40 minutes since its launch or has made no record for more than 10 minutes. The task is marked "failed" (reasons: over 40 minutes, no activity). Both limits can be changed in the Cockpit UI.
- 3j. If a Worker has not written "started" within 1 minute of launch, the Dispatcher kills it and marks the task "failed" (reason: no start).
- 3k. At each firing the Dispatcher starts the container if it is not running, then starts the proxy on the container network's gateway address if it is not listening. If either cannot be started, it skips the firing and records the reason.
- 3l. The start check (3j) is made at each firing, so a Worker that has not written "started" is killed within one firing interval after the 1-minute limit.
- 3m. The Dispatcher does not tear down the Workers' workspaces. Old task folders are mopped up later.
- 3n. If the free disk space on the data volume is below 10 GiB, the Dispatcher skips the firing and records the reason.
- 3o. The Dispatcher stores the launch time of each Worker. A Worker's last record is the time of its last request through the proxy; if the proxy cannot tell, it is the last-modified time of the Worker's session transcript. The runaway checks (3i) are made at each firing, so a Worker may exceed its limits by up to one firing interval.
- 3p. Every call the Dispatcher makes to the container tool has a time limit. If a call exceeds it, the Dispatcher skips the firing and records the reason.

**4. Workers**

- 4a. Up to 3 Workers run at once; this can be changed via "Settings" in the Cockpit UI, up to the number of `workerN` users defined in the image.
- 4b. All Workers run in one container, at any time.
- 4c. The container is Apple's native macOS `container` tool, using plain `container run` (not `container machine`): one long-lived container with `--init`, a fixed `--memory` (4-6 GB) and `--cpus`, volumes only for the worker area and an outbox, and no `--ssh`.
- 4d. A LaunchAgent in the owner's logged-in session keeps the proxy running and holds `caffeinate -i` for as long as it does.
- 4e. The image has no sudo; the Dispatcher starts each Worker with `container exec --user workerN`, using a `workerN` that no running Worker is using, under an in-guest `timeout` of twice the runaway limit (80 minutes by default) as a backstop if the Dispatcher is down.
- 4f. On the host, a small reverse proxy listens only on the container network's gateway address, forwards only `POST /v1/messages` (and `count_tokens`) to the model API, strips any client credential and adds the credentials corresponding to the owner's subscription; each Worker holds its own dummy token, so that the proxy can tell the Workers apart. The Workers' Claude Code points `ANTHROPIC_BASE_URL` at it.
- 4g. The token usage can be inferred from the proxy, and this shall be done.
- 4h. The Workers' environment switches off non-essential traffic: `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1`, `skipWebFetchPreflight: true` or the WebFetch tool denied, `ENABLE_CLAUDEAI_MCP_SERVERS=false`, `CLAUDE_CODE_DISABLE_ARTIFACT=1`, `CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL=1`, and a per-Worker `CLAUDE_CONFIG_DIR`.
- 4i. The Workers run as unprivileged users.
- 4j. Each Worker works in its own separate clone, without hardlinks.
- 4k. The Workers clone into a folder named after the task (`<task-id>/clone`) on the container's own disk or a named volume, so that clones of different tasks never overwrite each other; the bind mounts only hand work in and results out.
- 4l. The CLI, python, pytest and git are baked into the image.
- 4m. The Worker confirms that it has picked a task up by writing the file `outbox/started` (its content is ignored); the Dispatcher picks the file up and sets the status of the task to "WIP".
- 4n. The Worker composes a good-bye note, `outbox/goodbye.json`, with: `outcome` ("done" or "gave_up"), `summary` (a report of the work done), `branch`, and `tokens` (the tokens consumed).
- 4o. The Worker writes a git bundle of its branch, named in the note, into `outbox/result.bundle`.
- 4p. Each task has a folder `<worker area>/<task-id>/` with two subfolders: (1) `inbox/`: `main.bundle` (the current main of the task's repository) and `task.md` (the task's title and text), written by the Dispatcher; (2) `outbox/`: `started`, `goodbye.json` and `result.bundle`, written by the Worker.
- 4q. The Dispatcher launches each Worker with a fixed instruction: read `inbox/task.md`; work in your own clone; write `outbox/started` first; at the end write `outbox/goodbye.json` and `outbox/result.bundle`.

**5. Processing log**

- 5a. Each significant change of state related to Workers and the Dispatcher is recorded in the processing log; each entry names the task and its task folder.
- 5b. Every failed task records its reason in the processing log: no start, over 40 minutes, no activity, no note, or an unusable note.
- 5c. The processing log is stored where the processing occurs, on the owner's MacBook.
- 5d. The processing log is not part of any git repository.
- 5e. The processing log is backed up on GDrive once a day. The access details are stored in a configuration file which is not part of the git repository.

**6. Settings**

- 6a. Settings (the Dispatcher's interval and on/off switch, retries, the number of Workers and the two runaway limits) are stored in a configuration file in the Cockpit's local data folder, outside git.
- 6b. The Dispatcher reads the file at each firing.

## Approach

1. The first step was done on 2026-11-23: on macOS 15.3.1 the container has working networking and a Worker reaches a host listener on the gateway address; the socket relay also works. The gateway variant is used. The egress policy (what the Workers may reach) is still to be proposed.
2. If feasible, this would be a major re-work of the codebase of Project Cockpit.
3. Eliminate anything from the codebase which contradicts a specification set in this document.
4. When implementing, follow the philosophy of minimalism ("less is more").
5. If you find that important functionality or edge cases are left out in this spec, please note them.
6. If you believe that these are of critical importance, interrupt the work and seek input from the owner. Do not proactively implement code that is not based on the requirements here or explicitly approved by the owner.
