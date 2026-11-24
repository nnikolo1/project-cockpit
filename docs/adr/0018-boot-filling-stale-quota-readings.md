# 0018. Boot-filling job: stale or missing quota readings are unknown, not invented

Status: Superseded by docs/specs/dispatcher-and-workers.md (2026-11-24)
Earlier status: proposed (builder); the owner confirms or changes
Date: 2026-11-20
Modules: taskqueue
Amends: [0017](0017-boot-filling-quota-history.md)

## Context
The quota feed (`{observed_at, rate_limits: {five_hour, seven_day}}`) is written by the assistant's status line, which runs only for interactive sessions. Headless workers do not update it, and the job's own refresh call (a minimal headless request) does not either, yet the history said `refreshed` and the reading age grew past an hour. After a 5-hour reset the feed has no `five_hour` entry for hours. An earlier fix turned that missing entry into a synthetic fresh window (0 percent used, 5 h to reset), so the history, `usage.json`, the triggers and the stop threshold acted on invented numbers while workers were consuming tokens. Weekly tokens were right, the weekly percentage stayed at the last interactive reading.

## Decisions
- `parse_feed` returns `None` for a missing 5-hour window (an explicit unknown); it never invents a window. A feed without the weekly window, or with a malformed one, still raises.
- A reading is **fresh** only when the feed is not older than `feed_stale_minutes` (default null = 3 x `poll_minutes`) and the window is present. Otherwise it is **stale** (too old) or **missing** (no such window) or **unavailable** (feed unreadable). The feed age is measured from the feed's own `observed_at` (file modification time when absent).
- The record never presents a stale or missing reading as measured: `used_percentage` and `resets_at` are null, `source` is `stale`, `missing` or `unavailable`, `reading_age_s` is the age, and `reason` says why (for example `feed 62 min old (limit 30); refresh did not update the feed`).
- `source` is `refreshed` only when the feed's observation time moved after the refresh call; a refresh that ran but did not move it is reported as `refresh did not update the feed` (and the reading is `feed` when still fresh by age, `stale` otherwise).
- Token counts always come from the transcripts and are independent of the feed. A fresh window is counted over its own bounds (`resets_at` minus its length, basis `resets_at`); a stale, missing or unavailable one over the **rolling** window (last 5 h or 7 d back from now, basis `rolling`).
- The last good reading per window (fresh, at least 1 percent used, tokens above zero) is kept in `last-good-quota.json` in the data folder: time, used percentage, reset time, window tokens, window start and the **cap** = tokens / used share.
- **Estimate** while a window has no fresh reading and a cap is known: `estimated percentage = rolling-window tokens / cap` (clamped to 100). The history record carries `estimated: true`, `estimated_percentage` and `estimate_basis` (formula, window tokens, cap, the time the cap comes from and its age, the used share it came from); `usage.json` carries `estimated`, `used_percentage`, `estimate_basis` and `tokens_cap_estimate`; the poll log shows `~41% est.` and `five=stale/62min est. 41%`; the budget contract's poll line puts it in `*_est_used`, never in `*_used`.
- The estimate's reset time: the last known reset when still ahead; weekly: the last reset plus whole weeks; 5-hour: the window opened by the first transcript message after the previous reset, lasting 5 h; none when no message since (then the 5-hour triggers stay unknown).
- **The triggers and the stop threshold use the estimate** when the feed is stale, and the decision text and the poll log say so (`| quota: 5-hour window ESTIMATED 60% (...)`).
- **No cap known yet** (no good reading with tokens, or no token count): the window is unknown. Its triggers get the verdict `unknown` and never fire, no worker is started beyond the running ones (`worker_slots` returns 0), and the poll log states why. This applies to the weekly window too (a deliberate extension of the conservative rule).

## Limits
- The cap is a one-point estimate from this machine's transcripts only (other devices and Claude Web consume quota but leave no transcript), so an estimate can be too low; it is refreshed at every fresh reading.
- The rolling window is not the provider's window (which starts at first use): the estimate is rough near window boundaries.
- Fresh readings from before this change are not rewritten; earlier history rows keep their old values.
