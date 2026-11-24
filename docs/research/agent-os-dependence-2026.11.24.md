# Agent and OS dependence: analytic run

Status: research report, persisted as received (read-only run by a background agent, 2026-11-24, code at main `4c42347`)
Date: 2026-11-24
Modules: internal/bootfilling

Owner decisions on this report (2026-11-24): abstract base classes are accepted, a plugin registry is not (yet); the tool-neutral rewording of the specification (section 4) is applied; refactorings R1, R3 and R6 are to be done; R2, R4 and R5 and everything else are left to the memo `docs/memos/2026.11.24-refactoring-for-agent-and-os-independence.md`.

Marks: **[V]** verified by reading the code, a commit or the specification; **[I]** the agent's inference.

Limit of the run: the written conclusions of the earlier container-seam review were not found in `docs/`; what is known of it comes from the git history (commits `fe59e72` and `a06e7c3` moved `alive`, `kill`, `ensure_relay` and `data_folder` into `Runtime`; `ecbb8f7` made spec 4c, 4e, 4f and Approach 1 tool-neutral, with Apple's tool named as the current runtime) [V].

## 1. Where the dependence sits

### Claude (agent and model API)

| File:line | What it assumes | Where it sits |
|---|---|---|
| dispatcher/runtime.py:24-27 | `INSTRUCTION`, the launch prompt | In Runtime, mixed with the container logic |
| runtime.py:28-33 | `SWITCHES`, Claude Code's environment variables (4i) | Same |
| runtime.py:246-248 | `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CONFIG_DIR=/home/u/.claude` | Same |
| runtime.py:254 | The command `claude -p <prompt>` | Same |
| runtime.py:85-87 | The dummy token sent as an API key or bearer token | Small, generic in form |
| dispatcher/core.py:329-336 | `transcript_mtime`: reads `/home/{user}/.claude/projects/**/*.jsonl` (Claude's transcript layout) | **Spread into core.py** |
| dispatcher/proxy.py:21 | `UPSTREAM_HOST="api.anthropic.com"` | Module constants, mixed in with the generic proxy |
| proxy.py:22-25 | Allowed paths `/v1/messages` and `count_tokens`; the headers it drops (`x-api-key`) | Same |
| proxy.py:26 | `OAUTH_BETA` | Same |
| proxy.py:27 | `USAGE_FIELDS` | Same |
| proxy.py:42-48 | Credential handling | Same |
| proxy.py:51-72 | Anthropic's SSE events `message_start`/`message_delta` | Same |
| proxy.py:116-121 | Bearer token plus `anthropic-beta` | Same |
| dispatcher/image/stop_hook.py:1-5, 85-102 | Claude's hook protocol: JSON on stdin, `stop_hook_active`, `transcript_path`, `cwd`, exit code 2 | In the image (a named place) |
| stop_hook.py:20-36 | `transcript_tokens` reads Claude's transcript format | Same |
| stop_hook.py:55-82 | `problems()`, the checks themselves | **Agent-neutral**, but tied to the Claude adapter |
| dispatcher/image/Containerfile:1-2, 8, 12 | The Claude binary for Alpine arm64, copied in by hand | Image (named) |
| Containerfile:7, 13 | The Alpine libraries the CLI needs; `USE_BUILTIN_RIPGREP` | Image |
| Containerfile:11, 23 | Skill format and location `~/.claude/skills` | Image |
| Containerfile:18 | `WORKER_SETTINGS`, Claude's settings.json: `skipWebFetchPreflight`, tool permission names, the Stop hook config | Image |
| dispatcher/image/skill/SKILL.md | Claude skill front matter | Image |
| logic.py:821 and the Usage tab | A third copy of `USAGE_FIELDS` (also in proxy.py:27 and stop_hook.py:20); the tab only uses the sum (`c["total"]`) | **Spread over 3 files** |
| logic.py:505 | `BOT=("[bot]","noreply@anthropic")`, used to detect agent-assisted rule changes | Hard-coded constant (the rule globs themselves are config) |
| tools/e2e/stub_claude.py:56-63, 81 | Stub of the model: the `CLAUDE_CONFIG_DIR/projects/*.jsonl` layout, so that 3o sees activity | Test tool |
| tools/e2e/Containerfile.stub:3 | The stub replaces `/usr/local/bin/claude` | Test tool |
| tests/dispatcher/test_runtime.py:240-256, 249-251, 258-267 | The launch command, the `ANTHROPIC_*`/`CLAUDE_*` variables, the settings allowlist | Tests |
| tests/dispatcher/test_stop_hook.py:105-160 | The hook wiring and the transcript format | Tests |
| tests/dispatcher/test_proxy.py:52-66 | The `anthropic-beta`/OAuth header | Tests |
| tests/dispatcher/test_core.py:375 | The transcript fallback | Tests |
| tests/test_rules.py:34-35 | The `noreply@anthropic` detection | Tests |

The specification names Claude in Goals 1, 3o ("session transcript"), 4f (`ANTHROPIC_BASE_URL`), 4g (`/v1/messages`), 4i (all of it), 4m ("the CLI"), 4o (`tokens` from the transcript) and 4t ("skill", "Stop hook") [V].

### macOS

| File:line | What it assumes | Where it sits |
|---|---|---|
| core.py:282-283, 286, 289 | `sysctl -n kern.memorystatus_vm_pressure_level`, `pmset -g batt` and the parsing of their output | **In core.py**: in the "default probes" section, not in its own module |
| core.py:3, 79 | The launchd wording | Comments only. The firing logic is a one-shot `main()` that any periodic trigger can call [V] |
| web/index.html:453 | The UI hint "How often launchd starts a firing." | UI text |
| runtime.py:59 | `data_folder` in `~/Library/Application Support/com.apple.container` | Config (named) |
| runtime.py:122-170, 138-140, 159, 166, 282 | Apple `container` CLI syntax: `list --all --format json` with the `configuration.id` key, `run ... -v`, `stats --no-stream`, `build --dns`, `exec -d` | Runtime (named) |
| runtime.py:251-254 | `launch()` **repeats** the tool's exec syntax (`--user/--workdir/--env`) instead of using `exec()` | Duplicated inside Runtime |
| runtime.py:241-243, 259-272 | The assumption that killing the host-side `container exec` process ends the process in the guest. Stated in the docstring, not checked by the agent | Runtime |
| tools/e2e_failure_paths.py:147 | Calls `container stop cockpit-workers` directly, outside Runtime | **Bypasses the seam** |
| proxy.py:26 | `SOCKET_PATH_LIMIT=104`, the macOS `sun_path` size (Linux allows 108) | Constant. The stricter value, so it is portable as it is [I] |
| docs/deployment.md:96-124 | The LaunchAgent for the Cockpit service; a systemd unit is already given in the same document (126-153) | Docs, already covers both |

The specification names macOS in 3c (launchd), 4c, 4e and 4f (Apple's tool, already worded tool-neutrally), 4d (LaunchAgent, `caffeinate -i`), 5c (MacBook) and Approach 1 [V].

Not implemented in the code at all [V]: **4d** (the LaunchAgent with `caffeinate`) and **3c** (the launchd job): no plist or unit file is tracked.

Already portable [V]: CI runs the tests on both `ubuntu-latest` and `macos-latest` (`.github/workflows/test.yml:9`); `cockpit.py:87` uses `~/.local/share`; the Containerfile is OCI format.

## 2. What the seam review left open

- `launch()` builds the tool's command line by hand (runtime.py:251-254), and the e2e teardown bypasses Runtime (e2e_failure_paths.py:147) [V].
- `Runtime` holds the Claude launch content: the prompt, the environment and the `claude -p` command [V].
- Host probes (sysctl, pmset) and the transcript probe (`~/.claude/projects`) are still in core.py [V].
- What a replacement runtime must guarantee for `kill`/`alive` is not written down. [I] from general knowledge of Docker, not checked: killing a `docker exec` client does not kill the process it started in the container. So this guarantee is a real requirement on any replacement.

## 3. Proposed refactorings, ranked by value for the effort

**R1. An agent adapter, `dispatcher/agent.py`: high value, small effort.**
- What moves there: `INSTRUCTION`, `SWITCHES` and the agent part of the environment (base URL, token, config folder) from runtime.py; the command `claude -p`; the transcript glob from core.py:329-336.
- Interface: `class ClaudeCode:` with `command(prompt) -> list[str]`, `env(user: str, base_url: str, token: str) -> dict[str, str]`, `activity_glob(user: str) -> str`; and `Runtime.launch(user, task_id, agent)` and `core.transcript_mtime(runtime, agent, user)`.
- A replacement agent must provide: a headless CLI that takes a prompt; a model-API base URL and a token set through the environment; a per-user configuration folder; a file that is updated while it works (for 3o).
- Spec: 4f, 4i, 3o.

**R2. Seam fixes inside Runtime: high value, very small effort.**
- What changes: one builder, `Runtime.exec_argv(user, *cmd, env=None, workdir=None, detach=False) -> list[str]`, used by `exec()`, by `launch()` and by `ensure_relay()`; a new `Runtime.stop()`, used by tools/e2e_failure_paths.py instead of the hard-coded `container stop`.
- A replacement runtime must provide: run, start, stop and state (with its own parsing of the state output); exec as a user, with a workdir, environment and detach; a socket mount; stats, or `None`; an image build; **and the guarantee that killing the host-side exec process ends the guest process, or a `kill` that ensures it.**
- Spec: none (4c, 4e and 4f already say this).

**R3. A host-probe module, `dispatcher/host.py`: medium value, very small effort.**
- What moves there: from core.py:280-292, the memory pressure and the power source (`sysctl`, `pmset`).
- What stays in core: the load average and the free disk, which are already portable (`os.getloadavg`, `shutil.disk_usage`), and the container parts, which are in Runtime.
- Interface: `memory_pressure() -> str` and `power_source() -> str` ("AC", "battery" or "unknown").
- A Linux replacement needs the same two readings. [I] For example `/proc/pressure/memory` and `/sys/class/power_supply/*/online`.
- Spec: none (3g is already neutral).

**R4. Split the agent-neutral end check out of the Stop hook: medium value, small effort.**
- What moves: `problems()` and `commits_beyond_main()` go from stop_hook.py into `dispatcher/note.py`. That file is already in the image at `/opt/hook/` and already agent-neutral.
- Interface: `note.check_end(clone: str, task_id: str, outbox: str) -> list[str]`.
- What stays in `stop_hook.py`: only Claude's protocol (stdin JSON, `stop_hook_active`, exit 2) and `transcript_tokens`.
- A replacement agent must provide one of these: a hook that can refuse one stop and show text to the agent, plus a record of the session from which tokens can be counted; or no hook, with a wrapper that runs `check_end` after the agent exits and starts the agent once more with the problems (a design change for the owner to decide, not proposed here).
- Spec: 4t, 4o.

**R5. A provider section in the proxy, and one copy of the usage fields: medium value, small to medium effort.**
- What moves: into `class AnthropicAPI` in proxy.py: `host`; `paths`; `auth_headers(headers: dict, token: str) -> dict` (Bearer plus the OAuth beta); `usage(raw: bytes) -> dict[str, int]` (the current `extract_usage`). The rest stays generic: the Unix socket, telling the Workers apart, the usage JSONL.
- The proxy also writes `total` on each usage line. The Usage tab (logic.py) reads `total`, and its copy of `USAGE_FIELDS` goes. [I] Older usage lines without `total` would then count as 0, unless they are summed as now.
- A replacement provider must provide: an upstream host, the allowed paths, credential injection and a usage parser.
- Spec: 4g.

**R6. Agent-bot identity as configuration: low value, trivial effort.**
- What changes: `BOT` (logic.py:505) moves into the `rules` key of the `--modules` JSON, next to `managers`.
- Interface: `rules.bots: [str]`.
- Spec: none.

**Not proposed (less is more):** an abstract Runtime or Agent base class, or a plugin registry, because no second implementation exists (the owner has since accepted abstract base classes, not a registry); a scheduler abstraction in code: the firing is already a scheduler-neutral one-shot [V], so only the wording changes (3c, 4d).

## 4. Specification sub-points that would change: full replacement text

Applied on 2026-11-24 (owner: "Agree to tool-neutral rewording"):

- **3c.** The Dispatcher is started by the host's scheduler (on macOS: launchd) every 2 minutes; this can be changed via "Settings" in the Cockpit UI. It can also be disabled there. At each firing it starts at most one Worker, so the Workers are started one by one.
- **3o.** The Dispatcher stores the launch time of each Worker. A Worker's last record is the time of its last request through the proxy; if the proxy cannot tell, it is the last-modified time of the agent's record of the session (with Claude Code: its session transcript). The runaway checks (3i) are made at each firing, so a Worker may exceed its limits by up to one firing interval.
- **4d.** A service in the owner's logged-in session (on macOS: a LaunchAgent) keeps the proxy running and keeps the host from sleeping for as long as it does (on macOS: `caffeinate -i`).
- **4f.** A host Unix socket (its path shorter than 104 bytes) is made reachable inside the container by the runtime (with Apple's tool: mounted with `-v`, relayed over vsock); inside, a tiny relay exposes it on `127.0.0.1`, and the Workers' agent points its model-API base URL there (with Claude Code: `ANTHROPIC_BASE_URL`).
- **4g.** On the host, a small reverse proxy listens only on that socket, forwards only the model API's message requests (with Anthropic's API: `POST /v1/messages` and `count_tokens`) to the model API, strips any client credential and adds the credentials corresponding to the owner's subscription; each Worker holds its own dummy token, so that the proxy can tell the Workers apart.
- **4i.** The Workers' environment switches off the agent's non-essential traffic and gives each Worker its own configuration folder; with Claude Code: `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1`, `skipWebFetchPreflight: true` or the WebFetch tool denied, `ENABLE_CLAUDEAI_MCP_SERVERS=false`, `CLAUDE_CODE_DISABLE_ARTIFACT=1`, `CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL=1`, and a per-Worker `CLAUDE_CONFIG_DIR`.
- **4m.** The agent's CLI, python, pytest and git are baked into the image.
- **4o** (last clause only). "... and `tokens` (a whole number, the tokens consumed: the sum of the input, output, cache-creation and cache-read tokens; written by the end check (4t) from the agent's record of the session (with Claude Code: the session transcript), not by the Worker, so that it can be compared with the proxy's count)."
- **4t.** The note is both a skill and an end check. The Workers' skill (with Claude Code: a skill in each Worker's configuration folder) states what the note contains and when it is written. An end check, a script baked into the image (with Claude Code: a Stop hook), checks before a Worker may end: the note is a JSON object; `outcome` and `summary` are present; the fields are of the right type and size; and, unless the outcome is "gave_up", the clone holds at least one commit beyond main and `outbox/result.bundle` exists. If a check fails, the end check refuses the stop once and tells the Worker what is missing; if the check fails again the Worker ends, and the Dispatcher fails the task by 3e.
- **5c.** The processing log is stored where the processing occurs, on the owner's host (currently a MacBook).
- **Goals 1 and Approach 1:** no change recommended. Goals 1 is the owner's goal, not a mechanism, and Approach 1 is a historical record.

## 5. Gaps noted (outside "reduce the dependence", reported only)

- 4d and 3c are not implemented: there is no LaunchAgent and no `caffeinate` in the repository [V].
- The prompt `INSTRUCTION` (runtime.py:26) and SKILL.md:26 tell the Worker to write `tokens`, but 4o says the Stop hook writes them [V].
