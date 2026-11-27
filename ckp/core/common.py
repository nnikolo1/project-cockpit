"""Shared constants and helpers of the other ckp.core modules. Depends on nothing."""
import datetime as dt


STATES = ["wip", "unmerged", "failed", "merged"]  # set by the Dispatcher as issue labels; an issue carries at most one
STAGES = ["embryo", "released", *STATES, "cancelled"]


GAPS_MAX_AGE_H = 24    # standing policy: the components of the project metadata are refreshed within 24 hours


def _age_h(iso, now):
    """Hours since an ISO time (a time without a zone is UTC), or None when unreadable."""
    try:
        t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max((now - (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc))).total_seconds() / 3600, 0)
