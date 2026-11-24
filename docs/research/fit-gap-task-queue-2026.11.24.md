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
| 6 | container-use (Dagger) | Claude Code first among MCP agents | [repo](https://github.com/dagger/container-use): 4.1k stars, "stability-experimental" | Uncertain: experimental |
| 7 | Claude Squad | Several agents (Claude Code, Codex, Gemini, Aider); tmux and worktrees, no containers | [repo](https://github.com/smtg-ai/claude-squad): about 8.9k stars, AGPL-3.0 | Uncertain; interactive tool, no queue |
| 8 | Ruflo (formerly claude-flow) | "An agent meta-harness for Claude Code and Codex"; multi-model | [repo](https://github.com/ruvnet/claude-flow): about 82.3k stars, 10,644 commits; speed and security claims self-reported | Popular, but a large surface at odds with need 10 [I] |
| 9 | Sculptor (Imbue) | Claude Code and others; "experimental research preview" | [repo](https://github.com/imbue-ai/sculptor): 285 stars | Short-lived risk [I] |
| — | Slurm, HTCondor, Grid Engine, pueue, Docker/Podman, GitHub runners | Not tied to a model; in scope (F-767) | above | Maintained; only container tooling solves a spine item |

Generic multi-model frameworks and tools are not in this table (appendix A).

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
- **Claude Squad** (tmux and worktrees, several agents) and **container-use** are interactive tools with no queue [V repos above].

Each of these lacks one of: deterministic choice (constitution "The Dispatcher"), no push (3e), or a local log (5c). These are owner rules, not conveniences. **Keep the Dispatcher's queue and ranking.**

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
