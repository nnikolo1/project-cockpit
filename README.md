# Project cockpit

A light-weight, opinionated viewer over GitHub for agent-assisted projects. **Not** yet another project management system: it adds no store, no accounts, no server state.

![cockpit](docs/screens/cockpit.png)

More: [backlog](docs/screens/backlog.png), [timeline](docs/screens/timeline.png).

## The opinion
- Stages: embryo, released, in progress, completed, cancelled.
- Only the Manager releases, prioritises, sets deadlines or cancels.
- Deadlines are optional: set by the Manager, or inherited from the parent or module.
- Priority is P1-P3.
- Blockers and priority are always visible.

Full text: [docs/constitution.md](docs/constitution.md).

## Quickstart
Needs Python 3.10+ and an authenticated [`gh`](https://cli.github.com/).

    python3 cockpit.py --repo OWNER/REPO [--project N] [--read-only]

Open the printed `http://127.0.0.1:PORT/?t=TOKEN` URL. Optional: `--token-file PATH` (keep the token across restarts), `--modules` (JSON `{modules:[{id,name}], assign:[{path,module}]}`), `--git` (local clone), `--code DIR`.

Modules come from the Project field `Module` or `module:<name>` labels.
Sub-modules: a slash id, `alpha/one` (parent = prefix; list the parent too; optional `desc`). Label `module:alpha/one` assigns a task; the Project field stays the top level and a sub-label wins only under that top (else the field wins).
`assign` paths match by longest prefix, so a sub-module entry beats its parent; files matching only the parent stay in it. Deadlines inherit: task, parent task, sub-module, module (`MODULE_DEADLINES` keys are full ids); the Backlog names the source.
The metrics snapshot may carry `alpha/one` entries under `modules`; the parent's entry is the producer's roll-up, a sub-module without one shows "not wired". The Code tab reads `DIR/metrics.json` plus optional `erd|dataflow|deps|js.svg`; without them it shows "not wired". Contract: see `code_view` in `logic.py`.

## Card metadata
Module and sub-module cards show three blocks read from files a producer writes: **churn** (commits, lines, net, trend for 30 and 90 days, coloured by rank within the project: `▲ hot` top fifth, `▽ cold` bottom fifth, `– dormant` no change; a button switches to churn relative to size), **persistence footprint** (tables, files and folders, logging, volume, backup and retention, with "undocumented" shown as a gap) and **file ambiguity** (unsorted files with candidates, a marker above 30 percent; module-level ambiguous files on the overview). Every block says "as of" and warns when older than 48 hours; no file means "no data". Contract: [docs/contracts/card-metadata.md](docs/contracts/card-metadata.md); folder setting `meta` per project: [docs/deployment.md](docs/deployment.md).

## Submit, Cancel and the budget panel
Unless started with `--read-only`, the Backlog table has a **Submit** column (embryos: releases the task as P2 without a deadline, the same as the Release form with its defaults) and a **Cancel** column (open tasks). Each button needs two clicks: the first turns it into "Confirm?" (it disarms after 6 seconds or on any other click), the second acts. No bulk selection. The **Budget** page shows the quota windows (used share, time to reset, age and stale marker), token numbers with the cap marked as an estimate, a table of tasks released by the boot-filling job and a table of its latest polls, all read from files a producer writes (`--quota-feed`, `--usage-report`, `--decision-log`). Contract: [docs/contracts/budget-panel.md](docs/contracts/budget-panel.md).

The **Token Usage** page (read-only) shows the Dispatcher proxy's token counts per day, per 5-hour window and per Worker, and per task the proxy's sum next to the `tokens` of the task's good-bye note. It reads `proxy-usage.jsonl`, `processing/processing.jsonl` and `area/` from the project's data folder (`GET /api/usage`); a missing file shows as "no data". It shows no share of your quota: the proxy cannot know it.

## Settings
The **Settings** page shows and changes the seven values the Dispatcher reads at each firing (on/off, interval, retries, workers, runaway, no-activity and no-start limits), kept per project in `<data dir>/<project name>/dispatcher-settings.json` (mode 0600; a missing file shows the defaults). The page refuses a runaway limit below 30 minutes, a no-activity limit below 10 and a no-start limit below 1, and shows the cap on workers (the image's worker users); the Dispatcher itself does not enforce these. A Cockpit started with `--read-only` refuses to save (403) and shows the values read-only.

## Several projects
`--projects FILE` serves several repos from one instance (replaces `--repo`, `--manager`, `--project`, `--git`, `--modules`, `--phases`, `--code`; `--port`, `--lan`, `--read-only`, `--token-file` stay global):
`{"default": "widgets", "projects": [{"name": "widgets", "repo": "acme/widgets", "project": 3, "manager": "alice", "modules": "m.json", "git": "~/src/widgets", "code": "~/snap"}, {"name": "gadgets", "repo": "acme/gadgets"}]}`.
Only `name` and `repo` are required (optional: `project`, `manager`, `modules`, `git`, `code`, `phases`, `meta`). With more than one project the header shows a selector (remembered in the browser); every `/api/*` call takes `?project=NAME` (default: the `default`), `/api/projects` lists `[{name, repo}]`, and only the visible project is polled.
Rationale and limits: [ADR 0012](docs/adr/0012-several-projects-in-one-instance.md).

GitHub setup: `kit/` holds labels (`kit/setup.sh OWNER/REPO`), issue forms and the policy Action. Copy `kit/.github` to `.github` and `kit/policy.py` to `kit/policy.py` in the target repo, then `gh variable set MANAGER --body LOGIN`.

## Rules tab
Optional `rules` key in the `--modules` JSON: `{"rules": {"groups": [{"name": "Instructions", "paths": ["CLAUDE.md", "**/CLAUDE.md"]}], "managers": ["Name or e-mail"], "bots": ["[bot]", "noreply@anthropic"]}}`. `bots` lists substrings of author name and e-mail that mark an agent-assisted change; when absent, the two values shown are the default. Globs are repo-relative; `**/` spans directories (or none), `*` and `?` stay within one path segment.
Needs `--git` (a local clone). The tab lists the rule files and their last change, the last 90 days of changes with a diff excerpt, and open pull requests touching them.
A change is flagged when it carries a Co-Authored-By trailer or bot author, or is not by a listed Manager. Review marks stay in the browser (localStorage); nothing is stored server-side.

## How it works
- GitHub is the store; labels carry the protected state (`released`, `P1`-`P3`, `due:DATE`, `cancelled`), counted only if the Manager set them.
- One `gh` call per action. Actions are enabled unless started with `--read-only`.
- Acts as your `gh` login; stores no token.

## Limits
- One identity means no enforcement yet: the policy Action only reverts.
- Relies on GitHub sub-issues, issue dependencies, Projects and Actions variables.
- Reads the last 100 label events and 10 comments per issue.

## Deployment
Running it as a service and separating Manager, Orchestrator and Worker, with what is and is not enforced: [docs/deployment.md](docs/deployment.md).

## Decisions and notes
- Architecture decisions: [docs/adr](docs/adr/README.md).
- Memos (roles, shortcuts, roadmap): [docs/memos](docs/memos/README.md).
- The constitution: [docs/constitution.md](docs/constitution.md).

## Licence
[0BSD](LICENSE), (c) 2026 nnikolo1. Clone: `git clone https://github.com/nnikolo1/project-cockpit`.

## Assumptions

- **The agent is Claude (Claude Code).** The Workers, the Dispatcher's launch command, the Stop hook, the skill format and the proxy's credential handling assume it.
- **The host is macOS.** The Dispatcher runs under launchd, the Workers' container uses Apple's `container` tool, and the host probes (memory pressure, power source) use macOS commands.

These are assumptions, not goals. The code is kept as agnostic about the agent and the operating system as is practical: what depends on one of them stays in a small, named place (for example the container runtime adapter, the launch command, the image, the host probes), so that replacing it is a local change.

