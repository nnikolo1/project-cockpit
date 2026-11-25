# Page-code merge run, 2026-11-25

Integration branch `integration/page-merge-2026.11.25`, started from main at `b1a0d53a4` (main has moved on since; the interactive agent merges this branch and may meet small new conflicts). Order as briefed (owner F-857). After every merge: `node --check` of the page script and the full `python3 -m pytest`. Baseline on main: 755 passed, 1 skipped.

| Branch | Result | Conflicts and how resolved | Tests after |
|---|---|---|---|
| 114 | merged | `logic.py`: main's epoch conversion (`_iso_utc`) kept, the branch's `_iso` change dropped. `web/index.html`: main's local `t0` replaced by the branch's global, more robust `t0` (number, string, missing). Two adaptations: the branch's epoch-0 test expected `+00:00` (main's format has none); its new test file had no module-map entry. | 766 passed |
| 112 | merged | none | 772 passed |
| 110 | merged | none. Its own test `test_page_cells_tooltip_and_no_readings` failed on the branch itself (the 2031-05-20 day row only exists with a message that day): the test now adds one message. Map entry added. | 779 passed |
| 111 | merged | none | 791 passed |
| 113 | merged | `cockpit.py` `get_usage`: both kept (`estimates` from 111 and `monthly` from 113; the 90-day `summary`/`projects` rows dropped, as 113 retires that table). Map: both entries. `test_usage_order.py`: order list has `estimates`, title of `projects` is "Per project, by month". | 801 passed |
| 109 | merged | `README.md`: the `--projects` option list keeps `start_month`, `transcript_sources` and adds `compliance`. The compliance command runs from the project's own setting, no shell. | 812 passed |
| 101 | merged | none | 822 passed |
| 90 | merged, adapted | main already has PC-89 (`logic.order_modules`, `cockpit.ordered`, `module_lines`, `code_lines(sizes, assign, ids)`). The branch added a second set (`order_by_size`, `order_subs_by_size`, a second `code_lines`, `module_sizes`): all dropped. Instead `order_modules` now also orders the sub-modules of one module among their siblings (same key, never across parents) and reorders each parent's `children`. The branch's tests were rewritten against `order_modules`. Page: `sizeSum` (uses `m.lines`) shows the size in the sub-module meta line. Map entry added. | 825 passed |
| 77 | merged, adapted | `logic.py` `transcript_report`: PC-110's window-mean logic applied to the interactive and the headless rows alike. `web/index.html` history card: 110's note, peaks and tooltip kept; 77's headless card, per-source card only with several sources, "Interactive sessions with the most tokens". `README.md`: 113's paragraph with 77's wording on interactive/headless. Map and footprint note: both sides. One test adapted: `test_titles_and_the_headless_card...` needs an earlier-day headless record (main lists today by the hour only). | 833 passed |
| 79 | left out | Superseded by 113: the same table ("All projects (last 90 days)") is replaced by "Per project, by month", which already attributes folders to projects (`transcript_sources`, "unassigned"), sums all sources and marks the aggregate. Conflicts in `cockpit.py`, `web/index.html`, `tools/transcript_usage.py`, `README.md`. Merge aborted, nothing from it on the branch. | 833 passed |

Last result: node check ok, 833 passed, 1 skipped.

## Gaps recorded (merged, but not fully to the brief)

- 101: the owner's later comments on PC-101 say a task is editable only in the states before WIP (embryo, released), and the server refuses a save in any other state. The branch still offers Edit on WIP and closed tasks with a warning. Follow-up needed.
- 112: the brief asks to state the diagnosis (rendered widths) in the task note; the commit holds only the fix and its tests.
- 111: the estimates appear in their own card "Quota per vendor window", not in the cells of the history card (110); the history card's note still says "nothing is estimated". Follow-up: decide whether the history cells show the estimate.
- 77/110: the headless card now uses the same quota columns as the history card (an adaptation, not asked by 77).

## Policy notes

- No schema change, no deletions, no network use in the new tests. New test files all have a module-map entry (needed: the map tests fail otherwise; four branches lacked one).
- No hosting-service write, no push; only `gh issue view` was used.

## Follow-ups suggested (not filed)

1. PC-101: refuse and hide Edit outside embryo/released (owner's later comments), with tests.
2. PC-79: close as superseded by PC-113; its two extras not in 113 are a "Sources" column and links to dated session reports; file separately if still wanted.
3. 111/110: show estimates in the history cells, or reword the note.
4. Branches' own tests: a check in the Worker's definition of done that the full suite and the module-map tests pass on the branch (3 of 10 branches failed their own suite or map test).
