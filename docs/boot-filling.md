# Boot-filling job: install (dry run first)

Decision: [ADR 0014](adr/0014-boot-filling-job.md); implementation choices: [ADR 0015](adr/0015-boot-filling-implementation-choices.md). Code: `bootfill/`.

1. Copy `bootfill/config.example.json` to a local file and edit projects, tracking issue and paths. `mode` stays `dry-run` (the default): the job only logs what it would release and never starts a worker.
2. Create the tracking issue (daily summary comment; its `pause` label is a kill switch) and an optional per-project exclusion file `{"labels": [], "numbers": [], "title_patterns": []}`.
3. Try one poll by hand: `python3 -m bootfill.job --config CONFIG --data-dir DIR --once`.
4. Render the plist with `python3 -m bootfill.render_plist --config CONFIG --data-dir DIR --checkout COCKPIT_DIR --out FILE` (the interval is `poll_minutes`, default 10; it prints the matching `--quota-stale-min` for the cockpit), put the file in `~/Library/LaunchAgents/` and `launchctl load` it. The helper never loads anything. After changing `poll_minutes`, render again and reload.
5. After a week of dry run read `decision-log.jsonl` and the Budget page; only then set `"mode": "real"`.

Stop at any time: `touch DIR/PAUSE`, or label the tracking issue `pause`, or `launchctl unload` the plist.

## Parallel workers

The configuration key `workers` sets how many workers may run at the same time (default 2; 0 starts none; an unusable value falls back to the default). Change it in the local config; it takes effect at the next poll.

## Idle, scope and own calls

The job acts only when no Claude Code agent other than its own is active. Counted: Claude Code transcripts under `transcript_glob` (default the Claude Code projects folder) written within `idle_minutes` (default 10), and headless Claude Code command-line processes the job did not start (`process_check`, `process_names`, `process_exclude`). Not counted: Claude Web, the desktop chat app, the browser, an open but silent interactive session, and the job's own agents (quota refresh, judge, workers), whose transcripts are excluded (own calls run in `<data dir>/headless`; workers in `*-wt-boot-*` worktrees; `transcript_exclude` adds patterns). The poll log reason reads "other Claude Code agent active: last write 3 min ago" or "only own Claude Code agents (2 own worker(s) running)".

## Which task runs next

Workers run in parallel up to `workers`. When a slot frees, the job looks at the first `conflict_window` queued tasks (default 6) and takes the first in rank order that is unlikely to get in the way of the running ones (different project, different module, no shared path or shared-files label); `conflict_max_skips` (3) bounds how often a task may be passed over; the model judge decides only unclear cases (`conflict_judgement`). The decision log records chosen, skipped, signals and reasoning.
