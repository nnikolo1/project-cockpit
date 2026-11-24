# Contract: transcript-usage.jsonl

Exact token counts of the owner's interactive Claude Code sessions, one line per message, numbers only (no message text, no file contents).

Producer: `tools/transcript_usage.py` (arguments: `--source LABEL=DIR` one or more, `--out FILE`). Consumer: the Cockpit's Token Usage page.
Location: `<data folder>/<project>/transcript-usage.jsonl` (generated, never committed, mode 0600), updated atomically on each run: records of earlier runs are kept (the vendor tool deletes old transcripts, the history must outlive them) and a record seen again is refreshed, so a run is repeatable and never doubles a message.

One JSON object per line, oldest first:

| key | type | meaning |
|---|---|---|
| `ts` | string | the message's time, ISO 8601 UTC with seconds |
| `source` | string | the label of the `--source` the transcript came from (a project or worktree group) |
| `session` | string | the session id (the transcript's file name without extension); a sub-agent's messages carry their parent session's id |
| `agent` | string or null | the sub-agent transcript's name (without extension), null for the session's own messages |
| `message` | string | the message id as recorded; a message seen twice in a transcript is counted once |
| `model` | string or null | the model id |
| `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` | integer | the four counts as recorded by the vendor |

A key whose count is absent in a message is 0. A transcript line that cannot be read is skipped and counted in the producer's log.
