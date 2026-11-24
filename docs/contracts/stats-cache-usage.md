# Contract: stats-cache-usage.jsonl

A coarse backfill of the token history: the daily aggregates of the vendor coding tool's statistics cache (`stats-cache.json`) for the period before the per-message transcripts (contract [transcript-usage.jsonl](transcript-usage.md)) exist. Numbers only; nothing else of the cache is kept.

Producer: `tools/stats_cache_usage.py` (arguments: `--cache FILE`, `--out FILE`, `--exact FILE` zero or more). Consumer: the Cockpit's Token Usage page.
Location: `<data folder>/<project>/stats-cache-usage.jsonl` (generated, never committed, mode 0600). Each run rewrites the whole file atomically from the cache, so a run is repeatable; a missing or malformed cache leaves the file untouched.

One JSON object per line, oldest day first, one line per day and model:

| key | type | meaning |
|---|---|---|
| `day` | string | the day as `YYYY-MM-DD`, as the cache records it (the tool's local day) |
| `model` | string or null | the model id; null on the single line of a day with activity but no token data |
| `tokens` | integer or null | the day's token total of the model as recorded, without an input/output/cache split; null when the cache has none |
| `messages`, `sessions` | integer or null | the day's message and session counts; the cache does not split them per model, so every line of a day carries the same values (count them once per day) |
| `source` | string | always `stats-cache`: a daily aggregate, never exact per-message data |

A day that has at least one record in an `--exact` transcript-usage file (by its UTC day) is left out: the exact data wins. The page applies the same rule against the transcript file it reads, counts a day's tokens in the period's sum only (marked "daily aggregate, no split") and leaves them out of the per-source and per-session tables.
