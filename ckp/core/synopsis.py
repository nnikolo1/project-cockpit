"""The dated session synopsis a Worker gets with its task (constitution, "Context management"; contract docs/contracts/queue-interfaces.md,
"Synopsis and memory"). Pure functions: the caller reads and writes the files.

The synopsis of a project is `<queue root>/synopsis/<project>.md`, first line `Synopsis of <YYYY-MM-DD>`. The Dispatcher copies it into the
task's `inbox/synopsis.md`, with a first line `STALE: <n> days old` when it is older than STALE_DAYS (or `STALE: undated` without a
readable date); the daily gap check reports a missing or stale synopsis.
"""
import datetime as dt
import re

STALE_DAYS = 7
_FIRST = re.compile(r"^\s*Synopsis of (\d{4})[-.](\d{2})[-.](\d{2})\b")


def synopsis_date(text):
    """The date of the first line `Synopsis of YYYY-MM-DD` (or YYYY.MM.DD), or None."""
    m = _FIRST.match((text or "").split("\n", 1)[0])
    if not m:
        return None
    try:
        return dt.date(*map(int, m.groups()))
    except ValueError:
        return None


def age_days(text, today):
    """Whole days since the synopsis's date, or None when it has no readable date."""
    d = synopsis_date(text)
    return None if d is None else max((today - d).days, 0)


def stale_line(text, today, stale_days=STALE_DAYS):
    """The STALE line for the inbox copy, or '' when the synopsis is fresh."""
    n = age_days(text, today)
    if n is None:
        return "STALE: undated"
    return f"STALE: {n} days old" if n > stale_days else ""


def inbox_text(text, today, stale_days=STALE_DAYS):
    """The synopsis as the Worker reads it: the text, preceded by its STALE line when it is stale or undated."""
    line = stale_line(text, today, stale_days)
    return f"{line}\n{text}" if line else text


def gaps(text, today, stale_days=STALE_DAYS):
    """[(what, age in hours or None)] for the gap check: no synopsis, no readable date, or older than stale_days."""
    if text is None:
        return [("no session synopsis for the Workers (synopsis/<project>.md in the queue root)", None)]
    n = age_days(text, today)
    if n is None:
        return [("session synopsis has no readable date (first line `Synopsis of YYYY-MM-DD`)", None)]
    return [(f"session synopsis is {n} days old (limit {stale_days})", n * 24.0)] if n > stale_days else []
