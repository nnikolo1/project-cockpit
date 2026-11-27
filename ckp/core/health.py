"""Gaps in the project metadata, backup, CI, compliance and the action log. Depends on common, modules, rules, commits and usage."""
import datetime as dt
import json
import math
import pathlib
import re
from ckp.core.common import GAPS_MAX_AGE_H, _age_h
from ckp.core.modules import CARD_KINDS, GAP_WORDS, UNSORTED_WARN, _num, ambiguity_view, code_view, doc_modules, map_entry, map_path, parse_card, policy_text
from ckp.core.rules import rule_files
from ckp.core.usage import _epoch, _json_lines


# ---------- gaps in the project metadata (checker: tools/metadata_gaps.py, contract: docs/contracts/metadata-gaps.md) ----------
QUALITY_METRICS = ("complexity", "maintainability", "lint", "dead", "cycles", "duplication", "docstrings", "coverage", "tests")  # docs/contracts/quality-bands.md


def _gap(project, kind, what, module=None, age_h=None):
    return dict(project=project, module=module, kind=kind, what=what, age_h=None if age_h is None else round(age_h, 1))


def _latest_ts(text):
    """Newest `ts` of a JSON-lines text -> ISO string, or None (no text, no readable record)."""
    ts = [r["ts"] for r in _json_lines(text) or [] if _epoch(r.get("ts")) is not None]
    return max(ts, key=_epoch) if ts else None


def _stale(project, kind, label, age, max_h, module=None):
    return [_gap(project, kind, f"{label} is older than {max_h:g} hours", module, age)] if age is not None and age > max_h else []


