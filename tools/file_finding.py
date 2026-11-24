#!/usr/bin/env python3
"""File a policy finding: a task for a file that contradicts a standing policy (spec docs/specs/dispatcher-and-workers.md, 2b).

The task counts as released at once; the agent that files it does not work on it. The body carries the contradiction, the files,
the marker `<!-- filed-by: AGENT-ID -->` and an Origin block. Labels: policy-finding, author:interactive and, when given, module:ID.

Usage: file_finding.py --repo OWNER/NAME --title T --body TEXT-or-@FILE --policy "the policy it contradicts"
                       --file PATH [--file PATH ...] --filed-by AGENT-ID [--module ID] [--dry-run]
"""
import argparse
import datetime as dt
import logging
import subprocess
import sys

log = logging.getLogger("file_finding")
TIMEOUT = 60
LABELS = ("policy-finding", "author:interactive")


def build_body(text, policy, files, filed_by, today):
    """The issue body: what contradicts which policy, the files, the marker and an Origin block (no personal data)."""
    listing = "\n".join(f"- `{f}`" for f in files)
    return (f"{text.strip()}\n\n**Policy it contradicts:** {policy.strip()}\n\n**Files:**\n{listing}\n\n"
            f"<!-- filed-by: {filed_by} -->\n\n"
            f"<details><summary>Origin</summary>\n\nFiled by the agent `{filed_by}` on {today}: a policy finding "
            f"(released at once; the finder does not work on it).\n\n</details>\n")


def labels_for(module=None):
    return [*LABELS, f"module:{module}"] if module else list(LABELS)


def gh_run(args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=TIMEOUT, check=True).stdout.strip()


def file_finding(a, run=gh_run, today=None):
    """Build and (unless dry-run) file the issue; -> (the issue URL or None, the gh arguments)."""
    text = open(a.body[1:], encoding="utf-8").read() if a.body.startswith("@") else a.body
    body = build_body(text, a.policy, a.file, a.filed_by, today or dt.date.today().isoformat())
    labels = labels_for(a.module)
    args = ["issue", "create", "-R", a.repo, "--title", a.title, "--body", body]
    for l in labels:
        args += ["--label", l]
    if a.dry_run:
        log.info("dry run: nothing filed in %s with labels %s", a.repo, labels)
        return None, args
    url = run(args)
    log.info("filed %s (labels %s, filed by %s)", url, labels, a.filed_by)
    return url, args


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--body", required=True, help="text, or @FILE to read it from a file")
    ap.add_argument("--policy", required=True)
    ap.add_argument("--file", action="append", required=True)
    ap.add_argument("--filed-by", required=True)
    ap.add_argument("--module")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        url, args = file_finding(a)
    except (OSError, subprocess.SubprocessError) as e:
        log.error("could not file the finding: %s", e)
        return 1
    if url is None:
        print("gh " + " ".join(repr(x) for x in args))
    else:
        print(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
