# 0016. Boot-filling job: authority-need judgement

Status: proposed (builder); the owner confirms or changes
Date: 2026-11-20
Modules: kit

## Context
Background workers may end in a dead end that needs a resolution from the owner or the personal assistant. That stays an accepted limitation, but the job, an orchestrator that may release tasks, should avoid releasing tasks likely to dead-end for lack of authority. It does not formulate tasks and does not resolve dead ends.

## Decisions
- Step: after eligibility (ADR 0015) and before ranking, so the cap and the cut-off count only tasks that may run. Tasks excluded earlier by label, number or title pattern are unchanged.
- Cheap signals, each with a weight (`authority_weights`): a label from `authority_labels` (3); a strong pattern in title or body: approval or decision needed, owner decides, schema or DDL change, credentials handling, making something public (3); an open linked question or decision issue (blocker, parent, or `#N` reference) (3); a path named in the text that matches `protected_paths` (3); a weak pattern (send, submit, delete, spending, credentials word, legal or policy word, deploy, decide, migrate) (1). The project's exclusion file may add `authority_labels`, `authority_patterns` and `protected_paths`.
- Verdict by score: at or above `authority_skip_score` (3) `needs_authority`; at or above `authority_maybe_score` (1) `maybe`; else `none`.
- `maybe` goes to a model judge (title, labels, the signals and an excerpt of the body capped at `authority_excerpt_chars`, secrets, e-mail addresses and home paths redacted). The judge answers `needs_authority` or `none` with a one-line reason. Verdicts are cached for `authority_cache_hours` per task while title, labels and excerpt are unchanged; at most `authority_judge_max` calls per poll.
- Conservative failure: a `maybe` always carries a cheap signal, so the cheap signals were never clean. A failed judgement, a missing judge or an exhausted per-poll budget therefore leaves the task unreleased, and says so in the text. (The rule "eligible when the cheap signals were clean" is met by the `none` verdict, which needs no judge.)
- `authority_mode`: `skip` (default) does not release; the task stays an embryo. `flag` releases it but marks it (trail comment, release record, flagged line in the summary).
- Fail clearly (owner, 2026-11-20): every task that is not released for this reason is presented with the fixed status word `NEEDS YOU`, one sentence on why it was not released, one sentence on what exactly the owner must decide or do, and the signals. `core.needs_you_text` builds that text once; the decision-log record (`type: authority_skip`, fields status, verdict, signals, reason, why, todo, failure, text), the poll log (one warning line with the same text when the record is written, plus a count with numbers in the poll line), the budget contract line and the daily summary all embed it verbatim. A failed judge is named in it. No "skipped" or "unfinished" wording stands for a decision that is needed.
- A record is written once per change of text, not once per poll; the poll line counts every poll. The summary lists the tasks found at the last evaluation (state `authority_open`), so a skipped task is never hidden from the owner and can be released by hand.
- Budget contract: new `kind` `authority_skip`, documented in docs/contracts/budget-panel.md. The cockpit's `decision_view` ignores it without counting it unreadable; other unknown kinds are still counted.

## Limits
- Pattern and keyword signals are heuristics; the weak list over-triggers on purpose and costs judge calls, not releases. A task can still dead-end for a reason no signal sees.
- The summary list is only as fresh as the last poll that evaluated tasks (a poll where no trigger fires does not evaluate).
