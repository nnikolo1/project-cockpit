#!/usr/bin/env python3
"""Compliance check (PC-108): do a project's local policy files comply with the Cockpit's constitution? Reads only; never edits the project.

    python3 tools/compliance_check.py [--project NAME] [--projects-file FILE] [--all] [--data-dir DIR] [--release R]
                                      [--file-findings --filed-by AGENT-ID] [--semantic --token-cap N]

Layer 1, structural (always): the managed block of the instruction file is present, current and unedited; every required constitutional
rule is present by its marker `cockpit:RULE-ID`; a declared override names a known rule and the owner's date; every listed policy file has its
`Date:` and `Source:` lines. Layer 2, semantic (off by default, --semantic): a model lists candidate contradictions per policy file, under a
token cap; the cost is logged in the project's proxy-usage file (so it counts in the token statistics). Inputs and markers: docs/contracts/compliance-check.md.
Writes the report to DATA_DIR/<project>/compliance-report.md (shown on the Cockpit page) and, with --file-findings, one policy-finding task per
layer-1 violation (deduplicated by title against the open policy findings). Exit code: 0 compliant, 1 violations, 2 unusable input.
"""
import argparse
import datetime as dt
import hashlib
import json
import logging
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
import file_finding as FF  # noqa: E402
import logic as L  # noqa: E402

log = logging.getLogger("compliance_check")
CONSTITUTION, OWNER_DEFAULTS = ROOT / "docs" / "constitution.md", ROOT / "docs" / "owner-defaults.md"
MANIFEST = ".cockpit/policies.json"
REPORT_FILE = L.COMPLIANCE_REPORT_FILE
USAGE_FILE = "proxy-usage.jsonl"  # cockpit.USAGE_FILE: the token statistics read it
BEGIN, END = "<!-- cockpit:managed:begin -->", "<!-- cockpit:managed:end -->"
# Constitutional rules every project restates or references by marker (owner, 2026-11-25: both bind every project the Cockpit serves).
REQUIRED = ("daily-reports", "local-policy-files")
POLICY = "Local policy files comply with the Cockpit's constitution (compliance check, owner, 2026-11-25)"
MARKER = re.compile(r"cockpit:([a-z0-9-]+)")
OVERRIDE_WORD = re.compile(r"(?i)\boverrides?\b")
OVERRIDE = re.compile(r"(?i)\boverrides?:\s*cockpit:([a-z0-9-]+)\s*\(owner,\s*(\d{4}-\d{2}-\d{2})\)")
DATE_LINE = re.compile(r"(?m)^\s*Date:\s*\d{4}-\d{2}-\d{2}\b")
SOURCE_LINE = re.compile(r"(?m)^\s*Source:\s*\S")
SEMANTIC_DEFAULT_CAP = 20000


def slug(heading):
    """`Daily reports (owner, 2026-11-25)` -> `daily-reports`; the text before the first `(` or `:` names the rule."""
    return re.sub(r"[^a-z0-9]+", "-", re.split(r"[(:]", heading, 1)[0].lower()).strip("-")


def rule_ids(*texts):
    return {slug(m) for t in texts for m in re.findall(r"(?m)^##+\s+(.+)$", t or "")} - {""}


def release_of(*texts):
    """The Cockpit's constitution version: a short content hash of the constitution and the owner defaults."""
    return hashlib.sha256("\0".join(t or "" for t in texts).encode()).hexdigest()[:12]


def managed_block(text):
    """The text between the managed markers, or None when the block is missing or unterminated."""
    i = text.find(BEGIN)
    j = text.find(END, i + len(BEGIN)) if i >= 0 else -1
    return None if j < 0 else text[i + len(BEGIN):j]


def sha256(s):
    return hashlib.sha256(s.encode()).hexdigest()


