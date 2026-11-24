# Card metadata contract, version 1

The cockpit shows three extra blocks on every module and sub-module card: **change (churn)**, **persistence footprint** and **file ambiguity**. It computes none of them: a producer on the project side writes three JSON files, the cockpit reads them (read-only, [ADR 0013](../adr/0013-card-metadata-from-generated-files.md)). Examples use the module ids `module-a` and `module-a/one`.

## Where
Per project one folder: the key `meta` of the project in the `--projects` file, else `<data dir>/<project name>/cardmeta/` (the data dir is `~/.local/share/project-cockpit`, next to the project's token folder, see [deployment](../deployment.md)). The folder holds `footprint.json`, `churn.json`, `ambiguity.json`. Write each file atomically (write a temporary name, then rename).

## Rules for all three
- Header: `{"schema": 1, "kind": "<footprint|churn|ambiguity>", "project": "<project name as in the --projects file>", "generated_at": "<ISO 8601 UTC, e.g. 2026-11-20T06:00:00Z>"}`.
- A file with another `schema`, a wrong `kind` or another `project` is ignored and the card says "no data" with the reason. The same for a missing, empty or unparseable file. The cockpit never fails because of these files.
- Every card block shows "as of `generated_at`" and its age. Older than 48 hours: a stale warning.
- Module ids are the slash ids of the modules file (`module-a`, `module-a/one`). A module without an entry shows "no data for this module".
- No file contents, secrets or personal data: counts, sizes, paths and names only.

## `churn.json`
```
{header, "windows": [30, 90],
 "modules": {"module-a": {
    "own":   {"30": WINDOW, "90": WINDOW},     # files mapped to the module itself, sub-modules excluded
    "total": {"30": WINDOW, "90": WINDOW},     # the module with its sub-modules; for a sub-module equal to own
    "size":  {"source_lines": n, "test_lines": n, "files": n},
    "series": {"weekly": {"own": [WEEK, ...], "total": [WEEK, ...]}}}},    # optional, additive
 "unassigned": {"30": WINDOW, ..., "series": {...}}, "excluded": {"30": WINDOW, ...}, "mapping_check": {...}}
WINDOW = {commits, files, added, removed, net, churn,
          "source": {added, removed, churn}, "tests": {...}, "docs": {...},
          active_days, prev_churn, trend_pct}
WEEK = {"w": "YYYY-MM-DD", commits, added, removed, tests, docs}
```
- `churn` = added + removed lines; `net` = added - removed; `prev_churn` is the churn of the window before; `trend_pct` = (churn - prev_churn) / prev_churn * 100, or `null` when `prev_churn` is 0 (shown as "new activity").
- `series` (optional; a reader must work without it, the schema stays 1): `weekly.own` and `weekly.total` (own files of the module / with its sub-modules; for the `unassigned` entry both are the same) each hold 52 elements, one per week, oldest first, the last being the week of `generated_at`. `w` is the Monday (UTC) of the week; weeks without change are zeros. `added` and `removed` count all lines; `tests` and `docs` are the churn (added + removed) of test files and documents in that week, so source churn = added + removed - tests - docs. Same git walk, mapping and exclusions as the windows.
- `excluded` = `{files, lines, by_reason}`; `by_reason` holds `generated` (generated files such as lock files) and `deleted files` (each `{files, lines}`). History of a file that is not in the current tree is not code of any module and not "unmapped": it goes to `excluded` under `deleted files`, in no module bucket and not in `unassigned`, in the windows and in `series` alike. A file renamed or moved is followed to its new path (the commit that renames it); earlier history under the old path counts as deleted. `mapping_check`: `raw_lines` = `bucket_lines` + `excluded_lines` (generated + deleted files).
- Keys of the window objects are the window lengths as strings.
- The card shows `total` (falling back to `own`). The cockpit uses `size` for the relative view and shows `unassigned` on the "Not mapped" card; `excluded` and `mapping_check` are carried through, not shown yet.

### Colour coding (computed by the cockpit)
- Rank by `churn` among the modules of the same level (top-level modules together, sub-modules together), per window. Top fifth `hot`, bottom fifth `cold`, the rest `mid`; churn 0 is `dormant` and not ranked. Ties stay together. With fewer than 3 ranked modules, or all equal, everything stays `mid`.
- Option "colour by size": the same ranking on `churn / (source_lines + test_lines)`; without `size` the module shows no relative value.
- Each band has a symbol and a word beside the colour: `▲ hot`, `● mid`, `▽ cold`, `– dormant`. Colours reuse the priority palette (red, amber, blue, dashed grey).

## `ambiguity.json`
```
{header,
 "modules": {"module-a": {"own_files": n, "sub_files": {"one": n, "two": n},
                          "unsorted": [{"path": "...", "candidates": ["module-a/one", ...], "reason": "..."}]}},
 "module_level": [{"path": "...", "candidates": ["module-a", "module-b"], "reason": "..."}],
 "unassigned_nondoc": n}
```
- `own_files`: files that belong to the module itself; `sub_files`: files per sub-module (key as you like, shown as written); `unsorted`: files of the module that cannot be put into one sub-module. **Unsorted files are counted in neither `own_files` nor `sub_files`**: the module's file total is `own_files + sum(sub_files) + len(unsorted)`.
- Marker: unsorted share above 30 percent of that total (constant `UNSORTED_WARN` in `logic.py`).
- `module_level` (project overview): files claimed by several modules. `unassigned_nondoc`: code files that no module claims.

## `footprint.json`
```
{header, "modules": {"module-a": {
   "tables":  [{name, rows, owner, role, bytes}],
   "files":   [{path, kind, size_bytes | count, personal_data: bool, written_by}],
   "logging": [{logger, file, size_bytes, rotation}],
   "volume":  {"day": {rows, bytes}, "week": {rows, bytes}, "month": {rows, bytes}},
   "policy":  {"backup": "text", "retention": "text"}}}}
```
- `files` are files and folders (`count` for a folder: the number of files in it). Inside or outside the repository is decided by the cockpit from `path`: absolute, `~` and `..` paths are outside.
- `personal_data: true` shows a marker beside the entry.
- `policy`: free text. An empty, missing or "undocumented" value is shown as a **gap**, not as a blank.

## Versioning
A breaking change raises `schema`; the cockpit then shows "no data (unsupported schema)" instead of guessing. Adding optional keys is not breaking. Synthetic examples: `tests/fixtures/cardmeta/`.
