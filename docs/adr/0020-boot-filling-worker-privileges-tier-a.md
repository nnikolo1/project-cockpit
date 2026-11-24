# 0020. Boot-filling job: worker privileges, tier A (tool lists, no links, role marker, rules block)

Status: Superseded by docs/specs/dispatcher-and-workers.md (2026-11-24)
Earlier status: proposed (builder); the owner confirms or changes
Date: 2026-11-22
Modules: internal/bootfilling
Amends: [0014](0014-boot-filling-job.md), [0015](0015-boot-filling-implementation-choices.md), [0019](0019-boot-filling-never-twice.md)

## Context
A worker is a headless assistant session started by the job for one released task. It must hold strictly fewer rights than the Orchestrator and the Personal Assistant. The first command line allowed whole families (`git`, `gh`, `python3`, `pytest`), which includes merging, label edits and `gh api`.

## Decisions
1. **Tight tool lists as config.** `worker.command` holds a narrow `--allowedTools` value, `worker.disallowed` is rendered as `--disallowedTools` (`core.worker_command`). Owner clarification, 2026-11-22: a privileged agent (the Orchestrator, the Personal Assistant or the owner) releases the task and incorporates the result; the worker only takes a task up and commits in its local worktree. So a worker has local git only, no `git push` and no `gh pr` of any kind (both denied for any branch and remote), and hosting rights that shrink to viewing the issue, assigning it to the Manager login (its first action) and one marked comment when it stops without a commit. It may not use `gh api`, close or edit labels. A config value replaces a default list, never merges with it.
1a. **The job pushes and opens the draft pull request** (assumption of the Manager, for the owner to confirm). After a worker exits 0 the job reuses an existing pull request, else counts the branch's new commits: none means no push and no pull request, recorded as NEEDS YOU ("committed nothing"); some means the job pushes `refs/heads/<branch_prefix>N` (never the default branch, `core.check_push_target`) and opens a DRAFT pull request whose number is recorded for the awaiting-acceptance state. A failure of either step is NEEDS YOU with the reason. The job never merges.
2. **Verified semantics** (assistant CLI 2.1.308, trivial runs in a scratch folder): deny beats allow; a command with no matching allow rule is refused in a headless run; compound commands are split and each part must pass; a deny rule matches past leading `VAR=x` assignments; a space before the final `*` matters; a pattern ending in `:*` is read as the legacy prefix form, so `*:*` did not stop `git push origin boot/7:main` and `*:**` does.
3. **No symbolic links.** The job refuses to start a worker whose worktree (top level, one level down) contains a symbolic link, records `start_failed` with a NEEDS YOU line, and changes nothing. It never creates one.
4. **Role marker.** `worker_env` (default `BOOTFILL_ROLE=worker`) reaches the worker process, so a project's database layer can later pick its strictest role.
5. **Rules block.** `worker_rules` (generic default) follows the first action in the worker prompt.

## What is enforced, what is an instruction, what tier B adds
| Item | Kind |
|---|---|
| Allowed and denied tools and command shapes (no push, no pull request for a worker) | enforced by the assistant CLI's permission rules (a pattern, not a sandbox) |
| Symbolic-link check | enforced by the job before the start |
| Role marker | a signal; effective only when the project's database layer honours it |
| Push target of the job (prefix only, not the default branch) | enforced by the job's own check |
| Rules block (no deletion, no DDL, no merge, no push, commit scope, marked stop comment) | instruction only; not enforced |
| Database and network limits | NOT enforced: `python3` and `pytest` run arbitrary code, and a rule does not match the same program via another path or inside `sh -c` |

Tier B adds the real barrier: workers run as a separate operating-system user with its own credentials, file permissions and database role (cockpit tasks PC-38 and PC-27). Until then the lists lower the chance and the blast radius of a mistake; they do not stop a determined or confused worker.

## Consequences
Tracked symbolic links in a repository block all workers for it until removed (deliberate: fail closed). The local config must be updated by hand.
