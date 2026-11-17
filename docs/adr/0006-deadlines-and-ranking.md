# 0006. Deadlines are optional; the ranking is fixed and visible

Status: accepted
Date: 2026-11-17

## Context
Not every task has a date, and forcing one produces made-up dates. The Orchestrator still needs a clear answer to "which released task next?".

## Decision
- A deadline is optional. It is a protected `due:YYYY-MM-DD` label, so it counts only when the Manager set it (0003). A task without its own deadline inherits one along the chain in 0005; the Backlog names the source of an inherited date.
- Released, unblocked tasks are ranked (`cmp` in `web/index.html`) by, in order: priority P1 before P3 (no priority last); earlier effective deadline (none last); the number of tasks it unblocks (more first); `must` before `nice`; then the task key.
- Blockers and priority are always visible, never hidden by a filter.

## Consequences
- One predictable order, explainable in a sentence; the Orchestrator needs no private heuristic.
- Inheritance gives sub-tasks sensible dates without extra labels.
- Given up: weighting, estimates in the order, and per-person queues.
- The ranking lives in the page code. A headless Orchestrator must reimplement it or read it from the page; there is no API endpoint for it yet.
- If several `due:` labels exist, the most recently set one is used.

Revisit when: the order is needed outside the page (for example by an unattended agent), or when real use shows the four criteria rank badly.
