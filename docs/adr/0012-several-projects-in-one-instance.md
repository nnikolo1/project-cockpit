# 0012. Several projects in one instance, chosen by a selector

Status: accepted
Date: 2026-11-19

## Context
ADR 0011 ran one cockpit per repository. A Manager with several repos then runs several processes, tokens and ports, and opens several URLs.

## Decision
- One process can serve several projects: `--projects FILE` lists them (name, repo, optional Project number, Manager, modules file, clone, snapshot dir). The single-project flags still work and define one implicit project named after the repo; mixing both forms is refused at start.
- Every project-dependent API call takes `?project=NAME` (default: the configured default); an unknown name is a 404. `/api/projects` lists name and repo only. A steering action acts on the project named in the request, with that project's repo and Manager login.
- All caches (state, rules, commits) are keyed by project, so projects never mix; stale-on-error (0010) holds per project.
- The page shows a selector only when there is more than one project, remembers the choice in the browser, clears its page caches on a switch and polls only the visible project.
- Steering (`--steer`, read-only default) and the token stay global.

## Consequences
- One URL, one token, one service to run.
- Rate limits: every project draws on the same `gh` login's allowance. Only the visible project is polled, and a project is fetched when first opened, so cost grows with use, not with the number of projects.
- The `gh` login must have access to every configured repo, and one login is the actor for all of them. Steering any project means acting as that one login, so the Manager of each project must be that login (the start-up warning is given per project).
- Given up: a failing or slow project can not be isolated by killing a process; different tokens, ports or steering modes per project; a per-project OS user.

Revisit when: projects need different logins or different steering modes (run one instance each, as in 0011), or when polling several projects at once is wanted.
