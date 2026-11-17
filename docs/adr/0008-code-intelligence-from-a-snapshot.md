# 0008. Code intelligence is consumed from a snapshot, not computed

Status: accepted
Date: 2026-11-17

## Context
Code metrics and diagrams (size, complexity, test coverage, dependency pictures) are useful on the cockpit, but each language and project measures them differently. Computing them here would make the cockpit a tool chain of its own.

## Decision
- The cockpit only reads a snapshot directory (`--code`) produced elsewhere: `metrics.json`, and optionally `erd.svg`, `dataflow.svg`, `deps.svg`, `js.svg`. The expected shape is the contract in `code_view` (`logic.py`).
- Per module (and optionally per sub-module) the Code tab shows the producer's numbers, the snapshot commit and its date. A parent entry is the producer's roll-up.
- With no snapshot, or no entry for a module, the tab says "not wired". It never invents a value.
- There is deliberately no overall score or grade: a single number hides what a person should look at.

## Consequences
- The cockpit stays small and language-neutral; any producer can feed it.
- The numbers can be stale: the page shows the snapshot's commit and date, and a refresh needs the producer to run again.
- Given up: live metrics, alerts on thresholds, trends over time.

Revisit when: several projects feed snapshots and a shared format or history of snapshots is wanted.
