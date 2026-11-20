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

## Authority need

Before ranking, each eligible task is assessed for whether it would dead-end for lack of an authority the job and its workers do not have ([ADR 0016](adr/0016-boot-filling-authority-judgement.md)): authority labels, approval or decision wording, schema or credentials or public-release wording, an open linked question, protected paths (`protected_paths`), weaker keywords. Verdicts: `none` (eligible), `maybe` (model judge, title, labels and a capped redacted excerpt only) and `needs_authority`. With `authority_mode` `skip` (default) such tasks stay embryos; `flag` releases them marked. A failed or missing judge leaves a `maybe` task unreleased and the text says so. Every such task appears as `NEEDS YOU: ... Why: ... To release it: ... Signals: ...` (same wording) in `decision-log.jsonl` (`type: authority_skip`), the poll log, the budget `decisions.jsonl` (kind `authority_skip`) and the daily summary comment, which lists them with numbers and titles. Keys: see `config.example.json`; the exclusion file may add `authority_labels`, `authority_patterns`, `protected_paths`.

## Quota history

Every poll, also a skipped one, appends one record to `DIR/quota-history.jsonl` ([ADR 0017](adr/0017-boot-filling-quota-history.md)): the time, mode, idle verdict and, for the 5-hour and the weekly window, the used percentage, reset time, window start, the age and source of the reading (`refreshed` only when the feed really moved after the refresh call, `feed`, `stale`, `missing`, `unavailable`) and the token counts of the window computed from the Claude Code transcripts (input, output, cache creation, cache read, total, messages, and `own_total` for the job's own calls). Only timestamps, message ids and usage numbers are read, never message content; the scan is incremental (`DIR/token-cache.json`). A poll that is not idle reads the feed as it is (no refresh call). The poll log shows the readings, their sources and the token totals. The same numbers go to the budget page's `usage.json` (`usage_report`, default `<budget_dir>/usage.json`, `""` = off). Keys: `quota_history` (null = off), `quota_history_days` (default 400, pruned once a day), `transcript_glob`. Derived data: never commit it.

Token-only history of the past (hourly buckets, approximate, no percentages):
`python3 -m bootfill.quota_backfill --glob '~/.claude/projects/**/*.jsonl' --out DIR/quota-backfill.jsonl --since 2026-10-01`
It never writes inside the repository or over an existing file (`--overwrite`).

## Stale or missing quota readings

The quota feed is written by the assistant's status line, which runs only for interactive sessions; headless workers and the job's refresh call do not update it ([ADR 0018](adr/0018-boot-filling-stale-quota-readings.md)). A feed older than `feed_stale_minutes` (default 3 x `poll_minutes`) or without a 5-hour window is recorded as unknown: `used_percentage` null, source `stale` or `missing`, the reading age, and token counts of the rolling window (last 5 h or 7 d) from the transcripts. The last good reading and its cap (tokens / used share) are kept in `DIR/last-good-quota.json`. While a cap is known the job estimates the percentage as rolling-window tokens / cap, marks it `estimated: true` (history, `usage.json`) and `est.` (poll log), and the triggers and the stop threshold act on the estimate. With no cap known the window is unknown: no trigger of it fires and no new worker starts, and the poll log says why. Nothing is ever invented.

## Keeping the machine awake

A launchd job does not fire while the machine sleeps. While the job is armed (real mode, not paused) or workers are running, every poll starts `caffeinate -i -t N` (idle sleep only; the display may still sleep) for twice the poll interval (config keys `keep_awake`, default true, and `keep_awake_minutes`, null = 2 x `poll_minutes`). Each poll renews the hold, so the chain sustains itself and ends by itself a few minutes after the job is paused, unloaded or set to dry run. Limits of `caffeinate`: a closed lid or an empty battery still puts the machine to sleep; macOS only (elsewhere the step logs a warning and the poll goes on). The first poll must find the machine awake, which it does while you work.

