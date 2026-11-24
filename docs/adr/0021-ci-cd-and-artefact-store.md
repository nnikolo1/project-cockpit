# 0021. The Cockpit has a small element of CI/CD and an artefact store

Status: accepted (owner, 2026-11-24)
Date: 2026-11-24
Modules: taskqueue, internal/metadata
Amends: [0009](0009-stdlib-only-small-code-pure-tests.md)

## Context
The Cockpit began as a viewer over GitHub. In practice it also builds what its projects need (collectors, code snapshots, token statistics, reports, backups), checks it (the gap checker, the policy harness), and delivers its own releases and managed files to the projects, and the generated artefacts need a known place, an age and a version. The owner stated this on 2026-11-24.

## Decision
The frame is the objective of the constitution: a pocket-size Swiss Army knife for agent-assisted software development, in a nutshell and opinionated.

The Cockpit has a small element of continuous integration and delivery and of an artefact store:
- it builds, checks and delivers for every project it serves, through one daily upkeep job with ordered phases (refresh, policy checks, remediation tasks);
- generated artefacts live in a known place (a standard folder in each project, outside version control for generated data) with their age and version;
- it stays small: plain folders and manifests before any registry, borrow over build, nothing a person cannot read.

## Consequences
The README and the constitution say so; the tasks of the policy harness, the continuous delivery of the Cockpit to projects and the daily job follow this record. It does not make the Cockpit a project management system or a server with accounts.