def metadata_gaps(project, now, modules, cards, metrics, usage, policies, max_age_h=GAPS_MAX_AGE_H, descs=None,
                  tracked=None, assign=None, docs_heads=None, content=None, data_root=None, data_entries=None, timings=None, timing_limit=3.0, quality=None,
                  retention=None, backup=None, snapshot=None, synopsis=None):
    """Pure detection of what the Cockpit shows as no data / not wired / not measured / stale / missing. Every fact comes from the project's own
    data, already read; -> list of gap dicts {project, module|None, kind, what, age_h|None}; an empty list means clean. A check whose input is
    None is not run (e.g. no clone: no `tracked`).
      modules       list of module ids of the module map (None: no map, no per-module checks); descs {id: map desc}
      cards         {kind: parse_card(...) result} for footprint, churn, ambiguity
      metrics       parsed metrics.json of the code snapshot, or None (not configured or unreadable)
      usage         {quota, transcript, proxy: file text or None}
      policies      dict(config=rules_config(...) or None, files=tracked paths or None when there is no clone)
      tracked / assign: the clone's files and the map's `assign`
      docs_heads    {path: first text} of the tracked docs/*.md; content {id: {txt: bool, svg: bool}} of docs/modules/
      quality       dict(modules={id: the `/api/code` module payload}, thresholds=`quality_thresholds` of the card settings or None for the defaults)
      data_root     the project's data folder, data_entries the names in it; timings [(request, seconds|None)] of a live check
      retention     dict(path, text|None, key, min_days) of the coding tool's settings file (transcript_retention_gaps)
      backup        dict(text=the daily backup's status record or None when missing) (backup_status_gaps)
      snapshot      dict(configured, collector, last) of the code snapshot settings and the last refresh record (_snapshot_gaps)
      synopsis      dict(text=the project's session synopsis or None when missing) (ckp.core.synopsis.gaps, PC-70)"""
    g = []
    add = lambda kind, what, mod=None, age=None: g.append(_gap(project, kind, what, mod, age))
    mods = modules or []
    for kind in CARD_KINDS:
        c = cards.get(kind) or parse_card(None, kind)
        if c["status"] != "ok":
            add("card_data", f"{kind} card data: no data ({c['reason']})")
            continue
        g += _stale(project, "stale", f"{kind} card data", c["age_h"], max_age_h)
        have = c["data"].get("modules") if isinstance(c["data"].get("modules"), dict) else {}
        for mid in mods:
            if mid not in have:
                add("card_data", f"{kind} card: no data for this module", mid, c["age_h"])
        if kind == "footprint":
            _footprint_gaps(add, mods, have, c["age_h"], data_root, data_entries)
        if kind == "ambiguity":
            for mid, v in ambiguity_view(c["data"])["modules"].items():
                if v["high"]:
                    add("ambiguity", f"{len(v['unsorted'])} of {v['total']} files unsorted ({v['share']:.0%}, above {UNSORTED_WARN:.0%})", mid, c["age_h"])
        if kind == "churn" and tracked is not None and assign is not None:
            actual = sum(1 for f in tracked if map_path(f, assign) == "unassigned")
            shown = max((_num(w.get("files")) or 0 for w in (c["data"].get("unassigned") or {}).values() if isinstance(w, dict)), default=0)
            if shown > actual:
                add("churn", f"churn data counts {shown} unassigned files, the repository has {actual}: files that no longer exist", None, c["age_h"])
    _snapshot_gaps(add, g, project, now, mods, metrics, max_age_h, snapshot)
    for mid in mods:
        if content is not None:
            c = content.get(mid) or {}
            if not (c.get("txt") or (descs or {}).get(mid)):
                add("module_page", "module has no description text (no docs/modules file, no desc in the map)", mid)
            if not c.get("svg"):
                add("module_page", "module page has no diagram", mid)
    for key, label in (("quota", "quota history"), ("transcript", "transcript usage file"), ("proxy", "proxy usage file")):
        text = usage.get(key)
        last = _latest_ts(text)
        if last is None:
            add("token_usage", f"Token Usage: no {label}" if text is None else f"Token Usage: {label} has no readable record")
        elif key != "proxy":  # the proxy runs only while tasks run: idle is not stale
            g += _stale(project, "stale", label, _age_h(last, now), max_age_h)
    cfg, files = policies.get("config"), policies.get("files")
    if not cfg:
        add("policies", "Policies not configured (no usable `rules` in the module map)")
    elif files is None:
        add("policies", "Policies not configured (no local clone, so no files can be listed)")
    else:
        empty = [x["name"] for x in cfg["groups"] if not rule_files(files, {}, [x])]
        if len(empty) == len(cfg["groups"]):
            add("policies", "Policies list no files (no tracked file matches a rule group)")
        elif empty:
            add("policies", f"Policies: group(s) list no files: {', '.join(empty)}")
    for f in sorted(x for x in (tracked or []) if assign is not None and not map_entry(x, assign)):
        add("map", f"tracked file has no entry in the module map: {f}")
    if docs_heads is not None:
        for path, text in sorted(docs_heads.items()):
            if not doc_modules(text) and pathlib.PurePath(path).name != "README.md":
                add("documents", f"document has no Modules: line: {path}")
    if quality is not None:
        _quality_gaps(add, quality, mods)
    if retention is not None and usage.get("transcript") is not None:  # a project without collected transcripts does not use the tool
        for what in transcript_retention_gaps(**retention):
            add("transcript_retention", what)
    if backup is not None:
        for what, age in backup_status_gaps(backup.get("text"), now, max_age_h):
            add("backup", what, None, age)
    if synopsis is not None:
        from ckp.core.synopsis import gaps as synopsis_gaps
        for what, age in synopsis_gaps(synopsis.get("text"), now.date()):
            add("synopsis", what, None, age)
    for name, secs in timings or []:
        if secs is None or secs > timing_limit:
            add("slow", f"request {name} " + ("failed" if secs is None else f"took {secs:.1f} s (limit {timing_limit:g} s)"))
    return g


BACKUP_STATUS_FILE = "backup-status.json"  # in the project's data folder; written by tools/project_backup.py after every run (PC-62)


