# Budget panel contract, version 1

The **Budget** page shows how much of the usage quota is spent, and what the boot-filling job decided ([ADR 0014](../adr/0014-boot-filling-job.md)). The cockpit computes nothing it does not read from three files and changes nothing: producers write the files, the page reads them (read-only). Any file may be missing, empty or broken: the page then says "no data" for that part and never fails.

## Where
Default folder `~/.local/share/project-cockpit/budget/` (next to the project folders, see [deployment](../deployment.md)):

| File | Flag | Written by |
|---|---|---|
| `quota.json` | `--quota-feed PATH` | the quota feed of the assistant's status line (percentages and reset times) |
| `usage.json` | `--usage-report PATH` | an optional usage report (token counts per window) |
| `decisions.jsonl` | `--decision-log PATH` | the boot-filling job |

The files are global (one quota per account, not per project). Write each atomically (temporary name, then rename); the decision log is append-only. Never write file contents, secrets or personal data into them.

## `quota.json` (existing feed, read as is)
```
{"five_hour": {"used_percentage": 31.0, "resets_at": 1795176000},
 "seven_day": {"used_percentage": 55.5, "resets_at": "2026-11-24T09:00:00Z"}}
```
- `used_percentage` 0 to 100 (clamped). `resets_at`: epoch seconds or ISO 8601 (UTC when no zone). A window without a usable `used_percentage` is not shown.
- The feed carries no timestamp, so its **age is the file's modification time**. Older than **30 minutes** by default (three polls of the job at the default 10-minute interval; the cockpit flag `--quota-stale-min` sets it): a stale warning. A `resets_at` already in the past: the window is over and the reading is flagged "already reset".

## `usage.json` (optional)
```
{"schema": 1, "generated_at": "2026-11-20T10:30:00Z",
 "windows": {"five_hour": {"tokens_used": 310000, "tokens_cap_estimate": 1000000},
             "seven_day": {"tokens_used": 5550000}}}
```
- `tokens_cap_estimate` is optional. The provider publishes percentages, not token limits, so **every cap shown is an estimate** and is labelled so. Without a reported cap the page derives one as `tokens_used / used share` when the quota share is at least 1 percent, and says "from used share". Tokens left = cap minus used (also an estimate).
- Age: `generated_at` (file modification time when absent). Older than 24 hours: stale warning.

## `decisions.jsonl` (JSON lines, one object per line, append-only)
Every line has `kind` (`"release"` or `"poll"`) and `time` (ISO 8601 UTC). A line that is not JSON, has another kind or no readable `time` is skipped and counted ("n unreadable lines skipped"). The page shows the newest 50 of each kind, newest first. Free text is cut to 300 characters; keep it short and free of personal data.

**Release line** (one per task the job released, or would release in a dry run):
```
{"kind": "release", "time": "2026-11-20T10:00:05Z", "trigger": "five_hour_20", "rank": 1,
 "project": "demo", "issue": 7, "title": "Add a widget",
 "reason": "P1, due soonest, unblocks 2", "state": "released", "dry_run": false}
```
- `trigger`: name of the trigger that fired. `rank`: position in the job's ranking (number). `project`: project name as in the `--projects` file. `issue`: issue number. `title`: the task title. `reason`: why it has this rank (the ranking inputs, the value judgement). `state`: what happened, free word such as `released`, `would_release`, `skipped`, `failed`. `dry_run`: `true` shows a "dry run" marker; anything but `true` counts as false.

**Poll line** (one per poll, also when nothing was released):
```
{"kind": "poll", "time": "2026-11-20T10:00:00Z", "idle": true, "idle_reason": "no session activity for 40 min",
 "five_hour_used": 31.0, "five_hour_resets_at": "2026-11-20T12:00:00Z",
 "weekly_used": 55.5, "weekly_resets_at": "2026-11-24T09:00:00Z",
 "triggers": [{"name": "five_hour_20", "verdict": "fired"}, {"name": "weekly_50", "verdict": "not met"}],
 "decision": "release 20"}
```
- `idle`: boolean (anything else shows "?"). `five_hour_used`, `weekly_used`: percent as read at poll time (numbers). `*_resets_at`: text, ISO 8601 preferred. `triggers`: the verdict of every trigger evaluated (`verdict` `fired` is highlighted). `decision`: the outcome in words, for example `release 20` or `skip: not idle`.

## What the page does with them
Two window cards (used share bar, time to reset, reset time, tokens when a usage report exists, age and stale marker per source), a table of released tasks and a table of the latest polls. Missing files are reported per source. Fixtures for tests: `tests/fixtures/budget/`.
