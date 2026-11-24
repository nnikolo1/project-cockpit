# Metadata gaps report, version 1

Modules: internal/metadata

`tools/metadata_gaps.py --project NAME [--json]` reads what the Cockpit reads for a project and reports gaps; it changes nothing. The daily job reads the JSON.

Exit code: 0 no gap, 1 at least one gap, 2 unusable project or file argument.

```
{"schema": 1, "project": NAME, "generated_at": "ISO 8601 UTC", "max_age_hours": 24, "count": n,
 "gaps": [{"project": NAME, "module": "module-id" | null, "kind": KIND, "what": "text", "age_h": hours | null}]}
```

`age_h` is the age of the data the gap is about (null when there is none). `kind` is one of: `card_data`, `stale`, `snapshot`, `no_data`, `token_usage`, `policies`, `footprint`, `ambiguity`, `churn`, `module_page`, `documents`, `map`, `quality_bands`, `slow`, `transcript_retention` (the coding tool's transcript retention is unset or shorter than `--min-retention-days`, default 3650; `what` names the settings file; only for a project whose transcript usage file exists), `backup` (the daily backup's status record `backup-status.json` in the project's data folder is missing or unreadable, its last run failed, or its last successful backup is older than `--max-age-hours`; `age_h` is the age of the failed run or of the last good backup). `what` is for people; do not parse it.
