# Cockpit workflow

Version: 2026-11-25 (written and agreed by the owner; the short section of a project's managed instruction block that tells Claude how work flows through the Cockpit; derived from the constitution, which stays the single source, PC-92)
Modules: internal/agents

1. Tasks are issues on GitHub. Their states are embryo, released, WIP, unmerged, failed and merged, plus cancelled.
2. Only the owner releases a task. You release one, or set priority, deadline or cancelled, only on the owner's explicit request that names the task. A task without a priority label counts as P2.
3. Open questions are filed as issues labelled question and type:decision, as embryos. A new question gets its issue in the same turn you ask it.
4. A task you file ends with an "Origin" block: the owner's request, sanitised and corrected, the date and the session. Analyse every task for authority and rule conflicts when you file it, before reporting it filed.
5. An interactive agent may create tasks. Such a task counts as released after 10 days. A Worker suggests at most one task per run, in its good-bye note.
6. Tasks about the queue itself go through the queue like every other task.
7. A task blocked by an unmerged task is never assigned. Set the blocker with the blocked-by relation.
8. Merge tasks in the unmerged state without being asked. Check the commit against the task and the policies. If both hold, merge. If only part of the brief is met, merge and file a finishing task. If a policy or test fails, don't merge and file a follow-up task. You may delegate this, and parallel merging is fine when conflicts can be avoided.
9. A policy violation is a finding: a task labelled policy-finding, released at once, worked by someone other than the finder.
10. Generated components are refreshed within 24 hours. The daily job reports gaps and files them as tasks.
11. Local policy files are never overwritten automatically. Fixes are the owner's own act.
12. The Cockpit UI is the owner's alone. Agents use the hosting-service command line.
13. Workers never push and never change labels. Their results arrive as worker/<task> branches.
