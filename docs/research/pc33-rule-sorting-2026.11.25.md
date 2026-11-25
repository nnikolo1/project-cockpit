# PC-33: sorting the rules of the owner's projects (worked interactively with the owner)

Status: in progress, decided batch by batch (owner, 2026-11-25)
Date: 2026-11-25
Modules: internal/agents

Verdicts: **G** generic (opinionated but fit for any developer using Claude Code with background agents), **P** the owner's personal style, **S** specific to one project, **C** core (a mechanism of the Cockpit, not a project policy), **Split** (principle generic, details not). Owner's criterion (2026-11-25): the Cockpit is an opinionated tool, so an opinionated rule is still G; P is for personal style only.

## Batch 1: how the agent works (decided by the owner, 2026-11-25)

| # | Rule | Verdict | Form |
|---|---|---|---|
| 1 | Playing coach: the main agent orchestrates, delegates non-trivial work, does small tasks itself | G | rule line |
| 2 | Isolation by default: a sub-agent that changes code works in its own worktree and branch and commits there | G | rule line, in the brief template |
| 3 | Manager and janitor: review against the brief and policies, merge or send back, remove the worktree, push (opt-in) | G | skill |
| 4 | Considerate archivist: archive sanitised agent memories into the repository | G | rule line (the archive is part of the opinionated way) |
| 5 | Session reporting: a dated running log of the day | G | rule line; a harness check |
| 6 | Logging by default in application code | G | rule line |
| 7 | Re-usability: a recurring question becomes a documented, argument-driven script | G | rule line |
| 8 | Explain every command (a plain-language description on every shell call) | P | owner's instruction file |
| 9 | Manual interventions log | G | rule line, log template |
| 10 | Short-cuts get a mental note | G | rule line, note template |
| 11 | Less is more | P | owner's instruction file |
| 12 | Substantial coding work is split among sub-agents (to force clean seams and modular code) | G | rule line |
| 13 | Briefs trace every requirement to the owner | G | skill (`agent-brief`, exists) |
| 14 | Authority analysis of tasks | C | Cockpit mechanism |
