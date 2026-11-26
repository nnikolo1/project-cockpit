#!/usr/bin/env python3
"""Policy harness: standing policies as checks; a violation is a failing test that files its remediation (constitution, "Policy is code").

    python3 tools/policy_harness.py [--project NAME | --all] [--projects-file FILE] [--data-dir DIR] [--json] [--file-tasks] [--filed-by ID]

Runs every check of CHECKS over one project (default: the default of the projects file) or over every project of the projects file, and
prints a report (text, or JSON with --json). Default is a dry run: nothing is filed. With --file-tasks, every violation files a policy-finding
task in the project's repository (labels policy-finding and author:job, released at once; the finder, --filed-by, never works it), unless an
open policy-finding task carries the same marker `<!-- policy-check: ID | PROJECT | SUBJECT -->`. Filing goes through file_task(), with the
`gh` login (the account of tools/file_finding.py). The harness only reads project data and files tasks: it releases nothing else, edits no file
(never the module map) and touches only the projects listed in the projects file. A violation that disappears closes nothing.
Exit code: 0 no violation, 1 at least one violation, 2 the projects file or the project cannot be used.

A new policy is one entry in CHECKS (and one test in tests/tools/test_policy_harness.py).
"""
import argparse
import dataclasses
import datetime as dt
import json
import logging
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cockpit as C  # noqa: E402
import logic as L  # noqa: E402
import metadata_gaps as G  # noqa: E402
import file_finding as ff  # noqa: E402

log = logging.getLogger("policy_harness")
FILED_BY = "policy-harness"
MARKER = re.compile(r"<!--\s*policy-check:\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*-->")

# Thresholds, one named constant each (shared with the Cockpit's views where one exists there).
UNSORTED_SHARE = L.UNSORTED_WARN      # more than this share of a module's files unsorted
MAX_AGE_H = L.GAPS_MAX_AGE_H          # generated data and the last good backup: at most this many hours old


@dataclasses.dataclass(frozen=True)
class Check:
    id: str
    title: str
    policy: str          # the policy sentence it enforces, with the owner's date
    threshold: str       # the constant's name and value, for the finding
    remediation: str     # the work of the finding; {subject} is filled in
    run: object          # pure: (project data, now) -> [(subject, [evidence lines])]
    analysis: bool = False   # the work is an analysis whose output is suggestions for the owner


def unsorted_share(d, now):
    card = d["cards"].get("ambiguity") or {}
    if card.get("status") != "ok":
        return []
    out = []
    for mid, v in sorted(L.ambiguity_view(card["data"])["modules"].items()):
        if v["total"] and len(v["unsorted"]) / v["total"] > UNSORTED_SHARE:
            out.append((mid, [f"{len(v['unsorted'])} of {v['total']} files unsorted ({v['share']:.1%}, more than {UNSORTED_SHARE:.0%}), "
                              f"ambiguity card data of {card.get('generated_at')}"] +
                        [f"`{u.get('path')}`: candidates {', '.join(u.get('candidates') or []) or 'none'}; {u.get('reason') or 'no reason given'}"
                         for u in v["unsorted"]]))
    return out


def freshness(d, now):
    items = [(f"{k} card data", (d["cards"].get(k) or {}).get("age_h")) for k in L.CARD_KINDS]
    for key, label in (("quota", "quota history"), ("transcript", "transcript usage file")):  # the proxy runs only with tasks: idle is not stale
        last = L._latest_ts(d["usage"].get(key))
        items.append((label, L._age_h(last, now) if last else None))
    if d.get("metrics"):
        gen = L.code_view(d["metrics"])["generated_at"]
        items.append(("code snapshot", L._age_h(gen, now) if gen else None))
    return [(what, [f"{what} is {age:.1f} hours old (more than {MAX_AGE_H:g})"]) for what, age in items if age is not None and age > MAX_AGE_H]


def footprint(d, now):
    if d.get("modules") is None:  # no module map: no per-module policy
        return []
    card = d["cards"].get("footprint") or {}
    if card.get("status") != "ok":
        return [("footprint card", [f"no footprint card data ({card.get('reason', 'not read')}), so no module of {len(d['modules'])} has a footprint"])]
    have = card["data"].get("modules") if isinstance(card["data"].get("modules"), dict) else {}
    return [(mid, [f"module `{mid}` of the module map has no entry in the footprint card data of {card.get('generated_at')}"])
            for mid in d["modules"] if mid not in have]


