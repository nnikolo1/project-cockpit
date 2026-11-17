# 0001. GitHub is the store and the identity provider

Status: accepted
Date: 2026-11-17

## Context
The cockpit must show tasks, their tree, blockers and who changed what. A new database, user system or server would be a project of its own, and the constitution prefers adopting existing tooling over building (Purity, Frugality).

## Decision
All state lives in GitHub: issues are tasks, sub-issues are the tree, native "blocked by" links are the dependencies, labels carry classes and protected attributes, and a Project (v2) optionally adds fields such as the module. The app reads it with one GraphQL query per 100 issues through the `gh` CLI and acts as the user's own `gh` login. It stores no token, no database and no files of its own (the only exceptions are an optional access-token file and browser-side review marks, see 0004 and 0007). Who did what is whatever GitHub reports as the event actor.

## Consequences
- No accounts, backups, migrations or server to run; the data stays where the team already works.
- GitHub's history is the audit trail, so the cockpit cannot lose or fake it.
- We depend on GitHub features that are new (sub-issues, issue dependencies) and on its API allowance (see 0010).
- Given up: a data model of our own, offline use, and anything GitHub cannot express (for example a free-form event log).
- Identity is only as strong as GitHub logins; with one shared login there is no separation (see 0003).

Revisit when: GitHub changes or retires sub-issues or dependencies, or a project needs data GitHub cannot hold.
