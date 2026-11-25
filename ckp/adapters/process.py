"""Process adapter of the deploy command (PC-84): health probe, service restart and the Dispatcher's running Workers."""
import json
import logging
import os
import time
import urllib.request
from pathlib import Path

from ckp.adapters import run as _run

log = logging.getLogger("ckp.adapters.process")


def health(port, path="/", timeout=5, *, opener=urllib.request.urlopen):
    """HTTP GET on 127.0.0.1:port/path: `(ok, detail)`, ok on a 2xx or 3xx status; any error is a failed check, never raised."""
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with opener(url, timeout=timeout) as r:
            status = getattr(r, "status", None) or r.getcode()
    except Exception as e:  # urllib raises HTTPError for 4xx/5xx, URLError, timeouts, resets
        status = getattr(e, "code", None)
        detail = f"GET {url}: HTTP {status}" if status else f"GET {url} failed: {e}"
        log.warning("health check failed: %s", detail)
        return False, detail
    ok = 200 <= status < 400
    detail = f"GET {url}: HTTP {status}"
    (log.info if ok else log.warning)("health check %s: %s", "ok" if ok else "failed", detail)
    return ok, detail


def restart_service(label, *, plist=None, uid=None, pause_s=3, run=_run, sleep=time.sleep):
    """`launchctl bootout` then `bootstrap` of the user's job `label`, with a pause between (default plist:
    `~/Library/LaunchAgents/<label>.plist`). A bootout of a job that is not loaded is tolerated; returns `(ok, detail)`."""
    uid = os.getuid() if uid is None else uid
    plist = Path(plist) if plist else Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    domain = f"gui/{uid}"
    try:
        run(["launchctl", "bootout", f"{domain}/{label}"])
    except Exception as e:
        log.info("bootout of %s reported %s (it may not have been loaded)", label, e)
    sleep(pause_s)
    try:
        run(["launchctl", "bootstrap", domain, str(plist)])
    except Exception as e:
        detail = f"bootstrap of {label} failed: {getattr(e, 'stderr', None) or e}"
        log.error(detail)
        return False, detail
    log.info("service %s restarted", label)
    return True, f"{label} restarted"


def running_workers(state_file):
    """The task ids of the Workers in the Dispatcher's state file; none when the file is absent or unreadable."""
    try:
        st = json.loads(Path(state_file).read_text())
        workers = st["workers"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if not isinstance(workers, dict):
        return []
    return [str(w.get("task_id", k)) if isinstance(w, dict) else str(k) for k, w in workers.items()]