def backup(d, now):
    found = L.backup_status_gaps((d.get("backup") or {}).get("text"), now, MAX_AGE_H)
    return [("backup", [what + ("" if age is None else f" ({age:.1f} hours)") for what, age in found])] if found else []


CHECKS = (
    Check("structure.unsorted-share", "more than 30 percent of a module's files unsorted",
          "When more than 30 percent of a module's files are unsorted (kept in the module because they fit no sub-module unambiguously), "
          "that is a policy violation (structure policy, owner, 2026-11-24).",
          f"UNSORTED_SHARE = {UNSORTED_SHARE}",
          "Analyse the structure of the module `{subject}` and write suggestions for the owner (sub-modules, assignments of the unsorted files). "
          "The output is suggestions only: do not change the module map or move any file.", unsorted_share, analysis=True),
    Check("freshness.generated-24h", "generated data older than 24 hours",
          "The components of the project metadata, incl. code metrics, token usage, etc, are to be refreshed within 24 hours; "
          "stale components are refreshed, not only reported (standing policies (0) and (1), owner, 2026-11-24).",
          f"MAX_AGE_H = {MAX_AGE_H}",
          "Refresh the {subject} (run its collector) and find why it was not refreshed within the day; fix the cause.", freshness),
    Check("footprint.every-module", "a module without a persistence footprint",
          "Every module and sub-module shows its persistence footprint: tables, files and folders, logging, statistics, backup and retention "
          "policy (owner defaults for all projects, owner, 2026-11-20).",
          "none (any module of the map without a footprint entry)",
          "Write the persistence footprint of `{subject}` in the project's footprint producer (docs/contracts/card-metadata.md) and regenerate the card data.",
          footprint),
    Check("backup.fresh", "the daily backup missing, failed or stale",
          "Every project has a daily backup (PC-62); a missing, failed or stale backup is a gap the daily job files as a task, "
          "and generated data is refreshed within 24 hours (standing policy (0), owner, 2026-11-24).",
          f"MAX_AGE_H = {MAX_AGE_H}",
          "Find why the daily backup of the project is missing, failed or stale ({subject}), fix the cause and run the backup (tools/project_backup.py).",
          backup),
)


def run_checks(project, data, now, checks=CHECKS):
    """-> [violation dicts {check, project, subject, evidence}] of every check over one project's data."""
    return [dict(check=c.id, project=project, subject=str(s), evidence=ev) for c in checks for s, ev in c.run(data, now)]


def key(v):
    """The deduplication key (check id, project, subject), as its marker writes it."""
    return tuple(str(v[k]).replace("|", "/").replace("-->", "").strip() for k in ("check", "project", "subject"))


def marker(v):
    return "<!-- policy-check: {} | {} | {} -->".format(*key(v))


def finding(v, filed_by, today, checks=CHECKS):
    """A violation -> (title, body) of its policy-finding task: the policy sentence, the evidence, the remediation, the markers."""
    c = next(x for x in checks if x.id == v["check"])
    work = ("**Work (an analysis):** " if c.analysis else "**Work:** ") + c.remediation.format(subject=v["subject"])
    evidence = "\n".join(f"- {e}" for e in v["evidence"])
    title = f"Policy finding: {c.title}: {v['project']} {v['subject']}"
    body = (f"{work}\n\n**Policy:** {c.policy}\n\n**Check:** `{c.id}` (threshold {c.threshold}), project `{v['project']}`, "
            f"subject `{v['subject']}`\n\n**Evidence:**\n{evidence}\n\n{marker(v)}\n<!-- filed-by: {filed_by} -->\n\n"
            f"<details><summary>Origin</summary>\n\nFiled by `{filed_by}` (tools/policy_harness.py) on {today}: a policy finding "
            f"(released at once; the finder does not work on it). A violation that disappears does not close this task.\n\n</details>\n")
    return title, body


def open_keys(repo, run=ff.gh_run):
    """The keys of the open policy-finding tasks of a repository (their policy-check markers)."""
    issues = json.loads(run(["issue", "list", "-R", repo, "--state", "open", "--label", ff.LABELS[0], "--limit", "1000", "--json", "body"]) or "[]")
    return {tuple(m.groups()) for i in issues for m in MARKER.finditer(i.get("body") or "")}


