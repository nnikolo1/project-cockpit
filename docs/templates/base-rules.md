# Base rules

Version: 2026-11-25 (agreed by the owner as the first base text; the source from which a project's managed instruction block is rendered, PC-92)
Modules: internal/agents

Tags: **[db]** where the project has a database, **[UI]** where it has a UI, **[modules]** where modules are used, **[optional]** the project may leave it out.

## Working with agents
1. The main agent orchestrates: it delegates non-trivial work to sub-agents and does small tasks itself, weighing the cost of briefing against the task.
2. A sub-agent that changes code works in its own worktree and branch and commits there. It never writes to the main working tree.
3. When a sub-agent finishes, review its result against the brief and the policies. Merge it or send it back, then remove its worktree.
4. Split substantial coding work among sub-agents with disjoint files and an agreed interface, so the seams stay clean. If a task cannot be split, say why.
5. Every requirement in a brief for a sub-agent traces to something the owner said or approved. Propose, do not invent.
6. Agents that write to the same data get disjoint scopes. Never put two agents on the same entity.
7. **[db]** Agents open data stores read-only unless their task is a write.
8. **[optional]** Keep a dated running log of the day: terse, factual. Generated statistics go to the tool's data folder, not into git.
9. Nothing from an agent's memory enters git unsanitised. Archive only what is sanitised and useful as documentation.
10. Record every manual step or hand work-around in the manual-interventions log (when, who, what, why, status). Update its status when it gets automated.
11. Record every prototype short-cut in a note: what it is, why, its limits, and the first step to do it properly.
12. A recurring investigative question becomes a documented, argument-driven script.

## Safety and data
13. Irreversible or costly changes to infrastructure get an independent critique first, from a reviewer with no stake in the change. Draft, critique, then execute.
14. **[db]** A schema change needs the owner's explicit approval, one per change, showing the exact statement, the rows touched and how it can be undone.
15. **[db]** Deleting rows needs the owner's approval of the exact statement.
16. Never print, log or commit a credential.
17. Sanitise before anything enters a repository: no secrets and no personal data. Correct the spelling and grammar of the owner's quoted words as you write them.
18. Generated and processing data stays out of git unless the owner decides so for that file.
19. A negative conclusion (no data, blocked, empty) needs evidence: an observed status or response. An implausible conclusion about a well-known entity gets more scrutiny, not less. Check claims like "untracked" or "missing" with a real command.
20. **[modules]** Each module declares its persistence footprint: tables, files, logs, statistics and retention.
21. A browser tool may inspect your own UIs read-only: no clicks that change data, no credentials typed.

## Code and tests
22. Log important operations, state changes, external calls and failures with context, through the project's logging facility. Never log secrets.
23. Bound every retry loop: after a few identical failures, abort with a distinct log line.
24. Tests mirror the source tree and are fast and deterministic: no network, no external process.
25. Report coverage with every code change, not only pass or fail.
26. A bug fix to pure logic gets a regression test in the same change.
27. A gap the owner reports gets a generalised, project-agnostic test in the same change.
28. **[UI]** Every action the UI offers has a test. An action without one is a violation.
29. CI is automatic: no check, gate or report depends on a person remembering it. Run the tests locally by default. Hosted CI is the exception.
30. State the supported platform and test only it. No compatibility matrix unless the project promises one.
31. Gate chained commands on the tests: validate before writing, and join test and push with `&&`.

## Dialogue
32. Design talk is not a decision. Record it as discussion, and write no decision record, rule or task change until the owner says it is decided.
