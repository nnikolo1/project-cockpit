# 0009. Stdlib only, small code, pure-logic tests

Status: accepted
Date: 2026-11-17

## Context
The constitution asks for something light-weight. Dependencies and code volume are the usual ways a viewer turns into a system that needs its own maintenance. The working rules (`CLAUDE.md`) state Purity, Frugality, Brevity.

## Decision
- Python standard library only, plus the `gh` CLI and `git` already on the machine. The page is one HTML file with no build step.
- A line budget by habit, not by tool: every line must be required by the constitution or the Manager's words. Size at this date: `cockpit.py` 509 lines (server, `gh` calls, actions), `logic.py` 579 (pure logic), `web/index.html` 549 (page), 1,637 in all.
- Tests are pytest and pure logic only (`tests/`, `kit/test_policy.py`): no network, no `gh`, no browser. Actions are tested as plans (the list of `gh` calls) with a fake runner. CI runs them on Python 3.10 to 3.13 on Linux and macOS.

## Consequences
- Nothing to install beyond Python and `gh`; a clone is the whole deployment.
- Fast, deterministic tests that cannot rot because GitHub changed.
- Given up: libraries that would shorten some code, end-to-end tests, and tests of the page itself. A bug in the GraphQL query or the page is found only by use (screenshots in `tools/shots.py` help).

Revisit when: the line counts grow far beyond the figures above, or a page bug class appears that only a browser test would catch.