def file_task(repo, title, body, policy_id, run=ff.gh_run):
    """The one filing function (tests replace it): create the issue, then the trail comment naming the policy. -> the issue URL."""
    args = ["issue", "create", "-R", repo, "--title", title, "--body", body]
    for lab in ff.labels_for(None, "job"):
        args += ["--label", lab]
    url = run(args)
    run(["issue", "comment", url, "--body", f"Trail: filed and released at once as a policy finding of the check `{policy_id}` (policy is code, owner, 2026-11-24)."])
    log.info("filed %s", url)
    return url


def remediate(repo, violations, filed_by, today, file=file_task, keys=open_keys):
    """File a task per violation unless an open one has its key; -> the violations with `task` (URL), `duplicate` or `error` set."""
    try:
        have = keys(repo)
    except (OSError, subprocess.SubprocessError, ValueError) as ex:
        log.error("%s: cannot list the open findings, nothing filed: %s", repo, ex)
        return [dict(v, error=f"open findings not readable: {ex}") for v in violations]
    out = []
    for v in violations:
        if key(v) in have:
            out.append(dict(v, duplicate=True))
            continue
        try:
            out.append(dict(v, task=file(repo, *finding(v, filed_by, today), v["check"])))
            have.add(key(v))
        except (OSError, subprocess.SubprocessError) as ex:
            log.error("%s: could not file %s: %s", repo, key(v), ex)
            out.append(dict(v, error=str(ex)))
    return out


def text(rep):
    lines = []
    for p in rep["projects"]:
        lines += [f"{p['project']} | {v['check']} | {v['subject']} | {v['evidence'][0]}"
                  + (f" | filed {v['task']}" if v.get("task") else " | already open" if v.get("duplicate") else f" | error: {v['error']}" if v.get("error") else "")
                  for v in p["violations"]] or [f"{p['project']}: no violation"]
    return "\n".join(lines + [f"{rep['count']} violation(s){' (dry run: nothing filed)' if rep['dry_run'] else ''}"])


def main(argv=None, now=None, file=file_task, keys=open_keys):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="project name (default: the default of the projects file)")
    ap.add_argument("--all", action="store_true", help="every project of the projects file")
    ap.add_argument("--projects-file", default=G.DEFAULT_PROJECTS, help="the Cockpit's --projects file (default: %(default)s)")
    ap.add_argument("--data-dir", default=str(C.DATA_DIR), help="the Cockpit's data folder (default: %(default)s)")
    ap.add_argument("--json", action="store_true", help="print the JSON report instead of text lines")
    ap.add_argument("--file-tasks", action="store_true", help="file a policy-finding task per new violation (default: dry run)")
    ap.add_argument("--filed-by", default=FILED_BY, help="the finder's id in the task's filed-by marker (default: %(default)s)")
    a = ap.parse_args(argv)
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        projects, default = C.projects_from_file(json.loads(pathlib.Path(a.projects_file).expanduser().read_text()))
        chosen = projects if a.all else [p for p in projects if p["name"] == (a.project or default)]
        if not chosen:
            raise ValueError(f"unknown project {a.project!r}; configured: {', '.join(x['name'] for x in projects)}")
    except (OSError, ValueError) as ex:
        log.error("%s", ex)
        print(f"error: {ex}", file=sys.stderr)
        return 2
    rep = dict(schema=1, generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), dry_run=not a.file_tasks, projects=[])
    for p in chosen:
        vs = run_checks(p["name"], G.collect(p, pathlib.Path(a.data_dir).expanduser(), now), now)
        if vs and a.file_tasks:
            vs = remediate(p["repo"], vs, a.filed_by, now.date().isoformat(), file=file, keys=keys)
        log.info("project %s: %d violation(s)", p["name"], len(vs))
        rep["projects"].append(dict(project=p["name"], repo=p["repo"], violations=vs))
    rep["count"] = sum(len(p["violations"]) for p in rep["projects"])
    print(json.dumps(rep, indent=1) if a.json else text(rep))
    return 1 if rep["count"] else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sys.exit(main())
