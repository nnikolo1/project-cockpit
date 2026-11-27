"""Validation of the Dispatcher, module-card, queue-refresh and quota settings. Depends on common only."""
import datetime as dt
import re


# ---------- Dispatcher settings (spec 3c, 3f, 3i, 3j, 4a, 6a) ----------
# the page's narrower ranges (standing rule of 2026-11-24); the Dispatcher does not enforce them. PC-95: the free-disk floor keeps spec 3n's 10 GiB
SETTING_MINIMUMS = {"runaway_minutes": 30, "idle_minutes": 10, "start_minutes": 1, "min_free_disk_gib": 10}
SETTING_MAXIMUMS = {"hold_pressure_level": 4, "max_container_memory_percent": 100}
SETTING_LABELS = {"enabled": "enabled", "interval_minutes": "interval", "retries": "retries", "workers": "workers",
                  "runaway_minutes": "runaway limit", "idle_minutes": "no-activity limit", "start_minutes": "no-start limit",
                  "hold_pressure_level": "memory pressure level", "max_swap_gib": "swap limit", "min_free_memory_gib": "free memory floor",
                  "min_free_disk_gib": "free disk floor", "max_container_memory_percent": "container memory limit"}


def validate_settings(form, defaults, workers_cap):
    """The Dispatcher settings from a form -> a dict of the same keys, or ValueError with a clear message.
    `defaults` gives the keys and which are switches; whole numbers only, retries from 0, the rest from 1 (or SETTING_MINIMUMS), up to
    SETTING_MAXIMUMS, workers up to the image's cap."""
    out = {}
    for k, default in defaults.items():
        v, name = form.get(k), SETTING_LABELS.get(k, k)
        if isinstance(default, bool):
            if not isinstance(v, bool):
                raise ValueError(f"{name} must be on or off")
            out[k] = v
            continue
        if isinstance(v, str) and re.fullmatch(r"\s*\d+\s*", v):
            v = int(v)
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{name} must be a whole number")
        low = SETTING_MINIMUMS.get(k, 0 if k == "retries" else 1)
        if v < low:
            raise ValueError(f"{name} must be at least {low}" + (" minutes" if k.endswith("_minutes") else ""))
        if k in SETTING_MAXIMUMS and v > SETTING_MAXIMUMS[k]:
            raise ValueError(f"{name} must be at most {SETTING_MAXIMUMS[k]}")
        if k == "workers" and v > workers_cap:
            raise ValueError(f"workers must be at most {workers_cap} (the worker users defined in the image)")
        out[k] = v
    return out


# ---------- Module-card settings (Settings tab, section "Module cards") ----------
CARD_CHURN_COLOURS = ("size", "absolute")  # size = changed lines / current lines; absolute = lines added + removed
# Quality bands (docs/contracts/quality-bands.md): metric -> (direction, ok, warn); "low" = lower is better
QUALITY_DEFAULTS = {
    "complexity": {"direction": "low", "ok": 10, "warn": 20},
    "maintainability": {"direction": "high", "ok": 20, "warn": 10},
    "lint": {"direction": "low", "ok": 10, "warn": 30},
    "dead": {"direction": "low", "ok": 2, "warn": 5},
    "cycles": {"direction": "low", "ok": 0, "warn": 0},
    "duplication": {"direction": "low", "ok": 3, "warn": 5},
    "docstrings": {"direction": "high", "ok": 80, "warn": 50},
    "coverage": {"direction": "high", "ok": 80, "warn": 60},
    "tests": {"direction": "high", "ok": 0.5, "warn": 0.2},
}
QUOTA_SETTING_DEFAULTS = {"quota_plan_start": "2026-11-01",  # the first day of the current plan (and so of the same quota; owner, 2026-11-25, PC-111)
                          "quota_weak_readings": 20}          # an estimate resting on fewer readings is marked weak
