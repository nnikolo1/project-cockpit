# Contract: the project registry and the task functions (PC-140 slice 2) — DRAFT, not yet approved by the owner

Two parts that meet only at the project record. Everything is per project (the single-project setup is the special case).

## The project record

`{"name", "repo", "repo_dir", "short", "first" (first task number), "project_board" (optional), "modules" (optional path), "env" (optional)}`. `name` is the key; `short` is the task-id prefix (the part before the issue number).

## Part A: the registry (`ckp/store/registry.py`, `ckp/core/projects.py`, CLI `ckp project ...`)

- File: `<CP_HOME>/projects.json`, one list of project records, written atomically through `ckp.store.io`. The viewer's present `projects.json` (richer, with card-metadata paths) is read through an adapter and is not replaced in this slice.
- `add_project(record, *, dry_run=False)`: like `git init`: validates the record (repo is `owner/name`, `repo_dir` is a git clone whose remote matches), creates the project's data folder, the per-project queue folder and the empty environment record; idempotent for an identical record; refuses a clash of `name` or `short` with another project. Returns a Result `{ok, error, data}`.
- `list_projects()`, `get_project(name)`, `remove_project(name)` (removes the registry entry only; never data).
- CLI: `ckp project add --name --repo --repo-dir [--short] [--first]`, `ckp project list`, `ckp project show NAME`, `ckp project remove NAME`; JSON Result on stdout, exit 0/1/2 as `ckp deploy`.
- The Dispatcher's `projects` setting and the viewer read the registry (a follow-up, not this slice).

## Part B: the task functions (`ckp/core/tasks.py`, CLI `ckp task ...`)

Thin, project-scoped functions over the existing `dispatcher.tasks` code, no new logic in the page script: `list_tasks(project, state=None)`, `get_task(project, id)`, `file_task(project, title, body, labels)` (an embryo, never released), `comment(project, id, text)`. Release, cancel and priority stay the owner's (UI and the assistant on the owner's word); they are not exposed here.
- CLI: `ckp task list|show|file|comment --project NAME ...`, same Result shape.
- GitHub calls go through `ckp.adapters.github` with an injected runner, so the tests need no network.

## Out of scope here

PC-138 (the Dispatcher serving several projects), the migration of the live job, UI changes, deleting data.

## Split for sub-agents

Agent A: Part A (registry, `add_project`, CLI, tests). Agent B: Part B (task functions, CLI, tests). Disjoint files; the shared piece is the project record above and `ckp/cli.py`'s subcommand table, which I merge.
