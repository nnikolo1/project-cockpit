"""Heartbeats and the missing-heartbeat watch (PC-142). Every Result is `{ok, data, error}`; the outside signals go through the injected
`ping(url, ok) -> bool` and `notify(title, text) -> bool` (ckp/adapters/notify.py), so the tests send nothing.

Files, in the Cockpit's home (outside every repository):
- `heartbeats.json`: `{job: {"url": PING_URL, "every_h": HOURS}}`, the owner's checks at the monitoring service (mode 0600: a ping URL lets
  anyone ping the check). `every_h` absent: the job is pinged but never expected (a deploy).
- `heartbeat-state.json`: `{"jobs": {job: {"last", "ok"}}, "notified": {problem: time}}`, the last run of each job and the problems a
  notification was shown for.

A problem is a job with `every_h` whose last run is missing, failed, or older than `every_h` plus GRACE_H; or a project whose daily backup
status (`<home>/<project>/backup-status.json`, tools/project_backup.py) says failed or stale. A project with no backup status at all is
the gap checker's finding (tools/metadata_gaps.py), not a problem here. The watch notifies each problem once per RENOTIFY_H.
"""
import datetime as dt
import logging
import pathlib

import logic as L
from ckp.store import io, layout, registry

log = logging.getLogger("ckp.monitor")

GRACE_H = 1           # a daily job may start a little late
BACKUP_MAX_AGE_H = 26  # the daily backup, with the same grace on top of its day plus one hour for a late start
RENOTIFY_H = 24


def _home(home):
    return pathlib.Path(home).expanduser() if home else layout.home()


def _iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def config(home=None):
    d = io.read_json(_home(home) / "heartbeats.json", {})
    return {k: v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def _state(home):
    d = io.read_json(_home(home) / "heartbeat-state.json", {})
    d = d if isinstance(d, dict) else {}
    return {"jobs": d.get("jobs") if isinstance(d.get("jobs"), dict) else {}, "notified": d.get("notified") if isinstance(d.get("notified"), dict) else {}}


def _save_state(st, home):
    io.atomic_write_json(_home(home) / "heartbeat-state.json", st, indent=1)


def set_check(job, url, every_h=None, home=None):
    """Record the ping URL of a job (and how often it must run)."""
    if not job or not str(url).startswith("https://"):
        return {"ok": False, "data": None, "error": "a job name and an https:// ping URL are required"}
    if every_h is not None and every_h <= 0:
        return {"ok": False, "data": None, "error": f"--every-hours {every_h} must be positive"}
    cfg = config(home)
    cfg[job] = {"url": url, **({"every_h": every_h} if every_h else {})}
    io.atomic_write_json(_home(home) / "heartbeats.json", cfg, mode=0o600, indent=2)
    return {"ok": True, "data": {"job": job, "every_h": every_h}, "error": None}


def beat(job, ok, *, ping, home=None, now=None):
    """Record a run of `job` and ping its check when one is configured. Never fails the job: the Result says what was sent."""
    now = now or dt.datetime.now(dt.timezone.utc)
    st = _state(home)
    st["jobs"][job] = {"last": _iso(now), "ok": bool(ok)}
    try:
        _save_state(st, home)
    except OSError as e:
        log.warning("heartbeat state not written: %s", e)
    url = config(home).get(job, {}).get("url")
    sent = bool(url) and ping(url, bool(ok))
    log.info("heartbeat %s %s: %s", job, "ok" if ok else "failed", "pinged" if sent else "not pinged" if url else "no check configured")
    return {"ok": True, "data": {"job": job, "run_ok": bool(ok), "pinged": sent}, "error": None}


def problems(home=None, now=None, projects=None):
    """The open heartbeat and backup problems as `[{key, text}]`; `projects`: the names whose backup is checked (default: the registry)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    jobs, out = _state(home)["jobs"], []
    for job, c in sorted(config(home).items()):
        every = c.get("every_h")
        if not every:
            continue
        rec = jobs.get(job)
        age = L._age_h(rec.get("last"), now) if isinstance(rec, dict) else None
        if age is None:
            out.append({"key": f"heartbeat:{job}", "text": f"no heartbeat of {job} recorded"})
        elif not rec.get("ok"):
            out.append({"key": f"heartbeat:{job}", "text": f"the last run of {job} failed ({rec['last']})"})
        elif age > every + GRACE_H:
            out.append({"key": f"heartbeat:{job}", "text": f"no heartbeat of {job} for {age:.0f} hours (expected every {every:g})"})
    names = projects if projects is not None else [r.get("name") for r in registry.load(home) if r.get("name")]
    for name in names:
        text = io.read_text(_home(home) / name / L.BACKUP_STATUS_FILE)
        if text is None:
            continue
        for what, _age in L.backup_status_gaps(text, now, BACKUP_MAX_AGE_H):
            out.append({"key": f"backup:{name}", "text": f"{name}: {what}"})
    return out


def watch(*, notify, home=None, now=None, projects=None):
    """Notify every open problem not notified in the last RENOTIFY_H hours. The Result lists the problems and those notified now."""
    now = now or dt.datetime.now(dt.timezone.utc)
    found, st, shown = problems(home, now, projects), _state(home), []
    for p in found:
        age = L._age_h(st["notified"].get(p["key"]), now)
        if age is None or age >= RENOTIFY_H:
            if notify("Project cockpit", p["text"]):
                st["notified"][p["key"]] = _iso(now)
                shown.append(p["key"])
    st["notified"] = {k: v for k, v in st["notified"].items() if any(p["key"] == k for p in found)}   # a solved problem is notified anew
    try:
        _save_state(st, home)
    except OSError as e:
        log.warning("heartbeat state not written: %s", e)
    return {"ok": not found, "data": {"problems": found, "notified": shown},
            "error": "; ".join(p["text"] for p in found) or None}
