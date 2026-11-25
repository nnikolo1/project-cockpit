---
name: goodbye-note
description: What the good-bye note contains and when a Worker writes it. Use at the start and at the end of every task.
---

# Good-bye note

## Start

Read `inbox/task.md`. Then read `inbox/synopsis.md` when it exists: a dated synopsis of the project's latest interactive session (decisions, standing rules, work in flight, open questions), for context only; a first line `STALE: ...` says it may be out of date. Never copy its text into commits, issues or notes. Work in your own clone. Write the file `outbox/started` first (its content is ignored).

## End

1. Commit your work on the branch `worker/<task-id>`.
2. Write a git bundle of that branch to `outbox/result.bundle`.
3. Write the note `outbox/goodbye.json`, a JSON object:

| Field | Meaning |
|---|---|
| `outcome` (mandatory) | `"done"` or `"gave_up"`: your own assessment. `"gave_up"` fails the task. |
| `summary` (mandatory) | The work done, whatever the outcome. Up to 100 words and 400 characters. |
| `any_assumptions` | Assumptions not stated in the task brief, the skill and agent files or the codebase. |
| `any_limitations_or_caveats` | Limitations or caveats not stated there. |
| `any_open_questions` | Open questions. |
| `suggested_task` | Optional: one task you suggest, everything you want to say about it in this single field. A suggestion is a pre-embryo: the Dispatcher does not file it. |
| `tokens` | Leave it out: the end check writes it from the session transcript. |

The four optional text fields have the same size limit as `summary`.

If you cannot finish, you may still commit the partial work and write the outcome `"gave_up"`. A partial delivery is a failure, which the owner accepts.

## Stop hook

A Stop hook checks the note and the commit: the note is a JSON object, `outcome` and `summary` are present, the fields have the right type and size, and, unless the outcome is `"gave_up"`, the clone holds at least one commit beyond main and `outbox/result.bundle` exists. If something is missing, the hook refuses the stop once and tells you what; fix it. If the check fails again, you end and the task fails.

## Example

```json
{
  "outcome": "done",
  "summary": "Added the retry limit to the fetch loop and a test for it.",
  "any_assumptions": "A limit of three retries is enough.",
  "tokens": 48210
}
```
