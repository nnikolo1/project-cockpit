#!/usr/bin/env python3
"""Fail if a brief's Requirements section has a line without an owner tag.

Each line under "## Requirements" must be a bullet ending in [owner F-n] or
[approved F-n], and row F-n must exist in the given feedback log.

Usage: check_brief.py --log FEEDBACK_LOG.md BRIEF.md
"""
import argparse
import re
import sys

TAG = re.compile(r"\[(?:owner|approved) (F-\d+)\]\s*$")


def problems(brief, rows):
    out, in_section, found = [], False, False
    for n, line in enumerate(brief.splitlines(), 1):
        if line.startswith("## "):
            in_section = line.strip().lower() == "## requirements"
            found = found or in_section
            continue
        if not in_section or not line.strip():
            continue
        m = TAG.search(line)
        if not line.lstrip().startswith("- ") or not m:
            out.append(f"line {n}: no owner tag: {line.strip()[:70]}")
        elif m.group(1) not in rows:
            out.append(f"line {n}: {m.group(1)} is not in the feedback log")
    if not found:
        out.append("no '## Requirements' section")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("brief")
    ap.add_argument("--log", required=True)
    args = ap.parse_args()
    rows = set(re.findall(r"^\| (F-\d+) \|", open(args.log).read(), re.M))
    found = problems(open(args.brief).read(), rows)
    print("\n".join(found))
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
