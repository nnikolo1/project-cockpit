# Development, test and production: a design for the owner's projects, starting with the Project cockpit

Status: research report, design proposal (background agent, 2026-11-24; read-only on code and data, web research; nothing installed, run or changed)
Date: 2026-11-24
Modules: taskqueue

Owner briefs: F-809 (the design, generic, building on the earlier work), F-808 (the gap: production runs from the development clone), F-810 (equal weight to the infrastructure, the deployment and the observed constraints of the owner's MacBook), F-750, F-751, F-754, F-758, F-777, F-779 (principles).

Marks: **[V]** verified on 2026-11-24 by reading the code, a commit, a live `launchctl` record, the processing log or a cited source; **[I]** inference by the author.

## Short answer

- An environment is not a folder or a container: it is a **named set of everything a running system touches**: code, data, configuration and secrets, external accounts, scheduler jobs and ports, containers, browsers, and who may act there. Isolation means no two environments share any of these by accident, and a guard refuses to start when they would.
- **Production runs from immutable release folders, one per tag, behind a `current` link switched atomically** (revised in section 14; first draft: a clean checkout at a release tag); the working clone becomes development only; **test is created on demand and torn down**, on the same laptop, with its own names for everything (container, image tag, socket, data root, labels, ports, repository) and a stub of the model.
- **On this MacBook, production and test must take turns, not run side by side**: one container VM at a time, three Workers at most while the owner works, the test run only when production has no Worker running.
- **Deploy is one command** (tests, tag, export the release folder, switch `current`, reload the jobs, health check with automatic rollback on failure, deploy log; rollback switches `current` back). It reuses the pattern of the earlier, never-merged scripts.
- **Borrow** git (tags, `git archive`), launchd or systemd, Chrome for Testing for projects that drive a browser, the existing stub of the model and, optionally, a free external heartbeat monitor; **build** one environment record with its collision guard, the deploy script and the test-run wrapper. About three to three and a half days for the cockpit, a day for the first step, no recurring cost (section 14).

## 0. What the earlier work established, its scope and limits

The earlier work was done on 2026-11-12 in another of the owner's projects, one with a web app, a SQLite database, a browser extension that acts on third-party sites, and several scheduled jobs.

**What it established** [V, its documents and commits]:
1. A written placement of dev, test and prod: what production consists of (a control plane bound to macOS, a portable browser plane), and phases (laptop now; a second machine later only if needed). The owner decided then: production stays on the laptop; a separate machine only after a test runner proves its value.
2. One module per project that is the **only place that knows the environment**: one variable selects `prod`, `test` or `dev`; every path, port, database, vault service name, scheduler label and extension identity is derived from it; production's values are pinned unchanged; a non-production environment **refuses to start** when any of these collides with another environment's (paths compared after resolving links, nesting counts). A guard test fails on any new hard-coded production path elsewhere in the code.
3. A **test environment that is safe by construction** for an extension that can make irreversible moves on the outside world: independent locks at the app, the policy, the extension build (its own identity, the final action disabled) and a handshake (the build and the app must agree on the environment), plus a tripwire that stops the test environment when an irreversible move is detected.
4. **Release and promotion**: a pinned production checkout, never edited; annotated tags of the form `prod-YYYYMMDD-N` on commits contained in the main branch; a promotion script that is a dry run by default, with gates (tag on main, clean checkout that is not the development tree, nothing mid-run in production), a post-checkout import check that rolls the checkout back on failure, a service restart and a promotions log; **rollback is promoting the previous tag**.
5. A test pyramid for the extension: unit, recorded-page replays, a local mock site with a counter that proves the final action is never pressed, live canaries on real third-party sites (review-only), supervised live runs.
6. A rule for CI: no forward moves by an agent driving a browser; an agent may only observe a failed run, read-only.

**Its scope and limits:**
- It is **specific to one project with a browser extension**; the environment module, the locks and the handshake are written in that project's terms.
- It covers **infrastructure and code paths**; it says little about **who may act** in each environment, about **external accounts** other than the browser and the vault (the hosting-service repository, the model access, mail), and nothing about **agents running unattended** (the Task Queue did not exist).
- **None of it reached production**: the work sits on an unmerged branch last touched on 2026-11-13; the production checkout was never created; the promotion script was never run [V: the commits are not ancestors of the project's main branch].
- It did not size environments against the host.

**Reusable as is** (as patterns, not as code to copy): the one-place-knows-the-environment rule with its collision refusal and guard test; the tag naming and the dry-run-first promote script with gates and rollback-by-previous-tag; "safe by construction" locks for anything irreversible; the handshake between a client and its app; the no-forward-moves rule for agents in tests; the placement table as a format.

## 1. Facts, re-checked today

| # | Fact | Mark |
|---|---|---|
| F1 | The Cockpit service, the Dispatcher job (every 120 s) and the quota job (every 30 s) all run with the **working clone** as their working directory and code; a merge into the clone's main changes production at the next firing or restart | [V, `launchctl print`] |
| F2 | The Dispatcher bundles `main` of the clone given by `--repo-dir` (the working clone) and fetches Worker branches into it as `worker/<task-id>` | [V, `runtime.py` L197, `tasks.py` L156, live arguments] |
| F3 | The Dispatcher serves one repository (`--repo`, one job per project label) | [V] |
| F4 | Production data lives under one data root with one folder per project (state, settings, area, socket, usage file, processing log); the earlier test run's folder lies **inside that production data root** | [V, directory listing] |
| F5 | The Workers' container has a **fixed name** (`cockpit-workers`) and the image a fixed tag (`cockpit-worker:latest`); the failure-path test driver uses the same container name and **stops it at the end**, which would stop production's container and its running Workers | [V, `runtime.py` L48, `e2e_failure_paths.py` L147] |
| F6 | The test of the Dispatcher uses a kept throwaway private repository, a separate base folder, its own proxy socket, and a stub of the model in a derived image (`cockpit-worker:stub`) | [V, `tools/e2e/README.md`] |
| F7 | Workers run in one container with no network except the model proxy; up to six Worker users in the image; the live setting is **3 Workers** (lowered by the owner, F-808) | [V, settings file, Containerfile] |
| F8 | One machine: a MacBook with 16 GB of memory and 8 cores, macOS 15.3.1, 112 GiB free on the system volume | [V, `sysctl`, `sw_vers`, `df`] |
| F9 | The Cockpit service's launchd label carries the prefix of another project; the Dispatcher's and quota jobs use the cockpit's own prefix | [V] |

## 2. The observed constraints of the MacBook (F-810)

| Constraint | Evidence | Consequence for the design |
|---|---|---|
| **16 GB of memory, pressure at "warn"** | Hardware: 16 GB [V]. Processing log, 70 heartbeats on 2026-11-24: pressure "warn" in 45 of 47 heartbeats with **no Worker running**, and in every heartbeat with 1, 2 or 3 Workers; "normal" only twice, both with no Worker [V]. The owner saw level 2 with two Workers (F-808). | The warning is the host's **baseline** while the owner works (browser, interactive sessions, the other project's services), not only the Workers' doing [I]. Every extra VM or browser adds to an already pressed host. |
| **The container's own use is modest** | Container memory in the heartbeats: about 0.7 GB with no Worker, peaks of 0.9, 1.3 and 1.4 GB with 1, 2 and 3 Workers, against a 4 GB cap and 2 CPUs [V] | Six Workers would roughly need 2-2.5 GB inside the cap [I, extrapolated]; the limit is the host, not the cap. |
| **One lightweight VM per container** | Apple's tool "creates and runs Linux containers as lightweight virtual machines" [V, [apple/container](https://github.com/apple/container)] | A second container (for test) is a second VM: about 0.7 GB idle plus its Workers [I, from the production container's idle figure]. |
| **The container tool runs on a macOS it does not support** | Tool 1.7.1 on macOS 15.3.1 [V]; the project supports macOS 26 and does not address issues not reproducible there [V, [apple/container](https://github.com/apple/container)]; container-to-container networking needs macOS 26 APIs (secondary sources, e.g. [The New Stack](https://thenewstack.io/apple-containers-on-macos-a-technical-comparison-with-docker/?p=22795145)) [V as reported] | The socket relay and "no network" were established by test, not by documentation (spec, Approach 1) [V]. A test environment must re-prove its own isolation; it must not rely on container-to-container networking. |
| **104-byte limit on socket paths** | Spec 4f; the test driver warns to keep its base path short [V] | Every environment's data root must be short; a test root under a long temporary path fails. |
| **launchd as the scheduler** | A Worker was killed seconds after launch until the job abandoned its process group (commit 214830b) [V] | A deploy that reloads jobs must keep `AbandonProcessGroup`; bootout/bootstrap, not kick-start (cockpit standing rule) [V]. |
| **Sleep is possible** | Spec 3i: sleep gaps do not count towards the limits [V] | The proxy holds the host awake only while there is work (4d); a deploy or test run during sleep simply waits. |
| **No node in the Workers' image** | Containerfile: python, git, pytest, the agent's binary; no node [V] | Projects with JavaScript (an extension) cannot run their JS tests inside a Worker today; those tests run on the host, in test. |
| **The interactive session's own load** | Not measured separately [I]; heartbeats show warn with no Worker | Count the interactive session and its browser as a fixed tenant of the host. |
| **One disk** | 112 GiB free; the Dispatcher skips below 10 GiB (3n) [V] | Test data roots must be pruned after each run. |

## 3. The generic model: an environment and its dimensions

An environment is one record per project and environment name (`dev`, `test`, `prod`), kept **outside every repository** next to that environment's data (a small JSON file). Everything below is read from it; nothing else in the code names a production path, port, label or account. The record is the earlier work's "one place knows" rule made generic: the cockpit defines the common keys; a project adds its own (a database file, an extension identity, a browser profile) without the cockpit knowing what they mean; the guard only checks that no value is shared between two environments of the same host.

| Dimension | Production | Development | Test |
|---|---|---|---|
| **Code** | a clean clone at a release tag, never edited | the working clone and its worktrees; any commit | the commit under test, checked out into the test root |
| **Data** | the production data root (one per host, a folder per project) | none of production's; scratch folders per worktree | a fresh short test root per run, removed after; never inside production's root |
| **Test data** | — | synthetic | synthetic only: fixtures in the repository, or generated (Faker for record-shaped data); never a copy of production data unless the owner approves a scrubbed copy |
| **Config and secrets** | one folder outside any repository, files mode 0600 | none needed, or developer copies | its own folder; the model needs no credential (stub); a test token only for the test repository |
| **External accounts** | the real repositories | the real repositories, read; pushes only by the owner or the interactive session | the throwaway repository on an allow-list; no mail, no backup target, no third-party site unless the run is a live canary |
| **Model access** | the subscription through the proxy | the interactive session's own | the stub; the real model only in a live canary, with a cap |
| **Jobs and ports** | the launchd labels and the service port | none installed; ad-hoc ports | run in the foreground by the test command, never installed as jobs; its own port and socket |
| **Containers** | one container, one image tag per release | image builds | its own container name and image tag |
| **Browsers and profiles** (projects that drive one) | the owner's own profile | a throwaway profile | Chrome for Testing with its own `--user-data-dir` and an extension build with its own identity |
| **Who may act** | see section 5 | | |

Collision rule (from the earlier work): a non-production environment refuses to start when any path, port, socket, label, container name, image tag, repository or profile equals production's or lies inside a production folder. This is what would have caught F4 and F5.

## 4. The infrastructure per environment on this host

**Production** [V for what runs today]:
- The Cockpit service (long-running, launchd `KeepAlive`, one port, LAN).
- The Dispatcher (one-shot every 2 minutes) and the quota job per project (every 30 seconds).
- The proxy (started by the Dispatcher while there is work, under `caffeinate -i`).
- The Workers' container VM (4 GB cap, 2 CPUs; started by the Dispatcher, stays up).
- The daily jobs (backup, upkeep).
- Supervision is today's: launchd restarts the service; the Dispatcher starts the proxy, the container and the relay at each firing (3k) and writes the heartbeat (3g).
- Needs: about 0.7-1.4 GB for the container with up to 3 Workers [V], a port, the socket, disk above 10 GiB.

**Development** [I]:
- The working clone, the interactive session and its browser, image builds, `pytest`.
- No job and no service point at the working clone; to look at a change, the developer starts the Cockpit by hand on a development port against a copy of the data, or read-only.

**Test** (on demand, foreground, torn down) [I]:
- A test command creates a short test root, starts its own proxy on its own socket and its own container under a test name with the stub image, runs the Dispatcher in a loop against the throwaway repository (today's failure-path driver, F6), checks the results and stops **only its own** container and proxy.
- For a project with a browser extension, it also starts that project's test app on its test port and Chrome for Testing with a test profile; Chrome for Testing is "a Chrome flavor that specifically targets web app testing and automation use cases", without auto-update [V, [Chrome for Developers](https://developer.chrome.com/blog/chrome-for-testing)]; branded Chrome removed `--load-extension` in version 137, Chrome for Testing keeps it [V, [Chromium extensions PSA](https://groups.google.com/a/chromium.org/g/chromium-extensions/c/1-g8EFx2BBY)].

**What can run at the same time on this MacBook:**

| Combination | Fits? | Why |
|---|---|---|
| Production with up to 3 Workers + development (interactive work) | **Yes, today's state** | Observed: warn but working [V] |
| Production with 6 Workers + development | **Not recommended** | The host is at warn with none; the owner lowered to 3 (F-808) |
| Production Workers running + a test run with containers | **No** | A second VM on a pressed host; the test driver's stop would hit production (F5, until fixed) |
| Production idle (Dispatcher switched off, no Worker running) + a test run | **Yes** | One VM at a time; the Dispatcher's own on/off switch (6a) is the lock |
| Production + a test run without containers (unit tests, the cockpit started on a test port) | **Yes** | No VM; small |
| A browser-driving test (Chrome for Testing) + production Workers | **Only with 1-2 Workers** [I] | A second browser is the heaviest client after the VM |

**Sizing**: production 3 Workers while the owner works; up to 6 only when the owner is away and no test runs [I]. Test: one run at a time, 3 stub Workers (today's driver) [V]. Development: one interactive session.

**The first environment that does not fit** is any one that must run **alongside** production Workers: a continuous test or nightly canary while Workers run, more than three Workers during the day, or several browser instances. That would need a second host: a Linux mini-PC would run the Workers (container per task with cgroup limits) or the test runs, while the laptop keeps the Cockpit, the interactive session and production data [I; the option stays open, F-754]. Nothing in this design changes when that happens: the environment record names the host.

## 5. Who may act in each environment

| Actor | Production | Development | Test |
|---|---|---|---|
| **Owner** | releases tasks; decides deploys (or delegates them, question 1); rolls back | works with the interactive session | starts or requests live canaries |
| **Interactive session** | runs the deploy command after a green test run (if delegated); restarts services (standing rule); never edits the production checkout | merges Worker branches, commits, pushes | runs the test command |
| **Dispatcher** | runs only from the production checkout, only against production's record | — (its input is the development clone's main and its output a `worker/` branch there; that is task data, not its code) | the test command runs a Dispatcher from the commit under test against the test record |
| **Workers** | none: they never touch production | they work on development material, in their own clones, in the container, no network, never push | stub Workers only |

The Dispatcher reading `main` from and fetching into the working clone (F2) stays: it is the task repository, development data for the Dispatcher. What changes is only where the Dispatcher's **code** runs from [I].

## 6. The test pyramid and the promotion criteria

| Layer | Where | Model | Gate for |
|---|---|---|---|
| Unit (pure logic, `pytest`, no network) | anywhere, also in Workers | none | every merge |
| Integration (the Dispatcher against the throwaway repository, stub Workers, all failure paths) | the test environment | stub | a deploy that touches the Task Queue |
| End-to-end with the stub (the Cockpit started on the test port against the test data; for an extension project, Chrome for Testing against a local mock site) | the test environment | stub | a deploy that touches the service, the extension or the pages |
| Live canary (one real task with the real model, capped; for an extension, review-only on a real site) | the test environment, attended or at night | real | optional, on the owner's request |

Promotion from development to test: merged to main, unit tests green. From test to production: the layers above that apply to the changed modules are green; nothing mid-run in production (no Worker running for a Task Queue change; for an extension project, no run in flight, as the earlier gate did).

## 7. Deployment, step by step

The deploy command, one script per project (dry run by default):

1. **Check**: the commit is on main; the working tree is clean; unit tests green; the integration run green when the Task Queue changed.
2. **Tag**: `prod-YYYYMMDD-N`, annotated (the earlier naming).
3. **Wait for production to be idle** for the parts that need it: no Worker running (read from the Dispatcher's state file); switch the Dispatcher off through its own setting for the duration.
4. **Export** the release (revised, section 14): `git archive <tag>` into `releases/<tag>/`, made read-only; the running release is untouched.
5. **Check the new release folder**: imports and a quick self-test run from it; on failure, stop (nothing was switched). Then switch `current` to it atomically.
6. **Reload**: bootout and bootstrap every job of the project (service, Dispatcher, quota), as the cockpit's restart rule says; rebuild and re-tag the Worker image only when its recipe changed.
7. **Health check**: the service answers on its port (the `curl` check in `deployment.md`); the next Dispatcher heartbeat appears in the processing log; switch the Dispatcher on again.
8. **Record**: one JSON line in a deploy log in the production data root (time, tag, commit, previous tag, gates, result, who), and the manual-interventions row where the owner's rule asks for one. A failed health check switches `current` back, reloads and records the rollback.
9. **Rollback**: switch `current` to the previous release folder and reload (the previous folders are kept; the newest five, for example).

## 8. The hypothesis on PC-84, judged

PC-84 proposes: production = a separate clean clone updated by one command; development = the working clone; test = a throwaway repository and a temporary data root, with a stub of the model.

- **Right**: all three parts match the earlier work and this design; the one command with tests, tag, update, reload and rollback is the core.
- **Too small**:
  - it isolates code and data but not the **container name, image tag, socket and labels**; today's test driver would stop production's container (F5);
  - it does not say **what may run at the same time** on this host (section 4) or how production is held idle during a deploy or a test;
  - it does not cover a project with a **database** (schema changes: the earlier rule that migrations are additive, and a backup before a non-additive one), an **extension** (its own build identity and locks, a handshake), or a **browser profile** (Chrome for Testing, a test profile);
  - it gives **no guard**: nothing refuses a test run that names a production path or repository;
  - it does not say **who may deploy**, nor how the "restart after merge without asking" rule fits.
- **Add**: the environment record and collision guard; the test names for the container and image; the "take turns" rule; the deploy log; the per-project extension of the record.

## 9. Recommended design, in steps ordered by value

| # | Step | Effort [I] |
|---|---|---|
| 1 | **Production release folders for the cockpit** (revised, section 14): `releases/<tag>/` exported with `git archive` and made read-only, a `current` link switched atomically; the service, Dispatcher and quota job plists generated to point at `current` (their data paths stay); a deploy script with the steps of section 7 and section 14.3, automatic rollback on a failed health check; a JSON-lines deploy log. From then on a merge no longer changes production. | 1 day |
| 2 | **Test names for the Task Queue** (the container name part was done on main in commit `da5dc918`): image tag, socket, area, data root and labels read from the environment record; the test driver uses the test record and stops only its own container; a test root outside production's root; the driver refuses to run while production has a Worker running. | 0.5-1 day |
| 3 | **The environment record and guard**, generic: the common keys, the collision refusal, a guard test that fails on a hard-coded production path; written so another project adds its own keys. | 1 day |
| 4 | **Promotion criteria in the deploy script**: which test layers must be green for which modules. | 2-4 hours |
| 5 | **For a project with an extension, a database or a browser**: port the earlier patterns (locks by construction, handshake, test build identity, Chrome for Testing profile, additive migrations, scrubbed or synthetic data) into that project's record and deploy script; the earlier branch is a source of ideas, not something to merge as is. | per project; the earlier estimate for its first slice was 4.5 agent-days |
| 6 | **Later, only when something in section 4 does not fit**: a second host for Workers or tests. | open |

Steps 1 and 2 are about the Task Queue itself, so under the cockpit's standing rule they are `interactive-only` work, done by the interactive session, not by Workers [V, standing rule].

## 10. Borrow and build

**Borrow** (trust rules: at least 10 stars, a commit within 30 days, a licence; last push from the public GitHub API, used as a proxy for the last commit; checked 2026-11-24):

| Tool | Source | Stars, last push, licence | Replaces |
|---|---|---|---|
| git tags and a second clone | git itself | — | a deployment tool |
| launchd (macOS), systemd user units (Linux) | the operating system; recipes already in `deployment.md` | — | a process supervisor |
| Chrome for Testing (projects that drive a browser) | [GoogleChromeLabs/chrome-for-testing](https://github.com/GoogleChromeLabs/chrome-for-testing) | 1,318; 2026-11-24; Apache-2.0 | the owner's own Chrome in tests |
| Faker (only if a project needs record-shaped synthetic data) | [joke2k/faker](https://github.com/joke2k/faker) | 19,504; 2026-11-20; MIT | hand-written fake records |
| The existing model stub and failure-path driver | this repository (`tools/e2e/`) | — | a mock API service |
| Separate model workspaces with spend caps (only if a live canary uses API keys rather than the subscription) | [Claude workspaces](https://claude.com/resources/articles/workspaces): keys are tied to a workspace; per-workspace spend limits | product | a shared key |

**Looked at and not proposed**: Kamal ([basecamp/kamal](https://github.com/basecamp/kamal), 14,754; 2026-11-18; MIT: deploys containers to servers over SSH; too much for one laptop); direnv ([direnv/direnv](https://github.com/direnv/direnv), 15,561; 2026-11-23; MIT) and SOPS ([getsops/sops](https://github.com/getsops/sops), 23,840; 2026-11-23; MPL-2.0): trusted, but a 0600 file per environment outside the repository is enough today. age ([FiloSottile/age](https://github.com/FiloSottile/age), 24,452) fails the 30-day rule (last push 2026-08-29). The twelve-factor rule "strict separation of config from code", with its test that the code "could be made open source at any moment, without compromising any credentials" [V, [12factor.net](https://12factor.net/config)], is the principle the record follows.

**Build** (small, stdlib): the environment record and guard (step 3), the deploy script (step 1), the test-run wrapper around the existing driver (step 2).

## 11. The first small step (one day)

Step 1 for the cockpit alone (revised, section 14): export the first tag into `releases/<tag>/` and point `current` at it; write the deploy script in dry-run form and run it; then, with the owner's yes, regenerate the three jobs to run from `current` (bootout, bootstrap), check the service and the next heartbeat, and write the first deploy-log line. Success test: a commit in the working clone no longer changes what the service and the Dispatcher run, and `deploy --rollback` brings back the previous release in seconds.

## 12. What the owner has to decide

1. **Does a merge still reach production at once?** Options: (a) the interactive session runs the deploy command right after a green merge, as it now restarts the service without asking; (b) only the owner deploys. Recommendation: (a) for the cockpit, which keeps today's speed with a tag and a rollback; (b) for projects that act on the outside world.
2. **Where does the test environment run?** Recommendation: on this MacBook, taking turns with production Workers (section 4); a second host only when a combination that does not fit is needed.
3. **Which model does the test use?** Recommendation: the stub for every automated layer; the real model only in a live canary the owner asks for, with a cap.
4. **What happens to the earlier, never-merged environment work of the first project?** Recommendation: do not merge it as is; use it as the pattern for step 5 when that project's turn comes.
5. **Are test runs limited to an allow-list of throwaway repositories?** Recommendation: yes, the guard refuses any other repository in test; and (added in section 14) the test runs with a fine-grained GitHub token limited to the throwaway repository, so that GitHub, not only the guard, enforces it (a manual step of the owner).
7. **(New, section 14) May the daily backup and the Dispatcher ping a free external heartbeat monitor** (healthchecks.io, free plan; only a ping, no payload)? Recommendation: yes; it is the one way to learn that a job stopped while the Cockpit is not open.
6. **How many Workers in production during the day?** Recommendation: keep 3 while the owner works; more only when the host is otherwise idle.

## 13. The three facts I am least sure of

1. **That the memory warning is mostly the host's baseline and not the Workers'**: the heartbeats show "warn" with no Worker running, but they do not say which processes cause it; the interactive session's and the browser's share was not measured.
2. **That a second container VM costs about 0.7 GB idle**: inferred from the production container's idle figure; not measured for a second, concurrent container on macOS 15 with this unsupported tool version.
3. **That the service, the Dispatcher and the quota job run unchanged from a read-only release folder without `.git`** (revised with section 14): they take all their data paths as arguments [V for the Dispatcher], but whether any of them reads a file that exists only in the working clone (a code snapshot, `--git`, git metadata) or writes beside its code (Python's `__pycache__`) was not checked for the service and the quota job.

## 14. Fit to the bar: light yet state of the art

Owner briefs: F-813 ("as light as possible but it still needs to qualify as a SOTA solution for projects of this size"), F-814 (recent DevOps practice and tools; open source or free to modestly paid, a budget of 20 euros a month). Sources read on 2026-11-24; repository figures from the public GitHub API (stars; last push, used as a proxy for the last commit). Trust rules: at least 10 stars, a commit within 30 days (on or after 2026-10-25), a licence, reputation.

### 14.1 What current good practice includes, at this size

| # | Practice | Source (read 2026-11-24) | Simplest tool at this size |
|---|---|---|---|
| P1 | Configuration out of the code, per environment | Twelve-factor: "strict separation of config from code"; the code "could be made open source at any moment, without compromising any credentials" [V, [12factor.net/config](https://12factor.net/config)] | a 0600 file per environment outside the repository |
| P2 | Environment parity | "resists the urge to use different backing services between development and production"; "the same type and version of each of the backing services" [V, [12factor.net/dev-prod-parity](https://12factor.net/dev-prod-parity)] | the same image tag and the same Python in test and production |
| P3 | Trunk-based work | merge to trunk "at least once ... a day"; branches "typically last no more than a few hours"; "three or fewer active branches" [V, [DORA, trunk-based development](https://dora.dev/capabilities/trunk-based-development/)] | git; short `worker/` branches merged by the interactive session |
| P4 | Tagged, immutable, reproducible releases | "Every release should always have a unique release ID"; "a release cannot be mutated once it is created" [V, [12factor.net/build-release-run](https://12factor.net/build-release-run)]; builds "depend on known versions of build tools ... and dependencies" [V, [Google SRE book, Release engineering](https://sre.google/sre-book/release-engineering/)] | annotated git tags; the image's base pinned by digest; the agent's version pinned |
| P5 | Immutable release folders, atomic switch, instant rollback | "`releases` holds all deployments in a timestamped folder"; `current` points at the latest and still at the old one if a deploy fails; a log of "every deploy or rollback" [V, [Capistrano, structure](https://capistranorb.com/documentation/getting-started/structure/)]; a rename "will be an atomic operation (this is a POSIX requirement)" [V, Python `os.replace` documentation source] | `git archive` into `releases/<tag>/`, a `current` link replaced with `os.replace` |
| P6 | Infrastructure and jobs defined as files | infrastructure defined "through source code that can then be treated just like any software system" [V, [Fowler, 2016-03-01](https://martinfowler.com/bliki/InfrastructureAsCode.html)] | the plists generated from the environment record (`dispatcher/launchd.py` already generates them) |
| P7 | Health check and smoke test after deploy, rollback on failure | Capistrano switches only after a successful deploy [V, above]; automatic rollback on a failed check [I, common practice] | the existing `curl` check and the next heartbeat |
| P8 | Separate credentials per environment | fine-grained tokens "can be further limited to only access specific repositories", with expiry [V, [GitHub docs](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)]; model keys "tied to the Workspace" [V, section 10] | a fine-grained token for the test repository; the stub for the model |
| P9 | Ephemeral test environments | "throwaway, lightweight instances", started for the tests and removed afterwards [V, [Testcontainers](https://testcontainers.com/)] | a temporary test root and a test-named container, torn down by the test command |
| P10 | A record of what runs where; deploy observability | the four keys: deployment frequency, change lead time, change fail rate, failed deployment recovery time [V, [DORA, updated 2026-01-05](https://dora.dev/guides/dora-metrics-four-keys/)] | a JSON-lines deploy log from which the four keys can be computed; a `status` subcommand |
| P11 | Heartbeat monitoring of scheduled jobs | a job pings after each run; silence raises an alert [V, healthchecks.io: "Monitor 20 jobs" on the free plan, [pricing](https://healthchecks.io/pricing/)] | a ping from the daily backup and the Dispatcher |
| P12 | Backup and a tested restore | — [I, common practice] | the existing daily archive and `tools/backup_restore.py`; release folders are rebuilt from tags |

### 14.2 The design against the bar

| # | In the design? | How, or why not |
|---|---|---|
| P1 | Yes | the environment record and 0600 files outside the repository (section 3) |
| P2 | **Was missing, added** | the Worker image is built once per release, tagged with the release tag, tested, then the same tag runs in production; the Python version is recorded in the deploy log. Full parity of host and tools (macOS laptop versus a Linux box later) is not reachable on one laptop and is left out |
| P3 | Yes | the Task Queue's branches live for one task; the interactive session merges to main |
| P4 | Partly; **added** | tags existed; added: base image pinned by digest, agent version pinned (fit-gap item 1). Hermetic builds (Nix, Devbox) are left out as too heavy for a stdlib project |
| P5 | **Was weaker, changed** | the first draft checked out a tag in place in one clone; now immutable release folders and an atomic `current` switch: rollback is a link switch plus a reload, and a failed export never touches the running release |
| P6 | Partly; **made explicit** | production plists are generated from the record and point at `current`; nothing is edited by hand |
| P7 | Partly; **added** | the health check existed; added: automatic rollback when it fails |
| P8 | Partly; **added** | the stub covered the model; added: a fine-grained token limited to the throwaway repository for test (question 5). Separate identities for roles stay as in ADR 0011, phase 2 |
| P9 | Yes | section 4; the container-name collision was fixed on main (`da5dc918`) |
| P10 | Partly; **added** | the deploy log becomes JSON lines (time, tag, commit, previous tag, result, rollback) and `deploy status` prints the running tag per project and how many commits main is ahead. No dashboard: the four keys can be computed from the log when wanted |
| P11 | **Was missing, added (optional, question 7)** | the Cockpit shows heartbeats only when open; an external dead-man's switch reports a stopped job |
| P12 | Yes | existing backup and restore check; nothing added |

### 14.3 Tools surveyed (F-814)

Prices are list prices on the cited pages; "free" means no charge at the plan named.

| Area | Candidate | Licence | Price | Stars; last push | Trust | Verdict, what it replaces, effort |
|---|---|---|---|---|---|---|
| Release folders | Capistrano | MIT | free | 13,011; 2026-11-02 | passes | **pattern only**: Ruby and SSH for one laptop is more than a 100-line script |
| | Deployer | MIT | free | 11,127; 2026-08-26 | fails (30 days) | pattern only |
| Deploy to servers | Kamal | MIT | free | 14,754; 2026-11-18 | passes | not now: needs Docker and SSH hosts; reconsider with a Linux box |
| Task runner | just | CC0-1.0 | free | 36,826; 2026-11-15 | passes | **optional**: one `just test / deploy / rollback` surface across all projects, many community skills for Claude Code (skill directories, not verified for quality); adds one binary; ~1 hour per project |
| | Task (go-task) | MIT | free | 16,384; 2026-11-24 | passes | alternative to just; not needed |
| | make | GPL | free, preinstalled | — | — | works, poorer ergonomics |
| Tool versions and per-folder env | mise | MIT | free | 36,465; 2026-11-24 | passes | optional: pins the Python version (P2) and replaces direnv and a task runner in one; not now, a recorded version is enough |
| | direnv | MIT | free | 15,561; 2026-11-23 | passes | not needed: launchd sets the environment of jobs |
| Reproducible environments | Nix | LGPL-2.1 | free | 18,067; 2026-11-24 | passes | too heavy for this size |
| | Devbox | Apache-2.0 | free | 12,477; 2026-11-23 | passes | too heavy (built on Nix) |
| Images | Apple `container build` | Apache-2.0 | free | 52,061; 2026-11-13 | passes | **keep** (already used); tag per release, base by digest; no registry on one host |
| | Podman | — | free | not fetched (the API answered "moved") | unverified | only for a Linux box |
| Secrets | macOS Keychain, 0600 files | — | free, built in | — | — | **keep** |
| | sops | MPL-2.0 | free | 23,840; 2026-11-23 | passes | optional: only when secrets must be versioned in a repository; not now |
| | age | BSD-3 | free | 24,452; 2026-08-29 | fails (30 days) | not proposed |
| | Infisical | licence not stated by the API | free tier [I] | 30,730; 2026-11-24 | licence unclear | not proposed |
| | 1Password CLI | proprietary | not verified whether the owner holds a plan | — | — | not proposed |
| Supervision | launchd, systemd | — | free, built in | — | — | **keep** |
| | Supervisor | licence not stated by the API | free | 9,179; 2025-12-21 | fails (stars, 30 days) | not proposed |
| Heartbeat monitoring | healthchecks.io (hosted) | BSD-3 (code) | **free plan: 20 jobs, 100 log entries per job**; Supporter 5 USD a month, same limits | 10,544; 2026-11-23 | passes | **recommended, optional on the owner's yes (question 7)**: a `curl` after the backup and after each Dispatcher firing; ~1 hour |
| | Uptime Kuma (self-hosted) | MIT | free | 94,829; 2026-11-24 | passes | later, on an always-on box; a monitor on the monitored laptop sees nothing when it sleeps |
| | Gatus (self-hosted) | Apache-2.0 | free | 12,887; 2026-11-23 | passes | as Uptime Kuma |
| CI | GitHub Actions, hosted | — | public repositories: free on standard runners; private: included usage of 12 USD a month on Free, 18 USD on Pro [V, [GitHub billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)] | — | — | **keep** for the public cockpit repository; for private projects the deploy command's local test gate is the gate of record and hosted CI stays small (the owner once exhausted the allowance) |
| | Self-hosted runner | — | listed as free; usage does not reduce the included amount [V, same page] | — | — | not now: a runner on the laptop adds a service and, on a public repository, runs code from pull requests [I] |
| | act (workflows locally) | MIT | free | 72,779; 2026-11-19 | passes | not now: it needs Docker, which this host does not run [I] |
| Ephemeral test environments | Testcontainers for Python | Apache-2.0 | free | 2,322; 2026-10-09 | fails (30 days) | not proposed; the test command already does this |
| Logs and deploy markers | files with rotation (`newsyslog` on macOS, `logrotate` on Linux) [I] | — | free, built in | — | — | **keep**; the deploy log is the marker |
| Backup | the existing daily archive to Drive | — | the owner's existing Drive storage | — | — | **keep** |
| | restic | BSD-2 | free | 37,425; 2026-11-22 | passes | optional: versioned, encrypted backups if Drive archives are not enough; not now |

**Cost of the recommended set: 0 euros a month.** Optional with a price: healthchecks.io Supporter (5 USD a month, same limits, only to support the project); a GitHub Pro plan (more included Actions usage) only if private CI is wanted again. Both stay within the 20-euro budget.

### 14.4 What changed in the design

1. **Release folders instead of an in-place checkout** (sections 7, 9, 11, short answer): `releases/<tag>/` from `git archive`, read-only, and a `current` link switched with an atomic rename; keep the newest five. Same effort, instant rollback, no half-updated state.
2. **Automatic rollback** when the health check after a deploy fails (section 7).
3. **Deploy log as JSON lines** and a `status` subcommand (what runs where, how far main is ahead); the DORA four keys can be computed from it later.
4. **Parity**: the Worker image is tagged per release and tested before production uses the same tag; base image by digest; the Python version recorded.
5. **Per-environment credential for test**: a fine-grained token limited to the throwaway repository (question 5, a manual step of the owner).
6. **Optional external heartbeat** for the backup and the Dispatcher (new question 7).
7. **Nothing heavier added**: no deploy tool, no secrets manager, no self-hosted CI, no monitoring stack. `just` is the one optional addition, for a uniform command surface across projects.

The owner's questions 1 to 6 were re-checked: 1, 2, 3, 4 and 6 stand as written; 5 gains the scoped token; 7 is new.