def structural(manifest, files, rules, release):
    """Layer 1. manifest: the parsed .cockpit/policies.json; files: {path: text or None}; rules: the known rule ids; release: the Cockpit's
    current constitution version. -> [dict(file, what)], empty when compliant."""
    out = []
    add = lambda f, w: out.append(dict(file=f, what=w))
    inst = manifest.get("instruction_file") or "CLAUDE.md"
    text = files.get(inst)
    if text is None:
        add(inst, "instruction file missing")
        text = ""
    block = managed_block(text)
    if block is None:
        add(inst, "managed block missing")
    elif sha256(block) != manifest.get("managed_sha256"):
        add(inst, "managed block edited (its hash differs from the manifest)")
    elif manifest.get("cockpit_release") != release:
        add(inst, f"managed block outdated (synced to {manifest.get('cockpit_release')!r}, constitution is {release})")
    local = {inst: text.replace(BEGIN + block + END, "") if block is not None else text}
    for f in manifest.get("policy_files") or []:
        t = files.get(f)
        if t is None:
            add(f, "policy file missing")
            continue
        local[f] = t
        if not DATE_LINE.search(t):
            add(f, "policy file undated (no `Date: YYYY-MM-DD` line)")
        if not SOURCE_LINE.search(t):
            add(f, "policy file without a source line (no `Source:` line)")
    present = set(MARKER.findall(text)) | {m for t in local.values() for m in MARKER.findall(t)}
    for r in sorted(set(REQUIRED) | set(manifest.get("required") or [])):
        if r not in present:
            add(inst, f"constitutional rule `{r}` neither restated nor referenced (no marker `cockpit:{r}`)")
    for f, t in local.items():
        for n, line in enumerate(t.splitlines(), 1):
            if not OVERRIDE_WORD.search(line):
                continue
            m = OVERRIDE.search(line)
            if not m:
                add(f, f"override without the rule it overrides and the owner's date (line {n}; form `Overrides: cockpit:RULE-ID (owner, YYYY-MM-DD)`)")
            elif m.group(1) not in rules:
                add(f, f"override names an unknown constitutional rule `{m.group(1)}` (line {n})")
    return out


PROMPT = """You compare a project's local policy file with the constitution it must comply with.
List the passages of the local file that may contradict or weaken a constitutional rule. Answer with a JSON array only, each item
{{"local": "exact quotation from the local file", "constitution": "exact quotation from the constitution", "reason": "one sentence"}}; [] if none.

# Constitution
{constitution}

# Local policy file: {path}
{local}
"""


