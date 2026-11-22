# 0020. Boot-filling job: worker privileges, tier A (tool lists, no links, role marker, rules block)

Status: proposed (builder); the owner confirms or changes
Date: 2026-11-22
Modules: internal/bootfilling
Amends: [0014](0014-boot-filling-job.md), [0015](0015-boot-filling-implementation-choices.md), [0019](0019-boot-filling-never-twice.md)

## Context
A worker is a headless assistant session started by the job for one released task. It must hold strictly fewer rights than the Orchestrator and the Personal Assistant. The first command line allowed whole families (`git`, `gh`, `python3`, `pytest`), which includes merging, label edits and `gh api`.

## Decisions
1. **Tight tool lists as config.** `worker.command` holds a narrow `--allowedTools` value, `worker.disallowed` is rendered as `--disallowedTools` (`core.worker_command`). `git push` is allowed only to a branch with the job's prefix and denied with force, delete, mirror, `+`, a refspec colon, or `main`/`master`. The hosting CLI may view, comment, assign the issue (its first action) and create a draft pull request; it may not use `gh api`, merge, close, or edit labels. A config value replaces a default list, never merges with it.
2. **Verified semantics** (assistant CLI 2.1.308, trivial runs in a scratch folder): deny beats allow; a command with no matching allow rule is refused in a headless run; compound commands are split and each part must pass; a deny rule matches past leading `VAR=x` assignments; a space before the final `*` matters; a pattern ending in `:*` is read as the legacy prefix form, so `*:*` did not stop `git push origin boot/7:main` and `*:**` does.
3. **No symbolic links.** The job refuses to start a worker whose worktree (top level, one level down) contains a symbolic link, records `start_failed` with a NEEDS YOU line, and changes nothing. It never creates one.
4. **Role marker.** `worker_env` (default `BOOTFILL_ROLE=worker`) reaches the worker process, so a project's database layer can later pick its strictest role.
5. **Rules block.** `worker_rules` (generic default) follows the first action in the worker prompt.

## What is enforced, what is an instruction, what tier B adds
| Item | Kind |
|---|---|
| Allowed and denied tools and command shapes | enforced by the assistant CLI's permission rules (a pattern, not a sandbox) |
| Symbolic-link check | enforced by the job before the start |
| Role marker | a signal; effective only when the project's database layer honours it |
| Rules block (no deletion, no DDL, no merge, commit scope, marked stop comment) | instruction only; not enforced |
| Database and network limits | NOT enforced: `python3` and `pytest` run arbitrary code, and a rule does not match the same program via another path or inside `sh -c` |

Tier B adds the real barrier: workers run as a separate operating-system user with its own credentials, file permissions and database role (cockpit tasks PC-38 and PC-27). Until then the lists lower the chance and the blast radius of a mistake; they do not stop a determined or confused worker.

## Consequences
Tracked symbolic links in a repository block all workers for it until removed (deliberate: fail closed). The local config must be updated by hand.
