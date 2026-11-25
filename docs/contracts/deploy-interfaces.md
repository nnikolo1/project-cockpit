# Contract: what the three parts of the deploy command share (PC-84, phase 1)

Three agents build in parallel in separate branches: the store (A), the core (B) and the adapters (C). They meet only at the signatures below; each implements its side and does not change the others'. `home` is the Cockpit's home (`ckp.store.layout.home()` when omitted). A project is its short name; a tag is `vYYYY.MM.DD-N`.

## Store (A): `ckp/store/releases.py`, `ckp/store/env.py`, `ckp/store/lock.py`

- `releases.release_path(project, tag, home=None) -> Path`: `<home>/releases/<project>/<tag>/`.
- `releases.list_releases(project, home=None) -> list[str]`: tags on disk, newest first (sorted by the tag's date and counter).
- `releases.current(project, home=None) -> str | None` and `releases.previous(project, home=None) -> str | None`: the tag the `current` link points to; the one it pointed to before the last switch (kept in `<home>/releases/<project>/previous`, a link).
- `releases.install_release(project, tag, tree, home=None) -> Path`: copies the unpacked tree into `release_path`, makes it read-only; refuses to overwrite an existing tag (`FileExistsError`).
- `releases.switch_current(project, tag, home=None) -> None`: atomic (a temporary link, then `os.replace`); remembers the old target as `previous`; the tag must exist.
- `releases.rollback(project, home=None) -> str`: switch to `previous` and return its tag; `LookupError` when there is none.
- `env.load(project, home=None) -> dict`: `{environment name: {"port": int, "data_dir": str, "label": str}}` from `<home>/env/<project>.json`; `{}` when absent. `env.save(project, envs, home=None)` (atomic, mode 0600).
- `env.validate(all_envs) -> list[str]`: `all_envs` is `{project: {environment: record}}`; returns the problems as sentences: two environments sharing a port, a data folder or a label, a port outside 1024 to 65535, a data folder that contains another's; empty list when valid.
- `lock.shared(path, wait_s=0)` and `lock.exclusive(path, wait_s=30)`: context managers on a lock file (`fcntl.flock`); `lock.Busy` is raised when the lock cannot be taken within `wait_s`. The default lock file is `layout.deploy_lock(home)` (`<home>/deploy.lock`; A adds it to `ckp/store/layout.py`).

## Core (B): `ckp/core/deploy.py`, pure, with the adapters injected

- `WORKER_FACING`: the path prefixes whose change touches running Workers: `dispatcher/proxy.py`, `dispatcher/relay.py`, `dispatcher/runtime.py`, `dispatcher/image/`, `dispatcher/agent.py`.
- `touches_workers(changed_paths) -> list[str]`: the changed paths that match `WORKER_FACING`.
- `readiness(facts) -> list[dict]`: each `{"name", "ok", "detail"}`; `facts` is a dict with the keys `clean` (working clone clean and pushed), `tests_green`, `ahead_of_live`, `deploy_running`, `ci_ok` (may be None: unknown), `problems_open` (open heartbeat or backup problems, may be None), `rollback_target` (the previous release exists); every false condition has a sentence in `detail`.
- `plan(changed_paths, running_workers) -> dict`: `{"mode": "hot" | "held", "reasons": [...]}`: hot when `touches_workers` is empty; held otherwise, in which case the deploy proceeds only when `running_workers` is empty (the reason names the running tasks).
- `deploy(project, tag, env, ad, dry_run=True) -> dict`: the Result of the contract of PC-139 (`{ok, data, error, dry_run}`); `ad` is an object with the adapter functions below; the steps: readiness (stop with the list when not ok), plan (a held plan with running Workers stops with the reason), then, unless `dry_run`: take the exclusive deploy lock, `ad.fetch(tag) -> Path` (the unpacked tree), `ad.selftest(tree) -> (ok, detail)`, install, switch, `ad.restart(project, env)`, `ad.health(env) -> (ok, detail)`; on any failure after the switch roll back (`releases.rollback`, `ad.restart` again) and report `rolled back`; on success `ad.record(project, tag, env, "success" | "failure", detail)`. Every step is logged and appended to the result's `data["steps"]`.

## Adapters (C): `ckp/adapters/git.py`, `ckp/adapters/github.py`, `ckp/adapters/process.py`

- `git.next_tag(repo_dir, today) -> str`: the next `vYYYY.MM.DD-N` for the date (N counts the tags of that date); `git.tag(repo_dir, tag, sha) -> None`; `git.archive(repo_dir, tag, dest) -> Path` (`git archive` of the tag unpacked into `dest`); `git.changed_paths(repo_dir, old_tag, new_tag) -> list[str]` (`git diff --name-only`; `old_tag` None: all paths).
- `github.create_release(repo, tag, notes, asset=None) -> str` (the release URL; `gh release create`, the asset a tarball with its checksum), `github.record_deployment(repo, tag, environment, state, description) -> None` (`gh api` deployments and statuses), `github.latest_deployment(repo, environment) -> dict | None`.
- `process.health(port, path="/", timeout=5) -> (ok, detail)` (an HTTP GET on `127.0.0.1`), `process.restart_service(label) -> (ok, detail)` (`launchctl bootout` then `bootstrap` of the user's job, with the pause the Cockpit needs), `process.running_workers(state_file) -> list[str]` (the task ids in the Dispatcher's state file).
- Every external call goes through one injected function (`run(args) -> str`) so that the tests use fakes; none of the adapters is called by the store or by the core directly.

## Not in phase 1

The deploy command line (`cockpit deploy`), the Environments view, the heartbeat pings and the renderer of the service definitions for the release folders: the integrator (the interactive session) wires the parts together after the three branches are merged.

## Phase 2: the environments (PC-84)

- `facts["installed"]` (adapter): the release folder of the tag exists. `readiness` then counts `clean` and `tests_green` as met and `deploy` skips fetch, selftest and install: a rollback is a deploy of an installed tag. `ckp deploy --rollback` takes `releases.previous(project)` as the tag.
- `layout.deploy_log(home=None) -> Path`: `<home>/deploy-log.jsonl`; `ckp deploy --apply` appends one line per deploy, refusal or rollback: `{time, project, env, tag, rollback, ok, error}`.
- The adapter's default test gate is `tools/test_env.py --repo-dir CLONE`: the merge gate in a throwaway clone of HEAD with a temporary `CP_HOME` and `CP_ENV=test`, then a release rehearsal.
- `dispatcher.launchd.label(kind, project, env="production")`: `com.project-cockpit.<kind>.<project>`, or `com.project-cockpit.<env>.<kind>.<project>` for another environment; `dispatcher_plist` and `quota_plist` take `env` (another environment also gets `--container-name cockpit-workers-<env>`; every job gets `CP_ENV`). `render --env NAME --cockpit-dir DIR` refuses production from a git working clone and keeps DIR as given (a `current` link is not resolved).
