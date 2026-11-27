"""Rule files, the rule feed and the policy text. Depends on nothing else in ckp.core."""
import pathlib
import re


# ---------- rule files: which files steer the agents, and what changed in them ----------
RS, US, GS = "\x1e", "\x1f", "\x1d"  # record, field and body separators of the git formats below
LOG_FMT = f"{RS}%H{US}%aI{US}%an{US}%ae{US}%s{US}%b{GS}"  # then --numstat or -p output follows
BOT = ("[bot]", "noreply@anthropic")  # default of rules.bots
TRAILER = re.compile(r"^co-authored-by:\s*([^<\n]*?)\s*(?:<[^>\n]*>)?\s*$", re.I | re.M)
DIFF_CAP, LINE_CAP = 30, 200


def glob_re(g):
    """Glob -> regex over repo-relative '/' paths. `**/` = any directories (or none), `**` = anything, `*` and `?` stay inside one segment."""
    out, i = "", 0
    while i < len(g):
        if g.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif g.startswith("**", i):
            out, i = out + ".*", i + 2
        elif g[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif g[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(g[i]), i + 1
    return re.compile(out + r"\Z")


def rules_config(cfg):
    """The `rules` key of the --modules JSON -> {groups: [{name, res, paths}], managers: [lower-case], bots: [lower-case]}, or None when absent/malformed."""
    r = (cfg or {}).get("rules")
    if not isinstance(r, dict) or not isinstance(r.get("groups"), list):
        return None
    gs = [dict(name=str(g["name"]), paths=[str(p) for p in g["paths"]], res=[glob_re(str(p)) for p in g["paths"]])
          for g in r["groups"] if isinstance(g, dict) and g.get("name") and isinstance(g.get("paths"), list)]
    bots = [str(b).lower() for b in r["bots"]] if isinstance(r.get("bots"), list) else list(BOT)
    return dict(groups=gs, managers=[str(m).lower() for m in r.get("managers", [])], bots=bots) if gs else None


def rule_group(path, groups):
    """Name of the first group whose globs match `path`, else None."""
    return next((g["name"] for g in groups if any(r.match(path) for r in g["res"])), None)


def rule_pathspecs(groups):
    """git pathspecs covering all globs (`:(glob)` magic); the Python matching stays authoritative."""
    return [":(glob)" + p for g in groups for p in g["paths"]]


def is_agent_assisted(body, name, email, bots=BOT):
    """A Co-Authored-By trailer in the message, or an author matching one of `bots` (substrings, lower-case)."""
    who = f"{name} {email}".lower()
    return bool(TRAILER.search(body or "")) or any(b in who for b in bots)


def is_manager(name, email, managers):
    return name.lower() in managers or email.lower() in managers


def _records(text):
    """git output in LOG_FMT -> [(header fields, text after the body)]."""
    out = []
    for rec in text.split(RS)[1:]:
        head, _, rest = rec.partition(GS)
        out.append((head.split(US), rest))
    return out


def parse_numstat(rest, groups):
    """numstat lines -> [{path, group, add, del}] for the files that match a group (binary files count 0)."""
    out = []
    for ln in rest.splitlines():
        p = ln.split("\t")
        if len(p) == 3 and (g := rule_group(p[2], groups)):
            out.append({"path": p[2], "group": g, "add": int(p[0]) if p[0].isdigit() else 0, "del": int(p[1]) if p[1].isdigit() else 0})
    return out


def diff_excerpt(rest, groups, cap=DIFF_CAP):
    """`git show -U0` text -> [[sign, text]]: the first `cap` changed lines of the matching files; [' @', path] marks a file."""
    out, n, on = [], 0, False
    for ln in rest.splitlines():
        if ln.startswith("diff --git "):
            path = ln.rsplit(" b/", 1)[-1]
            on = rule_group(path, groups) is not None
            if on and n < cap:
                out.append(["@", path])
        elif on and n < cap and ln[:1] in ("+", "-") and not ln.startswith(("+++", "---")):
            out.append([ln[0], ln[1:][:LINE_CAP]])
            n += 1
    return out


def parse_rule_changes(numstat_txt, diff_txt, groups, managers, bots=BOT):
    """Outputs of two `git log` runs (LOG_FMT + --numstat / -p -U0) -> rule changes, newest first; commits without matching files are dropped."""
    diffs = {h[0]: rest for h, rest in _records(diff_txt)}
    out = []
    for (sha, date, name, email, subj, body), rest in _records(numstat_txt):
        files = parse_numstat(rest, groups)
        if not files:
            continue
        agent, mgr = is_agent_assisted(body, name, email, bots), is_manager(name, email, managers)
        out.append(dict(sha=sha[:7], date=date[:10], author=name, email=email, subject=subj, files=files, diff=diff_excerpt(diffs.get(sha, ""), groups),
                        co_authors=[c for c in TRAILER.findall(body or "") if c], agent_assisted=agent, by_manager=mgr, needs_attention=agent or not mgr))
    return out


def parse_last_changes(text, groups):
    """`git log --name-only` in LOG_FMT (newest first) -> {path: {date, sha, author, subject}}: the first sight of a path is its last change."""
    out = {}
    for (sha, date, name, _e, subj, _b), rest in _records(text):
        for p in rest.split("\n"):
            if p.strip() and p not in out and rule_group(p, groups):
                out[p] = dict(date=date[:10], sha=sha[:7], author=name, subject=subj)
    return out


def rule_files(paths, last, groups):
    """Tracked paths -> [{path, group, last: {...}|None}] for those matching a group, sorted by group order then path."""
    order = {g["name"]: i for i, g in enumerate(groups)}
    xs = [dict(path=p, group=rule_group(p, groups), last=last.get(p)) for p in paths if rule_group(p, groups)]
    return sorted(xs, key=lambda x: (order[x["group"]], x["path"]))


def pending_rule_prs(prs, groups):
    """`gh pr list --json number,title,author,files,url,createdAt` -> open PRs touching rule files, with those files."""
    out = []
    for p in prs:
        fs = [dict(path=f["path"], group=g) for f in p.get("files") or [] if (g := rule_group(f.get("path", ""), groups))]
        if fs:
            out.append(dict(number=p["number"], title=p["title"], author=(p.get("author") or {}).get("login", ""), url=p["url"], created=(p.get("createdAt") or "")[:10], files=fs))
    return out


def rule_feed(changes, repo):
    """Rule changes -> timeline entries (type 'rule change', lane 'rules')."""
    return [dict(d=c["date"], type="rule change", mod="rules", text=f"{c['subject']} ({', '.join(f['path'] for f in c['files'][:3])}{', ...' if len(c['files']) > 3 else ''})",
                 links=[(c["sha"], f"https://github.com/{repo}/commit/{c['sha']}")]) for c in changes]


REPORT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(html|svg|css)$")


def report_file(code_dir, name):
    """Path of one file of the snapshot's report/ folder, or None. Plain file names only (no folders, no '..')."""
    if not REPORT_NAME.match(name or ""):
        return None
    p = pathlib.Path(code_dir).expanduser() / "report" / name
    return p if p.is_file() else None
