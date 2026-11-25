# Deployment and role separation

Modules: devops

How to run the cockpit, and how far the Manager / Orchestrator / Worker separation of [the constitution](constitution.md) can really be enforced. Examples use the repo `acme/widgets`, the Manager login `alice`, a module `alpha`. Decision record: [ADR 0011](adr/0011-deployment-and-role-separation.md).

**Read this first.** In phase 1 (one GitHub identity for everyone) nothing is enforced; the rule is followed by agreement. Enforcement starts only when each role has its own identity (phase 2) and its own credentials that the other roles cannot read (phase 3). Section labels below: **[GitHub]** enforced by GitHub, **[OS]** enforced by the operating system, **[after]** reverted or flagged after the fact, **[convention]** nothing stops a violation.

## Installation workflow

One ordered path from "I have a project" to "the Cockpit shows it, the daily job and the Task Queue run for it". The README has the same steps in a nutshell; this is the detail. Markers: **by hand** (a person does it; the wrinkle rule of the constitution wants it automated or covered by an open task), **planned: PC-nn** (the command does not exist yet; do not expect it to work). Examples use the project `widgets`, the repo `acme/widgets` cloned at `~/src/widgets`, the Cockpit at `~/src/project-cockpit`.

### 0. The one command

    python3 tools/install.py [--dry-run] [--self-test]

Run from the clone (step 2). It does steps 1 and 3 to 7 below in order, interactively, and is idempotent. Each question has a default; a yes/no question shows its default in capitals (`[y/N]`, `[Y/n]`).

| Order | What it does | Writes or runs (after the plan is confirmed) | Expected output |
|---|---|---|---|
| 1 | The latest `python3.N` on PATH, else the running one | nothing | `python: PATH`; a `warning: Python 3.N ... is not the tested version 3.14` line when it is not 3.14; below 3.10 it stops with what to install (exit 2) |
| 2 | git, `gh`, `gh auth status`, the model credential (default `~/.config/project-cockpit/model-token` or `~/.local/share/project-cockpit/model-token`, or the typed path) | `~/.config/project-cockpit/install.json` (0600): the credential's path and the project folders, never a secret | `git: found`, `gh: found`, `gh login: found`, `model credential: found (PATH)`; anything else stops the run with `stopped, fix these first:` and one fix per line (exit 2) |
| 3 | Asks for the parent folders (default: those of `~/src`, `~/code`, `~/projects`, `~/dev`, ... that exist) and reads each sub-folder's `.git/config` | nothing | `Projects found:` and one line per clone: its GitHub `OWNER/NAME`, `no GitHub remote, skipped` or `(already installed)` |
| 4 | Per new project: `install the Cockpit in OWNER/NAME?` (default no) | `kit/setup.sh OWNER/NAME`; the entry in `~/.config/project-cockpit/projects.json`; the first collectors (step 6); `project_sync init` is skipped (planned: PC-92) | a declined project is not touched |
| 5 | The viewer service (default yes) | the LaunchAgent `~/Library/LaunchAgents/com.project-cockpit.viewer.plist` (macOS) or the unit `~/.config/systemd/user/project-cockpit.service` (Linux), then `launchctl bootstrap` or `systemctl --user enable --now` | `viewer service: already installed` on a second run |
| 5 | The daily upkeep job | nothing (planned: PC-91) | `daily upkeep job: planned: PC-91: skipped` |
| 5 | The Dispatcher and the quota collector, macOS with the `container` tool only (default yes) | the two launchd jobs of the one queue under `~/.local/share/project-cockpit/task-queue/launchd/`, loaded quota collector first | `Dispatcher: already installed` on a second run; the image build stays by hand (5) |
| 7 | The self-test | runs the gap check per project | `ready`, or `missing:` and one `- WHAT: FIX` line each (exit 1) |

Before doing anything it prints `Plan:` with every file it writes and every command it runs, and asks `Carry out these N steps?`; `--dry-run` stops after the plan (`dry run: nothing was written or run`). A command that fails prints the exact lines to run by hand. `--self-test` runs step 7 alone. Tests: `tests/tools/test_install.py` (steps, whole-run scenarios in a sandbox, no-leak) and `tests/test_install_commands.py` (drift).

### 1. Prerequisites (by hand)

