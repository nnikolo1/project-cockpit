# PC-33: sorting the rules of the owner's projects (worked interactively with the owner)

Status: sorting complete, all four batches decided (owner, 2026-11-25); what moves into a template, and in what form, is the next step
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

## Batch 3: code, tests and verification (decided by the owner, 2026-11-25)

| # | Rule | Verdict | Form |
|---|---|---|---|
| 30 | Bounded retries in fetch loops (abort after a few identical failures, distinct log line) | G | rule line, a code check possible |
| 31 | Tests mirror the source tree; fast, deterministic, no network or external process | G (the pure-logic scope under `pipeline/` is project-specific) | rule line |
| 32 | A code change reports test coverage, not only pass or fail | G | rule line |
| 33 | A bug fix to pure logic gets a regression test in the same change | G | rule line |
| 34 | Every gap the owner reports is covered by a generalised, project-agnostic test in the same change | G | rule line |
| 35 | Every UI action has a test | G (where there is a UI) | rule line, harness check |
| 36 | CI is automatic; no check depends on remembering to run it | G | rule line |
| 37 | Tests run locally by default; the hosted run is the exception | Split: the local-gate-first principle is generic, the reasons (metered minutes) are the owner's | rule line |
| 38 | Only the Python version in use is supported and tested | Split: "state the supported platform and test only it" is generic, Python 3.14 is the Cockpit's own choice | rule line |
| 39 | Gate chained commands on the tests (validate before writing, join test and push with `&&`) | G | rule line |
| 40 | Batch pushes when hosted CI minutes are metered | S | the private repository |
| 41 | Verify "untracked" or "missing" claims with a real check before stating them | G | folded into the evidence rule (21) |

## Batch 4: the Cockpit's task and release workflow, and the skills (decided by the owner, 2026-11-25)

Decided by the owner, 2026-11-25: the core (C) rules reach the Claude of a project that uses the Cockpit as a short, generated "Cockpit workflow" section of the managed block (PC-92), derived from the constitution, which stays the single source; a project does not describe the workflow itself.

Rows 42 to 58 and the skills, agreed by the owner as a whole (2026-11-25):

| # | Rule | Proposed |
|---|---|---|
| 42 | Open questions persisted as issues (`question`, `type:decision`) | C |
| 43 | Task provenance block and sanitising of owner words in tasks | C |
| 44 | Who may create tasks and when one counts as released; a Worker suggests at most one task | C |
| 45 | The assistant never sets `released`, priority, deadline or `cancelled` on its own initiative | C |
| 46 | All tasks go through the Task Queue; tasks about the queue are `interactive-only` | C |
| 47 | One global ranking, one project first by setting, the others equal | C |
| 48 | Blocked tasks are never assigned | C |
| 49 | The Interactive agent merges unmerged tasks, may delegate, parallel when conflict-free | C |
| 50 | Policy findings are filed and released by the finder, worked by someone else | C |
| 51 | Components refreshed within 24 hours; the daily job reports and files gaps | C |
| 52 | Local policy files are never overwritten automatically | C |
| 53 | The UI is the owner's alone; settings may narrow ranges | C |
| 54 | Module-map rules (ambiguity, null with a basis, notes, 30 percent threshold) | C, optional |
| 55 | Design talk is not a decision | G |
| 56 | The daily report is a policy | G, optional |
| 57 | Boot-filling delegation text in the jcrawler instruction file | obsolete (superseded by the Dispatcher) |
| 58 | Restart the Cockpit after a merge, redraw the Workflow page, test isolation with two users | S |

Skills: `agent-brief` G; manager review G; independent reviewer G; worker task protocol and filing a question C; `ats-investigator`, `crawl-qc` S; `code-writer` split.

## Step 1 done: the base text

The 32 generic rules were condensed into the base text `docs/templates/base-rules.md` (31 lines in four groups, with the conditional tags [db], [UI], [modules] and [optional]), agreed by the owner on 2026-11-25 without changes. Next steps: the generated Cockpit workflow section (step 2), the skills (step 3), the cleanup of the private project's instruction file (step 4), PC-34 (step 5).

## Step 2 done: the Cockpit workflow section

The owner edited the draft (rule 1 names GitHub, rule 5 loses "provisional") and agreed it on 2026-11-25; written to `docs/templates/workflow-section.md` (13 lines). Next: the skills (step 3), the cleanup of the private project's instruction file (step 4), PC-34 (step 5). Open: how rule 8 of the base text (the running log goes to the data folder, not into git) is reconciled with the constitution's Daily reports section.