def backup_status(text):
    """The daily backup's status record (its text, None when missing) -> dict(time, ok, archive, size, files, reason, last_ok), or None when
    missing or unreadable."""
    try:
        r = json.loads(text) if text else None
    except ValueError:
        return None
    if not isinstance(r, dict) or not isinstance(r.get("ok"), bool) or _epoch(r.get("time")) is None:
        return None
    return {k: r.get(k) for k in ("time", "ok", "archive", "size", "files", "reason", "last_ok")}


def backup_status_gaps(text, now, max_age_h=GAPS_MAX_AGE_H):
    """The status record's text -> [(what, age_h|None)]: no backup recorded, the last run failed, or the last good backup is too old."""
    r = backup_status(text)
    if r is None:
        return [("no backup recorded: no daily backup has run, or its status record is unreadable", None)]
    out = []
    if not r["ok"]:
        out.append((f"last backup failed at {r['time']}: {r.get('reason') or 'no reason given'}", _age_h(r["time"], now)))
    age = _age_h(r["last_ok"], now) if _epoch(r.get("last_ok")) is not None else None
    if age is None and r["ok"]:
        age = _age_h(r["time"], now)
    if age is None:
        out.append(("no successful backup recorded", None))
    elif age > max_age_h:
        out.append((f"last successful backup is older than {max_age_h:g} hours", age))
    return out


# ---------- the last CI run of main (tools/ci_main.py, the Task Queue page) ----------
CI_WORKFLOW = "test.yml"
GATE_LOG = "merge-gate-last.json"  # tools/merge_gate writes it into the clone's git folder (its LOG_NAME)
CI_FIELDS = "databaseId,status,conclusion,headSha,createdAt,url"
CI_RED = ("failure", "timed_out", "startup_failure")
RED_MAIN_TITLE = "CI is red on main: the test workflow fails"
RED_MAIN_POLICY = "Merging: a commit is merged only when it obeys all policies, tests passing included (CLAUDE.md, Merging)."


def ci_run(text):
    """`gh run list --json CI_FIELDS --limit 1` output -> dict(state: green|red|running|other|none, conclusion, sha, time, url)."""
    try:
        runs = json.loads(text or "[]")
    except ValueError:
        runs = None
    r = runs[0] if isinstance(runs, list) and runs and isinstance(runs[0], dict) else None
    if r is None:
        return dict(state="none", conclusion=None, sha=None, time=None, url=None)
    c = r.get("conclusion") or None
    state = "running" if r.get("status") != "completed" else "green" if c == "success" else "red" if c in CI_RED else "other"
    return dict(state=state, conclusion=c, sha=r.get("headSha"), time=r.get("createdAt"), url=r.get("url"))


def gate_result(text, branch="main"):
    """The merge gate's log (tools/merge_gate, LOG_NAME) -> the same dict as ci_run plus source="local gate", or None when there is
    none or it is not a plain run on `branch` (a gate run with another branch merged in says nothing about `branch` itself)."""
    try:
        r = json.loads(text or "null")
    except ValueError:
        return None
    if not isinstance(r, dict) or r.get("branch") != branch or r.get("merged") or r.get("state") not in ("green", "red"):
        return None
    return dict(state=r["state"], conclusion=r.get("reason") or ("success" if r["state"] == "green" else "failure"), sha=r.get("sha"),
                time=r.get("time"), url=None, source="local gate")


def ci_latest(*results):
    """The newest of the results that exist (ci_run's hosted run, gate_result's local run; None or state none is no result).
    PC-94/PC-129: the red-main report reads whichever result exists, the newer one when both do."""
    have = [r for r in results if r and r.get("state") not in (None, "none", "unknown")]
    if not have:
        return next((r for r in results if r), dict(state="none", conclusion=None, sha=None, time=None, url=None))
    return max(have, key=lambda r: _epoch(r.get("time")) or 0)


