# Page merge run 2 (integration/page-merge-2)

Nine Worker branches that touch the page code, merged one after another onto `integration/page-merge-2` (from main at fde62accd), in the order the brief gave. After every merge: `node --check` of the page script and the full `python3 -m pytest`. Nothing is pushed or merged into main.

## Result per branch

All nine merged; none left out or undone. Tests after each merge were green once its conflicts and the map entries were settled.

| Branch | Task | Merged | Conflicts and how they were resolved | Tests after |
|---|---|---|---|---|
| worker/project-cockpit-18 | PC-18 stagger, offline cases, 1-hour limit | yes | none; `tests/conftest.py` and `tests/test_stagger_offline.py` had no map entry: added | 945 passed |
| worker/project-cockpit-131 | PC-131 "Failed to fetch" on restart | yes | `get_state_served` (kept 18's `newWork`/`steerBlock`, added `warming`), `test_projects.py` (expected dict has both), page banner (kept 18's `ageBanner`, added the warming banner, dropped 131's older stale banner); map entry added | 964 passed |
| worker/project-cockpit-132 | PC-132 action log | yes | `do_act`: 131's action-id wrapper calls 132's logging wrapper (`_logged_act`), which calls `_do_act`; `api()` keeps 131's message and 132's `network` flag; `post()` carries 131's retries plus 132's page notes; `conftest.py` has both fixtures. Two 131 page tests got stubs (`PAGE_ID`, `note`, `D`), one 132 assertion regex was adapted | 975 passed |
| worker/project-cockpit-134 | PC-134 local time on the Task Queue | yes | `logic.py` (kept 18's helpers, took 134's `_when` in place of `_iso_utc`), banners now print times with 134's `tm()`; `test_compliance.py` and `tests/tools/test_ci.py` got `tmTxt`/`tm` stubs | 986 passed |
| worker/project-cockpit-130 | PC-130 queue refresh every 2 min | yes | `get_state_served`/`_served` (poll state also goes through `live_state`), queue bar uses `tm()` (the global `hm` is gone), ADR 0010 amendments of both sides kept, map entry | 1007 passed |
| worker/project-cockpit-135 | PC-135 refresh after a state change | yes | Large: the known-effect/re-read block placed before the action-id block; `_served` strips `raw` and flags `stale` while a re-read or deferred effect waits; `post()` applies `j.state`, flashes the row, marks other tabs stale, refreshes the tab; `paneBar` shows "your change applied" (not on the Queue pane, see follow-ups). Two real interaction defects fixed, see below. One 135 test was adapted (see below) | 1031 passed |
| worker/project-cockpit-136 | PC-136 Cancel feedback | yes | `post()` is now the join of 131 (retries, ids), 132 (log), 135 (refresh) and 136 (one action at a time, spinner, lasting note, lookup after no answer); `setState` also lays 136's overlay. 131's footer was in the conflict and is restored. 136's own test was red on its branch (see below) | 1039 passed |
| worker/project-cockpit-137 | PC-137 every UI action tested | yes | `conftest.py` (both); `tools/ui_actions.py` now also reads `_do_act` (the dispatch moved there); the enumeration found five untested actions from the other branches: tests added for `retryact` (with refusal), `toastx`, `notex`, `problems`, form `alog`; page harness adapted (action id and page id are random, `refreshTab` replaces `reload`, `window.addEventListener`, card settings now carry `queue_refresh_s`). The enumeration test was not weakened: nothing is in its classification table by exemption, the five were given tests and classified | 1081 passed |
| worker/project-cockpit-128 | PC-128 open issues on the module chips | yes | `logic.py`: both added blocks kept; the merge swallowed `return out` of `filter_events`, restored (four action-log tests had caught it); map entry | 1088 passed, twice in a row |

Last result: 1088 passed, 1 skipped, node check OK.

## Defects found and fixed while merging (not in any branch)

- 135 + 131: the known-effect step of an action waited on the lock that a running full load holds, so during the warm-up (131) every action blocked until the warm-up ended (131's own test caught it). `after_action` now waits at most 1 s; if the load still holds the state the effect is applied in a background thread when the load is stored, and the state is flagged stale meanwhile. No regression test of its own beyond 131's `test_release_cancel_and_edit_work_during_the_warm_up`.
- 135 + 131: an action's answer now carries the served state; 131 keeps the last 200 answers in `actions-seen.json`, so each would have held a whole state. The state copy is now left out of what is kept (regression test added); a repeated action is answered without it and the page refreshes anyway.
- 136: its own test `test_successful_cancel_release_and_edit_show_a_lasting_confirmation_and_the_new_row` was red on the branch (the test compared an object it had mutated later); fixed in the test. The overlay also dropped an entry as soon as a copy "showed" it, which let an older copy undo it; an entry is now dropped only when a copy was read from GitHub after the action (full load or the PC-135 re-read).
- 135 vs 18: a 135 test started an action during a rate-limit pause; since 18 the write side refuses actions then (503), so the test now starts the pause after the action. Intended behaviour of 18 is unchanged.

## PC-134 and the Dispatcher

The Dispatcher is touched in exactly one file: `dispatcher/plog.py`, `ProcessingLog._write` (3 lines). The record's `time` is now an ISO time with the offset (for example `2026-11-25T09:59:01+02:00`) instead of the naive local time. Old lines stay readable: the viewer (`logic._when` and the page's `tm()`) takes a string without an offset as the server machine's local time. Also changed: `docs/specs/dispatcher-and-workers.md` (one line) and the tests `tests/dispatcher/test_plog.py`. This runs live every two minutes once the Dispatcher picks the file up; no other Dispatcher file, setting or schema changes.

## What remains and why (gaps recorded, nothing blocks the merge)

- I read each task's issue text only as far as its first 1500-1800 characters and compared it with the diff stat and the diff of the conflicting parts; I did not re-verify every bullet of every task, and I judged policy conformance by the repository's own compliance and map tests (all green), not by reading each diff against CLAUDE.md and the constitution line by line. A review of the merged result against the task texts is still worth doing.
- PC-135: the Task Queue pane (`queueBar`) does not show the "your change applied ... confirmed by GitHub" note that the other panes show.
- PC-135 and PC-136 both put the effect of an action on the page (the server's patched state, the page's overlay). Merged as is: the overlay is harmless but redundant once the server copy carries the effect; it is the page's fallback when the answer carries no state (repeat, deferred effect).
- PC-131: after three network failures the page now also asks for the task's state (136); that lookup goes to the same unreachable server and will usually only say "not known". Acceptable, not improved.
- One timing-dependent test (`tests/test_compliance.py::test_button_starts_the_command...`) failed once in a full run and passed in 5 later runs; it is not from these branches (`main` has the same test and file).
- The README sections of the branches merged automatically; I did not re-read them for consistency.

## Follow-up tasks I suggest (not filed)

1. Show the PC-135 "change applied / confirmed" note on the Task Queue pane bar.
2. Drop the page overlay of PC-136 once the server's answer always carries the patched state (including repeats and deferred effects), or document why both stay.
3. A regression test for `after_action` with a full load in progress (the deferred path), with a fake lock holder.
4. Make `tests/test_compliance.py`'s running-state test deterministic.
5. Let the policy harness (PC-91) call `tools/ui_actions.py` (PC-137's third point) once PC-91 is on main; I did not check that it is.