QUEUE_REFRESH_DEFAULT, QUEUE_REFRESH_MIN = 120, 60  # PC-130: the Task Queue pane's refresh (owner, 2026-11-25: every 2 min); the page may narrow it to 60 s
QUEUE_BUDGET_RESERVE = 100  # below this many calls left in the hourly cap, the Queue pane falls back to the hourly refresh
CARD_SETTING_DEFAULTS = {"churn_colour": "size", "quality_thresholds": QUALITY_DEFAULTS, **QUOTA_SETTING_DEFAULTS,  # churn: what the overview showed before the option moved to Settings
                         "queue_refresh_s": QUEUE_REFRESH_DEFAULT}


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def validate_thresholds(raw, strict=True):
    """quality_thresholds from a form/file -> a full dict (a missing entry gets its default). strict: ValueError on an unknown metric
    or an invalid entry; otherwise an invalid entry falls back to its default."""
    raw = {} if raw is None else raw
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("quality thresholds must be an object")
        raw = {}
    if strict and (bad := [k for k in raw if k not in QUALITY_DEFAULTS]):
        raise ValueError("unknown quality metric: " + ", ".join(map(str, bad)))
    out = {}
    for k, d in QUALITY_DEFAULTS.items():
        e = raw.get(k)
        if e is None:
            out[k] = dict(d)
            continue
        ok = (isinstance(e, dict) and e.get("direction") == d["direction"] and _is_num(e.get("ok")) and _is_num(e.get("warn"))
              and (e["ok"] <= e["warn"] if d["direction"] == "low" else e["ok"] >= e["warn"]))
        if ok:
            out[k] = dict(direction=d["direction"], ok=e["ok"], warn=e["warn"])
        elif strict:
            raise ValueError(f"quality threshold {k}: direction must be {d['direction']}, ok and warn numbers with ok "
                             + ("<= warn" if d["direction"] == "low" else ">= warn"))
        else:
            out[k] = dict(d)
    return out


def validate_card_settings(form, strict=True):
    """The module-card options from a form -> a dict with every key of CARD_SETTING_DEFAULTS, or ValueError with a clear message."""
    v = form.get("churn_colour")
    if v not in CARD_CHURN_COLOURS:
        raise ValueError("churn colour must be one of: " + ", ".join(CARD_CHURN_COLOURS))
    return {"churn_colour": v, "quality_thresholds": validate_thresholds(form.get("quality_thresholds"), strict), **validate_quota_settings(form, strict),
            "queue_refresh_s": validate_queue_refresh(form.get("queue_refresh_s"), strict)}


def validate_queue_refresh(v, strict=True):
    """queue_refresh_s: whole seconds, at least QUEUE_REFRESH_MIN; missing -> the default. strict: ValueError on a bad value, else the default."""
    if v in (None, ""):
        return QUEUE_REFRESH_DEFAULT
    if isinstance(v, str) and re.fullmatch(r"\s*\d+\s*", v):
        v = int(v)
    if isinstance(v, int) and not isinstance(v, bool) and v >= QUEUE_REFRESH_MIN:
        return v
    if strict:
        raise ValueError(f"Task Queue refresh must be a whole number of seconds, at least {QUEUE_REFRESH_MIN}")
    return QUEUE_REFRESH_DEFAULT


def validate_quota_settings(form, strict=True):
    """quota_plan_start (YYYY-MM-DD) and quota_weak_readings (a whole number, at least 1) from a form/file; a missing one gets its default.
    strict: ValueError on an invalid value; otherwise it falls back to its default."""
    out = dict(QUOTA_SETTING_DEFAULTS)
    d, n = form.get("quota_plan_start"), form.get("quota_weak_readings")
    if d not in (None, ""):
        try:
            out["quota_plan_start"] = dt.date.fromisoformat(str(d)).isoformat()
        except ValueError:
            if strict:
                raise ValueError("plan start must be a date YYYY-MM-DD") from None
    if n not in (None, ""):
        try:
            k = int(n) if not isinstance(n, bool) and float(n) == int(float(n)) else 0
        except (TypeError, ValueError):
            k = 0
        if k >= 1:
            out["quota_weak_readings"] = k
        elif strict:
            raise ValueError("readings for a firm estimate must be a whole number of at least 1")
    return out
