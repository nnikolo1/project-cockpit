"""The GitHub gateway: the one place `gh` and local `git` are run (hourly call cap, rate-limit pause, offline refusal) and the budget it keeps."""
import logging
import collections
import subprocess
import threading
import time

from ckp.core import queue_view as core_queue_view
from ckp.api import context as api_context

log = logging.getLogger("cockpit")


# ---------- gh ----------
GH_CALLS_PER_HOUR = 600       # cap of gh calls this Cockpit makes per rolling hour (a state load is about 12 calls; the 5,000-point budget is shared with every other tool of the login, so the Cockpit keeps well below it)


RATE_LIMIT_PAUSE_S = 900      # after a rate-limit error with no reset time in it: no gh call for this long (the next hourly reset is at most 3600 s away; 15 min bounds the damage of a wrong guess)


_gh_queue = threading.RLock()  # the hourly queue: GitHub refreshes run one at a time (projects one after another, never in parallel bursts); re-entrant for a refresh that needs another queued key


GH_SUMMARY_EVERY = 25         # one summary line "gh calls in the last hour: n of cap" per this many calls


GH_REFRESH_S = 3600           # lazy refresh (owner F-699): GitHub data is fetched at most once per this many seconds per project; within it the cache is served with no call, an older cache is served at once and one refresh is queued


DISK_FRESH_S = GH_REFRESH_S   # a disk copy younger than this is used as is after a restart (nothing is fetched at startup)


class GitHubUnavailable(RuntimeError):
    """No gh call was made: the budget is exhausted or paused. `until` is the epoch time of the next call allowed."""
    def __init__(self, msg, until):
        super().__init__(msg)
        self.until = until


_gh_calls, _gh_lock, _gh_pause = collections.deque(), threading.Lock(), dict(until=0.0, rate_limited=False)


def _gh_gate(now=None):
    """Count one gh call, or raise GitHubUnavailable when the Cockpit is paused after a rate-limit error or over its hourly cap."""
    now = time.time() if now is None else now
    with _gh_lock:
        if api_context.OFFLINE:
            api_context.OFFLINE["gh_attempts"] = api_context.OFFLINE.get("gh_attempts", 0) + 1
            log.warning("offline copy: a GitHub call was refused (the backup lacks the data that needed it)")
            raise GitHubUnavailable("offline copy: the Cockpit makes no GitHub call; this data is not in the backup", 0)
        if now < _gh_pause["until"]:
            raise GitHubUnavailable(f"GitHub rate limit reached; the Cockpit makes no GitHub call until {api_context._clock(_gh_pause['until'])}", _gh_pause["until"])
        while _gh_calls and _gh_calls[0] <= now - 3600:
            _gh_calls.popleft()
        if len(_gh_calls) >= GH_CALLS_PER_HOUR:
            until = _gh_calls[0] + 3600
            log.warning("gh call cap reached: %d calls in the last hour, next call allowed at %s", len(_gh_calls), api_context._clock(until))
            raise GitHubUnavailable(f"the Cockpit's own limit of {GH_CALLS_PER_HOUR} GitHub calls per hour is reached; next call at {api_context._clock(until)}", until)
        _gh_calls.append(now)
        if len(_gh_calls) % GH_SUMMARY_EVERY == 0:
            log.info("gh calls in the last hour: %d of %d", len(_gh_calls), GH_CALLS_PER_HOUR)


def _gh_failed(msg, now=None):
    """After a failed call: a rate-limit error pauses every further call until the reset time (the message's, else RATE_LIMIT_PAUSE_S from now)."""
    now = time.time() if now is None else now
    if core_queue_view.is_rate_limited(msg):
        with _gh_lock:
            _gh_pause["until"] = core_queue_view.rate_limit_reset(msg, now) or now + RATE_LIMIT_PAUSE_S
            log.warning("GitHub rate limit hit (%s): no gh call until %s", msg[:100], api_context._clock(_gh_pause["until"]))


def run_gh(args):
    """Run `gh <args>` and return stdout; raises RuntimeError with gh's message on failure, GitHubUnavailable (no call made) while paused or over the hourly cap."""
    _gh_gate()
    log.info("gh %s", " ".join(a if len(a) < 60 else a[:57] + "..." for a in args))
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError("gh timed out")
    if r.returncode:
        msg = r.stderr.strip() or f"gh exited {r.returncode}"
        _gh_failed(msg)
        raise RuntimeError(msg)
    return r.stdout


# ---------- rules ----------
def run_git(p, args, timeout=30):
    """Run `git -C <clone> <args>` (list-based, bounded) and return stdout."""
    return subprocess.run(["git", "-C", p["git"], *args], capture_output=True, text=True, check=True, timeout=timeout).stdout


STATE_CALLS = 12   # gh calls of one state load (see GH_CALLS_PER_HOUR)


def gh_room(now=None):
    """GitHub may be called for one state load now: no rate-limit pause, room in the hourly cap, not an offline copy."""
    now = time.time() if now is None else now
    with _gh_lock:
        return not api_context.OFFLINE and now >= _gh_pause["until"] and sum(c > now - 3600 for c in _gh_calls) + STATE_CALLS <= GH_CALLS_PER_HOUR


def gh_calls_left(now=None):
    """How many gh calls the Cockpit's own hourly cap still allows now."""
    now = time.time() if now is None else now
    with _gh_lock:
        return GH_CALLS_PER_HOUR - sum(1 for t in _gh_calls if t > now - 3600)