def semantic(local, constitution, model, cap, data_dir=None, now=None):
    """Layer 2. local: {path: text}; model(prompt, max_tokens) -> (answer text, dict of the USAGE fields). Stops before the cap is spent.
    Every call's cost is appended to DATA_DIR/proxy-usage.jsonl as worker `compliance-check`. -> (candidates, tokens spent)."""
    out, spent = [], 0
    for path, text in local.items():
        if spent >= cap:
            log.warning("semantic layer: token cap %d reached, %s not read", cap, path)
            break
        answer, usage = model(PROMPT.format(constitution=constitution, path=path, local=text), cap - spent)
        used = sum(v for v in usage.values() if isinstance(v, int))
        spent += used
        if data_dir is not None:
            pathlib.Path(data_dir).mkdir(parents=True, exist_ok=True)
            rec = dict(ts=(now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"), worker="compliance-check", **usage)
            with open(pathlib.Path(data_dir) / USAGE_FILE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        log.info("semantic layer: %s read, %d tokens", path, used)
        try:
            items = json.loads(answer)
        except ValueError:
            log.warning("semantic layer: unreadable answer for %s", path)
            continue
        out += [dict(file=path, **{k: str(c.get(k, "")) for k in ("local", "constitution", "reason")}) for c in items if isinstance(c, dict)]
    return out, spent


def report(project, violations, release, today, candidates=None, tokens=None):
    """The markdown report. A clean project says so with the date and the constitution version."""
    h = [f"# Compliance report: {project}", "", f"Checked {today} against constitution {release}.", ""]
    if not violations:
        h.append("Compliant: no structural violation.")
    else:
        h += [f"{len(violations)} violation(s):", ""] + [f"- `{v['file']}`: {v['what']}" for v in violations]
    if candidates is not None:
        h += ["", f"## Semantic candidates (for the owner; not a verdict; {tokens} tokens)", ""]
        h += [f"- `{c['file']}`: \"{c['local']}\" vs \"{c['constitution']}\": {c['reason']}" for c in candidates] or ["None."]
    return "\n".join(h) + "\n"


def title(project, v):
    return f"Compliance: {project}: {v['file']}: {v['what']}"


def file_findings(project, repo, violations, filed_by, run=FF.gh_run, today=None):
    """One policy-finding task per violation whose title is not already an open policy finding. -> the titles filed."""
    open_titles = set(json.loads(run(["issue", "list", "-R", repo, "--label", "policy-finding", "--state", "open",
                                      "--limit", "500", "--json", "title", "--jq", "[.[].title]"]) or "[]"))
    filed = []
    for v in violations:
        t = title(project, v)
        if t in open_titles:
            continue
        a = argparse.Namespace(repo=repo, title=t, body=f"The compliance check found: {v['what']}.", policy=POLICY, file=[v["file"]],
                               filed_by=filed_by, module=None, dry_run=False)
        FF.file_finding(a, run=run, today=today)
        filed.append(t)
    return filed


def read_project(git):
    """The project's manifest and the files it names (None for a missing file). ValueError when the manifest is missing or unreadable."""
    root = pathlib.Path(git).expanduser()
    try:
        manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise ValueError(f"{root / MANIFEST}: {ex}") from None
    if not isinstance(manifest, dict):
        raise ValueError(f"{root / MANIFEST}: expected a JSON object")
    names = [manifest.get("instruction_file") or "CLAUDE.md", *(manifest.get("policy_files") or [])]
    files = {}
    for n in names:
        try:
            files[n] = (root / n).read_text(encoding="utf-8")
        except (OSError, ValueError):
            files[n] = None
    return manifest, files


def check(p, data_dir, constitution, defaults, release, today, model=None, cap=SEMANTIC_DEFAULT_CAP, filed_by=None, run=FF.gh_run):
    """Run the check for one project entry (name, repo, git) and write its report. -> the violations."""
    manifest, files = read_project(p["git"])
    rules = rule_ids(constitution, defaults)
    violations = structural(manifest, files, rules, release)
    d = pathlib.Path(data_dir) / p["name"]
    candidates = tokens = None
    if model is not None:
        local = {k: v for k, v in files.items() if v is not None}
        candidates, tokens = semantic(local, constitution, model, cap, d)
    d.mkdir(parents=True, exist_ok=True)
    (d / REPORT_FILE).write_text(report(p["name"], violations, release, today, candidates, tokens), encoding="utf-8")
    log.info("project %s: %d violation(s), report in %s", p["name"], len(violations), d / REPORT_FILE)
    if filed_by and violations:
        log.info("filed %d policy finding(s)", len(file_findings(p["name"], p["repo"], violations, filed_by, run, today)))
    return violations


def main(argv=None, model=None):
    import cockpit as C
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="one project (default: the default of the projects file)")
    ap.add_argument("--all", action="store_true", help="every project in the projects file (the daily job)")
    ap.add_argument("--projects-file", default="~/.config/project-cockpit/projects.json")
    ap.add_argument("--data-dir", default=str(C.DATA_DIR))
    ap.add_argument("--release", help="the constitution version checked against (default: the hash of this clone's constitution)")
    ap.add_argument("--file-findings", action="store_true", help="file one policy-finding task per violation (deduplicated)")
    ap.add_argument("--filed-by", default="compliance-check", help="the finder's agent id in the filed tasks")
    ap.add_argument("--semantic", action="store_true", help="off by default: also run the semantic layer (spends tokens)")
    ap.add_argument("--token-cap", type=int, default=SEMANTIC_DEFAULT_CAP)
    a = ap.parse_args(argv)
    try:
        projects, default = C.projects_from_file(json.loads(pathlib.Path(a.projects_file).expanduser().read_text()))
        if not a.all:
            projects = [x for x in projects if x["name"] == (a.project or default)]
            if not projects:
                raise ValueError(f"unknown project {a.project!r}")
    except (OSError, ValueError) as ex:
        log.error("%s", ex)
        return 2
    if a.semantic and model is None:
        log.error("--semantic needs a model function; none is wired in this build")  # the Worker or the daily job injects one
        return 2
    cons, defs = CONSTITUTION.read_text(encoding="utf-8"), OWNER_DEFAULTS.read_text(encoding="utf-8")
    release, today, rc = a.release or release_of(cons, defs), dt.date.today().isoformat(), 0
    for p in projects:
        if not p.get("git"):
            log.warning("project %s: no local clone, not checked", p["name"])
            rc = max(rc, 2)
            continue
        try:
            v = check(p, pathlib.Path(a.data_dir).expanduser(), cons, defs, release, today, model if a.semantic else None, a.token_cap,
                      a.filed_by if a.file_findings else None)
        except (ValueError, OSError, subprocess.SubprocessError) as ex:
            log.error("project %s: %s", p["name"], ex)
            rc = max(rc, 2)
            continue
        rc = max(rc, 1 if v else 0)
    return rc


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sys.exit(main())
