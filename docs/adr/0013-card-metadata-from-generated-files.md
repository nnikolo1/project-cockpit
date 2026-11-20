# 0013. Card metadata comes from generated files, not from the cockpit

Status: accepted
Date: 2026-11-20

## Context
Cards of modules and sub-modules should show how much the code changes (churn), what a module persists (tables, files, logs, volume, backup and retention) and which files cannot be put into one module. Measuring these here would need a database, a git history walk and a mapping engine in the cockpit, against the constitution (Frugality) and ADR 0008.

## Decision
- A producer on the project side writes `footprint.json`, `churn.json` and `ambiguity.json` into a per-project folder (`meta` key of the project, default `<data dir>/<project name>/cardmeta/`). The versioned contract is [docs/contracts/card-metadata.md](../contracts/card-metadata.md).
- The cockpit validates and displays them (`logic.card_meta`, endpoint `/api/cardmeta`, cached 60 s). A missing, old or foreign file means "no data" for that block; nothing is invented and nothing fails.
- Every block shows the file's `generated_at` and age, with a stale warning after 48 hours.
- Churn is colour coded by rank within the project (top fifth hot, bottom fifth cold, zero dormant), always with a symbol and a word; an option ranks by churn relative to size.

## Consequences
- The cockpit stays language- and project-neutral; any producer can feed it.
- Numbers can be stale; the page says how old they are.
- Given up: live values, per-file drill-down, thresholds and alerts.

Revisit when: a second producer needs a field the contract lacks (raise `schema`), or the bands need user-set thresholds instead of ranks.