def red_main_body(run):
    """The text of the red-main finding (tools/file_finding.py adds the policy, the files and the Origin block)."""
    where = (f"The last local merge gate run on main (`tools/merge_gate`, pre-push hook) is red ({run['conclusion']})" if run.get("source") == "local gate"
             else f"The last run of the workflow `{CI_WORKFLOW}` on main is red ({run['conclusion']}): {run['url']}")
    return (f"{where}, commit {run['sha']}, started {run['time']}.\n\n"
            "Work: find the failing tests, fix the cause (not the test), and confirm that `tools/merge_gate` passes.")


RETENTION_MIN_DAYS = 3650  # owner, 2026-11-24: keep the session transcripts (the source of the token statistics) as long as possible


def transcript_retention_gaps(path, text, key, min_days=RETENTION_MIN_DAYS):
    """The coding tool's settings file (its text, None when missing) -> [what] when its transcript retention `key` is not a whole number of
    days at least `min_days`: unset means the tool's short default applies and old transcripts are deleted."""
    if text is None:
        return [f"transcript retention not set: no settings file {path} (the tool's default applies); set {key} to at least {min_days}"]
    try:
        data = json.loads(text)
    except ValueError:
        return [f"transcript retention unknown: settings file {path} is not valid JSON; set {key} to at least {min_days}"]
    v = data.get(key) if isinstance(data, dict) else None
    if v is None:
        return [f"transcript retention not set: no {key} in {path} (the tool's default applies); set it to at least {min_days}"]
    if isinstance(v, bool) or not isinstance(v, int):
        return [f"transcript retention unusable: {key} is {v!r} in {path}; set it to a whole number of at least {min_days} days"]
    if v < min_days:
        return [f"transcript retention {v} days in {path} is shorter than {min_days}; set {key} to at least {min_days}"]
    return []


def _quality_gaps(add, q, mods):
    """(l) a displayed metric without a band (no threshold, or the payload carries no band for it) and a measured module whose Quality view has no bar."""
    th, payload = q.get("thresholds"), q.get("modules") or {}
    measured = {mid: m for mid, m in payload.items() if isinstance(m, dict) and m.get("filesN") and (not mods or mid in mods)}
    for key in QUALITY_METRICS:
        t = (th or {}).get(key) if th is not None else True
        ok = isinstance(t, dict) and t.get("direction") in ("low", "high") and _num(t.get("ok")) is not None and _num(t.get("warn")) is not None
        if th is not None and not ok:
            add("quality_bands", f"quality metric {key} has no band: no usable threshold in quality_thresholds")
        elif any(key not in (m.get("bands") or {}) for m in measured.values()):
            add("quality_bands", f"quality metric {key} has no band in the code payload")
    for mid, m in sorted(measured.items()):
        if not any(isinstance(b, dict) and b.get("band") in ("ok", "warn", "bad") for b in (m.get("bands") or {}).values()):
            add("quality_bands", "Quality view shows a bar for no metric at all", mid)


def _footprint_gaps(add, mods, have, age, data_root, data_entries):
    declared = []
    for mid in mods:
        m = have.get(mid)
        if not isinstance(m, dict):
            continue
        pol = m.get("policy") if isinstance(m.get("policy"), dict) else {}
        for key in ("backup", "retention"):
            if policy_text(pol.get(key))[1]:
                add("footprint", f"persistence footprint: no {key} statement", mid, age)
        for f in m.get("files") or []:
            if isinstance(f, dict):
                declared.append(str(f.get("path", "")))
                if f.get("exists") is False:
                    add("footprint", f"declared file does not exist: {f.get('path')}", mid, age)
        for lg in m.get("logging") or []:
            if isinstance(lg, dict):
                if lg.get("file"):
                    declared.append(str(lg["file"]))
                elif str(lg.get("rotation") or "").strip().lower() in GAP_WORDS:
                    add("footprint", f"logger {lg.get('logger')}: no file and no stated destination", mid, age)
    if data_root and data_entries is not None:
        root = pathlib.Path(data_root).expanduser()
        ds = [pathlib.Path(x).expanduser() for x in declared if x.startswith(("/", "~"))]
        for name in sorted(data_entries):
            e = root / name
            if not any(d == e or e in d.parents or d in e.parents for d in ds):
                add("footprint", f"file in the project's data folder not declared by any footprint: {name}", None, age)


