# Contract: the compliance check of local policy files (PC-108)

`tools/compliance_check.py` checks whether a project's local policy files comply with the Cockpit's constitution (owner, 2026-11-25). It only reads the project; it writes a report in the Cockpit's data folder and, with `--file-findings`, policy-finding tasks. A fix is the owner's own act (constitution, "Local policy files: human in the loop").

## Inputs

The project's manifest `.cockpit/policies.json` (written by the project sync of PC-92; until then by hand):

```
{"instruction_file": "CLAUDE.md",          the instruction file (default CLAUDE.md)
 "policy_files": ["docs/policy.md", ...],  the local policy files (markdown)
 "managed_sha256": "<hex>",                sha256 of the managed block's text between the two markers
 "cockpit_release": "<version>",           the constitution version the block was synced from
 "required": ["rule-id", ...]}             optional: rules to restate beyond the Cockpit's own list
```

The Cockpit's side: `docs/constitution.md` and `docs/owner-defaults.md`. A rule id is the slug of a `##` heading up to its first `(` or `:` (`## Daily reports (owner, 2026-11-25)` is `daily-reports`). The version is the first 12 hex digits of the sha256 of both files (`--release` overrides it). The rules every project restates or references: `daily-reports`, `local-policy-files` (`REQUIRED` in the tool), plus the manifest's `required`.

## Markers in the project's files

- Managed block: `<!-- cockpit:managed:begin -->` ... `<!-- cockpit:managed:end -->` in the instruction file.
- Rule reference: `cockpit:RULE-ID`, anywhere in the instruction file or a policy file.
- Override: `Overrides: cockpit:RULE-ID (owner, YYYY-MM-DD)` on the line of the local rule. Any other line outside the managed block that uses the word "override" is a violation.
- Policy file header: a `Date: YYYY-MM-DD` line and a `Source: ...` line.

## Layer 1, structural (always)

Violations, one line each in the report: instruction file missing; managed block missing; managed block edited (hash differs); managed block outdated (`cockpit_release` differs from the version checked against); policy file missing; policy file undated; policy file without a source line; required rule without its marker; override without rule and owner's date; override naming an unknown rule.

## Layer 2, semantic (`--semantic`, off by default)

A model function `model(prompt, max_tokens) -> (answer, usage)` is injected by the caller (a Worker or the daily job); this build wires none. Per policy file it returns candidates `{local, constitution, reason}` (two quotations and a reason) for the owner, never a verdict. Calls stop when `--token-cap` (default 20000) is spent. Each call appends `{ts, worker: "compliance-check", input_tokens, output_tokens, ...}` to `<data dir>/<project>/proxy-usage.jsonl`, so it counts in the token statistics.

## Outputs

- `<data dir>/<project>/compliance-report.md`: `Checked YYYY-MM-DD against constitution VERSION.`, then `Compliant: no structural violation.` or `N violation(s):` and one line per violation, then the semantic candidates when the layer ran. The Cockpit page shows its summary (`/api/queue` field `compliance`).
- With `--file-findings`: one `policy-finding` task per violation through `tools/file_finding.py`, titled `Compliance: PROJECT: FILE: WHAT`, skipped when an open policy finding has that title. Semantic candidates are filed only once the owner confirms them.
- Exit code: 0 compliant, 1 violations, 2 unusable input (no projects file entry, no clone, no manifest).
