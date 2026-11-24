# Addendum to the Task Queue fit-gap: Agent Teams and Claude Ship as parts

Status: research addendum (background agent, 2026-11-24; web research only, no code run)
Date: 2026-11-24
Modules: taskqueue

Brief: F-788 ("consider Agent Teams and Claude Ship, not as a complete solution but whether we can enlist them for some of the functionality"). Builds on `fit-gap-task-queue-2026.11.24.md` (gaps G1-G10, combination M1, trust rules, owner constraints); nothing there is repeated. Neither name appears in that report or the earlier research.

Marks: **[V]** read in the cited primary source on 2026-11-24; **[I]** inference by this author.

## 1. What the two are

| | Agent Teams | Claude Ship |
|---|---|---|
| Exact name, owner | "Agent teams" in Claude Code, Anthropic [V, [docs](https://code.claude.com/docs/en/agent-teams)] | "Claude Ship", Anthropic: three agents, Code Review, PR Steward, Claude Approvals; plus the local commands `/ship` and `/steward` [V, [docs](https://code.claude.com/docs/en/claude-ship), [product page](https://claude.com/product/claude-ship)] |
| Date, status | Introduced 2026-02-05 "as a research preview" [V, [announcement](https://www.anthropic.com/news/claude-opus-4-6)]; today still "experimental and disabled by default", with "known limitations around session resumption, task coordination, and shutdown behavior" [V] | Announced 2026-11-23 (press [V: 9to5Mac, Help Net Security headlines]); Code Review "generally available", PR Steward and Claude Approvals "in public beta" [V]; `/ship` needs Claude Code v2.1.330 or later [V, [/ship](https://code.claude.com/docs/en/ship-command)] |
| Vendor or community | Vendor feature of the coding tool, not of the SDK as such | Vendor product (cloud agents on GitHub) plus vendor commands in the CLI |
| Install, run | Set `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`; ask the lead session in natural language to spawn teammates [V] | Org agents: an Owner enables them in claude.ai Organization settings, per repository [V]. `/ship`, `/steward`: typed in an interactive CLI session in a local clone; needs `gh` 2.63+ signed in, git 2.38+ [V] |
| Plans | No plan restriction stated on the page [V]; usage counts against the plan like any session [I from costs page] | Claude Ship agents: "for organizations on Team and Enterprise plans", billed to usage credits "separately from your plan's included usage" [V]. `/ship` runs "in your session ... against your usage" [V]; the page names no plan limit [V]. The `/code-review` it uses "works on any plan" [V, [/code-review](https://code.claude.com/docs/en/code-review-local)] |

**Name ambiguity.** Many community skills are also called `/ship` (claudekit, gstack, BuilderIO and others, seen in search results [V]); several commit, push and some merge. Many community guides describe "agent teams" [V, search results]. This addendum judges only the vendor's features, because the brief names them as products and the vendor's pages are the primary sources; community `/ship` skills would also need the repository trust check and were not judged.

## 2. How each works, as far as it matters here [V unless marked]

**Agent Teams.** A lead session spawns teammates, each "a full, independent Claude Code session". Shared task list with states pending, in progress, completed, dependencies, self-claim "using file locking"; mailbox files at `~/.claude/teams/{team}/inboxes/{agent}.json`; task list at `~/.claude/tasks/{team}/`. Hooks `TeammateIdle`, `TaskCreated`, `TaskCompleted` can veto with exit code 2. Limits that decide the fit:
- "Spawning teammates also requires an interactive session. In non-interactive mode with the `-p` flag, including Agent SDK sessions, Claude doesn't spawn teammates."
- "One team per session ... You can't ... share a team across sessions"; no resume of in-process teammates; "Task status can lag".
- No per-teammate permission modes at spawn; teammates inherit the lead's mode; all share the lead's checkout unless the work is split by files ("Two teammates editing the same file leads to overwrites").
- Token use: "approximately 7x more tokens than standard sessions when teammates run in plan mode" [V, [costs](https://code.claude.com/docs/en/costs)].
- The task list is filled by the lead model, not by a deterministic rule.

**Claude Ship (org agents).** Run in the vendor's cloud on GitHub pull requests; PR Steward "pushes commits with its fixes" and is "designed not to approve or merge"; Approvals reads `.claude/approvals.yml` and can post an approving review where the policy allows. Not available with Zero Data Retention, HIPAA or customer-managed keys.

**`/ship` and `/steward` (local).** `/ship` "can commit your work, push your branch, and open the pull request as you", then reviews with `/code-review`, then keeps a "watch" in the session that pushes fixes, merges the base branch in, answers comments. "Cloud sessions don't have them." During a watch Claude Code blocks merge, approve, auto-merge and force-push commands and refuses `WebFetch`/`WebSearch`, but "the limits on a watch don't cover everything". A deny rule `Bash(git push *)` means "no watch starts".

**`/code-review` (the reusable piece inside `/ship`).** Reviews "the changes on your branch", any target (`main...branch`, commit range, path), reports as text; runs in the foreground with `-p`; follows `CLAUDE.md` even in `-p`; any plan; `--fix` edits the working tree; `ultra` goes to the cloud. Posting to GitHub only with `--comment`.

## 3. Judged as parts of M1

Owner constraints, sources as in the earlier report: subscription access through the vendor's coding tool (spec Goals 1, 4g; F-755 to F-757); no network but the model by default, per-kind policy held by the owner (4c, 4s; F-762, F-763); a Worker never pushes or merges (3e, 4p); only the owner releases (constitution "The Dispatcher", 2b); small and local (need 10, ADR 0009; 5c local log); borrow over build (F-766); Claude-only (F-767).

| Gap | Agent Teams | Claude Ship agents (cloud) | `/ship` / `/steward` (local) | `/code-review` alone |
|---|---|---|---|---|
| G1 one queue | N: session-scoped list, one team per session, lead-model ranking, lost on session end [V] | N | N | N |
| G2 project Workers | N | N | N | N |
| G3 images | N | N | N | N |
| G4 isolation | N: teammates share the lead's checkout and mode [V] | P only in the vendor's cloud [I] | N | N |
| G5 network policy | N | N | N (a watch refuses web tools, but that is not a network boundary [V]) | N |
| G6 context | N: teammates load `CLAUDE.md` and skills like any session [V]; nothing new over M1 | N | P: `.claude/skills/steward/SKILL.md` as per-repository instructions [V] — same mechanism as skills already in M1 | N |
| G7 quota accounting | N (raises usage ~7x) | N (separate usage credits) | N | N |
| G8 daily jobs | N | N | N | N |
| G9 retries, failures | N | P: Steward re-runs failed checks on PRs [V] — PRs are not our flow | P: same, as the owner, in a session | N |
| G10 host | N | N | N | N |

**Not a gap but adjacent: the end check / owner review of a Worker's branch.** `claude -p "/code-review main...<branch>"` is a vendor-built, any-plan, non-interactive reviewer that reads only the local repository [V for plan, `-p`, local targets; I that it needs no network beyond the model]. It could run in the Dispatcher's end check, or in the owner's review before a merge, inside a no-network Worker. This is the one piece worth enlisting; it fills none of G1-G10.

**Per candidate, against the constraints:**

- **Agent Teams.** Fails as a Worker mechanism: Workers run `claude -p`, where teammates are not spawned [V]. As a queue it would be a second, non-deterministic task state (constitution "The Dispatcher"), the failure class the earlier report rejects for M3 and M5. Experimental [V]. In the owner's interactive session it could replace the ad-hoc fan-out of background agents, but that is not a Task Queue gap, and the hooks `TaskCreated`/`TaskCompleted` already give the same veto points our Stop hook gives [I]. Seam if tried: none with the Dispatcher; a separate interactive tool. Effort: 0 hours to try (one environment variable); nothing it replaces. **Pattern only**: file-locked self-claim and per-agent mailbox files are small ideas for a local queue, nothing to adopt.
- **Claude Ship agents (cloud).** Fails three constraints at once: Team or Enterprise plan only [V] (the owner's access is a consumer subscription, F-755 to F-757), it pushes commits to GitHub pull requests [V] (3e, 4p), and it runs in the vendor's cloud with separate billing (5c, the hard cap). Not enlisted.
- **`/ship` and `/steward`.** Fail 3e by design: they commit, push and open the PR as the user [V]; with `Bash(git push *)` denied they do nothing useful [V]. Interactive only. Could be used by the owner himself on his own branches after he merges a Worker's bundle, which is outside the Task Queue. Not enlisted for Workers.
- **`/code-review` (from the Ship family).** Fits all constraints: vendor tool, subscription, local, no push, Claude-only, no new state. Seam: the Dispatcher (or the owner) calls it on the Worker's branch after the good-bye note; findings go into the log or the note, the Dispatcher still owns task state. Effort: 2-4 hours to wire one call into the end check and log its text [I]. Replaces nothing we have; it adds a second reader of the diff. Status: a bundled command, documented without a beta label [V]; Claude Code version churn is the risk.

Trust rules: both are vendor features, so the repository floor does not apply (F-777, F-779). Judged on status: Agent Teams experimental [V]; Claude Ship PR Steward and Approvals public beta, Code Review GA [V]; `/ship` one day old [V].

## 4. Recommendation

- **Agent Teams: not enlisted.** It cannot run in `-p` Workers and its task list is a second, model-driven, session-bound state. Revisit only if the vendor makes it work headless and stable.
- **Claude Ship agents and `/ship`: not enlisted.** Team/Enterprise only, cloud, and they push; all three break owner rules.
- **Enlist `/code-review` alone, later and optionally**, as a reader in the end check or the owner's review. First step (a day or less): on one finished Worker branch, run `claude -p "/code-review main...<branch>"` inside the existing no-network Worker container and compare its findings with the owner's own review.
- **M1 is unchanged.** Neither candidate covers any of G1-G10.

**The three facts least sure of:**
1. That `/code-review` on a local target needs no network beyond the model and works through the proxy in a no-network Worker [I; not run].
2. That `/ship` and `/steward` work on a Pro or Max subscription: the `/ship` page names no plan, only the org agents are stated as Team and Enterprise [V for the org agents; I for the commands].
3. That Agent Teams has no plan restriction: its page states none [V], but nothing positively confirms availability on every plan [I].
