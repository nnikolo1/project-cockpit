#!/usr/bin/env python3
"""Revert protected-label changes made by anyone but the Manager.
Env: MANAGER (login), GITHUB_REPOSITORY, GH_TOKEN. Flag: --dry-run."""
import json, os, subprocess, sys

PROTECTED = {"released", "P1", "P2", "P3", "cancelled"}


def decide(events, manager, protected=PROTECTED):
    """events: GitHub issue events. Returns [(issue, label, op, actor)], op in add|remove.
    Desired state of (issue,label) = fold of the Manager's events only; actual = fold of all.
    The latest non-Manager actor who made actual differ from desired is reported."""
    evs = sorted((e for e in events if e.get("event") in ("labeled", "unlabeled")
                  and e["label"]["name"] in protected),
                 key=lambda e: (e["created_at"], e["id"]))
    state = {}  # (issue,label) -> [desired, actual, offender]
    for e in evs:
        k = (e["issue"]["number"], e["label"]["name"])
        s = state.setdefault(k, [False, False, None])
        on = e["event"] == "labeled"
        s[1] = on
        if e["actor"]["login"] == manager:
            s[0] = on
        s[2] = e["actor"]["login"]
    return [(i, l, "remove" if a else "add", who)
            for (i, l), (d, a, who) in sorted(state.items()) if d != a]


def _gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def _pages(text):  # --paginate prints concatenated JSON arrays
    dec, i, out = json.JSONDecoder(), 0, []
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        v, i = dec.raw_decode(text, i)
        out += v
    return out


def main():
    manager, repo = os.environ["MANAGER"], os.environ["GITHUB_REPOSITORY"]
    dry = "--dry-run" in sys.argv
    events = _pages(_gh("api", "--paginate", f"repos/{repo}/issues/events?per_page=100"))
    for issue, label, op, who in decide(events, manager):
        live = set(json.loads(_gh("api", f"repos/{repo}/issues/{issue}", "--jq", "[.labels[].name]")))
        if (label in live) == (op == "add"):  # already correct (stale history)
            continue
        msg = (f"unauthorised: {label} set by {who}, reverted" if op == "remove"
               else f"unauthorised: {label} removed by {who}, restored")
        print(f"#{issue} {op} {label} ({msg})")
        if dry:
            continue
        if op == "remove":
            _gh("api", "-X", "DELETE", f"repos/{repo}/issues/{issue}/labels/{label}")
        else:
            _gh("api", "-X", "POST", f"repos/{repo}/issues/{issue}/labels", "-f", f"labels[]={label}")
        _gh("api", "-X", "POST", f"repos/{repo}/issues/{issue}/comments", "-f", f"body={msg}")


if __name__ == "__main__":
    main()
