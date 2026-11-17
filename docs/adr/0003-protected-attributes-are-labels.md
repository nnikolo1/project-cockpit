# 0003. Protected attributes are labels, trusted by their actor

Status: accepted
Date: 2026-11-17

## Context
The constitution says only the Manager releases tasks, sets priority or deadlines, and cancels. GitHub labels can be set by anyone with write access, and the cockpit has no store of its own (0001).

## Decision
The protected attributes are labels: `released`, `P1` to `P3`, `due:YYYY-MM-DD` and `cancelled`. A protected label counts only when the latest event that set or removed it was made by the Manager (`policy` in `logic.py`). Otherwise it is ignored and the task shows an "unauthorised" flag: detect and ignore. An optional workflow in `kit/` (`policy.yml` with `policy.py`) reverts such changes on label events and every 10 minutes, because changes made with the default workflow token start no workflows.

## Consequences
- The rule needs no extra store and works on any repo with the kit labels.
- The limit, plainly: in phase 1 everyone acts as one GitHub login, which is the Manager. Then every change looks like the Manager's, nothing is detected, and the workflow has nothing to revert. Enforcement is reactive and only meaningful once actors have separate identities (see the roles memo).
- Even then, revert happens after the fact; a wrong label is visible for up to about 10 minutes, and the cockpit ignores it meanwhile.
- Given up: real prevention (GitHub labels have no per-label permissions).
- Only the latest event counts: if the Manager sets a label and someone else later removes and re-adds it, it is ignored until the Manager sets it again.

Revisit when: agents get their own identities, or GitHub offers per-label permissions.
