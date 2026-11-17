# 0002. An opinionated subset of GitHub semantics

Status: accepted
Date: 2026-11-17

## Context
GitHub issues can mean anything. A cockpit needs a small, fixed vocabulary so that a glance tells where each task stands, and so that "what may happen next" can be checked by code.

## Decision
A task has exactly one stage, derived (never stored) from labels, assignee and state, in this order:
1. cancelled: label `cancelled`, or closed as "not planned";
2. completed: closed for any other reason;
3. in progress: open and assigned;
4. released: open, unassigned, label `released` counted (see 0003);
5. embryo: everything else.

Allowed moves are planned in `_plan` (`cockpit.py`): release only an embryo; change priority or deadline only on a released or in-progress task; cancel only an open task. A finished task is never reopened: the page offers "File a follow-up task" instead.

## Consequences
- Anyone can read a stage off an issue without the app; the app and GitHub cannot disagree.
- The rules are small enough to test as pure functions.
- Given up: custom workflows, a "reopen" path, extra stages such as review or blocked (blockers are shown separately, not as a stage).
- Closing a task by hand in GitHub, with the wrong reason, changes its stage; the app takes GitHub at its word.

Revisit when: a real project needs a stage the five cannot express, or follow-up tasks prove too clumsy for corrections.
