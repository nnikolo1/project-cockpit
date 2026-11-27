"""Git log parsing, commit lanes and the timeline feed. Depends on modules."""
import logging
import datetime as dt
import json
import pathlib
import re
from ckp.core.modules import map_path

log = logging.getLogger("cockpit")


def commit_lanes(files, assign):
    return {map_path(f, assign) for f in files} or {"unassigned"} if assign else {"all"}


def parse_git_full(text):
    """`git log --name-only --format=@%H|%ad|%s --date=iso` -> [{sha,date,subject,files}] (newest first, as git prints)."""
    out = []
    for line in text.splitlines():
        if line.startswith("@"):
            sha, d, subj = line[1:].split("|", 2)
            out.append(dict(sha=sha, date=dt.date.fromisoformat(d[:10]), subject=subj, files=[]))
        elif line.strip() and out:
            out[-1]["files"].append(line.strip())
    return out


def group_commits(commits, assign, repo):
    """Same-day commits of one module -> one feed entry with several commit links (shown as short shas)."""
    g = {}
    for c in commits:
        d = dt.date.fromisoformat(c["date"][:10]) if isinstance(c["date"], str) else c["date"]  # a disk copy keeps the day as an ISO string
        for lane in sorted(commit_lanes(c["files"], assign)):
            g.setdefault((d, lane), []).append(c)
    out = []
    for (d, lane), cs in g.items():
        links = [(c["sha"][:7], f"https://github.com/{repo}/commit/{c['sha']}") for c in cs[:5]]
        if len(cs) > 5:
            links.append((f"+{len(cs) - 5} more", f"https://github.com/{repo}/commits?since={d}&until={d + dt.timedelta(days=1)}"))
        out.append(dict(d=str(d), type="commit", mod=lane, links=links,
                        text=cs[0]["subject"] + (f" (+{len(cs) - 1} more commits)" if len(cs) > 1 else "")))
    return out


HEAT_LEVELS = 4  # shades 1..4 above the empty shade 0


def shade_level(n, mx):
    """Heatmap shade 0..4 of a day's value n against the largest day mx (0 = none; mirrored by `shade` in web/index.html)."""
    if not n or n <= 0 or not mx or mx <= 0:
        return 0
    return min(HEAT_LEVELS, -(-HEAT_LEVELS * n // mx))


def activity_days(tasks, events, mod_of):
    """Task activity per day and module for the Timeline heatmap: one count per task created or closed (from the tasks) and per
    label/assign event (closed events are the tasks' `closed`). -> [{d, mod, n}]; an unreadable timestamp is skipped."""
    g = {}

    def add(ts, mod):
        try:
            d = dt.date.fromisoformat(str(ts)[:10]).isoformat()
        except ValueError:
            return
        g[(d, mod)] = g.get((d, mod), 0) + 1
    for t in tasks:
        add(t.get("created"), t["module"])
        if t.get("closed"):
            add(t["closed"], t["module"])
    for e in events:
        if e["kind"] != "closed":
            add(e["t"], mod_of.get(e["key"], "unassigned"))
    return [dict(d=d, mod=m, n=n) for (d, m), n in sorted(g.items())]


def issue_entries(tasks):
    """Issues opened/closed; same day+module+kind collapse to one entry listing the first few."""
    g = {}
    for t in tasks:
        for kind, key in (("opened", "created"), ("closed", "closed")):
            if t.get(key):
                g.setdefault((t[key][:10], t["module"], kind), []).append(t)
    out = []
    for (d, m, kind), xs in g.items():
        text = f"{xs[0]['key']} {kind}: {xs[0]['title']}" if len(xs) == 1 else f"{len(xs)} issues {kind}"
        links = [(x["key"], x["url"]) for x in xs[:8]] + ([(f"+{len(xs) - 8} more", xs[0]["url"].rsplit("/", 1)[0])] if len(xs) > 8 else [])
        out.append(dict(d=d, type="issue", mod=m, text=text, links=links))
    return out


def ref_link(ref, repo):
    """A timeline.json ref -> (text, url or None)."""
    base = f"https://github.com/{repo}"
    if ref.get("sha"):
        return ref["sha"][:7], f"{base}/commit/{ref['sha']}"
    if ref.get("url"):
        return ref.get("text", "link"), ref["url"]
    p = ref.get("path")
    if p:
        m = re.match(r"^(.*):(\d+)$", p)
        return ref.get("text") or p, f"{base}/blob/main/{m.group(1)}#L{m.group(2)}" if m else f"{base}/blob/main/{p}"
    return ref.get("text", ""), None


def module_dir(root, mod):
    """docs/project/<module>/ of a module (its modules.json `docs` file's folder when given)."""
    docs = mod.get("docs")
    return pathlib.Path(root) / (pathlib.Path(docs).parent if docs else pathlib.Path("docs/project") / mod["id"])


def static_entries(d, mod_id, repo):
    """<d>/{timeline,backlog}.json -> feed entries; [] when the module has no data yet (read-only)."""
    out = []
    try:
        for e in json.load(open(d / "timeline.json"))["events"]:
            out.append(dict(d=e["date"], type=e["type"], mod=mod_id, text=e["text"], links=[l for l in (ref_link(r, repo) for r in e.get("refs", [])) if l[1] or l[0]]))
    except FileNotFoundError:
        pass
    try:
        b = json.load(open(d / "backlog.json"))
        out.append(dict(d=b["read_on"], type="gap", mod=mod_id, text=f"Backlog built from the stock-taking: {len(b['items'])} items", links=[]))
    except FileNotFoundError:
        pass
    return out


def filter_feed(feed, module=None, typ=None):
    return [e for e in feed if (not module or e["mod"] == module) and (not typ or e["type"] == typ)]