def _snapshot_gaps(add, g, project, now, mods, metrics, max_age_h, setting=None):
    """setting: dict(configured, collector, last) of the project (`code` set, `code_collect` set, the last refresh record or None);
    None: the settings are unknown and only the snapshot itself is checked."""
    s = setting or {}
    if setting is not None and not s.get("configured"):
        add("snapshot", "code snapshot not configured (no `code` folder in the project settings)")
        return
    if setting is not None and not s.get("collector"):
        add("snapshot", "code snapshot has no collector command (no `code_collect` in the project settings), so it is never refreshed")
    last = s.get("last")
    if isinstance(last, dict) and last.get("ok") is False:
        add("snapshot", f"code snapshot refresh failed at {last.get('ts')}: {last.get('reason')}", None, _age_h(last.get("ts"), now))
    if metrics is None:
        add("snapshot", "code snapshot not wired (not configured or metrics.json unreadable)" if setting is None else "code snapshot not wired (metrics.json missing or unreadable)")
        return
    view = code_view(metrics)
    age = _age_h(view["generated_at"], now) if view["generated_at"] else None
    if age is None:
        add("snapshot", "code snapshot has no readable generated_at, so its age is unknown")
    g += _stale(project, "stale", "code snapshot", age, max_age_h)
    for mid in mods:
        m = view["modules"].get(mid)
        if m is None:
            add("snapshot", "code snapshot: not wired (no metrics for this module)", mid, age)
        elif not m["filesN"]:
            add("snapshot", "quality metrics not measured (no analysed files)", mid, age)
        elif m["pyN"]:
            if m["cov"] is None:
                add("snapshot", "no coverage figure for a module with Python files", mid, age)
            if m["cc"] is None:
                add("no_data", "module card: worst complexity: no data", mid, age)
    cov = [_age_h(((m.get("metrics") or {}).get("test_coverage") or {}).get("data_file_modified"), now) for m in (metrics.get("modules") or {}).values()]
    cov = [a for a in cov if a is not None]
    g += _stale(project, "stale", "coverage file", max(cov) if cov else None, max_age_h)


# ---------- offline copy from a backup (PC-63) ----------
# What each pane reads, as groups of alternatives: a pane has its data when every group has at least one present item. Items: "cache:<what>"
# (the Cockpit's cache file of the project), "data:<path>" (a file under the project's data folder), "code" (the snapshot's metrics.json),
# "git" (a local clone), "norules" (no rules configured: the Policies parts have nothing to show). Settings fall back to the defaults, as online.
OFFLINE_PANES = (
    ("tasks and states", "/api/state", (("cache:state",),)),
    ("ranking", "/api/state", (("cache:state",),)),
    ("open questions", "/api/questions", (("cache:state",),)),
    ("timeline", "/api/timeline", (("cache:state",), ("cache:timeline", "cache:commits", "git"))),
    ("rules", "/api/rules", (("cache:rules", "git", "norules"),)),
    ("rules' open pull requests", "/api/rulesprs", (("cache:rulesprs", "norules"),)),
    ("task queue (local part)", "/api/queue", (("data:dispatcher-state.json", "data:processing/processing.jsonl"),)),
    ("token usage", "/api/usage", (("data:proxy-usage.jsonl", "data:quota-history.jsonl", "data:transcript-usage.jsonl", "data:processing/processing.jsonl"),)),
    ("settings", "/api/settings", ()),
    ("module card settings", "/api/cardsettings", ()),
    ("code", "/api/code", (("code",),)),
    ("module cards", "/api/cardmeta", (("cache:cardmeta",) + tuple(f"data:cardmeta/{k}.json" for k in CARD_KINDS),)),
)


