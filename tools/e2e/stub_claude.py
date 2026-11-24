#!/usr/bin/env python3
"""A stand-in for the `claude` program, for the failure-path test of the Dispatcher (tools/e2e_failure_paths.py).

It ignores its arguments. The case is the first line `MODE=<name>` of <area>/<task-id>/inbox/task.md (the line is looked for in the
file, because the Dispatcher writes a title line before the issue text). Environment: WORKER_TASK_ID, WORKER_OUTBOX (<area>/<task-id>/outbox),
CLAUDE_CONFIG_DIR; the working directory is the Worker's clone.
Modes: nostart noactivity overtime gaveup nocommit nonote badnote success.
"""
import json
import logging
import os
import re
import subprocess
import sys
import time

log = logging.getLogger("stub_claude")
MODES = ("nostart", "noactivity", "overtime", "gaveup", "nocommit", "nonote", "badnote", "success")


def read_mode(task_md):
    with open(task_md, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"MODE=(\w+)\s*$", line)
            if m:
                return m.group(1)
    raise ValueError(f"no MODE=<name> line in {task_md}")


def sleep_forever(tick=None):
    while True:
        if tick:
            tick()
        time.sleep(20 if tick else 3600)


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def commit_and_bundle(task_id, outbox, commit=True):
    git = ["git", "-c", "user.name=stub", "-c", "user.email=stub@example.invalid"]
    if commit:
        subprocess.run([*git, "checkout", "-b", f"worker/{task_id}"], check=True)
        write("stub-result.txt", f"work of the stub for {task_id}\n")
        subprocess.run([*git, "add", "stub-result.txt"], check=True)
        subprocess.run([*git, "commit", "-m", f"stub work for {task_id}"], check=True)
    subprocess.run(["git", "bundle", "create", os.path.join(outbox, "result.bundle"), f"worker/{task_id}"], check=True)


def note(outbox, outcome):
    write(os.path.join(outbox, "goodbye.json"), json.dumps({"outcome": outcome, "summary": f"stub, outcome {outcome}"}))


def run(mode, task_id, outbox, config_dir):
    started = os.path.join(outbox, "started")
    if mode == "nostart":
        return sleep_forever()
    write(started, "")
    if mode == "noactivity":
        return sleep_forever()
    if mode == "overtime":
        folder = os.path.join(config_dir, "projects", "stub")
        os.makedirs(folder, exist_ok=True)
        return sleep_forever(lambda: write(os.path.join(folder, "session.jsonl"), "{}\n"))
    if mode == "nocommit":
        return note(outbox, "done")
    if mode in ("gaveup", "nonote", "badnote", "success"):
        commit_and_bundle(task_id, outbox)
    if mode in ("gaveup", "success"):
        note(outbox, "gave_up" if mode == "gaveup" else "done")
    elif mode == "badnote":
        write(os.path.join(outbox, "goodbye.json"), "this is not JSON {")
    elif mode not in ("nonote",):
        raise ValueError(f"unknown mode {mode!r}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    task_id, outbox = os.environ["WORKER_TASK_ID"], os.environ["WORKER_OUTBOX"]
    mode = read_mode(os.path.join(os.path.dirname(outbox), "inbox", "task.md"))
    log.info("task %s, mode %s", task_id, mode)
    run(mode, task_id, outbox, os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
