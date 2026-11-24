# 0017. Boot-filling job: quota history with token counts

Status: Superseded by docs/specs/dispatcher-and-workers.md (2026-11-24)
Earlier status: proposed (builder); the owner confirms or changes
Date: 2026-11-20
Modules: taskqueue
Amended by: [0018](0018-boot-filling-stale-quota-readings.md) (source `refreshed` only when the feed moved; stale and missing readings; estimates)

## Context
The owner wants the job to record the quota utilisation at the moment of firing, including token counts (2026-11-20). The status-line feed is overwritten each time and has no history; the poll log showed `n/a` whenever a poll skipped before the quota step; the provider publishes percentages, not token limits.

## Decisions
- One append-only record per poll, written for every outcome (not idle, paused, feed unreadable, below threshold, released). File `quota-history.jsonl` in the data folder (key `quota_history`, null = off). Record: `kind` `quota_snapshot`, `schema` 1, `time`, `mode`, `idle` (true, false or null when not checked), a short `idle_reason`, the `decision`, and per window (`five_hour`, `seven_day`): `used_percentage`, `resets_at`, `window_start`, `window_basis`, `reading_age_s`, `source`, `reason` (when relevant) and `tokens`.
- Source of a reading: `refreshed` (the one minimal call ran this poll, idle polls only, unchanged behaviour), `feed` (the feed file read as is, no call; the status line keeps it fresh while sessions run; age = file modification time; if the refresh failed, the stale feed is used and `reason` says so), `unavailable` (with the reason; percentages null).
- Window start = `resets_at` minus 5 h or 7 d (`window_basis` `resets_at`). When no usable reading exists or its window is already over, the bounds are the rolling window ending now (`window_basis` `rolling`), so tokens are always counted and honestly labelled.
- Tokens: `input`, `output`, `cache_creation`, `cache_read`, `total` (their sum), `messages`, `own_total` (tokens from the job's own transcripts: the headless folder and worker worktrees, i.e. the exclude patterns the idle check uses) and a `note` when the window holds no message. Both ends of the window are inclusive.
- Transcript scan: Claude Code transcripts under `transcript_glob`, all of them (the job's own calls consume quota too). Only the timestamp, message id, request id and the four usage numbers are used; content is never stored or logged. Repeated usage lines of one message (same id and request id, also across files) count once, taking the largest figure per field (streaming writes partial counts first). Incremental: `token-cache.json` in the data folder keeps per-file offset, size and mtime (files named by a hash, no paths) and the events of the last 8 days; a partial last line waits for the next scan; unreadable lines and files are counted and skipped.
- `usage.json` for the budget page is rewritten atomically on every poll (`usage_report`: null = `<budget_dir>/usage.json`, "" = off): `tokens_used` per window (the total) plus extra fields (`tokens_input`, `tokens_output`, `tokens_cache_creation`, `tokens_cache_read`, `messages`, `tokens_own`, `window_start`, `window_basis`). Compatible: readers of contract version 1 ignore unknown fields; `tokens_cap_estimate` is not written (the page derives its own).
- Retention: rows older than `quota_history_days` (default 400) are pruned by the job at most once a day (marker `quota-history.pruned`), logged.
- Backfill: `python3 -m bootfill.quota_backfill --glob G --out FILE --since DATE` writes hourly UTC buckets, token-only, `reconstructed: true`, `percentages: null`. Approximate (only this machine's transcripts; deleted transcripts are missing). It refuses to write inside the repository or over an existing file.
- The poll log line gains the source and age of each reading and the token totals, and shows the readings also when the poll skips. The budget contract's poll line is unchanged.

## Limits
- Percentages cannot be reconstructed for the past; a poll with no reading in a window records none.
- Tokens come from this machine's transcripts only; usage from other devices or Claude Web counts in the percentage but not in the tokens, so a derived cap is an estimate.
- Cache-read tokens are counted in `total` as the transcripts report them; the provider's quota may weigh the four kinds differently. The four parts are stored separately so the weighting can change later without a rescan.
- A feed older than the status-line interval is read as is and its age is recorded; the job does not call the refresh just to record history.