def backup_gaps(present):
    """Set of present items -> [{pane, api, missing: [alternatives of each unmet group]}]: the panes whose data is not in the backup."""
    out = []
    for pane, api, groups in OFFLINE_PANES:
        missing = [" or ".join(g) for g in groups if not any(x in present for x in g)]
        if missing:
            out.append(dict(pane=pane, api=api, missing=missing))
    return out


def pane_problem(api, body):
    """None, or why the answer to a pane's request in an offline copy carries no data or would allow steering."""
    if not isinstance(body, dict):
        return "not a JSON object"
    if api == "/api/state":
        if not isinstance(body.get("tasks"), list) or not isinstance(body.get("source"), dict):
            return "no task state"
        return "steering is on" if body["source"].get("steering") else None
    if api == "/api/questions":
        bad = [p for p in body.get("projects", []) if p.get("error")]
        return f"project unreadable: {bad[0]['error']}" if bad else None
    if api == "/api/timeline":
        return None if isinstance(body.get("entries"), list) else "no timeline"
    if api == "/api/rules":
        return f"rules unavailable: {body['error']}" if body.get("error") else None
    if api == "/api/rulesprs":
        return None if body.get("available") or not body.get("configured") else f"open pull requests unavailable: {body.get('error')}"
    if api in ("/api/settings", "/api/cardsettings"):
        return "steering is on" if body.get("steering") else None
    if api == "/api/code":
        return None if body.get("wired") else "code snapshot not wired"
    return None


COMPLIANCE_REPORT_FILE = "compliance-report.md"  # in the project's data folder; written by tools/compliance_check.py (PC-108)


def compliance_summary(text):
    """The compliance report's text -> dict(checked, release, violations) for the Cockpit page, or None when missing or unreadable."""
    m = re.search(r"Checked (\S+) against constitution (\S+)\.", text or "")
    if not m:
        return None
    n = re.search(r"(?m)^(\d+) violation\(s\):", text)
    return dict(checked=m.group(1), release=m.group(2), violations=int(n.group(1)) if n else 0)

# ---------- compliance report (written by the compliance check, PC-108; read by Settings > Policies) ----------
COMPLIANCE_REPORT = "docs/compliance/compliance-report.md"  # one current report per project, inside its clone, overwritten by each run


