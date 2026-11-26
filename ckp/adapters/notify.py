"""Outside signals of the monitoring (PC-142): the heartbeat ping to the agreed monitoring service and the desktop notification.
The one place that knows the service's URL scheme (a GET on the check's ping URL, `<url>/fail` for a failed run, as healthchecks.io
takes it) and the host's notifier (macOS `osascript`, elsewhere `notify-send`). Neither ever raises: a missed signal is a warning."""
import logging
import sys
import urllib.request

from ckp.adapters import run as _run

log = logging.getLogger("ckp.adapters.notify")


def ping(url, ok=True, *, opener=urllib.request.urlopen, timeout=10):
    """GET the ping URL (`/fail` appended for a failed run): True when the service answered 2xx. No payload is sent."""
    target = url.rstrip("/") + ("" if ok else "/fail")
    try:
        with opener(target, timeout=timeout) as r:
            status = getattr(r, "status", None) or r.getcode()
    except Exception as e:  # noqa: BLE001 - a network failure must not fail the job that pings
        log.warning("heartbeat ping failed: %s", e)
        return False
    return 200 <= status < 300


def desktop(title, text, *, run=_run, platform=sys.platform):
    """Show a desktop notification: True when the notifier ran."""
    if platform == "darwin":
        q = lambda s: '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'
        args = ["osascript", "-e", f"display notification {q(text)} with title {q(title)}"]
    else:
        args = ["notify-send", str(title), str(text)]
    try:
        run(args)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("desktop notification not shown: %s", e)
        return False
