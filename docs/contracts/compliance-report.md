# Compliance report, version 1

Modules: cockpitwebpage

The compliance check (PC-108) writes one current report per project, `docs/compliance/compliance-report.md` inside the project's clone (the `git` setting), and overwrites it on every run; it writes no other file. Whether the report is committed is the owner's call (proposal: the job does not commit it). The Cockpit only reads it: Settings > Policies, Compliance (PC-109).

## The check command

The project setting `compliance` (a command string, split like a shell would, or a list of arguments; `--compliance` for a single project) is the same command the terminal and the daily job run. The Cockpit's **Run compliance check** button starts it detached, with the clone as working folder and the environment variable `COMPLIANCE_REPORT` set to the report's path relative to the clone. One run at a time per project. The run's record is `<data dir>/<project name>/compliance-run.json` (`{"state": "running"|"ended", "pid", "started", "ended", "exit"}`, times in Unix seconds) with the command's output in `compliance-run.log` next to it; the page polls it every 3 seconds while a run is on. A run recorded as running whose process is gone (the Cockpit restarted) shows as interrupted.

## The report

Markdown, sections in this order. The Cockpit parses only the `Checked:` line and the `## Violations` section; the rest is for people.

```
# Compliance report

Checked: 2026-11-25 10:00 UTC

## Scope
Every path and glob searched for policy documents; the files found; the files skipped, each with the reason.

## Policies discovered
Each policy: heading or id, source file and line, the constitutional rule it maps to or "local only".

## Violations
none
```

or, with violations, one `###` block each:

```
## Violations
### CLAUDE.md:12
> the quoted passage,
> over one or more lines
- Rule: the constitutional rule it violates
- Reason: why the passage violates it

## Constitution
Version and date of the constitution checked against.
```

- `### FILE[:LINE]`: the policy file (relative to the clone) and optionally the line.
- `>` lines: the quoted passage (joined with spaces).
- `- Rule:` and `- Reason:`: one line each.
- `Checked:` is the date shown next to the report link; without it the file's modification time is shown.

A violation stays highlighted on the page until a later report no longer lists it. Fixing a policy file is the owner's own act (constitution: local policy files are never overwritten automatically).