def parse_compliance(text):
    """The compliance report (docs/contracts/compliance-report.md) -> dict(checked, violations=[{file, line, passage, rule, reason}]).
    `checked` is the `Checked:` line (None when absent). Only the `## Violations` section is read; "none" there means no violation."""
    m = re.search(r"^Checked:\s*(.+?)\s*$", text, re.M)
    sec = re.search(r"^## Violations[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    out = []
    for block in re.split(r"^### ", sec.group(1) if sec else "", flags=re.M)[1:]:
        head, *body = block.split("\n")
        file, _, line = head.strip().rpartition(":") if re.search(r":\d+$", head.strip()) else (head.strip(), "", "")
        field = lambda k: next((b.split(":", 1)[1].strip() for b in body if re.match(rf"-\s*{k}\s*:", b, re.I)), "")
        out.append(dict(file=file, line=int(line) if line else None, passage=" ".join(b.lstrip("> ").rstrip() for b in body if b.startswith(">")).strip(),
                        rule=field("Rule"), reason=field("Reason")))
    return dict(checked=m.group(1) if m else None, violations=out)


def compliance_run_state(status, alive):
    """The run status file's record (or None) and whether its process still lives -> the state the page shows:
    idle (never started here), running, done, failed (exit code not 0), interrupted (recorded as running, but the process is gone)."""
    if not status:
        return "idle"
    if status.get("state") == "running":
        return "running" if alive else "interrupted"
    return "done" if status.get("exit") == 0 else "failed"


# ---------- action log (PC-132): one JSON line per state-changing action or page failure; never a token, a credential or a task text ----------
ACTION_LOG_LIMIT = 200  # the Settings view shows at most this many events
_LOG_PARAMS = ("p", "d", "b", "module", "type", "cls", "parent", "id", "enabled")  # short, non-secret values kept as given
_LOG_SIZES = ("body", "desc")  # texts: only their size is kept
_SECRETISH = re.compile(r"\b(gh[pousr]_[A-Za-z0-9_]{10,}|github_pat_[A-Za-z0-9_]{10,}|sk-[A-Za-z0-9_-]{10,})\b|([?&]t=)[^ &\"']+")


def scrub(text, secrets=(), limit=300):
    """A message safe for the action log: known secrets and token-shaped strings replaced, cut to `limit` characters."""
    s = str(text if text is not None else "")
    for x in secrets:
        if x:
            s = s.replace(x, "<secret>")
    return _SECRETISH.sub(lambda m: (m.group(2) or "") + "<secret>", s)[:limit]


def _short(v, limit=40):
    return v if isinstance(v, (bool, int, float)) else scrub(v, limit=limit)


def _page_id(v):
    return v if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", v) else ""


def _now_iso(now):
    return (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")


def action_record(form, outcome, msg, ms, task=None, version=None, now=None, secrets=()):
    """The action log's line of one action posted to /api/act: what, on which task, the non-secret parameters (the new title, the size of an
    edited text, never the text), outcome (ok | refused | failed), message, duration in ms, the page's id and the code version."""
    params = {k: _short(form[k]) for k in _LOG_PARAMS if form.get(k) not in (None, "")}
    if form.get("title"):
        params["title"] = scrub(form["title"], secrets, 200)
    params.update({f"{k}_chars": len(str(form[k])) for k in _LOG_SIZES if form.get(k) is not None})
    return dict(t=_now_iso(now), source="server", project=_short(form.get("project") or "", 60), action=_short(form.get("action") or "", 30),
                task=_short(task if task is not None else form.get("n") or "", 30), params=params, outcome=outcome, msg=scrub(msg, secrets),
                ms=int(ms), page=_page_id(form.get("page")), version=version or "")


PAGE_KINDS = dict(network="failed", script="failed", refused="refused")


def page_record(ev, project, version=None, now=None, secrets=()):
    """A failure the page reports (/api/event): a network error on an action, a script error, a refusal it showed -> the action log's line.
    ValueError when it is not one (unknown kind, no page id): the endpoint refuses it rather than log noise."""
    kind, page = ev.get("kind"), _page_id(ev.get("page"))
    if kind not in PAGE_KINDS or not page:
        raise ValueError("an event needs a kind (network, script, refused) and the page's id")
    ms = ev.get("ms")
    ok_ms = isinstance(ms, (int, float)) and not isinstance(ms, bool) and math.isfinite(ms)
    return dict(t=_now_iso(now), source="page", kind=kind, project=_short(project or "", 60), action=_short(ev.get("action") or "", 30),
                task=_short(ev.get("task") or "", 30), params={}, outcome=PAGE_KINDS[kind], msg=scrub(ev.get("msg"), secrets),
                ms=int(ms) if ok_ms else None, at=scrub(ev.get("at"), limit=40), page=page, version=version or "")


def filter_events(lines, action="", outcome="", task="", limit=ACTION_LOG_LIMIT):
    """Action log lines, newest first, -> the parsed events that match every given filter, at most `limit`.
    Unreadable lines are skipped. `task` matches the task key or number (with or without '#')."""
    out, want = [], str(task).strip().lstrip("#").lower()
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or (action and e.get("action") != action) or (outcome and e.get("outcome") != outcome):
            continue
        if want and str(e.get("task", "")).lstrip("#").lower() != want:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out
