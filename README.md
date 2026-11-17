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

    python3 cockpit.py --repo OWNER/REPO [--project N] [--steer]

Open the printed `http://127.0.0.1:PORT/?t=TOKEN` URL. Optional: `--token-file PATH` (keep the token across restarts), `--modules` (JSON `{modules:[{id,name}], assign:[{path,module}]}`), `--git` (local clone), `--code DIR`.

Modules come from the Project field `Module` or `module:<name>` labels. The Code tab reads `DIR/metrics.json` plus optional `erd|dataflow|deps|js.svg`; without them it shows "not wired". Contract: see `code_view` in `logic.py`.

GitHub setup: `kit/` holds labels (`kit/setup.sh OWNER/REPO`), issue forms and the policy Action. Copy `kit/.github` to `.github` and `kit/policy.py` to `kit/policy.py` in the target repo, then `gh variable set MANAGER --body LOGIN`.

## How it works
- GitHub is the store; labels carry the protected state (`released`, `P1`-`P3`, `due:DATE`, `cancelled`), counted only if the Manager set them.
- One `gh` call per action. Read-only unless `--steer`.
- Acts as your `gh` login; stores no token.

## Limits
- One identity means no enforcement yet: the policy Action only reverts.
- Relies on GitHub sub-issues, issue dependencies, Projects and Actions variables.
- Reads the last 100 label events and 10 comments per issue.

## Licence
[0BSD](LICENSE), (c) 2026 nnikolo1. Clone: `git clone https://github.com/nnikolo1/project-cockpit`.
