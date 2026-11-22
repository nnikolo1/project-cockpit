# Overlap of Study 2A and Study 2B

Date: 2026-11-22
Modules: docs

Two independent studies were given only the owner's brief and context digest (`brief-owner-2026.11.22.md`, `context-digest-owner-2026.11.22.md`). This note lists only what both reached on their own. Source: the two reports (branches `study2-a` and `study2-b`, summary sections) and the comparison in the second reviewer's report. Their facts are not verified by the author of this note.

1. **Adopt the vendor's own cloud runtime** (Claude Code cloud scheduled tasks and cloud sessions) as the runner, not a generic engine.
2. **Reject Temporal, Airflow, Slurm and BPMN engines.** They add a server and do not address the opaque quota, human acceptance or task interference.
3. **GitHub stays the single source of truth,** the queue and the acceptance step: issues, labels, native "blocked by" links and pull requests. Read current facts, never replay history.
4. **Review capacity is the binding constraint, not quota.** Limit work in progress by the number of unreviewed results.
5. **Retire the quota-driven release triggers and most of the custom unattended job:** no local daemon, keep-awake, tool lists or symlink checks.
6. **Tasks needing local data cannot run in the cloud.** They stay local or interactive.
7. **A one-week spike** on sandbox issues, passing on no double starts and no blocked starts.
8. **The same caveats:** preview features with undisclosed caps, a quota shared with interactive use, and terms and limits that are still moving.
