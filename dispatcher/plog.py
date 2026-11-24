"""The processing log (spec docs/specs/dispatcher-and-workers.md: 5a-5e, 3g).

One JSON line per entry in `<data_dir>/processing.jsonl`. The data folder and the backup configuration must lie outside any git
repository (5d, 5e). The backup (5e) takes the configuration path and an injected `upload(config, path)`; it runs at most once a day.
"""
import datetime as dt
import json
import logging
import pathlib
import re

log = logging.getLogger("dispatcher.plog")

REASONS = ("no start", "over N minutes", "no activity", "no note", "unusable note", "no commit in the bundle")   # 5b; N is the runaway limit
LOG_NAME, STAMP_NAME = "processing.jsonl", "backup.stamp"


def _outside_git(path):
    p = pathlib.Path(path).resolve()
    for d in (p, *p.parents):
        if (d / ".git").exists():
            raise ValueError(f"{path} lies inside a git repository ({d}); the processing log and its backup settings must not")


class ProcessingLog:
    def __init__(self, data_dir, now=dt.datetime.now):
        _outside_git(data_dir)
        self.dir = pathlib.Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / LOG_NAME
        self.now = now

    def _write(self, **rec):
        rec = dict(time=self.now().isoformat(timespec="seconds"), **rec)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        log.info("processing log: %s", rec)

    def event(self, task_id, task_folder, state, reason=None):
        self._write(task=task_id, folder=str(task_folder), state=state, **({"reason": reason} if reason else {}))

    def failure(self, task_id, task_folder, reason):
        if reason not in REASONS and not re.fullmatch(r"over \d+ minutes", reason):
            raise ValueError(f"unknown failure reason {reason!r}; expected one of {REASONS}")
        self.event(task_id, task_folder, "failed", reason)

    def heartbeat(self, line):
        self._write(heartbeat=line)

    def backup_due(self):
        """True when no backup was made today."""
        try:
            return (self.dir / STAMP_NAME).read_text().strip() != self.now().date().isoformat()
        except FileNotFoundError:
            return True

    def backup(self, config_path, upload):
        """Once a day: read the access details from the configuration file (a JSON file outside git) and call upload(config, log_path).
        -> True if uploaded; False if not due or the upload failed (the failure is logged, the next firing retries)."""
        if not self.backup_due():
            return False
        _outside_git(config_path)
        config = json.loads(pathlib.Path(config_path).read_text())
        try:
            upload(config, self.path)
        except Exception as ex:
            log.error("processing log backup failed: %s: %s", type(ex).__name__, str(ex)[:200])
            return False
        (self.dir / STAMP_NAME).write_text(self.now().date().isoformat())
        log.info("processing log backed up")
        return True