| What | Why | Check (expected output) |
|---|---|---|
| macOS on Apple silicon | the Dispatcher runs under launchd, the Workers' image is Alpine arm64 and runs in Apple's `container` tool; the viewer alone also runs on Linux (systemd unit in 5) | `uname -sm`: `Darwin arm64` |
| Python 3.14, stdlib only | everything in this repository | `python3 --version`: `Python 3.14` |
| `node` on the machine that merges | `tools/merge_gate` runs the page-code tests with it and fails without it | `node --version`: any version |
| `git` and the GitHub CLI `gh`, logged in as the Manager | the Cockpit acts as the `gh` login and stores no GitHub token | `gh auth status`: `Logged in to github.com account LOGIN` |
| Apple's `container` tool, its service started | the Workers' container | `container system status`: the service is running |
| The model credential in a file outside git, mode 0600 (the owner's subscription token) | the Dispatcher's proxy adds it to the Workers' requests; it is never logged | `ls -l TOKENFILE`: `-rw-------` |
| The Claude Code binary for Alpine arm64, named `claude`, in the image build context | the Workers' image fetches nothing at run time (`dispatcher/image/Containerfile`) | `file CONTEXT/claude`: an ELF arm64 executable |
| The owner's status-line script writing `~/.local/share/pmpoc/quota.json` | the quota collector reads it; without it the Budget and Token Usage pages show no quota | the file exists and changes while a session runs |
| `"cleanupPeriodDays": 3650` in `~/.claude/settings.json` | transcripts feed the token statistics and the backups | `metadata_gaps.py` stops reporting `transcript_retention` |

Created by hand and why: the `gh` login and the model credential are the owner's secrets (no tool may create them for the owner); the `claude` binary is not committed (licence and size); the status-line script lives in the owner's tool settings. The one command checks every row it can and names the fix; installing software (Python, git, `gh`, the `container` tool) is outside what it may do. Only the `gh` login and the model credential are by hand by nature; the other by-hand rows (software, the `claude` binary and image, the status-line script, the retention setting, the queue's `projects` setting) need an open task each (wrinkle rule): to be filed (PC-100 note).

### 2. Get the Cockpit

    git clone https://github.com/nnikolo1/project-cockpit ~/src/project-cockpit

Expected: a clone with `cockpit.py` at its root. Nothing is installed elsewhere.

### 3. Add a project

Done by the one command (0, order 4), which runs these by-hand steps 1 and 2 for each project you confirm. **Planned: PC-92.** `python3 tools/project_sync.py init` will create the project's `.cockpit/` folder with the templates of the manifest, the module map and the footprint declaration. Until then, by hand:

1. Labels in the project's repo: `kit/setup.sh acme/widgets` (idempotent; prints one line per label created or updated).
2. The project's entry in `~/.config/project-cockpit/projects.json` (format: README, "Several projects"), for example `{"default": "widgets", "projects": [{"name": "widgets", "repo": "acme/widgets", "git": "~/src/widgets", "modules": "~/src/widgets/docs/cockpit-modules.json"}]}`.
3. A module map (`{modules:[{id,name}], assign:[{path,module}]}`, the Cockpit's own is `docs/cockpit-modules.json`) and a footprint declaration (the Cockpit's own is `docs/footprint-declared.json`) committed in the project.
4. Optional: `kit/.github` to `.github` in the project (issue forms, policy Action), see 4, phase 1.

### 4. Start the Cockpit viewer as a service

Done by the one command (0, order 5). By hand: write the LaunchAgent (macOS) or the systemd user unit (Linux) of section 5 with `--projects ~/.config/project-cockpit/projects.json --token-file ~/.local/share/project-cockpit/token`, then load it (table in 5). Expected: `launchctl print gui/$(id -u)/org.example.project-cockpit | grep state` shows `state = running`; the check of "Token, upgrade, check" answers `303`, then JSON. A trial without a service: `python3 cockpit.py --projects ~/.config/project-cockpit/projects.json` prints the `http://127.0.0.1:8795/?t=TOKEN` URL.

### 5. Install the daily upkeep job and the Dispatcher

- **Daily upkeep job, planned: PC-91 (with PC-92).** Until then its steps run by hand, per project, in this order: `tools/code_snapshot_refresh.py`, `tools/cardmeta_collect.py`, `tools/module_map_upkeep.py`, `tools/metadata_gaps.py`, `tools/compliance_check.py --all --file-findings`, `tools/ci_main.py --repo OWNER/NAME --file-task`, `tools/project_backup.py` (each in the README).
- **The Workers' image (by hand)**: copy the `claude` binary to the root of a build context that holds this repository's layout, then `container build --dns 1.1.1.1 -t cockpit-worker:latest -f dispatcher/image/Containerfile CONTEXT`. Expected: the build ends with the image tag; `container image list` shows `cockpit-worker`.
- **The Dispatcher and the quota collector**: `python3 -m dispatcher.launchd render --project widgets --repo acme/widgets --repo-dir ~/src/widgets --token-file TOKENFILE` prints the paths of the two property lists and the two `launchctl bootstrap gui/$(id -u) FILE` commands; run those **by hand**, quota collector first. Expected: `launchctl print gui/$(id -u)/com.project-cockpit.dispatcher.widgets` shows the job; the Settings page shows the Dispatcher's values (it is on by default; switch it off there to install without starting Workers).

### 5a. Migrating to the one Task Queue (PC-67)

Before: one Dispatcher job, one quota job and one data folder per project (`com.project-cockpit.dispatcher.NAME`, `<data dir>/NAME/`). After: one queue for all projects ([specification](specs/dispatcher-and-workers.md) 3a, 3c, 4a, 5; [contract](contracts/queue-interfaces.md)): one Dispatcher job, one state file, one proxy, one container, one Worker pool, one data root. Only the projects the settings list are served; today that is the Project cockpit only. Steps marked **by hand** are done by a person.

**What changes**

- The job: the Label loses its project suffix (`com.project-cockpit.dispatcher`; the quota job likewise) and the arguments of the old job that name one project (`--repo`, `--repo-dir`, `--settings`, `--state`, `--area`, `--socket`, `--usage-file`, `--data-dir`) are replaced by `--data-root` and the list of served projects read from the settings. Open: the exact arguments and how a project's repo and clone are found (the code of PC-67 decides; this note follows it).
- The settings key: `projects`, a list of `{"short", "repo", "repo_dir", "first"?}` entries, one per project the queue serves; `first: true` ranks that project's tasks before all others. With one entry, or none, the Dispatcher works as before. A project not in the list is never touched. Adding another project is the owner's act, in the settings.
- The data root: `~/.local/share/project-cockpit/task-queue/` (`--data-root`), laid out as in the contract: `dispatcher-state.json`, `processing/processing.jsonl`, `area/<project>/<task-id>/`, `proxy.sock`, `proxy-usage.jsonl`, `quota-history.jsonl`. The old per-project folder `<data dir>/<project>/` keeps its other files (cardmeta, tokens, backup status).

**Order**

1. **By hand**: switch the Dispatcher off in Settings and wait until no Worker runs (`container exec` list empty, the Task Queue tab shows none). Expected: nothing is WIP.
2. **By hand**: unload the old jobs: `launchctl bootout gui/$(id -u)/com.project-cockpit.dispatcher.cockpit` and `.../com.project-cockpit.quota.cockpit`. Expected: `launchctl print` of each says it is not found.
3. Merge the PC-67 code; pull (`git pull --ff-only`), restart the viewer (section 8).
4. **By hand, the move of the live data**: copy, do not move, the old files into the data root, then check them; keep the old folder until step 8.
   `mkdir -p ~/.local/share/project-cockpit/task-queue/area`
   `cp -p OLD/dispatcher-state.json OLD/proxy-usage.jsonl OLD/quota-history.jsonl ROOT/`
   `cp -Rp OLD/processing ROOT/processing`
   `cp -Rp OLD/area ROOT/area/cockpit`
   (OLD is `~/.local/share/project-cockpit/cockpit`, ROOT the data root.) Open: whether old processing-log lines get a `project` field added (the specification 5a wants it on every entry) or stay without it; the reader's tolerance of old lines is the code's to state. The task folders hold the Workers' good-bye notes and bundles: copied with the area, so they must be kept.
5. Add `projects` to the settings (the Project cockpit with `first: true`, plus only the projects the owner has configured); the Dispatcher switch stays off.
6. Render and load the new job: the new `render` command prints the property lists and the `launchctl bootstrap` lines (quota job first); run them **by hand**. Rebuild or keep the image: no change needed (open: image recipes per project, PC-71).
7. Switch the Dispatcher on in Settings. Expected: the heartbeat in `ROOT/processing/processing.jsonl`, entries naming the project `cockpit`; the Task Queue tab shows the old and new history.
8. After a day of normal runs, **by hand**, delete the copied files from the old folder (not its cardmeta, token or backup files). Log each by-hand step in `docs/manual-interventions-log.md`.

**Roll back**: switch the Dispatcher off in Settings and unload the new job; `git revert` the PC-67 merge and pull; re-render and load the old per-project jobs (`dispatcher.launchd render --project cockpit ...`, section 5). Before step 8 the old folder is still complete up to the move, so the old jobs continue from it; work done by the new job since step 7 is only in the data root, so copy `ROOT/processing/processing.jsonl` lines of that period back by hand if they must be kept. After step 8 a rollback needs the daily backup (`tools/project_backup.py`, Retention in Drive) to restore the deleted files.

### 6. First collectors run and the first check

    python3 tools/code_snapshot_refresh.py --project widgets
    python3 tools/cardmeta_collect.py --project widgets --repo ~/src/widgets --map ~/src/widgets/docs/cockpit-modules.json
    python3 tools/metadata_gaps.py --project widgets

Expected: `code_snapshot_refresh.py` exits 0 (`fresh` or `refreshed`), or 2 with `not_configured` while the project has no `code` folder and `code_collect` command. `cardmeta_collect.py` logs `written: .../cardmeta/footprint.json`, `churn.json` and `ambiguity.json` under `~/.local/share/project-cockpit/widgets/cardmeta/`; after a reload the module cards show churn, footprint and ambiguity "as of" now. `metadata_gaps.py` prints one line per gap (`project | module | what | age`), then `N gap(s)`, and exits 1. A new project typically shows: `code snapshot not configured`; `Token Usage: no quota history`, `no transcript usage file`, `no proxy usage file` (until the quota collector and the first Worker ran); `no backup recorded`; `module page has no diagram`; `document has no Modules: line`; `declared file does not exist` for footprint paths the jobs have not created yet; a module above 30 percent unsorted files; and the transcript retention, if step 1's setting is missing. Each gap is a step of this list not done yet, or a task to file.

### 7. Verify with one command

    python3 tools/install.py --self-test

Checks the prerequisites of step 1 (Python, git, `gh` and its login, the model credential's path and mode 0600, on macOS the `container` tool), the projects file and each project's clone, the viewer service loaded and answering on port 8795, the Dispatcher's two jobs loaded (macOS), the transcript retention, and runs `tools/metadata_gaps.py` per project (gaps are reported, they do not make the result "missing": a new project always has some, step 6). Prints `ready` (exit 0) or `missing:` with one fix per line (exit 1). The one command ends with it.

### 8. Upgrade and uninstall

- **Upgrade**: `git -C ~/src/project-cockpit pull --ff-only`, then restart the viewer (unload and load: a kick-start reuses the old definition) and re-render and reload the Dispatcher's two jobs; rebuild the image when `dispatcher/image/` or `dispatcher/relay.py` changed. Nothing to migrate.
- **Uninstall**: `launchctl bootout gui/$(id -u)/com.project-cockpit.dispatcher.widgets` and `.../com.project-cockpit.quota.widgets`, stop the viewer (section 5), `container stop cockpit-workers` and `container delete cockpit-workers`, delete the property lists, the unit, the project's entry in the projects file and `~/.local/share/project-cockpit/widgets`. Nothing was written to GitHub except by your own actions, the Workers' pull requests and the kit labels.

## 1. What runs where

| Piece | Where | Notes |
|---|---|---|
| `cockpit.py` | one process, one machine | stdlib Python 3.14, no database, no state on disk except the optional token file |
| `gh` CLI | same machine, same OS user | the cockpit runs `gh` and acts as whoever `gh` is logged in as; it stores no GitHub token |
| Access token | random per start, or `--token-file PATH` (created mode 0600) | given once as `?t=TOKEN`, then an HttpOnly, SameSite=Strict cookie; hidden in the request log. The cookie has no `Secure` flag |
| Listen address | `127.0.0.1:8795` (`--port`) | `--lan` binds `0.0.0.0`, plain HTTP: the token and cookie travel unencrypted |
| Mode | steering | every `POST /api/act` gets 403 only when started with `--read-only` |
| Inputs | GitHub (always); `--modules` JSON; `--git` local clone; `--code` snapshot dir; `--phases` JSON; the card-metadata folder | the clone, snapshot and metadata files are read, never written |

**Card metadata folder.** Per project the cockpit reads `footprint.json`, `churn.json` and `ambiguity.json` ([contract](contracts/card-metadata.md)) from the `meta` key of the project in the `--projects` file, default `~/.local/share/project-cockpit/<project name>/cardmeta/` (next to the project's token folder). Example: `{"name": "widgets", "repo": "acme/widgets", "meta": "~/.local/share/project-cockpit/widgets/cardmeta"}`. The folder is written by a producer on the project side, not by the cockpit; missing files just show "no data". This repository ships generic collectors for it (`tools/cardmeta_collect.py --repo PATH --map MODULE_MAP.json --project NAME`, stdlib, read-only on the repository; the footprint collector also reads a committed declaration, see its docstring). The cockpit's own map is `docs/cockpit-modules.json` and its declaration `docs/footprint-declared.json`.

**Budget panel files.** The Budget page reads `quota.json`, `usage.json` and `decisions.jsonl` ([contract](contracts/budget-panel.md)) from `~/.local/share/project-cockpit/budget/`; `--quota-feed`, `--usage-report` and `--decision-log` set other paths. They are written by external producers, read-only for the cockpit; a missing file shows "no data".

The token protects the page; it is one shared secret, not a user. Whoever holds it can do, unless the cockpit runs with `--read-only`, everything the `gh` login can do through the cockpit's actions. Treat it like the login itself.

## 2. The three roles

The Manager's rights are the only ones that matter for the protected attributes (`released`, `P1`-`P3`, `due:`, `cancelled`, [ADR 0003](adr/0003-protected-attributes-are-labels.md)). The cockpit counts such a label only if the Manager set it. So the Orchestrator and the Workers must never hold the Manager's credential, nor run as the OS user that holds it.

| | Manager | Orchestrator | Worker |
|---|---|---|---|
| Identity | the human's own account | a machine account or a GitHub App installation | one token per Worker (or per sandbox) |
| GitHub level | owner / admin | Write (needed to merge) | Write, or Read plus a fork (unverified, see 8) |
| May set protected labels | yes (the only one that counts) | **can** (labels have no per-label permission), then it is flagged and reverted | **can**, same |
| May merge | yes | yes: reviews and merges Worker PRs | no (ruleset needs approval from someone else) |
| May push to `main` | only via the ruleset's rules | no direct pushes (ruleset) | no; own branch or fork only |
| Reads | everything | issues, PRs, code | the task it is given |
| Reaches the cockpit | the token URL, a steering instance | not at all, or a read-only instance | not at all |

Why the Orchestrator must not use the cockpit in steering mode: the cockpit's actions run as the `gh` login of the cockpit process. An Orchestrator that can drive a steering cockpit borrows the Manager's rights.

### The Manager's assistant

The same AI tool may also act as the Manager's personal assistant: it files and releases tasks when the Manager explicitly asks it to, in a conversation. From the point of view of identity and access it **is the Manager**: same GitHub identity, same authority. This is a fourth capacity, not a fifth role to separate: it is the Manager acting through a tool.

Consequence: an assistant session holds the Manager's credentials. Once identities are separated (phase 2 and 3), it must never share an OS user, a logged-in `gh`, a token file or a terminal session with Orchestrator or Worker sessions, or those sessions would hold the Manager's authority too. Run it where the Manager works, and keep the Orchestrator and Workers elsewhere. Releases made by the assistant should leave a trace (for example a comment on the task saying that it was released on the Manager's explicit request), because GitHub shows them as the Manager's own actions.

## 3. Segregation layers and their strength

| Layer | Prevents | Strength |
|---|---|---|
| Separate GitHub identities | the event log shows who acted; the rule can tell the Manager from the rest | **[GitHub]** records it; prevents nothing by itself |
| Collaborator roles | merging, pushing, editing workflows and variables need Write; Triage cannot push or merge | **[GitHub]**. Labels are open to Triage and above: no per-label permission exists |
| Fine-grained tokens per role | limits a token to chosen repos and permissions (Issues, Pull requests, Contents, Variables, Projects) | **[GitHub]**. Cannot withhold labels: both Issues-write and Pull-requests-write list the label endpoints |
| Ruleset on `main` | requires a PR, N approvals, approval of someone other than the last pusher, CODEOWNERS review | **[GitHub]**. Admins and listed bypass actors are exempt. Private repos need a paid plan (unverified for rulesets) |
| CODEOWNERS on constitutional paths | a change to `docs/constitution.md`, `CLAUDE.md`, `.github/`, `kit/` needs the Manager's review | **[GitHub]** when the ruleset requires code-owner review |
| Cockpit detect-and-ignore | a protected label set by a non-Manager does not count and shows as "unauthorised" | **[after]**: the page is correct, the label is still there |
| Policy workflow (`kit/.github/workflows/policy.yml`) | reverts such a label, comments, on label events and every 10 minutes | **[after]**: up to about 10 minutes visible; label changes made with the workflow token start no workflows, hence the schedule |
| Repo variable `MANAGER` | names the Manager for the policy workflow | **[GitHub]** only as far as nobody with Write edits it: Write may set variables (see 7) |
| Separate OS users or containers, token files 0600 with different owners | an agent cannot read another role's `gh` credentials or token file | **[OS]**, and only if agents really run as different users or in containers that mount nothing of the Manager's |
| Cockpit `--read-only` guard | with `--read-only` no action is possible; at start it warns when the `gh` login is not the Manager | **[convention]**: a warning, not a refusal |

Honest limit: processes of the same OS user can read that user's `gh` credentials (the keychain item or `~/.config/gh`). One user on one machine is therefore never separation, whatever the labels say. Separate OS users stop a casual read, not root or a shared writable path; separate hosts or containers are stronger. Projects v2 field edits emit no events, so a changed Project field cannot be attributed to an actor.

## 4. Setup recipes

### Phase 1: single identity (nothing is enforced)

1. `gh auth login` as the Manager on the machine that will run the cockpit.
2. Create the labels: `kit/setup.sh acme/widgets` (idempotent: creates or updates labels).
3. Copy `kit/.github` to `.github` in the target repo (issue forms; the policy workflow is optional and does nothing useful yet).
4. Start: `python3 cockpit.py --repo acme/widgets --token-file ~/.local/share/project-cockpit/widgets/token` (add `--read-only` for an instance that cannot act).

Guaranteed: read-only by default, a token on the page, no stored GitHub secrets. **Not** guaranteed: that anyone but the Manager set a protected label. Every change made through this `gh` login, including by agents on this machine, counts as the Manager's.

### Phase 2: a separate Orchestrator identity

1. Create the identity: a second GitHub account (machine user) or a GitHub App installed on `acme/widgets`. Apps give short-lived installation tokens attributed to the app (documented); a machine user is simpler but is a seat and has a long-lived credential.
2. Name the Manager for the policy: `gh variable set MANAGER --body alice -R acme/widgets`, and start the cockpit with `--manager alice` (default: the repo owner).
3. Give the Orchestrator Write: `gh api -X PUT repos/acme/widgets/collaborators/acme-orchestrator -f permission=push` (the legacy value `push` is Write; on organisation repos `write` is also accepted, unverified).
4. Install the policy: copy `kit/policy.py` to the same path and `kit/.github/workflows/policy.yml` to `.github/workflows/` in the repo.
5. Add `.github/CODEOWNERS` naming the Manager for the constitutional paths, e.g. `docs/constitution.md @alice`, `.github/ @alice`, `kit/ @alice`, `CLAUDE.md @alice`.
6. Create a ruleset on the default branch: PR required, 1 approval, code-owner review, approval of the latest push by someone else. Create it in the UI (Settings, Rules) or `gh api -X POST repos/acme/widgets/rulesets --input ruleset.json`; the JSON schema is not verified here, check against the REST docs.
7. Run the Orchestrator with its own token only: `GH_TOKEN` in its environment, or its own `gh auth login` under its own OS user. The Manager's cockpit keeps running under the Manager's user.
8. **Verify (forgery test)**, with the Orchestrator's credential: create a test issue, then `gh issue edit N --add-label released -R acme/widgets`. Expect: the cockpit shows the task as unauthorised and not released after its next refresh (at most about 2 minutes; Refresh forces it); the policy run (`gh workflow run policy -R acme/widgets` to run it now) removes the label and comments "unauthorised ... reverted". Then, as the Manager, add the label: it now counts. Delete the test issue.

### Phase 3: separate Workers

1. One identity or token per Worker, or per sandbox: a fine-grained token limited to `acme/widgets` with Contents (write) and Pull requests (write); no Variables, no Projects, no Administration. Or a fork per Worker with no rights on the upstream (unverified for private repos).
2. Run each Worker as its own OS user or in a container that mounts only its work tree and its own token. No mount of the Manager's or Orchestrator's home, `gh` config or token files.
3. The ruleset (phase 2) already stops Worker pushes to `main` and Worker merges.
4. **Verify**: (a) as a Worker, try `git push origin HEAD:main`: expect rejection. (b) Repeat the phase-2 forgery test with a Worker token: expect the same flag and revert. (c) As each Worker's OS user, `cat` the Manager's token file and `gh auth status` against the Manager's config: both must fail with permission errors **[OS]**.

## 5. Running as a service

Use one cockpit instance (it can serve several projects, see ADR 0012), under the Manager's user, loopback unless a phone must reach it. Replace paths and names. Keep `PATH` explicit: services do not inherit your shell's, and `gh` must be found.

### macOS: LaunchAgent

Write `~/Library/LaunchAgents/org.example.project-cockpit.plist` (in the user's own login domain, no sudo; a `gui/` job also reaches the user's keychain, where `gh` keeps its token):

    <?xml version="1.0" encoding="UTF-8"?>
    <plist version="1.0"><dict>
      <key>Label</key><string>org.example.project-cockpit</string>
      <key>ProgramArguments</key><array>
        <string>/usr/bin/python3</string><string>/Users/alice/src/project-cockpit/cockpit.py</string>
        <string>--repo</string><string>acme/widgets</string>
        <string>--token-file</string><string>/Users/alice/.local/share/project-cockpit/widgets/token</string>
      </array>
      <key>WorkingDirectory</key><string>/Users/alice/src/project-cockpit</string>
      <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string></dict>
      <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
      <key>StandardOutPath</key><string>/Users/alice/.local/share/project-cockpit/widgets/cockpit.log</string>
      <key>StandardErrorPath</key><string>/Users/alice/.local/share/project-cockpit/widgets/cockpit.log</string>
    </dict></plist>

| Task | Command |
|---|---|
| Create the data folder | `mkdir -p ~/.local/share/project-cockpit/widgets` |
| Syntax check | `plutil -lint ~/Library/LaunchAgents/org.example.project-cockpit.plist` |
| Start (load) | `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.example.project-cockpit.plist` |
| Restart | `launchctl kickstart -k gui/$(id -u)/org.example.project-cockpit` |
| Status | `launchctl print gui/$(id -u)/org.example.project-cockpit \| grep state` |
| Stop (unload) | `launchctl bootout gui/$(id -u)/org.example.project-cockpit` |

Add `--lan` and `--port N` only for phone access; the first LAN connection may raise an OS firewall prompt for Python. Add `--read-only` for a read-only instance.

### Linux: systemd user unit

`~/.config/systemd/user/project-cockpit.service`:

    [Unit]
    Description=Project cockpit (acme/widgets)
    After=network-online.target

    [Service]
    ExecStart=/usr/bin/python3 %h/src/project-cockpit/cockpit.py --repo acme/widgets --token-file %h/.local/share/project-cockpit/widgets/token
    WorkingDirectory=%h/src/project-cockpit
    Environment=PATH=/usr/local/bin:/usr/bin:/bin
    Restart=on-failure
    StandardOutput=append:%h/.local/share/project-cockpit/widgets/cockpit.log
    StandardError=inherit

    [Install]
    WantedBy=default.target

| Task | Command |
|---|---|
| Load and start | `systemctl --user daemon-reload && systemctl --user enable --now project-cockpit` |
| Restart | `systemctl --user restart project-cockpit` |
| Status and log | `systemctl --user status project-cockpit`; `journalctl --user -u project-cockpit` |
| Run without a login session | `loginctl enable-linger alice` |
| Stop | `systemctl --user disable --now project-cockpit` |

`gh` must be authenticated for that user without a keyring prompt (a headless host usually keeps it in `~/.config/gh/hosts.yml`, mode 0600); `append:` needs systemd 240 or newer (unverified for your distribution).

### Token, upgrade, check

- **Rotate the token**: delete the token file and restart; every browser logs in again with the new URL. Read it only when needed: `cat TOKENFILE` (macOS: `pbcopy < TOKENFILE`).
- **Rotate the GitHub credential**: `gh auth refresh` or `gh auth login` again as the same account; no restart is needed, the next `gh` call uses it.
- **Upgrade**: `git -C ~/src/project-cockpit pull --ff-only`, then restart (table above). Nothing to migrate.
- **What a restart costs** (PC-131): the viewer listens within a second or two of starting, before its warm-up; until the warm-up is done it answers from its disk copies (the page says "warming up, data from <time>") and actions work. The page retries an action that could not reach the viewer (three tries, 5 s apart, then a Retry button) and marks it with an action id, so the viewer applies it once (`<data dir>/actions-seen.json`); failed actions stay readable under "Recent problems" at the foot of the page. A restart is still a short outage, a deploy event: switching a new release in on its port (PC-84) removes it.
- **Check after a start**: `T=$(cat TOKENFILE)`; `curl -si "http://127.0.0.1:8795/?t=$T"` should answer `303` with `Set-Cookie: t=...`; then `curl -s -b "t=$T" http://127.0.0.1:8795/api/state | head -c 300` should show JSON with `"steering": true` (or false when read-only). Without the cookie `/api/state` answers 403. A POST to `/api/act` on a read-only instance answers 403.

### One instance for several projects

Instead of one service per repo, list the repos in a `--projects` file (see the README) and start one process: `python3 cockpit.py --projects ~/.config/project-cockpit/projects.json --token-file ... [--read-only]`. There is one token and one mode for all projects. The `gh` login must have access to every configured repo, and each project's `manager` (default: its repo owner) should be that login, since every action runs as it; a mismatch is warned about per project at start. Pages poll only the project on screen, but all projects share the login's rate-limit allowance ([ADR 0012](adr/0012-several-projects-in-one-instance.md)). For different logins or modes, keep one instance each.

## 6. Operations

- **Rate limits**: one GraphQL query per 100 issues; the state is cached 2 minutes, the page refreshes itself every 3 minutes while visible, Refresh forces a fetch ([ADR 0010](adr/0010-rate-limit-resilience.md)). The cockpit shares the `gh` login's hourly allowance with that user's other tools.
- **502 "Retry" card**: GitHub failed or refused (rate limit, outage, expired login). If a good copy exists, the cockpit serves it and retries in about 15 seconds; the card appears when there is none (a cold start). Check `gh auth status` and `gh api rate_limit`, then press Retry.
- **Logs**: stdout to the log file above; the token is redacted from request lines. Warnings name stale data, refused actions and a Manager mismatch. Besides, every action and every failure a page reports is one JSON line in `<data dir>/<project>/actions.jsonl` (mode 0600, rotated at 1 MB, five files kept; Settings > Action log shows it; format in the README, section Settings).
- **Backups**: nothing server-side. The state lives in GitHub; review marks of the Rules tab live in the browser's local storage. Keep your `--modules` JSON in the repo. The token file is disposable. A project backup (cache, data folder, module map and code snapshot; layout in [contracts/backup.md](contracts/backup.md)) can be run as a read-only offline copy with `cockpit.py --offline BACKUP`, and `tools/backup_restore.py BACKUP` checks that it restores every pane.
- **Daily backup to Google Drive (PC-62)**: the daily job runs, per project, `python3 tools/project_backup.py --project NAME --drive "<the folder the Drive desktop app syncs, its My Drive>" --transcripts <the coding tool's transcript folder of the project, e.g. ~/.claude/projects/-Users-you-src-NAME>` (repeat `--transcripts` for several folders). It writes `project_cockpits/NAME/NAME-YYYY-MM-DD.tar.gz` there, create-only (a taken name gets `-2`, `-3`, ...; nothing in Drive is ever edited or deleted by it), and the Drive app uploads it. The archive holds the project's cache files, the listed files of its data folder (processing log, token statistics, settings, state, card data, good-bye notes), the code snapshot, the projects file and the mirror of the raw session transcripts (`<data dir>/NAME/transcripts/`, never pruned); never the token files, an environment file or any other file whose name marks a secret, never logs or task bundles, and nothing that lies inside the project's clone. The transcripts hold conversation text and may hold secrets or personal data: point `--drive` only at the owner's own Drive. Every run writes `<data dir>/NAME/backup-status.json`; the gap checker reports a missing, failed or stale (over 24 hours) backup as kind `backup`, the daily job files it as a task, and the Task Queue tab shows the last backup's time and result. Exit code 0 written, 1 failed, 2 unusable arguments.
- **Retention in Drive (manual step of the owner)**: in script.google.com create a project under the owner's own account, paste `tools/drive_retention.gs`, run `installDailyTrigger` once (it asks for Drive access) and log the installation in the manual-interventions log. Every day it keeps the newest 30 archives of each sub-folder of `project_cockpits` and moves older ones to the bin; it counts, it does not look at ages, so a pause in the backups never removes the last ones.
- **Transcript retention**: the coding tool deletes session transcripts older than its retention period (Claude Code: `cleanupPeriodDays`, default 30 days), and the transcripts are the source of the token statistics and an input of the backups; set it to 3650 or more in the user settings file of the account that runs the sessions (`~/.claude/settings.json`, e.g. `"cleanupPeriodDays": 3650`). `tools/metadata_gaps.py` reports it as a `transcript_retention` gap until it is set.
- **Uninstall**: see the installation workflow, step 8.
- **Policy workflow cost**: the 10-minute schedule is about 4,300 runs a month. On a public repo standard runners are free (documented); on a private repo included usage is a dollar amount ($12 on Free, $18 on Pro or Team; documented) and a minimum 1-minute rounding per run would put the schedule near $26 a month at the 2-core Linux rate (our estimate, unverified). Loosen the cron if that matters (5 minutes is the minimum; runs can be delayed under load; scheduled runs use the default branch; documented).

## 7. Threat notes: what is still possible

| Actor | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| Orchestrator | everything the Manager can, undetectably | sets a protected label (flagged, reverted within about 10 minutes); can change the `MANAGER` variable (Write) and so blind the policy workflow, but the cockpit's own `--manager` still flags it; can drive a steering cockpit if it can reach it or read the token file | same, unless it runs as a different OS user or host from the Manager's cockpit and credentials |
| Worker | same as the Orchestrator | as the Orchestrator, if it holds Write or Pull-requests-write; cannot merge | sets a label (flagged); cannot merge, push to `main` or read the others' credentials |
| Both, on one OS user | n/a | can read the Manager's `gh` credential: no separation | prevented by OS users or containers, not by GitHub |
| Anyone with Write | n/a | can edit workflows on a branch and, if the ruleset allows, merge the change: CODEOWNERS on `.github/` closes this | same |
| Anyone who learns the cockpit token | acts as the Manager's login unless `--read-only` | same | same; use loopback, a short-lived token, or a read-only instance |
| Anyone on the LAN (`--lan`) | sees the token in plain HTTP | same | same |
| Admin or bypass actor | bypasses the ruleset | same | same: keep the list empty |

A revert after the fact is not prevention: a wrongly set `released` label can be seen by agents polling GitHub directly, which do not consult the cockpit. Agents should read protected state through the cockpit's view, not raw labels.

## 8. To verify

Not checked against official documentation or a live repo; treat as open.

- Worker as a fork with Read on the upstream: which actions (labels, comments) a Read user has on a private repo.
- Collaborator permission names and Triage availability on personal (non-organisation) repos; `permission=push` versus `write` in the collaborator API.
- Ruleset JSON schema for `gh api .../rulesets`, and ruleset availability for private repos on each plan.
- Whether a GitHub App can be a ruleset bypass actor or code owner in the way assumed, and whether an App's label events carry the app as actor in the same form the cockpit and `policy.py` expect (they read `actor.login`).
- Whether Projects v2 edits by a user token need an extra permission for user-owned (non-organisation) projects: the permissions page lists none for user projects.
- Actions billing: rounding per run and whether scheduled runs count against the included amount.
- `StandardOutput=append:` availability on the distribution in use; keychain access of a `gui/` LaunchAgent after a reboot without a login.
- That the Manager's `gh` credential is unreadable from a container or other OS user in a given setup: test with the phase-3 step 4(c).
