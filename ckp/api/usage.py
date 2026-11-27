"""API group `usage`: the Token Usage report from the project's proxy, processing and transcript files."""
import logging
import datetime as dt
import importlib.util
import json
import pathlib
import sys

from ckp.core import usage as core_usage
from ckp.api import cache as api_cache, context as api_context

from ckp.api.result import ok
log = logging.getLogger("cockpit")


# The Token Usage tab's files, under DATA_DIR/<project name>/ (the folder the Settings page uses)
USAGE_FILE, PLOG_FILE, AREA_DIR, QUOTA_FILE = "proxy-usage.jsonl", "processing/processing.jsonl", "area", "quota-history.jsonl"


TRANSCRIPT_FILE, AGGREGATE_FILE = "transcript-usage.jsonl", "stats-cache-usage.jsonl"


SNAPSHOT_REFRESH_FILE = "code-snapshot-refresh.json"  # the last result of tools/code_snapshot_refresh.py (card-metadata contract)


def _usage_one(p, data_dir=None):
    d = (data_dir or api_context.DATA_DIR) / (p.get("name") or p["repo"].split("/")[-1])

    def goodbye_tokens(folder):  # only the task folder's name is taken from the log; the file is read inside the area
        try:
            t = json.loads(api_context._read_text(d / AREA_DIR / pathlib.PurePath(folder).name / "outbox" / "goodbye.json")).get("tokens")
        except (TypeError, ValueError, AttributeError):
            return None
        return t if isinstance(t, int) and not isinstance(t, bool) else None
    return core_usage.usage_report(api_context._read_text(d / USAGE_FILE), api_context._read_text(d / PLOG_FILE), goodbye_tokens, api_context._read_text(d / QUOTA_FILE))


def usage_served(p):
    return api_cache.cached(api_context.pk(p, "usage"), lambda: get_usage(p), 15, swr=True)


def get_usage(p, data_dir=None, projects=None, today=None):
    """The selected project's report plus `estimates` (the quota per vendor window, measured or estimated) and `monthly`: every project the Cockpit serves per month (L.monthly_by_project), from each one's
    proxy file and processing log and from the transcript and statistics-cache files of all their data folders."""
    rep = _usage_one(p, data_dir)
    d = (data_dir or api_context.DATA_DIR) / (p.get("name") or p["repo"].split("/")[-1])
    rep["sessions"] = core_usage.transcript_report(api_context._read_text(d / TRANSCRIPT_FILE), rep["quota"], api_context._read_text(d / AGGREGATE_FILE))
    rep["heatmap"] = core_usage.heat_days(rep.get("days"), rep["sessions"])
    try:  # the plan start and the weak threshold live with the card settings (Settings > Module cards > Quota estimates)
        qs = json.loads(api_context._read_text(d / "cockpit-settings.json") or "{}")
    except ValueError:
        qs = {}
    rep["estimates"] = core_usage.quota_estimates(api_context._read_text(d / QUOTA_FILE), api_context._read_text(d / USAGE_FILE), api_context._read_text(d / TRANSCRIPT_FILE),
                                         qs if isinstance(qs, dict) else {})
    ps = list((projects or api_context.PROJECTS or {p.get("name", ""): p}).values())
    folders = [(data_dir or api_context.DATA_DIR) / (q.get("name") or q["repo"].split("/")[-1]) for q in ps]
    rep["monthly"] = core_usage.monthly_by_project(
        [dict(name=q.get("name") or q["repo"].split("/")[-1], dir=pathlib.Path(q["git"]).name if q.get("git") else q.get("name") or q["repo"].split("/")[-1],
              sources=q.get("transcript_sources"), start=q.get("start_month"), usage=api_context._read_text(f / USAGE_FILE), plog=api_context._read_text(f / PLOG_FILE))
         for q, f in zip(ps, folders)],
        [api_context._read_text(f / TRANSCRIPT_FILE) for f in folders], [api_context._read_text(f / AGGREGATE_FILE) for f in folders],
        today or dt.datetime.now(dt.timezone.utc).date())
    rep["reconcile"] = _reconcile_card(d, p.get("stats_cache"))
    return rep


def _reconcile_card(d, cache=None):
    """The card "How these numbers fit": tools/token_reconcile.py on the same files (local day: the server's zone); None if it fails."""
    try:
        R = _tool("token_reconcile")
        return R.card(R.reconcile(R.load(d, cache)))
    except Exception:  # a defect of the analysis must not take the Token Usage page down
        log.exception("token reconciliation failed")
        return None


def _tool(name):
    """A module of tools/ (not a package), loaded once."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, api_context.ROOT / "tools" / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


# ---------- API entry points (Result) ----------
def usage(ctx):
    """GET /api/usage: the Token Usage report."""
    return ok(usage_served(ctx.p))
