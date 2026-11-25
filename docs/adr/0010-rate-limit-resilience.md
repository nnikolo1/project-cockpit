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

- Amendment 2026-11-24 (owner F-699 to F-704, lazy refresh): GitHub data is refreshed at most once per `GH_REFRESH_S` (1 hour) per project. A request within the hour is served from the cache with no call; an older copy is served at once and one refresh is queued (per key at most one pending; all run one at a time through `_gh_queue`). Nothing is fetched at startup when a disk copy is under an hour old. Only the owner's explicit Refresh click on a pane bypasses the hour, and only for that pane's own request (Cockpit, Task Queue: task state; Timeline: state and commits when there is no clone; Open questions: every project's state); it is queued, logged, counted, and a rate-limit pause still stops it. No timer, page load, restart or action fetches; an action plans from the cached state and does not drop it. Local tabs never depend on GitHub: Token Usage, Settings (the pull-request list of Policies is its own request, `/api/rulesprs`, hourly cache, shows 'not available now' alone), Code (its module list comes from the module map file via `/api/code`), the Task Queue's Dispatcher part (`/api/queue`). Each GitHub pane says "data from HH:MM, next refresh after HH:MM". After a rate-limit error no call is made until the reset (`_gh_gate`); an hourly cap `GH_CALLS_PER_HOUR` bounds the total.
- Amendment 2026-11-25 (owner, PC-119, a WIP task shown with no Worker running): the Task Queue's task state is also refreshed when the Dispatcher's processing log has a state change newer than the cached copy, at most once per `LIVE_REFRESH_S` (2 minutes) per project, only with room for one state load in the hourly cap and never during a rate-limit pause (`cockpit.live_state`). Until then the page lays the log's newer stages over the cached table, marked "live", and a WIP task no running Worker holds shows "WIP? (no Worker running)"; the "Being processed now" card shows its own live time.

## Consequences
- Several open browser tabs cost about one fetch per 2 minutes in total.
- A GitHub outage degrades to slightly old data, not an error page. On a cold start with no copy yet, the error is shown.
- Given up: instant freshness (a change made in GitHub can take up to about 5 minutes to appear), and fine-grained updates.
- Limits to know: 100 label events and 10 comments are read per issue (README, Limits).

Revisit when: repos grow so large that one state fetch is slow or costly, or webhooks become an option.
