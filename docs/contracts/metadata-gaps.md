# Metadata gaps report, version 1

Modules: internal/metadata

`tools/metadata_gaps.py --project NAME [--json]` reads what the Cockpit reads for a project and reports gaps; it changes nothing. The daily job reads the JSON. It runs `tools/code_snapshot_refresh.py --project NAME` first, so that a stale code snapshot is refreshed, not only reported ([card-metadata contract](card-metadata.md), "Code snapshot"); the `snapshot` gaps then cover a snapshot that is not configured, has no collector command, failed its last refresh or is unreadable, and a `stale` gap one older than 24 hours.

Exit code: 0 no gap, 1 at least one gap, 2 unusable project or file argument.

```
{"schema": 1, "project": NAME, "generated_at": "ISO 8601 UTC", "max_age_hours": 24, "count": n,
 "gaps": [{"project": NAME, "module": "module-id" | null, "kind": KIND, "what": "text", "age_h": hours | null}]}
```

`age_h` is the age of the data the gap is about (null when there is none). `kind` is one of: `card_data`, `stale`, `snapshot`, `no_data`, `token_usage`, `policies`, `footprint`, `ambiguity`, `churn`, `module_page`, `documents`, `map`, `quality_bands`, `slow`, `transcript_retention` (the coding tool's transcript retention is unset or shorter than `--min-retention-days`, default 3650; `what` names the settings file; only for a project whose transcript usage file exists). `what` is for people; do not parse it.
