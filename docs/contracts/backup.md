# Contract: a project backup the Cockpit can run offline

A backup holds everything the Cockpit's page shows for one project. The daily backup (PC-62) writes it, and `cockpit.py --offline BACKUP` serves it (PC-63). An offline copy only reads the restored data. It makes no GitHub call, needs no credential and has steering off. It is a read-only view of the past, not a second store of task state (ADR 0001).

## Layout

A tar archive (any compression `tarfile` reads) or a folder. `backup.json` sits at the top of the archive or inside its one top folder.

```
backup.json        {"created": <epoch seconds>, "project": {"name", "repo", "manager"?, "project"?,
                     "modules"?, "code"?, "phases"?, "git"?}}   paths relative to the backup's top folder
cache/             the Cockpit's cache files of the project: <name>_<what>.json from its --cache-dir
                   (state, timeline, commits, rules, rulesprs, cardmeta, biggest, moduledoc_<id>, ...)
data/<name>/       the project's data folder (<data dir>/<name>/): dispatcher-state.json, processing/processing.jsonl,
                   proxy-usage.jsonl, quota-history.jsonl, transcript-usage.jsonl, dispatcher-settings.json,
                   cockpit-settings.json, area/*/outbox/goodbye.json, cardmeta/{footprint,churn,ambiguity}.json
files/...          where the manifest's paths point: the module map (and its modules/ folder of module pages),
                   the code snapshot folder, the phases file and, optionally, a clone; files/projects.json: the projects file
```

The daily backup (`tools/project_backup.py`) writes `<project>-YYYY-MM-DD.tar.gz` with one top folder of that name. It packs, from the data folder, only `dispatcher-state.json`, `dispatcher-settings.json`, `cockpit-settings.json`, `projects.json`, the processing log, the usage files (proxy, quota, transcript, statistics cache), `backup-status.json`, `cardmeta/*.json`, `area/*/outbox/goodbye.json` and `transcripts/` (the never-pruned mirror of the coding tool's raw session transcripts: conversation text, the one deliberate exception to numbers only). It never packs a file whose name marks a secret (a token, credential, environment or key file: refused and listed as `refused` in the status record), a link, a log, a task bundle, or anything inside the project's clone (the code and a module map kept in the repository are in the repository; such a path is left out of the manifest).

The module cards' files are read from `data/<name>/cardmeta/`; a `meta` setting is not used offline. A path in the manifest that points outside the backup is refused. An archive member with an absolute path, `..`, a link or a device is also refused.

## Which pane reads what

`logic.OFFLINE_PANES` lists each pane, its request and the items it needs. Each need is a group of alternatives, and one present item in each group is enough:

| Pane | Request | Needs |
|---|---|---|
| tasks and states, ranking, open questions | `/api/state`, `/api/questions` | `cache:state` |
| timeline | `/api/timeline` | `cache:state` and one of `cache:timeline`, `cache:commits`, a clone |
| rules | `/api/rules` | `cache:rules`, a clone, or no rules configured |
| rules' open pull requests | `/api/rulesprs` | `cache:rulesprs` or no rules configured |
| task queue (local part) | `/api/queue` | the Dispatcher's state file or the processing log |
| token usage | `/api/usage` | one of the usage files or the processing log |
| settings, module card settings | `/api/settings`, `/api/cardsettings` | nothing (defaults when the file is missing, as online) |
| code | `/api/code` | the snapshot's `metrics.json` |
| module cards | `/api/cardmeta` | `cache:cardmeta` or one of the `cardmeta/*.json` files |

## Checks

- **Gap checker:** `python3 tools/backup_restore.py --gaps BACKUP` lists every pane whose data is not in the backup. An offline Cockpit also logs these gaps at startup, and its banner lists them.
- **Restore test:** `python3 tools/backup_restore.py BACKUP` unpacks the backup into a temporary folder and starts the Cockpit offline on it. A `gh` that only records being called comes first on PATH. The test then requests every pane's data, every module page and one action. It fails (exit 1) on any of these:
  - a gap;
  - a pane with no data;
  - an action that is not refused;
  - a GitHub call the Cockpit refused, or a run of `gh`.

  `tests/test_offline.py` runs it on a generated backup. `COCKPIT_BACKUP=<backup> pytest tests/test_offline.py` runs it on a real one.

## Offline behaviour

- Every cached copy is served as it is, whatever its age. Data that is not cached is computed from the restored local files when that is possible; otherwise the request fails with "offline copy: … not in the backup". Nothing is written into the backup.
- The page shows the banner "offline copy, data from <created>" on every tab. Settings are shown read-only, and every action is refused (403).
- `--offline` excludes `--projects`, the single-project flags, `--cache-dir` and `--simulate-orchestrator`. `--port`, `--lan` and `--token-file` keep working.
