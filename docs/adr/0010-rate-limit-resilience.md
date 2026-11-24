# 0010. Rate-limit resilience: cache, stale-on-error, one call

Status: accepted
Date: 2026-11-17

## Context
GitHub gives each login a shared hourly allowance and also limits bursts. The cockpit runs under the user's own login, so its calls compete with the user's other tools and agents. A page that re-queries on every click would burn the allowance and then fail exactly when it is needed.

## Decision
- The whole task state comes from one GraphQL query per 100 issues, including labels, assignees, parent, blockers, recent comments and the label, assignee and close events. Per-issue calls are avoided.
- The built state is cached for 5 minutes (`STATE_TTL_S`, `get_state`); the page refreshes itself every 3 minutes, only while visible; a Refresh button forces a fetch.
- If a refresh fails (limit, outage), `cached` serves the last good copy, logs a warning with its age, and retries in about 15 seconds instead of on every request.
- Any steering action clears the cache so the next view shows the result.
- Amendment 2026-11-24 (owner F-698, the budget ran out and the page was empty): a failed refresh always shows the old copy (memory, else disk) with a note: its age, the reason, and when GitHub is tried again (`staleNote`). A disk copy younger than `DISK_FRESH_S` (= `GH_REFRESH_S`, one hour) is used as is after a restart, so a restart costs no call. Every gh call passes `_gh_gate`: after a rate-limit error no call is made until the reset (the time in the message, else `RATE_LIMIT_PAUSE_S`), and at most `GH_CALLS_PER_HOUR` calls per rolling hour are made, with a summary log line every `GH_SUMMARY_EVERY` calls. Actions and Refresh pass the same gate and fail with a clear message when it is closed. The Task Queue's running Workers, processing log and settings are read from local files (`/api/queue`) and never wait for GitHub; the Settings and Token Usage tabs are local too.

## Consequences
- Several open browser tabs cost about one fetch per 2 minutes in total.
- A GitHub outage degrades to slightly old data, not an error page. On a cold start with no copy yet, the error is shown.
- Given up: instant freshness (a change made in GitHub can take up to about 5 minutes to appear), and fine-grained updates.
- Limits to know: 100 label events and 10 comments are read per issue (README, Limits).

Revisit when: repos grow so large that one state fetch is slow or costly, or webhooks become an option.
