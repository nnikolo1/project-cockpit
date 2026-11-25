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

## Batch 2: safety and data rules (decided by the owner, 2026-11-25)

| # | Rule | Verdict | Form |
|---|---|---|---|
| 15 | Independent review before irreversible or costly infrastructure changes | G | rule line, independent-reviewer skill; the AWS incident is an example |
| 16 | Several agents writing to shared data get disjoint scopes and an assignment record | G | rule line |
| 17 | Schema changes (DDL) need the owner's explicit approval, one per change | G (applies where a database exists) | rule line |
| 18 | Deleting rows needs the owner's approval of the exact statement | G (applies where a database exists) | rule line |
| 19 | Agents open the real data store read-only unless their task is a write | G | rule line, a check where there is a data layer |
| 20 | Persistence footprint declared per module | Split: the feature (collector, contract, cards) is core; the rule that every module declares one is generic where modules are used | rule line (conditional) |
| 21 | Evidence rule for negative conclusions; implausible results get more scrutiny | G | rule line |
| 22 | Sanitise before anything enters a repository (no secrets, no personal data) | G | rule line, a check |
| 23 | Fix spelling and grammar of the owner's words before they are written into a repository | G | rule line |
| 24 | Generated data stays out of git | G | rule line |
| 25 | No credential printed or committed anywhere | G | rule line, a check |
| 26 | Drive application folders are create-only | S | jcrawler |
| 27 | Forward moves in application workflows go through the extension | S | jcrawler |
| 28 | Claude in Chrome may inspect the owner's own UIs, read-only, without asking | Split: the read-only limits are generic, the standing permission is the owner's | rule line (limits) |
| 29 | The public repository uses generic wording and carries no private project data | S | the Cockpit's own repository |
