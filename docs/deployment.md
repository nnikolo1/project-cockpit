# Deployment and role separation

How to run the cockpit, and how far the Manager / Orchestrator / Worker separation of [the constitution](constitution.md) can really be enforced. Examples use the repo `acme/widgets`, the Manager login `alice`, a module `alpha`. Decision record: [ADR 0011](adr/0011-deployment-and-role-separation.md).

**Read this first.** In phase 1 (one GitHub identity for everyone) nothing is enforced; the rule is followed by agreement. Enforcement starts only when each role has its own identity (phase 2) and its own credentials that the other roles cannot read (phase 3). Section labels below: **[GitHub]** enforced by GitHub, **[OS]** enforced by the operating system, **[after]** reverted or flagged after the fact, **[convention]** nothing stops a violation.

## 1. What runs where

| Piece | Where | Notes |
|---|---|---|
| `cockpit.py` | one process, one machine | stdlib Python 3.10+, no database, no state on disk except the optional token file |
| `gh` CLI | same machine, same OS user | the cockpit runs `gh` and acts as whoever `gh` is logged in as; it stores no GitHub token |
| Access token | random per start, or `--token-file PATH` (created mode 0600) | given once as `?t=TOKEN`, then an HttpOnly, SameSite=Strict cookie; hidden in the request log. The cookie has no `Secure` flag |
| Listen address | `127.0.0.1:8795` (`--port`) | `--lan` binds `0.0.0.0`, plain HTTP: the token and cookie travel unencrypted |
| Mode | read-only | every `POST /api/act` gets 403 unless started with `--steer` |
| Inputs | GitHub (always); `--modules` JSON; `--git` local clone; `--code` snapshot dir; `--phases` JSON | the clone and snapshot are read, never written |

The token protects the page; it is one shared secret, not a user. Whoever holds it can do, in `--steer` mode, everything the `gh` login can do through the cockpit's actions. Treat it like the login itself.

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
| Reaches the cockpit | the token URL, `--steer` | not at all, or a read-only instance | not at all |

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
| Cockpit `--steer` guard | no action without `--steer`; at start it warns when the `gh` login is not the Manager | **[convention]**: a warning, not a refusal |

Honest limit: processes of the same OS user can read that user's `gh` credentials (the keychain item or `~/.config/gh`). One user on one machine is therefore never separation, whatever the labels say. Separate OS users stop a casual read, not root or a shared writable path; separate hosts or containers are stronger. Projects v2 field edits emit no events, so a changed Project field cannot be attributed to an actor.

## 4. Setup recipes

### Phase 1: single identity (nothing is enforced)

1. `gh auth login` as the Manager on the machine that will run the cockpit.
2. Create the labels: `kit/setup.sh acme/widgets` (idempotent: creates or updates labels).
3. Copy `kit/.github` to `.github` in the target repo (issue forms; the policy workflow is optional and does nothing useful yet).
4. Start: `python3 cockpit.py --repo acme/widgets --token-file ~/.local/share/project-cockpit/widgets/token` (add `--steer` to act).

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
        <string>--steer</string>
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

Add `--lan` and `--port N` only for phone access; the first LAN connection may raise an OS firewall prompt for Python. Drop `--steer` for a read-only instance.

### Linux: systemd user unit

`~/.config/systemd/user/project-cockpit.service`:

    [Unit]
    Description=Project cockpit (acme/widgets)
    After=network-online.target

    [Service]
    ExecStart=/usr/bin/python3 %h/src/project-cockpit/cockpit.py --repo acme/widgets --token-file %h/.local/share/project-cockpit/widgets/token --steer
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
- **Check after a start**: `T=$(cat TOKENFILE)`; `curl -si "http://127.0.0.1:8795/?t=$T"` should answer `303` with `Set-Cookie: t=...`; then `curl -s -b "t=$T" http://127.0.0.1:8795/api/state | head -c 300` should show JSON with `"steering": true` (or false when read-only). Without the cookie `/api/state` answers 403. A POST to `/api/act` on a read-only instance answers 403.

### One instance for several projects

Instead of one service per repo, list the repos in a `--projects` file (see the README) and start one process: `python3 cockpit.py --projects ~/.config/project-cockpit/projects.json --token-file ... [--steer]`. There is one token and one mode for all projects. The `gh` login must have access to every configured repo, and each project's `manager` (default: its repo owner) should be that login, since every action runs as it; a mismatch is warned about per project at start. Pages poll only the project on screen, but all projects share the login's rate-limit allowance ([ADR 0012](adr/0012-several-projects-in-one-instance.md)). For different logins or modes, keep one instance each.

## 6. Operations

- **Rate limits**: one GraphQL query per 100 issues; the state is cached 2 minutes, the page refreshes itself every 3 minutes while visible, Refresh forces a fetch ([ADR 0010](adr/0010-rate-limit-resilience.md)). The cockpit shares the `gh` login's hourly allowance with that user's other tools.
- **502 "Retry" card**: GitHub failed or refused (rate limit, outage, expired login). If a good copy exists, the cockpit serves it and retries in about 15 seconds; the card appears when there is none (a cold start). Check `gh auth status` and `gh api rate_limit`, then press Retry.
- **Logs**: stdout to the log file above; the token is redacted from request lines. Warnings name stale data, refused actions and a Manager mismatch.
- **Backups**: nothing server-side. The state lives in GitHub; review marks of the Rules tab live in the browser's local storage. Keep your `--modules` JSON in the repo. The token file is disposable.
- **Uninstall**: stop the service (tables above), delete the unit or plist and the data folder. Nothing was written to GitHub except by your own actions and the kit labels.
- **Policy workflow cost**: the 10-minute schedule is about 4,300 runs a month. On a public repo standard runners are free (documented); on a private repo included usage is a dollar amount ($12 on Free, $18 on Pro or Team; documented) and a minimum 1-minute rounding per run would put the schedule near $26 a month at the 2-core Linux rate (our estimate, unverified). Loosen the cron if that matters (5 minutes is the minimum; runs can be delayed under load; scheduled runs use the default branch; documented).

## 7. Threat notes: what is still possible

| Actor | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| Orchestrator | everything the Manager can, undetectably | sets a protected label (flagged, reverted within about 10 minutes); can change the `MANAGER` variable (Write) and so blind the policy workflow, but the cockpit's own `--manager` still flags it; can drive a steering cockpit if it can reach it or read the token file | same, unless it runs as a different OS user or host from the Manager's cockpit and credentials |
| Worker | same as the Orchestrator | as the Orchestrator, if it holds Write or Pull-requests-write; cannot merge | sets a label (flagged); cannot merge, push to `main` or read the others' credentials |
| Both, on one OS user | n/a | can read the Manager's `gh` credential: no separation | prevented by OS users or containers, not by GitHub |
| Anyone with Write | n/a | can edit workflows on a branch and, if the ruleset allows, merge the change: CODEOWNERS on `.github/` closes this | same |
| Anyone who learns the cockpit token | acts as the Manager's login if `--steer` | same | same; use loopback, a short-lived token, or a read-only instance |
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
