# Contract: what the three Task Queue changes of 2026-11-24 share

Three changes are built in parallel in separate branches (Worker image management, one queue for all projects, context management for Workers). They meet only at the points below; each branch implements its own side and does not change the others'. Where a signature below is not yet in the code, the branch that needs it adds it with exactly this shape, and the others call it with exactly this shape.

## The project key

A task belongs to a project. `Task.project` (string) is the repository's short name (the part of the task id before the issue number, for example `cockpit` in `cockpit-44`). One queue for all projects introduces the field; today it is derived from `Task.id` (`id.rsplit("-", 1)[0]`) by a helper `tasks.project_of(task_id)`. Every other change uses that helper, never its own parsing.

## Runtime calls

`Runtime.prepare_task(task_id, repo, task_text, project=None)` and `Runtime.launch(user, task_id, agent=None, project=None)`: `project` is passed through by the Dispatcher; `None` means the single default project (today's behaviour, so nothing breaks while a branch is unmerged).

`Runtime.image_for(project)` -> the image reference (a tag) the container of that project must run; `None` project or no project recipe gives the base image `Config.image`. Added by the image change; used by the one-queue change to choose a container per project when one is configured.

## The shared processing-data root

One folder for all projects (the constitution): `<root>/` with `dispatcher-state.json`, `processing/processing.jsonl` (records carry a `project` field), `area/<project>/<task-id>/` for task folders, `proxy.sock`, `proxy-usage.jsonl` (lines carry the Worker user, as today), `quota-history.jsonl`, `synopsis/<project>.md` (the dated session synopsis of context management), `memory/<project>/` (the project memory folder). The root defaults to `~/.local/share/project-cockpit/task-queue` and is an argument (`--data-root`). Until the one-queue change is merged the old per-project folders keep working: every new path above is computed by one function `layout.paths(root, project)` (module `dispatcher/layout.py`, added by the one-queue change); the other changes read paths only through it, with a fallback to the old layout when it does not exist yet.

## Synopsis and memory

`<root>/synopsis/<project>.md`: plain text, first line `Synopsis of <date>`, at most about 400 words, no secrets, no personal data, no quotations of the owner. The Dispatcher copies it to `<task folder>/inbox/synopsis.md` at launch with a first line stating its age in days when older than 7 days ("STALE: <n> days old"). The project memory folder is `<root>/memory/<project>/`; a Worker's settings point `autoMemoryDirectory` at a copy inside its own home, refreshed at launch.
