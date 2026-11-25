#!/usr/bin/env python3
"""Red-main report: read the state of main and, when it is red, file a policy finding once.

The state is whichever result exists, the newer when both do (PC-94, PC-129): the last hosted run of the test workflow on main,
and, with --repo-dir, the last local merge gate run on main in that clone (the pre-push hook runs it; tools/merge_gate logs it).

The finding (labels policy-finding, author:job) counts as released at once; the job that files it does not work on it (standing
policy (2), 2026-11-24). It is filed only while no open issue has the same title, so a main that stays red is reported once.
Meant for the daily upkeep job (PC-91); until that job exists, the Task Queue page shows the same state.

Usage: ci_main.py --repo OWNER/NAME [--repo-dir CLONE] [--file-task] [--filed-by ID]
Output: the state as JSON on stdout. Exit: 0 not red, 2 red, 1 error.
"""
import argparse
import json
import logging
import pathlib
import subprocess
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import logic as L  # noqa: E402
import file_finding as ff  # noqa: E402

log = logging.getLogger("ci_main")


def local_gate(repo_dir):
    """The last plain merge gate run on main in the clone `repo_dir` (logic.gate_result), or None."""
    r = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "--git-common-dir"], capture_output=True, text=True)
    try:
        return None if r.returncode else L.gate_result((pathlib.Path(repo_dir) / r.stdout.strip() / L.GATE_LOG).read_text())
    except OSError:
        return None


def last_run(repo, run=None, repo_dir=None):
    """The newer of the hosted run and the local gate run; GitHub unavailable is an error only when no local result exists."""
    local = local_gate(repo_dir) if repo_dir else None
    try:
        hosted = hosted_run(repo, run)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        if local is None:
            raise
        log.warning("hosted run unavailable, using the local merge gate result: %s", e)
        hosted = None
    return L.ci_latest(hosted, local)


def hosted_run(repo, run=None):
    return L.ci_run((run or ff.gh_run)(["run", "list", "-R", repo, "--workflow", L.CI_WORKFLOW, "--branch", "main", "--limit", "1", "--json", L.CI_FIELDS]))


def file_red_main(repo, state, filed_by, run=None):
    """File the red-main finding unless an open issue has its title; -> the URL, or None when one is already open."""
    run = run or ff.gh_run
    found = json.loads(run(["issue", "list", "-R", repo, "--state", "open", "--search", f'"{L.RED_MAIN_TITLE}" in:title', "--json", "title,url"]) or "[]")
    if any(i["title"] == L.RED_MAIN_TITLE for i in found):
        log.info("red-main finding already open: %s", found[0]["url"])
        return None
    a = types.SimpleNamespace(repo=repo, title=L.RED_MAIN_TITLE, body=L.red_main_body(state), policy=L.RED_MAIN_POLICY,
                              file=[f".github/workflows/{L.CI_WORKFLOW}"], filed_by=filed_by, module=None, author="job", dry_run=False)
    return ff.file_finding(a, run=run)[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--repo-dir", help="the local clone whose merge gate log is read too")
    ap.add_argument("--file-task", action="store_true", help="file the red-main finding when main is red")
    ap.add_argument("--filed-by", default="daily-upkeep-job")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    try:
        state = last_run(a.repo, repo_dir=a.repo_dir)
        if state["state"] == "red" and a.file_task:
            state["task"] = file_red_main(a.repo, state, a.filed_by)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log.error("could not read the CI state or file the finding: %s", e)
        return 1
    print(json.dumps(state, indent=1))
    return 2 if state["state"] == "red" else 0


if __name__ == "__main__":
    sys.exit(main())
