# Boot-filling job: install (dry run first)

Decision: [ADR 0014](adr/0014-boot-filling-job.md); implementation choices: [ADR 0015](adr/0015-boot-filling-implementation-choices.md). Code: `bootfill/`.

1. Copy `bootfill/config.example.json` to a local file and edit projects, tracking issue and paths. `mode` stays `dry-run` (the default): the job only logs what it would release and never starts a worker.
2. Create the tracking issue (daily summary comment; its `pause` label is a kill switch) and an optional per-project exclusion file `{"labels": [], "numbers": [], "title_patterns": []}`.
3. Try one poll by hand: `python3 -m bootfill.job --config CONFIG --data-dir DIR --once`.
4. Copy `bootfill/com.project-cockpit.bootfill.plist.template`, replace the placeholders, put it in `~/Library/LaunchAgents/` and `launchctl load` it.
5. After a week of dry run read `decision-log.jsonl` and the Budget page; only then set `"mode": "real"`.

Stop at any time: `touch DIR/PAUSE`, or label the tracking issue `pause`, or `launchctl unload` the plist.
