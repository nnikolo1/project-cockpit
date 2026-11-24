#!/usr/bin/env python3
"""Failure-path test of the Dispatcher against a throwaway repository, with the model replaced by tools/e2e/stub_claude.py.

Files one issue per case (title `case <mode>`, body `MODE=<mode>`), runs the Dispatcher repeatedly until every case issue carries
`unmerged` or `failed`, compares the final label and the failure reason in the processing log with the expectation, prints PASS or
FAIL per case, stops the container and the proxy. Exit status 1 if a case fails (or does not finish).

    python3 tools/e2e_failure_paths.py --repo owner/throwaway --cockpit ~/Documents/project-cockpit --base /tmp/e2e \\
        --image cockpit-worker:stub --token-file ~/path/to/token

--base is a working folder outside any git repository; keep its path short (the proxy's socket path must stay under 104 bytes).
"""
import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time

log = logging.getLogger("e2e_failure_paths")

# mode -> (final state label, failure reason; None when the task does not fail)
EXPECTED = {
    "nostart": ("failed", "no start"),
    "noactivity": ("failed", "no activity"),
    "overtime": ("failed", "over 2 minutes"),
    "gaveup": ("failed", "the Worker gave up"),
    "nocommit": ("failed", "no commit in the bundle"),
    "nonote": ("failed", "no note"),
    "badnote": ("failed", "unusable note"),
    "success": ("unmerged", None),
}
SETTINGS = {"enabled": True, "interval_minutes": 1, "retries": 0, "workers": 3, "runaway_minutes": 2, "idle_minutes": 1,
            "start_minutes": 1}
FIRING_GAP = 35          # seconds between firings
TOTAL_LIMIT = 15 * 60    # seconds, all firings together
FIRING_LIMIT = 300       # seconds one firing may take (the first one starts the container)
CALL_LIMIT = 60          # seconds a gh or git call may take
FINAL = ("unmerged", "failed")


def final_label(labels):
    return next((l for l in FINAL if l in labels), None)


def judge(mode, label, reason, branch_fetched):
    """The problems found for one case (an empty list means PASS)."""
    want_label, want_reason = EXPECTED[mode]
    problems = []
    if label != want_label:
        problems.append(f"label {label!r}, expected {want_label!r}")
    if want_reason is not None and reason != want_reason:
        problems.append(f"reason {reason!r}, expected {want_reason!r}")
    if mode == "gaveup" and not branch_fetched:
        problems.append("branch worker/<task-id> was not fetched")
    return problems


def failure_reasons(log_path):
    """task id -> reason of its last `failed` entry in the processing log."""
    out = {}
    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("state") == "failed" and "task" in rec:
                    out[rec["task"]] = rec.get("reason")
    except OSError:
        pass
    return out


def run(cmd, cwd=None, timeout=CALL_LIMIT):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed ({p.returncode}): {p.stderr.strip()[:300]}")
    return p.stdout


def file_issue(repo, mode):
    out = run(["gh", "issue", "create", "-R", repo, "--title", f"case {mode}", "--body", f"MODE={mode}"])
    number = int(re.search(r"/(\d+)\s*$", out.strip()).group(1))
    log.info("filed case %s as issue %d", mode, number)
    return number


def labels_of(repo, number):
    return {l["name"] for l in json.loads(run(["gh", "issue", "view", str(number), "-R", repo, "--json", "labels"]))["labels"]}


def branch_exists(repo_dir, task_id):
    p = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/worker/{task_id}"], cwd=repo_dir,
                       capture_output=True, timeout=CALL_LIMIT)
    return p.returncode == 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="the throwaway repository, owner/name")
    ap.add_argument("--cockpit", required=True, help="the project-cockpit checkout that holds the dispatcher package")
    ap.add_argument("--base", required=True, help="working folder (clone, area, settings, state, log, socket)")
    ap.add_argument("--image", default="cockpit-worker:stub", help="the stub image")
    ap.add_argument("--container-name", default="cockpit-workers-test", help="the test's own container: never the production one")
    ap.add_argument("--token-file", required=True, help="the proxy's credential file")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    base = os.path.abspath(a.base)
    paths = {k: os.path.join(base, k) for k in ("repo", "area", "settings.json", "state.json", "data", "usage.jsonl", "p.sock")}
    os.makedirs(paths["area"], exist_ok=True)
    short = a.repo.split("/")[1]
    try:
        if not os.path.isdir(paths["repo"]):
            run(["gh", "repo", "clone", a.repo, paths["repo"]], timeout=120)
        with open(paths["settings.json"], "w") as f:
            json.dump(SETTINGS, f)
        cases = {mode: f"{short}-{file_issue(a.repo, mode)}" for mode in EXPECTED}
        cmd = [sys.executable, "-m", "dispatcher.core", "--settings", paths["settings.json"], "--state", paths["state.json"],
               "--area", paths["area"], "--socket", paths["p.sock"], "--token-file", a.token_file, "--usage-file", paths["usage.jsonl"],
               "--repo", a.repo, "--repo-dir", paths["repo"], "--data-dir", paths["data"], "--image", a.image,
               "--container-name", a.container_name]
        labels = {}
        start = time.time()
        while time.time() - start < TOTAL_LIMIT:
            try:
                run(cmd, cwd=a.cockpit, timeout=FIRING_LIMIT)
            except (RuntimeError, subprocess.TimeoutExpired) as e:
                log.warning("a firing failed: %s", e)
            labels = {m: final_label(labels_of(a.repo, int(t.rsplit("-", 1)[1]))) for m, t in cases.items()}
            log.info("states: %s", labels)
            if all(labels.values()):
                break
            time.sleep(FIRING_GAP)
        else:
            log.error("time limit of %d s reached; cases not finished: %s", TOTAL_LIMIT, [m for m, l in labels.items() if not l])
        reasons = failure_reasons(os.path.join(paths["data"], "processing.jsonl"))
        failed = 0
        for mode, task_id in cases.items():
            problems = judge(mode, labels.get(mode), reasons.get(task_id), branch_exists(paths["repo"], task_id))
            failed += bool(problems)
            print(f"{'FAIL' if problems else 'PASS'} {mode} ({task_id})" + (": " + "; ".join(problems) if problems else ""))
        return 1 if failed else 0
    finally:
        for cmd in (["container", "stop", a.container_name], ["pkill", "-f", f"dispatcher.proxy --socket {paths['p.sock']}"]):
            try:
                subprocess.run(cmd, capture_output=True, timeout=CALL_LIMIT)
            except (OSError, subprocess.TimeoutExpired) as e:
                log.warning("%s failed: %s", cmd[0], e)


if __name__ == "__main__":
    sys.exit(main())
