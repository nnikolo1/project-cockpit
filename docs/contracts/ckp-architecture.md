# Contract: porting the Cockpit onto the `ckp` command-line API structure

Status: working contract for the refactoring slices (owner: "refactor the code base to use the command-line API structure we started working on ... until all the existing functionality is ported", 2026-11-27; split among sub-agents along these seams). Same repository, ported in place; the old tests are the parity oracle.

## Target layout

- `ckp/core/`: pure logic, no I/O, no network. The themes of today's `logic.py`: `tasks_model` (stage, derive, dispatchable, blocking, ranking, ops, proposals, questions), `modules` (module map, files, snapshot, code view, relations, card metadata, churn, bands, footprint), `rules` (rule files, rule feed, policy text), `commits` (git parsing, lanes, feed), `usage` (usage report, transcripts, quota, estimates), `queue_view` (queue state, capacity, log rows, live overlay), `settings` (validation), `health` (gaps, backup, CI, compliance). Names are a guide; a function goes to the theme it belongs to.
- `ckp/adapters/`: every external call (git, GitHub, processes, the container tool, files) behind one injected `run(args)`; no module outside the adapters calls `subprocess` or `gh`.
- `ckp/store/`: files and folders (layout, io, cache, releases, env, registry, lock).
- `ckp/queue/`: today's `dispatcher/` package (core, runtime, proxy, relay, tasks, plog, host, quota, launchd, agent, note) moved over.
- `ckp/server/`: the viewer's HTTP handlers (today's `cockpit.py`), thin: they call API functions and return their Results.
- `ckp/cli.py`: one subcommand per API function; the command and the HTTP endpoint call the same function.

## Rules for every slice

1. **No change of behaviour.** Move first, change nothing else. The existing tests (about 1,300) must pass unchanged, except import paths; a test is changed only to follow a move.
2. **Shims during the migration.** The old module (`logic.py`, `cockpit.py`, `dispatcher/*`) re-exports from the new place (`from ckp.core.x import *` style or explicit names), so other slices and the live service keep working. Shims are deleted only in the final cleanup slice, never by a parallel slice.
3. **API functions return a Result** `{"ok": bool, "error": str|None, "data": ...}`, take an injected runner for I/O, and log through `logging` (loggers named `ckp.*`).
4. **Each slice ends green:** `tools/merge_gate` passes on its branch; new tracked files have entries in `docs/cockpit-modules.json`; no schema or data change, no new dependency.
5. **Disjoint files per slice.** A slice touches only the files it owns; shared files (`docs/cockpit-modules.json`, `ckp/cli.py` subcommand table) are edited in one clearly separated block and merged by the Interactive Agent.
6. The live service is not touched: no restart, no job change, no deploy.
