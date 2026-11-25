#!/usr/bin/env python3
"""Pre-push hook: run the merge gate on the working tree and block the push when it is red (PC-129; tests run locally by default).

    python3 tools/pre_push.py --install [--repo DIR]   install the hook into the clone DIR (default: the current one)
    (run by git as .git/hooks/pre-push)                run `tools/merge_gate`; exit 1 (push refused) unless it passes

The installed hook is a two-line shell script that calls this file, so a change here needs no reinstall. Installing is
idempotent and reports what it did: "installed", "already installed", or "refused" when another pre-push hook is there
(it is never overwritten). Nothing installs the hook on its own: only this command, run by the owner or by tools/install.py.
`MERGE_GATE` (a command line) replaces the gate, for tests. Exit: 0 pushed or installed, 1 refused.
"""
import argparse
import logging
import os
import pathlib
import shlex
import subprocess
import sys

log = logging.getLogger("pre_push")
HERE = pathlib.Path(__file__).resolve()
MARK = "# project-cockpit pre-push hook (tools/pre_push.py)"


def hook_text():
    return f"#!/bin/sh\n{MARK}\nexec python3 {shlex.quote(str(HERE))} \"$@\"\n"


def hook_path(repo):
    """Where git looks for the pre-push hook of the clone `repo` (core.hooksPath and worktrees included)."""
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-path", "hooks/pre-push"],
                       capture_output=True, text=True)
    if r.returncode:
        raise ValueError(f"{repo} is not a git clone: {r.stderr.strip()}")
    return pathlib.Path(r.stdout.strip())


def install(repo):
    """-> (done, message): install the hook into `repo` unless it is there; never overwrite another hook."""
    path = hook_path(repo)
    current = path.read_text() if path.is_file() else None
    if current == hook_text():
        return True, f"already installed: {path}"
    if current is not None:
        return False, f"refused: another pre-push hook is at {path}; remove it or merge it by hand, then run again"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(hook_text())
    path.chmod(0o755)
    return True, f"installed: {path} (runs the merge gate before every push)"


def run_gate():
    """-> the gate's exit code; git runs the hook at the top of the working tree, so the gate tests that tree."""
    cmd = shlex.split(os.environ["MERGE_GATE"]) if os.environ.get("MERGE_GATE") else [sys.executable, str(HERE.parent / "merge_gate")]
    return subprocess.run(cmd, stdin=subprocess.DEVNULL).returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true", help="install the hook into the clone (idempotent)")
    ap.add_argument("--repo", default=".", help="the clone to install into (default: the current directory)")
    ap.add_argument("rest", nargs="*", help=argparse.SUPPRESS)  # git passes the remote's name and URL
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    if a.install:
        try:
            ok, msg = install(pathlib.Path(a.repo))
        except ValueError as e:
            ok, msg = False, str(e)
        log.info("pre-push hook: %s", msg)
        return 0 if ok else 1
    if run_gate() == 0:
        return 0
    log.error("pre-push: the merge gate is red, push refused (fix the tests; `git push --no-verify` skips the hook)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
