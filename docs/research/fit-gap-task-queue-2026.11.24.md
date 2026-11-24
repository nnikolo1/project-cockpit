# Fit-gap of the Task Queue against existing frameworks, runners and products

Status: research report (background agent, 2026-11-24; read-only on the code, web research)
Date: 2026-11-24
Modules: taskqueue

Marks: **[V]** verified by reading the cited source or the code on 2026-11-24; **[I]** inference by the author of this report.

Owner briefs:
- F-746: the study.
- F-747: batch schedulers.
- F-748: a Linux host.
- F-750: burden of proof.
- F-751, F-752, F-754: how to weigh the host.
- F-755 to F-757: the terms.
- F-758: incremental path.
- F-759 and F-760: the spine is task isolation, Worker images, and sessions and context.
- F-761: frameworks for context.
- F-762 to F-766: network policy, Claude focus, hand-crafted context, piggy-backing.
- F-767 to F-783: Claude-only frameworks, prior art (HPC, hybrids, write-ups), trust rules, the gap-coverage method, the decision table, earlier reports.

## Short answer

- **Host assumed: the owner's macOS laptop, as today.** Section 6 gives the Linux host side by side. Neither host gets a head start.
- **Keep what works.** The current pieces ran four tasks end to end on 2026-11-24 with isolation, notes and merges (owner, F-758). No verified reason was found that any of them cannot be kept.
- **The gaps are on the three spine items, and each can be filled with borrowed parts:**
  1. **Task isolation:** per-Worker isolation is in place, including no network except the model, which is worth keeping as the default (F-762). What is missing is a network policy per task kind (default none, named allowlists, F-763), per-task resource limits, and a boundary that does not depend on an unsupported container tool. Borrow a per-task container (Linux) or the vendor's sandbox runtime (laptop) only when that boundary is replaced.
  2. **Worker images:** there is one hand-built image with no per-project dependencies, no version and no rebuild trigger. Borrow the standard recipe-and-layer-cache model: a Containerfile, or devcontainer.json, per project on a shared base, with the recipe and lock-file hash as the tag. This is the biggest missing bit for projects other than the cockpit.
  3. **Sessions and context:** the vendor's coding tool already provides project memory (`autoMemoryDirectory`), context injection at session start (a SessionStart hook), compaction, resume and fork, and sub-agents. The Claude-specific frameworks (the Agent SDK, Ruflo) can wrap a Worker that stays the vendor's tool, so need 2 is unchanged, but nothing in them is needed. Piggy-back on the tool; write only the synopsis and the wiring.
- **Secondary:**
  - Do not adopt a batch scheduler (Slurm, Grid Engine, HTCondor, pueue). On one node it replaces little and mirrors task state (section 5).
  - The vendor's cloud (Projects, cloud sessions) covers isolation, images and context best. It conflicts with the owner's rules on push, determinism and the local log.
- **The most promising mongrel** (section 0.3): our Dispatcher (task state, queue, ranking, note) + a standard container engine (images with layer cache, a container per task, network per container) + Claude Code's own mechanisms (memory, hooks, `setup-token`, usage metrics). The three own disjoint state, so nothing is stored twice.

## 0. The method (F-772): today's gaps, a coverage matrix, the best combinations

### 0.1 The gaps we have right now

| Gap | Today | Missing | Source |
|---|---|---|---|
| **G1** One queue for all projects | One Dispatcher per repository: `--repo` is a single value (`dispatcher/core.py:478-504` [V]) | Several projects in one ranking, cockpit first | Constitution "The Dispatcher" (owner, 2026-11-24) |
| **G2** Project-bound Workers, warm clone, pre-built stack | A bundle of main per task, one generic image [V] | A per-project stack and an incrementally updated clone | Owner, F-746 and F-758; constitution "Context management" (attachment) |
| **G3** Worker image management | One hand-built `cockpit-worker:latest`; the agent binary is "copied there by hand" (Containerfile [V]) | A recipe per project, a hash key, a base plus project layer, a rebuild trigger, a pinned agent | Owner, F-759 |
| **G4** Task isolation per task and per project | Users, mode-700 homes and a private mount namespace in one shared container, one 4 GB cap (runtime.py:50, :91-95 [V]); the namespace was added after the shared mount was found to ignore modes (c0ae6e5, observed) | Per-task limits; a boundary not resting on an unsupported tool version | Owner, F-759; spec 4b, 4c; container-seam review §1 |
| **G5** Network policy per task kind | No network but the model for every Worker (4c, 4s [V]) | An owner-held policy per kind, default none, named allowlists | Owner, F-762 and F-763 |
| **G6** Session and context management | Per-Worker config folder, `CLAUDE.md` in the clone, transcripts mirrored [V]; the owner's hand-crafted practices not yet applied to Workers | Project memory for Workers, the dated synopsis (PC-70) | Constitution "Context management"; owner, F-760 and F-765 |
| **G7** Accounting of the shared model quota | Proxy counts per Worker (4h), transcript and statistics-cache usage, quota history (commits c8396d7, 47d5b1b [V]) | One view across interactive sessions and Workers per window (3h: statistics only) | Spec 3h, 4h; owner, F-758 |
| **G8** Scheduling of the daily jobs | Separate LaunchAgents per job (commits 2377cb3, b8a028e, c8396d7 [V]); the daily backup and upkeep jobs exist (PC-62, PC-50, PC-54 [V]) | One place that schedules and reports all daily jobs [I] | Owner, F-758 |
| **G9** Retries and failure handling | Failure reasons recorded (5b); no retries, a setting exists (3f) [V] | A retry policy actually applied; requeue after host or tool faults [I] | Spec 3f; owner, F-758; observed: aborted launches (commit 95c6f67) |
| **G10** Host choice | The laptop, with Apple `container` 1.7.1 on an unsupported macOS (seam review §1 [V]) | A supported boundary; an option for a Linux host | Owner, F-748, F-751, F-754 |

### 0.2 Coverage matrix

**Candidate rules** (F-776 to F-779):
- A repository is a candidate only if it has at least 10 stars **and** a commit since 2026-10-25. The stars and the last commit date are stated, read on 2026-11-24 from the repository page and its commit feed [V].
- Popularity only ranks. Products and specifications without a repository (the vendor's cloud, the Dev Container spec, host timers) are judged on their documentation.
- A repository that fails the rules, or another verifiable trust check (no licence, a single anonymous author), is labelled **untrusted** in appendix B and is not a candidate.
- **Untrusted code is never installed or run in our set-up.** Only its ideas may be read as a pattern, and only after the owner's review.

**C** covers, **P** partly, **N** no. Evidence is in the sections named in the last column, or in §5 "Prior art".

| Candidate (stars; last commit) | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G8 | G9 | G10 | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Ours (today)** | N | P | P | P | P | P | P | P | P | P | 0.1 |
| Docker (moby 72.3k; 2026-11-24) / Podman (33.4k; 2026-11-24), with layer cache and a container per task | N | C | C | C | C | N | N | N | N | P (native on Linux; VM on macOS) | §2, §3 [V cache; I rest] |
| Dev Container spec (specification) | N | P | C | P | P (firewall script) | N | N | N | N | P | §3 [V] |
| Claude Code's own mechanisms (`autoMemoryDirectory`, hooks, `setup-token`, OpenTelemetry; product) | N | N | N | N | P (Bash sandbox allowlist) | C | P (per-session tokens) | N | N | C | §4, §2 [V] |
| Sandbox runtime `srt` (7.3k; 2026-11-12) | N | N | N | P (no limits) | C (settings per run) | N | N | N | N | C (both hosts) | §2 [V; I limits] |
| Sandcastle (9.3k; 2026-11-16; Claude Code support not stated on its page) | N | N | N | C (bubblewrap, Seatbelt, Docker or Podman providers) | N (not covered) | N | N | N | N | C | §5 [V] |
| Claude cloud (Projects, cloud environments, scheduled tasks; product) | P (Claude chooses) | C (setup snapshot) | C (setup script, rebuild triggers) | C (VM per session) | C (per environment) | C (project memory, cloud only) | P (plan limits) | C (scheduled tasks) | P (continue at limit reset) | C | §2-§5 [V]; conflicts with push (3e), determinism, local log (5c) |
| GitHub Action (10.1k; 2026-11-24) on a self-hosted runner | P (per repository) | N | P | P (Linux containers) | N | N | N | C (cron) | P (re-run) [I] | P | §5 [V] |
| **Kelos** (360; 2026-11-24; Apache-2.0): Kubernetes, tasks from GitHub issues | P (TaskSpawner from issues, label "agent-ready"; no ranking stated) | C (workspace: repo, setup command, image) | P | C (pod per task, resources) | N (not described) | P (injected files) | P ("token usage" in task status) | C (cron trigger) | P [I] | P (needs Kubernetes) | §5 [V] |
| **Optio** (1.1k; 2026-11-22; MIT): Kubernetes, tickets to PRs | P (queue on BullMQ and Postgres, several repos) | C (pod per repo) | P | P (pod per repo, worktrees inside) | N (author: no egress restrictions) | N | N | N | C (auto-resume on failures) | P (Kubernetes) | §5 [V] |
| **ProPR** (40; 2026-11-24; Apache-2.0, v0.9.3): labelled issue to PR | P (label per issue; Epic mode in order) | P | P [I] | C (container and worktree per run) | P (optional allowlist firewall, off by default) | N | N | N | P [I] | P (Linux amd64) | §5 [V] |
| **Cyrus** (913; 2026-11-24; Apache-2.0): assigned issues to sessions | P (Linear, GitHub, GitLab) | P (worktree per issue) | N | P (worktree only) | N (not documented) | N | N | N | N | C [I] | §5 [V] |
| **CodeForge** (15; 2026-11-23; MIT): REST task queue, Docker | P (REST tasks; label trigger only as middleware) | N | N | P ("isolated in Docker") | N | N | P ("cost and usage tracking", "key pool (subscription mode)") | N | N | P | §5 [V] |
| Slurm (4.5k; 2026-11-24) / Grid Engine, i.e. Open Cluster Scheduler (57; 2026-11-23), on one node | C (one queue, partitions) | N | N | P (cgroups) | N | N | N (CPU, not tokens) | P [I] | C (`--requeue`) | P (Linux only) | §5 [V] |
| HTCondor (339; 2026-11-23) | C | N | N | P | N | N | N | P [I] | P | P | §5 [V partly] |
| pueue (6.4k; 2026-11-18) | P (groups) | N | N | N | N | N | N | P (`--delay`) | P [I] | C | §5 [V] |
| launchd / systemd timers (system tools) | N | N | N | N | N | N | N | C | N | C | [I] |
| Claude Agent SDK (8.6k; 2026-11-23) | N | N | N | N | N | P (resume, fork) | P | N | N | C | §4 [V] |
| Ruflo (82.3k; 2026-11-24) | N | N | N | N | N | P (cross-session memory) | N | N | N | P | §4 [V] |

**What the matrix shows:**
- No candidate covers G1 the way the owner's rules require: an owner release gate, deterministic ranking, cockpit first. Kelos, Optio, ProPR and Cyrus come closest; each takes work straight from issues or labels and lets the agent push and open PRs.
- G3, G4 and G5 are covered by a container engine.
- G6 is covered by Claude Code's own mechanisms.
- G8 is covered by host timers or Kelos' cron.
- G9 is covered by Optio's auto-resume or a scheduler's requeue, and cheaply by our own setting.
- **Network:** none of the real hybrids found makes a no-network sandbox the default. ProPR's allowlist is optional and off; Optio's author says agents have open egress.

### 0.3 The best combinations

| Combination | Covers | Seam: who owns what, how they talk | Effort [I] | Risks |
|---|---|---|---|---|
| **M1: Ours + container engine + Claude Code mechanisms** (+ host timers) | G2-G6 and G8 borrowed; G1, G7 and G9 stay ours (small) | Task state: GitHub through our Dispatcher only. Images and containers: the engine (no task state). Sessions and memory: Claude Code's files. Our runtime adapter calls the engine's CLI (as today with the Apple tool); hooks call small scripts. | 4-6 days: images 1, per-task container and network 1-2, context 1-1.5, one queue 1 | One source of task state. Glue is the existing adapter plus two hooks. On macOS a per-task container costs a VM each (Apple tool) and Docker Desktop cannot mount the socket reliably (seam review §3), so full G4 and G5 favour Linux, while G3 and G6 work on both hosts. |
| **M2: Ours + `srt` + Claude Code mechanisms** | G4 (no limits on macOS), G5, G6, G10; G3 not covered (no image model) | Task state ours; the sandbox is a per-run settings file | 2-4 days | No per-project stack without a separate environment manager; `srt` is beta |
| **M5: Kelos (or Optio) as the runner + our gate** (Linux, Kubernetes, for example k3s) | G2, G4, G8 (Kelos) or G9 (Optio), G7 partly (Kelos) | **Two task-state sources**: GitHub labels (ours) and Kelos Tasks or Optio's Postgres queue. Our Dispatcher would set the trigger label only after release and ranking. | 1-2 weeks [I] | Kubernetes on one node; agents push and open PRs (3e); no default network policy; the runner's own queue mirrors ours |
| **M3: Slurm + Apptainer images + a sandbox + our thin layer** (the HPC mongrel) | G1 (queue), G3, G4, G9 (requeue); G5 only with a sandbox | **Two task-state sources**: GitHub (ours) and the Slurm queue (mirrored hold and release) | 2-3 weeks [I] | The mirrored queue is the failure class of 2026-11-20; MUNGE and the daemons; built for shared clusters |
| **M4: Ours + Claude cloud** (the Dispatcher starts `claude --cloud` sessions) | G2-G6, G8, G10 from the cloud | Task state in GitHub plus session state in the cloud; results arrive as pushed branches | 2-3 days | Conflicts with 3e (no push), 5c (local log) and the hard cap; two state sources; research-preview terms |

### 0.4 Decision table: build, borrow or mongrel (F-780)

Efforts and maintenance are estimates [I], based on the sizes in §3 and §7, the line counts of our code, and what each tool's documentation asks to be run.

**Host obstacles on macOS** (§6): the Apple tool on an unsupported OS, the socket path and relay, one VM per container, Docker Desktop's socket mount, launchd lifecycle quirks, memory shared with interactive work.

**Trigger** means the macOS obstacles of that row outweigh its benefit, so the Linux mini-PC option (kept open, F-754) applies.

| Row | Gaps covered | Effort for the rest | Upkeep for one person | Main risks (trust) | Host needs; what a move changes | Trigger? | Verdict |
|---|---|---|---|---|---|---|---|
| **Keep building (ours, today)** | partly all, fully none | G1-G9 by hand: ~2-3 weeks | Highest; all our code | Apple tool on an unsupported OS | macOS with the obstacles named; moving makes G4 and G5 easier | Only if the Apple tool breaks | Not alone |
| Docker / Podman | G2, G3, G4, G5 | G1, G6-G9: ~1 week | Low (a mature engine) | None new (trusted) | Linux natively; macOS through a VM | Yes, for per-task G4 and G5 | Borrow, inside M1 |
| `srt` | G4 (partly), G5, G10 | G2, G3, G6-G9 | Low, but beta | Beta configuration format (trusted) | Both as is | No | Borrow only if the Apple tool fails (M2) |
| Claude cloud | G2-G6, G8, G10 | G1 deterministic, G7, G9, plus the rule conflicts | Lowest | Lock-in; second state; 3e and 5c conflicts | None (cloud) | n/a | Not now (owner rules) |
| Kelos | G2, G4, G8; G1, G6, G7 partly | Release gate, ranking, no-push, network policy: ~1-2 weeks | Medium (Kubernetes) | Second task state; agents push (trusted) | Linux with Kubernetes | Yes (it needs Linux) | Pattern; borrow only within M5 |
| Optio | G2, G9; G1, G3, G4 partly | Gate, ranking, network, no-push: ~1-2 weeks | Medium-high (Kubernetes, Postgres, BullMQ) | Second state; open egress; auto-merge (trusted) | Linux with Kubernetes | Yes | Pattern |
| ProPR | G4; G1, G2, G5 partly | Gate, ranking, G6, G7: ~1 week | Medium (pre-1.0) | Pushes PRs; small (40 stars) (trusted) | Linux amd64 | Yes | Pattern (allowlist firewall) |
| Cyrus | G10; G1, G2, G4 partly | Most of G3-G9 | Medium | Worktree-only isolation (trusted) | Either | No | Pattern |
| CodeForge | G1, G4, G7 partly | Most | Medium (15 stars) | Small project (trusted, near floor) | Docker | Yes | Pattern |
| Slurm / Grid Engine / HTCondor | G1, G9; G4 partly | Gate mirror, G2-G7: ~2-3 weeks | High (daemons, MUNGE) | Second task state; failure class of 2026-11-20 (trusted) | Linux (HTCondor also lists macOS) | Yes | Build, not borrow |
| pueue | G10; G1, G8, G9 partly | Most | Low | None (trusted) | Both | No | Not needed |
| GitHub Action, self-hosted | G8; G1, G3, G4 partly | Gate, no-push, network | Low-medium | Pushes; per-repository workflows (trusted) | macOS runner; containers need Linux | Partly | Not now |
| **M1: ours + container engine + Claude Code mechanisms** | G2-G6, G8 borrowed; G1, G7, G9 ours | 4-6 days | Low-medium: one adapter, two hooks | One state source (all trusted) | G3 and G6 on macOS as is; G4 and G5 per task better on Linux | Yes for G4 and G5 per task; no for G3 and G6 | **Mongrel (recommended)** |
| M2: ours + `srt` + Claude Code mechanisms | G4, G5, G6, G10 | G3 needs an environment manager | Low | `srt` beta | Both as is | No | Fallback mongrel |
| M3: Slurm + Apptainer + sandbox + ours | G1, G3, G4, G9 | Gate mirror, G5-G7: 2-3 weeks | High | Second state | Linux | Yes | No |
| M4: ours + Claude cloud | G2-G6, G8, G10 | Rule conflicts | Low | 3e and 5c conflicts; lock-in | None | n/a | No, unless the owner relaxes 3e and 5c |
| M5: Kelos or Optio + our gate | G2, G4, G8 or G9 | Gate mirror, network, no-push: 1-2 weeks | Medium-high | Second state; Kubernetes | Linux | Yes | Keep as the alternative if M1 falls short |

**Patterns worth copying, from write-ups and trusted repositories** (F-775; as useful as a tool):
- **dev-agents** (dev.to; pattern only, its repository is unreachable). An issue label leads, through a self-hosted runner, to a filesystem queue of YAML files. Priority is set by the file-name prefix, there is a per-project `flock`, and it runs on one small box on a Pro subscription. This is a minimal G1 pattern close to ours.
- **Anthropic's sandboxing post** (2025-10-20). Network access goes only "through a unix domain socket connected to a proxy server running outside the sandbox". That is the design our proxy and relay already follow, which validates keeping them.
- **ProPR's optional allowlist firewall**, and Kelos' `podOverrides`. Patterns for G5 and per-task limits in M1.
- A **CONNECT-only proxy with a named allowlist per job** is described by an untrusted repository (appendix B). It may be read as an idea only after the owner's review. ProPR's optional allowlist firewall is the trusted reference for the same G5 pattern.

**The most promising mongrel is still M1.** It holds together because:
- each part owns a different kind of state (tasks, images and containers, sessions), so nothing is mirrored;
- the seams already exist in our code (the runtime adapter, the Stop hook);
- every part is mature and widely used with Claude Code;
- it adds no daemon beyond the container engine.

The real hybrids (M5) prove the combination is common, but each one hands the queue and the push to the runner, and that conflicts with the owner's release gate and with 3e.

**Hosts.** On the laptop M1 gives G3 and G6 at once, and G4 and G5 per task only at the cost of a VM per container. On a Linux host it gives all of them natively. That is the concrete trigger for the host question (G10), not a head start for Linux.

## 1. The needs, with their sources

| # | Need | Source |
|---|---|---|
| 1 | Tasks are GitHub issues, the single source of truth | spec 2d; ADR 0001 |
| 2 | The model is reached with the owner's subscription through Claude Code | spec Goals 1, 4g |
| 3 | Sandbox with no network except to the model | spec 4c, 4f, 4g, 4s. Laptop: 5c. Linux: F-746 and F-748 only |
| 4 | Release gate, ranking, one Worker per firing, global limit | spec 2b, 3a, 3c, 4a; ADR 0006; constitution "The Dispatcher" |
| 5 | Results are unmerged branches; a Worker never pushes or merges | spec 3e, 4p |
| 6 | One Task Queue for all projects, the cockpit first | constitution "The Dispatcher"; owner-defaults "Production" |
| 7 | Worker bound to a project; isolation between Workers and projects | constitution "Context management"; spec 4j, 4k, 4l; commit c0ae6e5. **Not traced in the documents:** "warm clone with incremental updates", "pre-built stack" and "private namespaces" as requirements (brief F-746 only). |
| 8 | Dated synopsis plus project memory, no secrets, never in commits | constitution "Context management" |
| 9 | Processing data in one place for all projects | constitution "The Dispatcher" |
| 10 | Small, local, understandable by one person | constitution "Principles"; ADR 0009 |

**Earlier studies.** The overlap note (`overlap-2a-2b-2026.11.22.md`) recorded that both studies rejected Temporal, Airflow, Slurm and BPMN engines; that is not repeated here. The note also says "tasks needing local data cannot run in the cloud". That is now out of date: a Project thread can run on the owner's computer through Remote Control. Such a thread runs without project memory loaded [V, [Projects](https://code.claude.com/docs/en/claude-projects)].

## 1a. What the earlier reports already established (F-783)

Read on 2026-11-24 from git history without checking out (`git log --all -- docs/research/*`, then `git show <commit>:<path>`). Dates are those of the reports.

| Report (date) | What it covered; main finding | This report: agrees, differs (why), reuses |
|---|---|---|
| orchestration-fit-gap-a, -b (2026-11-22) | Under the older brief: no workflow engine; a stateless reconciliation loop over GitHub; at-most-once starts by an intent marker read back; WIP limit by Little's law; hybrid concurrency control | Agrees on no engine and on one source of task state. The WIP limit was later dropped by the owner (synthesis, "no review-backlog cap"), so it is not proposed here. |
| sota-ai-orchestration, study C (2026-11-22) | Literature on graphs, lazy execution and scheduling for LLM agents; ideas transfer, products do not | Not repeated; theory unchanged |
| review-orchestration (2026-11-22) | Engines do not remove the claim-to-start window; mutual exclusion really rests on one dispatcher on one machine | Agrees: M1 keeps one Dispatcher on one host. Reused as the reason against M3 and M5's second queue. |
| study2-a, study2-b (2026-11-22) | Adopt the vendor cloud (scheduled tasks, Projects), GitHub as queue, PRs as acceptance | **Differs**: since then the owner specified a local Dispatcher, no push (3e) and a local log (5c). The cloud remains row M4. |
| review2-orchestration (2026-11-22) | Issue triggers cannot be filtered by label; the cloud proxy blocks GraphQL; branch protection unavailable on the private repository's plan [observed]; 25 of 44 released tasks need local data | Reused: these facts still weigh against M4. The local-data count supports local Workers (M1). Not re-verified here. |
| decision-memo (2026-11-22) | Merged five sources; a no-regret core (complete reads or no decision, claim before start); rejected Baton (3 commits, idle since 2026-03-27) | Agrees; Baton would also fail the trust rules (F-779) |
| build-baseline (2026-11-22) | The build: tick, processing log, heartbeat, limits and kills, good-bye note, launcher, adopted sandbox | It is the "keep building" row of §0.4 |
| oss-survey (2026-11-22) | No open-source framework covers the dispatcher's defining needs (user and clone per worker, kill policy, verified note, counting proxy); engines supply the cheap parts; AI orchestrators (Symphony and ports, Bernstein, Gas Town, vibe-kanban and others) conflict with no-retry and no-merge | Agrees and reuses its engine and orchestrator verdicts. This report adds issue-to-PR runners not named in its summary (Kelos, Optio, ProPR, Cyrus, CodeForge). Their verdict is the same: pattern, not borrow. |
| deep-three (2026-11-22) | Adopt no engine; build with a seam; DBOS as fallback; engines cannot kill a process inside a container (moby#9098); large deadwood | Agrees; the kill point reappears in the seam review. Its DBOS and Temporal fallback is kept implicitly (out of scope here, since no engine is proposed). |
| apple-container-vs-orbstack (2026-11-22) | Apple's tool wins on egress (no-network mode plus a socket relay) if three hands-on tests pass on macOS 15.3; otherwise upgrade or OrbStack with a weaker boundary | Agrees; the tests later passed (seam review §1). New here: the Linux host option (F-748), kept neutral (F-754). |
| sandbox-options (2026-11-22) | The boundary must be the OS or a VM, not the agent's tool lists; a linked worktree is not self-contained (its `.git` points outside) | Agrees; spec 4k (separate clones, no hardlinks) already follows it |
| feasibility-container-networking (2026-11-23) | Documentation and source of Apple's tool; not run at first (no installation) | Superseded by the later observed run (seam review §1) |
| synthesis-build-vs-borrow (2026-11-23) | Build the small dispatcher, adopt components; credential through a Console API key, because a host proxy injecting a subscription token "conflicts with the vendor text" | Agrees on build-with-seam. **Differs on the credential**: the owner has since specified subscription access (spec 4g) and recorded his reading of the terms for his single-user case (F-755 to F-757). §9 quotes the same vendor text and finds no clear conflict with that use case. The synthesis' reading is not discarded; it remains the documented alternative, and the decision is the owner's. |
| trenches (2026-11-22) | Practitioner reports: silent failure dominates; review is the bottleneck; deterministic machinery outside the model gets the credit | Agrees; supports keeping the deterministic decision core and the end check. Reused instead of repeating a practitioner survey. |

**What is new in this report** compared with all of the above:
- the spine items (Worker images, a network policy per task kind, context management), from owner needs raised after 2026-11-23 (F-759, F-760, F-763);
- the Claude-only and trust rules (F-767, F-777 to F-779);
- the Linux host option (F-748);
- the gap-coverage method (F-772).

## 2. Task isolation (spine 1)

**Today** [V, code and commits]:
- One long-lived Apple `container` for all Workers (spec 4b, 4c), with one memory limit of 4 GB for the six Workers (`runtime.py:50`).
- No network interface. The only way out is the proxy socket, through a relay (4f, 4s).
- Unprivileged `workerN` users with mode-700 homes.
- A private user and mount namespace (`unshare -Urm`). It exists because the shared mount ignores file modes between users (runtime.py:91-92, c0ae6e5).
- One clone per task (4k, 4l).
- The credential stays outside the Worker, injected by the proxy (4g).

**What is missing:**
- A per-task memory and CPU limit.
- A boundary that does not rest on Apple's tool 1.7.1 on macOS 15. The tool supports only macOS 26, and `--network none` worked against its macOS 15 documentation (container-seam review §1 [V]).
- Per-project separation beyond the task folder [I].

| Candidate | Unit of isolation | Network | Per-task limits | Host | Evidence |
|---|---|---|---|---|---|
| Ours (today) | User + namespace in one shared container | None but the proxy | One shared cap | Laptop | code [V] |
| One container per task (Docker or Podman) | Container per task, image per project | `--network none` or an internal network [I] | `--memory`, `--cpus` per container [I] | Linux (native); macOS through a VM | [I]; the Apple tool runs one VM per container (seam review sources) |
| Vendor dev container with egress firewall | Container | Default-deny `init-firewall.sh`, needs `NET_ADMIN` [V] | Docker flags | Docker | [devcontainer](https://code.claude.com/docs/en/devcontainer) |
| Vendor sandbox runtime `srt` | The whole Claude Code process (Seatbelt or bubblewrap) | "denies network access" by default; allowlist per domain [V] | Not mentioned [I] | Both; beta research preview [V] | [sandbox environments](https://code.claude.com/docs/en/sandbox-environments) |
| Docker Sandboxes | microVM with its own Docker daemon [V] | — | — | Docker product | same page |
| Cloud sessions / Projects | "each session gets a fresh virtual machine" [V] | Limited by default; can be disabled while the model stays reachable [V] | Vendor's | Vendor cloud | [cloud environments](https://code.claude.com/docs/en/cloud-environments), [cloud](https://code.claude.com/docs/en/claude-code-on-the-web) |
| Slurm | Job in cgroups ("Confine jobs ... to specific memory resources") [V] | Not provided [I] | `--mem`, `--time` [V] | Linux only [V] | [cgroups](https://slurm.schedmd.com/cgroups.html), [platforms](https://slurm.schedmd.com/platforms.html) |
| Kubernetes + Kueue | Pod per job | NetworkPolicy [I] | Requests and limits [I] | Linux | [Kueue](https://kueue.sigs.k8s.io/docs/tasks/run_jobs) |
| Sculptor, container-use | Docker container per agent [V] | Varies | Docker | Docker | [Sculptor](https://docs.imbue.com/features/containers), [container-use](https://dagger.io/blog/agent-container-use) |

**The credential.** If the Worker holds its own token instead of using the proxy, the vendor warns that a dev container does "not prevent ... exfiltrating anything accessible inside the container, including the Claude Code credentials" [V]. With egress limited to the model host, the path left is a commit carrying the token [I]. A secret scan of the bundle in the end check would close it, about 10 lines [I].

### Network policy per task kind (F-762, F-763)

**What no network buys** [I]:
- private repository content cannot leave except through the model API;
- a prompt injection cannot reach out to other hosts;
- runs depend only on the image and the bundle, so they are reproducible;
- isolation is cheap: one flag instead of a firewall to maintain.

**What it costs:**
- dependencies and tools must be prepared at build time (section 3);
- a task that needs the internet needs a different kind of Worker, with an allowlist the owner names and grants.

**The requirement this implies** (F-763): a network policy per task kind or per project, held by the owner, default none, with named allowlists where needed. How the candidates support it:

| Candidate | Different policy per job? | Cost |
|---|---|---|
| Ours (today) | No: one container with no network for all Workers (4b, 4c) [V] | A second container or a per-Worker proxy allowlist would have to be built [I] |
| Container per task (Docker or Podman) | Yes: network chosen per container (`none`, or an internal network to an allowlisting proxy) [I] | Small; the policy becomes a field of the task kind [I] |
| `srt` | Yes: a settings file per run, passed with `--settings` [V]; default "denies network access" [V] | A file per policy |
| Vendor cloud environments | Yes: one access level and allowlist per environment ("None", "Limited" with allowed domains, "Full"), plus "Allow package managers" [V, [cloud environments](https://code.claude.com/docs/en/cloud-environments)] | An environment per policy, chosen per session |
| Slurm, pueue, GitHub runner | No network policy of their own [I] | Needs a container underneath anyway |

The no-network default is easy or default in our design, in `srt` and in a container per task. The cloud defaults to "Limited", not none [V]. Candidates that install packages during a task, such as cloud setup scripts and the "Allow package managers" default, count against unless the install moves to the build step, which the cloud cache in effect does [V: the snapshot is taken after setup].

**Verdict.**
- Laptop: keep today's isolation. It works.
- Borrow per-task containers on a Linux host, which retires the relay and the namespace workaround [I]. Alternatively borrow `srt` on the laptop if the Apple tool breaks.
- The per-task limit is the only real gap.
- Burden of proof (F-750): the closest candidate is a per-task Docker container. It lacks nothing verified, so on Linux it is the part to borrow. On the laptop the Apple tool gives one VM per container [I from the seam-review source], which multiplies memory, so the shared container stays there.

## 3. Worker image management (spine 2)

**Today** [V, `dispatcher/image/Containerfile`]:
- One image, `cockpit-worker:latest`: python 3.12 on Alpine, git, pytest, ripgrep, the Claude binary "copied there by hand; not committed", the relay, the Stop hook and the skill.
- Six users are baked in.
- The image is built by `container build`, through `Runtime.build_image`.
- "Nothing is fetched at run time."
- Missing:
  - **project dependencies**: a Worker with no network cannot install a project's packages, so a project whose tests need more than python and pytest cannot run [I];
  - **a version or cache key**: the tag is `:latest`;
  - **a rebuild trigger** and **a pinned agent version**.

| Model | Recipe | Cache key / version | Rebuild trigger | Per project | Dependencies without network | Source |
|---|---|---|---|---|---|---|
| Containerfile + layer cache (Docker, Podman, Apple `container build`) | Containerfile held by the owner | Layer cache: "If a layer changes, all other layers that come after it are also affected" [V]. A tag from a hash of the recipe plus lock files is a convention [I]. | A changed recipe or lock file | A base image plus `FROM base` per project [I] | Installed at build time from the lock file [I] | [Docker cache](https://docs.docker.com/build/cache/) |
| Dev Container spec (`devcontainer.json`) | Image or Dockerfile, plus Features, plus `onCreate`/`updateContent`/`postCreate` [V] | `build.cacheFrom` [V]; images "that can be uploaded and used by other users" [V] | Recipe change | One file per repository, read by the vendor's tooling [V] | Build or `onCreate` step | [spec](https://containers.dev/implementors/spec/) |
| Nix flakes | `flake.nix` | `flake.lock` pins every input [V]; "reproducibility is not actually guaranteed" [V] | Lock change | Per repository | Pre-built store [I] | [nix.dev](https://nix.dev/concepts/flakes.html) |
| Vendor cloud environments | A setup script per environment [V] | A filesystem snapshot, cached only if setup takes under about 5 minutes [V] | "when you change the environment's setup script or allowed network hosts, and when the cache reaches its expiry after roughly seven days" [V] | One environment per project [I] | Setup may use the network; "Allow package managers" [V] | [cloud environments](https://code.claude.com/docs/en/cloud-environments) |
| Slurm, pueue, HTCondor | None. Jobs run in the node's environment [I]. | — | — | — | — | — |

**Verdict: borrow the Containerfile-plus-layer-cache model, which we already half use.** Add:
- one base image (agent, git, hook, skill, users);
- one small Containerfile per project (`FROM base`, install from the project's lock file);
- a tag equal to a hash of the base and project recipes plus the lock files;
- a check at each firing that the tag of the task's project exists. If it does not, skip the firing with the reason "image missing". Rebuilding stays a separate step.

Effort: about a day for the build script and the tag check [I]. Disk: one base plus a thin layer per project [I].

A `devcontainer.json` per repository is the borrowed format if the owner wants the recipe to live with each project and be usable interactively [V spec]. It is optional.

Pin the agent version in the recipe. The vendor documents `npm install -g @anthropic-ai/claude-code@X.Y.Z` with `DISABLE_AUTOUPDATER=1` [V, devcontainer page].

Warm start: a Worker starts from the image, plus `git` from the task's bundle (3d). A persistent per-project mirror fetched incrementally is a small optional addition [I] (need 7, untraced).

## 4. Agent sessions and context management (spine 3)

**Today, in the Workers** [V, code]:
- A per-Worker `CLAUDE_CONFIG_DIR` with fixed settings, the Stop hook and the good-bye skill (Containerfile).
- The task text in `inbox/task.md` and a fixed launch instruction (runtime.py:26).
- The repository's `CLAUDE.md` in the clone.
- The tool's auto memory is unused.
- No synopsis.
- Transcripts are mirrored by `tools/transcript_usage.py` (token counts) and by the daily backup (`transcripts/`, `docs/contracts/backup.md`).

**Today, hand-crafted in the owner's interactive setup, not yet applied to Workers** (F-765; read in the repository and in the owner-level instruction text available to this agent, no transcripts):

| Practice | Where | Reusable by Workers as it is? |
|---|---|---|
| Layered instruction files: user-level `CLAUDE.md` plus project `CLAUDE.md` | owner-level file; project `CLAUDE.md` (21 lines here) | **Project file: yes, already loaded from the clone.** User-level file: no. It is not in the Worker's config folder, and parts of it address the interactive roles (delegation, worktrees, pushing). A Worker-level extract would be needed. |
| File-based memory: a `MEMORY.md` index plus typed notes (user, project, feedback, reference) | the owner's auto-memory folder | **Mostly yes.** It is the vendor's auto-memory format [V memory page], so `autoMemoryDirectory` can point a Worker at it. `type: user` notes must be filtered out (personal). |
| Archive sanitised memories into `docs/agent-memories` | owner-level rule "Considerate Archivist" | **Yes, as read-only project context.** It is in the repository, so it is already in the clone. |
| Session report: a dated running log per session (`docs/session_reports/`) | owner-level rule "Session Reporting" | The latest report is the natural input for the synopsis. It is in the repository, so it is in the clone (when the project keeps it there). |
| Feedback log (owner remarks, F-numbers) | the first project's `docs/project/pm-poc/feedback-log.md` | Yes as reading, through the clone. Too long to inject whole [I]. |
| Dated session synopsis for Workers (PC-70) | constitution "Context management"; task PC-70 (feedback log F-742, F-743) | Not built. This is the one missing piece. |
| Skills | `.claude/skills/agent-brief` here; the Worker's good-bye skill | **Yes.** Repository skills sit in the clone [I: project skills load from `.claude/skills`]. |

**Missing** (constitution "Context management"):
- the dated synopsis;
- the owner's memory handed to the Worker;
- both visibly stale when old, and never in commits.

What the vendor's coding tool already offers [V]:
- **Project memory.** Auto memory lives in `~/.claude/projects/<project>/memory/` with a `MEMORY.md` index. Up to the first 200 lines or 25 KB of the index load at start. It is "shared across worktrees" and "machine-local". `autoMemoryDirectory` relocates it, and `autoMemoryEnabled` or `CLAUDE_CODE_DISABLE_AUTO_MEMORY` turns it off. [[memory](https://code.claude.com/docs/en/memory)]
- **Context at session start.** A SessionStart hook's `additionalContext` "passes a string from your hook into Claude's context window ... before the first prompt". [[hooks](https://code.claude.com/docs/en/hooks)]
- **Compaction.** A PreCompact hook can block compaction or instruct the summary. Every hook gets `transcript_path`. Stop can block and continue the session (already used, 4t). [hooks]
- **Resume and fork.** `--resume`, `--continue` and fork of sessions [[headless](https://code.claude.com/docs/en/headless), [Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview)].
- **Sub-agents.** [Agent SDK]
- **Projects (cloud).** Project memory files read by "Every cloud thread", project instructions "up to 16,000 characters", and "You don't manage context windows in a project". [Projects]

What Claude-specific frameworks add (F-761, F-767; only frameworks built for Claude or Claude Code are considered):
- **Claude Agent SDK.** The vendor's own library: sessions to "resume or fork later", hooks, sub-agents and memory loading, programmable in Python or TypeScript [V, [Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview)]. It "runs the Claude Code binary" [V], so a wrapper around a Worker uses the account the same way as today. It could replace `claude -p` in the launch if a programmatic wrapper is ever needed; for a one-shot Worker the CLI and hooks suffice [I].
- **Ruflo (formerly claude-flow).** "An agent meta-harness for Claude Code and Codex" with vector memory and a plugin to "Save and restore agent memory across sessions" [V, [repo](https://github.com/ruvnet/claude-flow)]. It is a large surface (swarms, 100+ agents) at odds with need 10, and its claims are self-reported [I].
- **Can they wrap a Worker that stays the vendor's tool?** Yes [I]:
  - a wrapper writes the synopsis and the memory extract before launch (into the memory folder or a SessionStart hook);
  - it harvests the good-bye note and the transcript after the session.
- For one owner and tens of tasks, a Markdown folder with an index does what a memory store does [I].
- Generic multi-model frameworks are left out (appendix A).

**Hand-crafted versus the frameworks.**
- The owner's practices already match what the vendor tool offers: instruction files, a `MEMORY.md`-indexed memory, skills.
- They go beyond the Claude-specific frameworks in curation: typed notes, a sanitised archive, dated logs.
- Those frameworks add programmatic sessions (the Agent SDK) or vector memory (Ruflo), which tens of tasks do not need [I].
- Recommendation: **keep the practices hand-crafted; borrow the tool's mechanisms to deliver them to Workers.**

**Recommendation: piggy-back first** (F-766; hours are estimates [I]):

| Need | Piggy-back route (configure or install) | Hours | Build route, only if needed |
|---|---|---|---|
| Worker attached to its project | Already the case: the clone carries `CLAUDE.md`, `.claude/skills` and archived memories | 0 | — |
| Project memory for the Worker | Set `autoMemoryDirectory` in the Worker's settings to a per-project copy of the owner's memory with `type: user` notes removed, mounted read-only [V setting; I mount] | 2-4 | A filter for `type: user` notes, about 20 lines |
| User-level rules that apply to Workers | A Worker-level `CLAUDE.md` in each Worker's config folder, extracted from the owner's file [V: user-scope `CLAUDE.md` is loaded] | 1-2 | — |
| Dated synopsis | Write: a skill or a SessionEnd hook in the interactive session that turns the session report into the synopsis (SessionEnd has "a 1.5-second budget" [V], so a skill fits better) [I]. Deliver: a SessionStart hook in the Worker that injects it with its age via `additionalContext` [V]. | 3-5 | The synopsis text itself is the only new content, written by the interactive agent. No code beyond a 10-line hook. |
| Stale visibly | The SessionStart hook prints the synopsis date and a "stale" line past a threshold | included | — |
| Never in commits | The memory and the synopsis live outside the clone (config folder and mount); the end check refuses a bundle that contains them [I] | 1 | A few lines in `note.py` |
| Resume, fork, compaction, sub-agents | Built into Claude Code [V]; nothing to do for one-shot Workers | 0 | — |
| Transcripts and history | Keep the existing mirror | 0 | — |

The closest Claude-specific candidate, Projects' memory, is cloud-only and is not loaded for local threads [V]. The Agent SDK and Ruflo would add a wrapper or a store that nothing needs. So no build route is needed beyond the few lines named.

## 4a. Traction in the Claude community (F-764; checked 2026-11-24)

The target agent is Claude Code and its SDK. The candidates are ranked by how closely they are built around it. Counts are from the repository pages on 2026-11-24 [V]; release dates were not visible on the pages.

| Rank | Candidate | Built for Claude Code? | Evidence | Likely in a year [I] |
|---|---|---|---|---|
| 1 | Claude Code's own mechanisms (hooks, auto memory, `-p`, `setup-token`, OpenTelemetry) | Is Claude Code | Vendor docs cited above | Yes. These are the product. Details change; the docs show frequent version notes. |
| 2 | Claude Agent SDK | Vendor | [python SDK](https://github.com/anthropics/claude-agent-sdk-python): 8.6k stars, 263 issues | Yes |
| 3 | Claude Code GitHub Action | Vendor | [repo](https://github.com/anthropics/claude-code-action): 10.1k stars, 2.3k forks, 498 issues | Yes |
| 4 | Sandbox runtime `srt` | Vendor, "built for Claude Code", "Beta Research Preview" | [repo](https://github.com/anthropics/sandbox-runtime): 7.3k stars, 896 commits | Likely, but the configuration format may change [V: README says so] |
| 5 | Projects, cloud sessions, scheduled tasks | Vendor | Docs above; beta or research preview | Likely; terms and limits move |
| — | container-use (Dagger) | Claude Code first among MCP agents | [repo](https://github.com/dagger/container-use): 4.1k stars; last commit 2026-08-12 | **Untrusted (stale)**, appendix B |
| 7 | Claude Squad | Several agents (Claude Code, Codex, Gemini, Aider); tmux and worktrees, no containers | [repo](https://github.com/smtg-ai/claude-squad): about 8.9k stars, AGPL-3.0 | Uncertain; interactive tool, no queue |
| 8 | Ruflo (formerly claude-flow) | "An agent meta-harness for Claude Code and Codex"; multi-model | [repo](https://github.com/ruvnet/claude-flow): about 82.3k stars, 10,644 commits; speed and security claims self-reported | Popular, but a large surface at odds with need 10 [I] |
| 9 | Sculptor (Imbue) | Claude Code and others; "experimental research preview" | [repo](https://github.com/imbue-ai/sculptor): 285 stars | Short-lived risk [I] |
| — | Slurm, HTCondor, Grid Engine, pueue, Docker/Podman, GitHub runners | Not tied to a model; in scope (F-767) | above | Maintained; only container tooling solves a spine item |

Generic multi-model frameworks and tools are not in this table (appendix A).

Last commits, read from the commit feeds on 2026-11-24 [V]: claude-code-action 2026-11-24, claude-agent-sdk-python 2026-11-23, sandbox-runtime 2026-11-12, Claude Squad 2026-11-13, Ruflo 2026-11-24, Sculptor 2026-11-23. All of these pass F-779. container-use (2026-08-12) does not and is untrusted (appendix B).

Open issues about using a Claude Code subscription with these tools were not searched issue by issue. That is a gap of this report.

## 5. Secondary: queueing, ranking, batch schedulers

**Batch schedulers (F-747).** The cluster concept mapping:
- **Hold and release** fit: `sbatch -H`, then release [V, [sbatch](https://slurm.schedmd.com/sbatch.html)]. But our "released" is derived from GitHub with the 10-day rule and no label event (spec 2b). A poller would have to mirror it into the scheduler. That is a second state store, the failure class of the night of 2026-11-20 (context digest).
- **Priority, QOS and fair-share** have no deadline, "unblocks" or must/nice factor [V, [priority](https://slurm.schedmd.com/priority_multifactor.html)]. QOS and limits need slurmdbd with MySQL or MariaDB [V, [accounting](https://slurm.schedmd.com/accounting.html)].
- **`afterok`** starts a job "after the specified jobs have successfully executed" [V]. Our blockers resolve on a human merge days later [I].
- **Licences** keep jobs "pending until licenses become available" [V, [licenses](https://slurm.schedmd.com/licenses.html)]. But the model quota is statistics only (3h), so a licence would only restate the Worker limit [I].
- **Prolog and epilog** exist; a prolog failure means "the node being set to a DRAIN state and the job requeued" [V, [prolog](https://slurm.schedmd.com/prolog_epilog.html)]. That is harsh on a single node.
- **`sacct`** counts CPU and memory, not tokens [I].
- **The 20-minute no-activity and 1-minute start checks** (3i, 3j) were not found in any scheduler read.
- **Hosts.** Slurm "does not currently" run on macOS [V]. HTCondor lists macOS [V, [condor_hold](https://www.mankier.com/1/condor_hold)], with concurrency limits [V, [HTCondor](https://htcondor.readthedocs.io/en/latest/admin-manual/cm-configuration.html)]. Grid Engine continues as Open Cluster Scheduler 9.1.0 (2026-03-12) [V, [gridengine.eu](https://gridengine.eu/index.php/grid-engine-specific-news/286-open-cluster-scheduler-910-and-gridware-cluster-scheduler-910-released-2026-03-12)]. pueue runs on both hosts, with groups, stash and enqueue, `--after` and `--priority`; timeouts are not mentioned [V, [pueue](https://github.com/Nukesor/pueue), [changelog](https://raw.githubusercontent.com/Nukesor/pueue/main/CHANGELOG.md)].

**Answer to F-747: a scheduler on one node plus a thin layer is not simpler than our Dispatcher.** The thin layer would still hold release, rank, the activity and start checks, the note-to-state step, the labels and the log, which is most of `core.py` [I]. The scheduler adds MUNGE and two or three daemons [V, [quickstart](https://slurm.schedmd.com/quickstart_admin.html)].

**Other runners.**
- **GitHub Action** [V, [action](https://code.claude.com/docs/en/github-actions)]: authenticates with `CLAUDE_CODE_OAUTH_TOKEN` and pushes. Concurrency groups keep one pending run by default, or up to 100 with `queue: max` [V, [concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)]. Container jobs need Linux [V, [runners](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)].
- **Cloud scheduled tasks** [V, [routines](https://code.claude.com/docs/en/routines)]: "research preview", Issue "labeled" triggers with hourly caps, pushes to `claude/` branches.
- **Self-hosted environments** [V, [self-hosted](https://code.claude.com/docs/en/self-hosted-environments)]: "public beta on Team and Enterprise plans" only.
- **Projects** [V]: a thread limit "isn't a hard cap"; threads open PRs.
- **Agent view** [V, [agent view](https://code.claude.com/docs/en/agent-view)]: interactive background sessions in worktrees.
- **Claude Squad** (tmux and worktrees, several agents) is an interactive tool with no queue [V]. container-use is untrusted (stale, appendix B).

Each of these lacks one of: deterministic choice (constitution "The Dispatcher"), no push (3e), or a local log (5c). These are owner rules, not conveniences. **Keep the Dispatcher's queue and ranking.**

### Prior art: hybrids of Claude Code with schedulers, runners and issue trackers (F-769, F-771, F-774 to F-779)

**Searches tried (2026-11-24):**
- Slurm: `"claude code" slurm sbatch headless "claude -p" HPC cluster`; `Claude Code agents Slurm job submission orchestrator github repository`.
- Other cluster managers: `"claude" "grid engine" OR qsub OR HTCondor OR LSF claude code agent batch jobs`; `hpc-agent pypi SGE SLURM ...`.
- HPC: `anthropic "long-running" Claude Code Slurm tmux compute node`; `Claude Code Apptainer Singularity container HPC compute node no internet`; `HPC centre documentation coding agents Claude Code compute nodes outbound internet proxy job array agents`; `ORNL claude-hpc wrapper container`.
- Workflow engines: `Claude Code Temporal workflow orchestration coding agents sandbox open source`.
- Kubernetes and issue loops: `Claude Code Kubernetes jobs agents queue github issues pull requests open source operator`; `github issues to slurm jobs claude code ...`; `self-hosted autonomous Claude Code issue label queue docker sandbox network allowlist pull request runner`.
- CI and queues: `Claude Code GitLab CI OR Buildkite OR Jenkins OR Celery headless agent jobs`.
- Write-ups: `blog "claude code" unattended queue github issues label docker sandbox ...`; `Hacker News "Show HN" Claude Code agents parallel containers issues queue orchestrator`; `anthropic engineering blog Claude Code autonomous agents sandbox container network isolation`.

**What the searches show.** Hybrids are common, but each one is partial:
- **Issue-to-PR runners on Kubernetes or Docker:** several trusted ones (Kelos, Optio, ProPR, Cyrus, CodeForge).
- **Claude Code under Slurm:** well documented by HPC centres.
- **Grid Engine:** only untrusted projects.
- **LSF, Buildkite, Jenkins and Celery:** nothing specific. The vendor documents GitLab CI/CD (beta) [V, [GitLab CI/CD](https://code.claude.com/docs/en/gitlab-ci-cd)].
- **Temporal:** one community proof of concept (no Claude Code named) and one untrusted repository.
- **Nothing found combines** an owner release gate, a no-network default, subscription access through the vendor's binary, per-project images and context hand-over. This suggests our mix of needs is uncommon rather than solved elsewhere [I].

**Repositories that pass the trust rules** (at least 10 stars and a commit since 2026-10-25; both read 2026-11-24 [V]). Reputation per F-782 is from the owner and organisation pages read on 2026-11-24 [V] unless marked.

| Repository | Stars; last commit | Set-up | Gaps addressed | Maturity | Reputation |
|---|---|---|---|---|---|
| [Kelos](https://github.com/kelos-dev/kelos) | 360; 2026-11-24 | Kubernetes controller. TaskSpawner turns GitHub issues (and Jira, Linear, cron, webhooks) into agent pods; workspaces with repo, setup command and image; task status records "token usage"; PRs opened "without merging it" | G1, G2, G4, G6, G7, G8 (P or C, see matrix) | Active; Apache-2.0; 102 open issues | Organisation with 12 repositories, the others 0-3 stars; no public members; 49 forks. A young organisation centred on one project [I]. |
| [Optio](https://github.com/jonwiggins/optio) | 1.1k; 2026-11-22 | Kubernetes, BullMQ and Postgres. Tickets from GitHub Issues or Linear lead to a pod per repo, Claude Code or Codex in worktrees, CI watched, auto-resume, optional auto-merge | G1, G2, G4, G9 | Active; MIT; [Show HN](https://hn.svelte.dev/item/47520220), 88 points | Single author with 74 followers and other non-trivial repositories (90, 75 and 27 stars); 137 forks of Optio; discussed on HN |
| [ProPR](https://github.com/integry/propr) | 40; 2026-11-24 | A labelled issue leads to a Docker container and worktree per run, then a PR. Subscription or key; optional allowlist firewall | G1, G2, G4, G5 | v0.9.3, pre-1.0; Apache-2.0 | Single author (13 followers); an older PHP project (17 stars); 9 forks. Low external reference: none found in the searches. |
| [Cyrus](https://github.com/ceedaragents/cyrus) | 913; 2026-11-24 | Issues assigned in Linear, GitHub or GitLab lead to a worktree per issue and an agent session, with approvals | G1, G2, G4 | Active; Apache-2.0 | Backed by a product with paid plans (README) [V]; 193 forks; the organisation page returned 404 |
| [CodeForge](https://github.com/freema/codeforge) | 15; 2026-11-23 | Go service: REST task queue, Docker, Redis progress, a `create-pr` endpoint, a "key pool (subscription mode)", cost and usage tracking | G1, G4, G7 | Small; MIT | Single author with 32 followers and several MCP repositories (187, 105, 73 stars); a dev.to write-up by the same author |
| [Sandcastle](https://github.com/mattpocock/sandcastle) | 9.3k; 2026-11-16 | Library: agents in bubblewrap, Seatbelt, Docker, Podman or cloud sandboxes, with branch strategies | G4, G10 | MIT; Claude Code not named on its page | Author with 56.9k followers and widely used repositories (`skills` 332k stars, `ts-reset` 8.7k); 996 forks |

Vendor repositories (Anthropic: `sandbox-runtime`, `claude-code-action`, `claude-agent-sdk-python`) and the established engines (Slurm by SchedMD, HTCondor, Podman, Moby) are maintained by established organisations. Their stars and commit dates are in the matrix (§0.2).

**Write-ups** (not filtered by stars). Per F-781 the linked repository is checked:
- **W**: a working system described, and its repository passes the trust rules.
- **P**: pattern source only; the linked tool fails the rules or cannot be checked.
- **S**: a sketch or documentation with no repository.

| Title; author or site; date | Linked repository: stars; last commit | Set-up described | Gaps | Mark |
|---|---|---|---|---|
| "Making Claude Code safer with sandboxing"; Anthropic engineering; 2025-10-20 [V, [post](https://www.anthropic.com/engineering/claude-code-sandboxing)] | `anthropics/sandbox-runtime`: 7.3k; 2026-11-12 | Network only "through a unix domain socket connected to a proxy server running outside the sandbox"; "effective sandboxing requires both filesystem and network isolation" | G4, G5 | W |
| "From GitHub issue to merged PR ..."; dev.to; posted 13 March, year not shown [V, [post](https://dev.to/hassan_4e2f0901edda/from-github-issue-to-merged-pr-building-an-ai-coding-pipeline-that-runs-itself-4gbk)] | `bing107/dev-agents`: link returned 404 | Issue label, `repository_dispatch`, self-hosted runner on a Raspberry Pi 4; filesystem YAML queue, priority by file-name prefix; per-project `flock`; Pro subscription; auto-merge | G1, G9 | P (repository unreachable) |
| "I built a small self-hosted runner for AI coding tasks"; Tomáš Grasl; dev.to; 15 February, footer 2026 [V, [post](https://dev.to/tom_grasl_798b66e85f7aa/i-built-a-small-self-hosted-runner-for-ai-coding-tasks-3a37)] | `freema/codeforge`: 15; 2026-11-23 | REST tasks, Docker, Redis; label trigger as middleware | G1, G4 | W |
| "Show HN: Optio"; jawiggins; about 7 months before 2026-11-24 [V] | `jonwiggins/optio`: 1.1k; 2026-11-22 | See Optio above; the author says agents have no egress restrictions | G1, G2, G9 | W |
| "Show HN: Fleet"; sermakarevich; about 5 months before 2026-11-24 [V, [thread](https://hn.svelte.dev/item/48256389)] | `sermakarevich/fleet`: 19; 2026-09-29 (stale) | `claude -p` sessions claim tasks from a git-backed queue (beads) in `~/.fleet`; `max_concurrent` 3; per-task logs; several subscriptions rotated | G1, G7 | P (untrusted, stale) |
| Kempner Institute handbook; Harvard; updated 2026-10-11 [V, [page](https://handbook.eng.kempnerinstitute.harvard.edu/s5b_agentic_ai_workflows/using_agentic_ai_on_the_cluster.html)] | none | `claude -p` in batch jobs; login in the home directory works "on every node and in batch jobs"; outbound access on compute nodes; resume from `~/.claude/projects/` | G6, G10 | S (institutional documentation) |
| ExCL `claude-hpc`; ORNL; guidance introduced at the July 2026 meeting [V, [docs](https://docs.excl.ornl.gov/quick-start-guides/claude-code)] | `code.ornl.gov/7ry/claude-hpc` (GitLab): stars and commit date not checked | Claude Code in an Apptainer container from `claude-code.def`; persistent container home with the login; repo at `/work`; Slurm configuration and MUNGE bound; network not stated | G3, G4 | P (repository unverified) |
| Duke DCC AI access [V, [page](https://dcc.duke.edu/dcc/ai-access/)] | none | An SSH proxy provisions or reuses a Slurm job; a watchdog ends idle jobs | G9, G10 | S |
| Purdue RCAC [V, [page](https://docs.rcac.purdue.edu/agentic-ai/running_agents/on_cluster/)]; NERSC [V, [page](https://docs.nersc.gov/development/coding-agents/claude-code/)]; JHPCE, 2026-10-05 [V, [page](https://jhpce.jhu.edu/sw/claude-example/)] | none | Agents on login nodes submitting `sbatch`; sandbox namespaces "often unavailable" there (Purdue); `setup-token` (NERSC); API key in settings (JHPCE) | G10 | S |
| NICHD BSPC, "Running agents in containers" [V, [page](https://nichd-bspc.github.io/llm/running-containers.html)] | not checked | Singularity on the NIH cluster; "These containers do NOT lock down network access" | G4 | S |
| Temporal "Sandbox Orchestration Harness"; 2026-05-07 [V, [post](https://temporal.io/blog/temporal-sandbox-orchestration-harness-the-missing-layer-for-running-agents)] | a temporalio "ephemeral-workers-poc" repository named in the post: not checked | Temporal plus sandbox providers; no coding agent named; proof of concept | G9 | S |
| Anthropic write-up on long-running Claude with Slurm and tmux | — | Seen in search results only; both URLs returned 404 | — | not verified |

**What a Slurm or Grid Engine plus Claude Code set-up looks like in these examples** [I, synthesised]:
- a job script or SSH proxy runs `claude -p` or a tmux session on a compute node;
- the login sits in the home directory or in `setup-token`;
- compute nodes have outbound network;
- context passes through files or `--resume`;
- Apptainer images are built ahead (ORNL).

None takes tasks from GitHub issues with a release gate, cuts the network to the model, or counts tokens per job.

**Does it change the recommendation? No.** The hybrids confirm M1's parts:
- container per task and per-project images (Kelos, Optio, ProPR, ORNL);
- a proxy outside the sandbox (Anthropic's own post);
- a small file queue on one box (dev-agents).

They also confirm that the queue and the release gate are where every existing tool differs from the owner's rules.

### Summary fit-gap, needs 1 to 10

| Candidate | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Our Dispatcher | + | + | + (unsupported tool) | + | + | − (one repo per run) | ~ | − | + | ~ |
| Projects + cloud | − | + | ~ | − | − | + | + (images by setup script) | + | − | + |
| GitHub Action, self-hosted runner | ~ | + | ~ | − | − | ~ | ~ | − | − | ~ |
| Per-task containers + layer-cached images (Linux) | n/a | + | + | n/a | + | n/a | + | n/a | n/a | + |
| `srt` (laptop or Linux) | n/a | + | + (beta) | n/a | + | n/a | ~ | n/a | n/a | + |
| Slurm, Grid Engine, HTCondor, Kueue | − | + | − | ~ | + | + | ~ | − | ~ | − |
| pueue | − | + | − | ~ | + | + | − | − | ~ | + |
| Claude Agent SDK (as a wrapper) | n/a | + | n/a | n/a | + | n/a | n/a | ~ | n/a | ~ |

Column 2 shows how a candidate uses the account (F-755):
- **+**: the vendor's binary with the owner's login, the same use as today. Every candidate left in scope is **+**.
- "6 − (one repo per run)": `dispatcher/core.py:478-504` takes a single `--repo` [V].

## 6. Hosts side by side (F-748, F-751, F-752, F-754)

| | Laptop (today) | Linux host (mini-PC or VM) |
|---|---|---|
| Isolation | Shared container, users, namespace workaround | Container per task, cgroup limits per task [I] |
| Model path | Socket + vsock relay + proxy, 104-byte path (4f) | Allowlisted egress or an internal network to the proxy [I] |
| Images | Apple `container build` | Docker or Podman with layer cache, image per project [I] |
| Scheduler of firings | launchd. A lifecycle fix was needed: every Worker had been killed seconds after launch (commit 214830b) | systemd timer, same one-shot firing [I] |
| Memory | Competes with interactive work; one 4 GB cap for six | Dedicated |
| Sleep | Handled (3i); little weight (F-752) | — |

**Cost of staying on the laptop** (hoops found in the repository):
- the unsupported tool version, and an egress guarantee observed against its documentation;
- the socket path limit and the relay;
- the launchd process-group fix;
- the private namespace;
- the kill-on-exec guarantee, observed only on 15.3.1;
- memory competing with interactive work.

**Sizing for about six Workers** [I]:
- A Worker runs only the agent; six fit in a 4 GB cap today (`runtime.py:50`; F-748).
- With project test runs: 6 to 8 cores, 16 GB RAM, about 512 GB SSD (task folders are kept, 3m; floor 10 GiB, 3n).

**New problems a Linux host would create** [I]:
- a token on a second machine (`setup-token` is documented "For CI pipelines, scripts, or other environments where interactive browser login isn't available" [V, [authentication](https://code.claude.com/docs/en/authentication)]);
- results fetched to the laptop over SSH, which keeps 3e;
- the GDrive backup credentials (5e) on the box;
- SSH-only administration and security updates;
- the laptop keeps the Cockpit (reading processing data remotely), interactive sessions and merges.

**What would make the move worthwhile:**
- per-task memory limits are needed;
- images per project multiply;
- ten Workers are wanted (4a);
- or the Apple tool breaks.

**No hardware purchase is recommended.** A Linux VM can test the same image first.

## 7. Recommendation: an incremental path (F-758)

**Host: the laptop.** Every step also works on Linux.

**Working pieces: keep** [V that they exist; working per the owner, F-758]:

| Piece | Keep? |
|---|---|
| Dispatcher loop (`core.py`) | Keep |
| Proxy and relay | Keep while on the Apple tool |
| Runtime adapter | Keep the seam |
| Ranking (`tasks.py`) | Keep |
| Processing log | Keep |
| Inbox and outbox | Keep |
| Stop hook and note | Keep |
| Per-user isolation | Keep |

No rip-and-replace: no verified reason was found that any of them cannot stay.

**Missing bits**, in order of value:

| # | Missing bit | Borrow or build | From | Effort [I] |
|---|---|---|---|---|
| 1 | **Images per project** (dependencies without network, version tag, rebuild check) | Borrow | Containerfile + layer cache; base image + `FROM base` per project; hash tag; pinned agent version | ~1 day |
| 2 | **Context management** (project memory, dated synopsis) | Piggy-back (section 4) | `autoMemoryDirectory`, a Worker-level `CLAUDE.md`, a SessionStart hook, a skill to write the synopsis from the session report | about 8-12 hours |
| 3 | **One queue for all projects** (today one `--repo`) | Build (small) | The cockpit's `--projects` file (ADR 0012) as input; rank across projects with cockpit first | ~1 day |
| 4 | **Per-task resource limits, and a network policy per task kind** (default none, owner-named allowlists) | Borrow | Container per task with `--memory`/`--cpus` and a network per container (Linux); `srt` settings per run (laptop); the policy is a field of the task kind | 1-2 days, with the host step |
| 5 | **Accounting of the shared quota** | Borrow | The proxy's counts (4h) stay; optionally the vendor's `claude_code.token.usage` per `session.id` [V, [monitoring](https://code.claude.com/docs/en/monitoring-usage)] | small |
| 6 | **Scheduling of the daily job** | Borrow | launchd / systemd timer, as the Dispatcher does | small |
| 7 | **Failure handling and retries** | Build (setting exists, 3f) | — | small |
| 8 | **Host choice** | Owner's decision | Section 6 | — |

**First step that fits in a day:**
1. Split the image into a base image and one project layer for a second project.
2. Tag both with a recipe-plus-lock-file hash.
3. Add the "image missing" skip to the firing.
4. Run one real task of that project end to end.

The Task Queue's own tasks are `interactive-only` (spec 2b).

**Second step:** context management (item 2), tried on one task. Check that the synopsis's age is shown and that it never enters a commit.

## 8. The honest case against switching further

- **The cloud** (Projects, cloud environments) has the best isolation (a fresh VM per session), the best image management (setup-script snapshots with rebuild triggers) and the best context management (project memory). Its costs:
  - lock-in to a research preview or beta;
  - Claude chooses the threads;
  - pushes to GitHub;
  - vendor-side logs.
- **`srt` and dropping the proxy** would remove plumbing. They would also put the credential inside the Worker and replace a VM boundary with a process sandbox, while `srt` is in beta [V].
- **A Linux host** removes the macOS hoops, but adds a second machine to secure and to back up.
- **In every option the decision core stays ours.** The saving is in plumbing, not in what the owner reasons about.

## 9. The vendor's terms (F-755 to F-757; no legal opinion)

**The owner's use case, as the owner states it** (F-756): one person, his own account, his own projects, no third-party users, and no one else's requests passing through the Task Queue. **The owner's interpretation** (F-755): the subscription allows use on several hosts, and the terms aim at token resale and end-user intermediation.

The relevant sentences [V]:
- **Several hosts.** The [consumer terms](https://www.anthropic.com/legal/consumer-terms) (effective 2025-10-08) have no clause about devices. They say "You may not share your Account login information ... or make your Account available to anyone else."
- **Scripted use.** The consumer terms forbid access "through automated or non-human means ... Except when you are accessing our Services via an Anthropic API Key or where we otherwise explicitly permit it". The vendor documents `claude setup-token` for "CI pipelines, scripts" with a subscription [authentication], and `claude -p` "for scripts and CI/CD" [headless].
- **Resale and intermediation.** The [legal page](https://code.claude.com/docs/en/legal-and-compliance) says third-party developers may not "route requests through Free, Pro, Max, or Ultra plan credentials on behalf of their users". The [usage policy](https://www.anthropic.com/legal/aup) forbids to "Resell, proxy, or otherwise provide access to Claude through unauthorized means".

Read against this use case, nothing in these texts was found to conflict with it: the vendor documents scripted use of its own tool with the owner's subscription, and the restrictive sentences concern other users and resale. The decision is the owner's.

Every candidate left in scope uses the account the same way as today's Workers: the vendor's binary with the owner's login.

## 10. Open questions for the owner

- **Q1.** Images: may each project carry its own image recipe (a Containerfile or `devcontainer.json` in its repository), or should the recipes live in the cockpit?
- **Q2.** Need 7 ("warm clone, pre-built stack, private namespaces") is not in the specification or the constitution. Should it be added?
- **Q3.** Should the synopsis be written by the interactive session at its end (a hook or skill), or by the daily job?
- **Q4.** Should the move to a Linux VM be tested as soon as per-task limits are wanted, or only when the Apple tool breaks?

**Least certain** [I]:
- that a base-plus-project image layout fits every project without network at run time (for example, projects that download data or models during tests);
- how much of `core.py` a batch scheduler would really replace;
- that `autoMemoryDirectory` plus a read-only mount behaves well for six concurrent Workers sharing one project memory.

## Appendix A. Left out: not Claude-specific (F-764, F-767)

None of these is the only way to meet a spine item:
- **LangGraph** ([repo](https://github.com/langchain-ai/langgraph); checkpointers and a cross-thread Store [V, [persistence](https://docs.langchain.com/oss/python/langgraph/persistence)]);
- **CrewAI**;
- **Microsoft Agent Framework / AutoGen** (AutoGen in maintenance mode [V via its repository notice]);
- **OpenHands** (models "supported by LiteLLM" [V, [docs](https://docs.openhands.dev/openhands/usage/llms/llms)]);
- **SWE-agent** (superseded by mini-swe-agent [V, [repo](https://github.com/SWE-agent/SWE-agent)]);
- **Vibe Kanban** ("sunsetting" [V, [README](https://raw.githubusercontent.com/BloopAI/vibe-kanban/HEAD/README.md)]).

## Appendix B. Untrusted repositories (F-777 to F-779; checked 2026-11-24)

Untrusted code is never to be installed or run in our set-up. Only its ideas may be read as a pattern, and only after the owner's review.

- [husk](https://github.com/MeteoSwiss/husk): 4 stars; last commit date not visible. Untrusted (below 10 stars). Idea: a CONNECT-only proxy with an allowlist per job, and a Slurm broker outside a seccomp and bubblewrap sandbox.
- [ResearchLoop](https://github.com/researchloop/researchloop): 2 stars; last commit date not visible. Untrusted (below 10 stars). Idea: `claude -p` steps chained with `--resume` inside Slurm or SGE jobs.
- [sagaflow](https://github.com/npow/sagaflow): 0 stars. Untrusted (below 10 stars). Idea: Temporal activities, with results surfaced by a SessionStart hook.
- [k3sc](https://github.com/abix-/k3sc): 0 stars. Untrusted (below 10 stars). Idea: labels as a state machine (ready, claude-X, needs-review, needs-human) and `max_slots`.
- [Fleet](https://github.com/sermakarevich/fleet): 19 stars; last commit 2026-09-29. Untrusted (stale).
- [container-use](https://github.com/dagger/container-use): 4.1k stars; last commit 2026-08-12. Untrusted (stale).
- [claude-code-containers](https://github.com/ghostwriternr/claude-code-containers): 246 stars; last commit 2025-06-24. Untrusted (stale).
- [hpc-agent](https://pypi.org/project/hpc-agent/): repository and stars not found. Unverified, not a candidate.
- dev-agents (its repository link returned 404). Only the write-up counts.
