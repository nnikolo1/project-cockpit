"""Project cockpit v1: a JSON API + one web page over GitHub (stdlib + the `gh` CLI; no token stored, acts as the gh login).
Every action is exactly one GitHub operation (labels, sub-issues, dependencies, a repo variable); the only local copy is the cache of the slow answers (--cache-dir).

Startup of the viewer: the command line, the access token, the listening server and the warm-up (`python3 cockpit.py ...` runs `main`)."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
import logging
import os
import pathlib
import secrets
import threading
import time

from ckp.store import layout as store_layout
from ckp.api import code as api_code, context as api_context, health as api_health, state as api_state, usage as api_usage
from ckp.server import handler as server_handler

log = logging.getLogger("cockpit")


def warm(projects=None):
    """Fill the caches of every project in the background (at startup): with a disk copy this only starts the refreshes, without it it computes."""
    def one(p):
        for name, fn in (("state", api_state.get_state), ("rules", api_code.get_rules), ("timeline", api_state.get_timeline), ("code", api_code.code_snapshot), ("usage", api_usage.usage_served), ("module pages", api_code.warm_module_pages)):
            try:
                fn(p)
            except Exception as ex:
                log.warning("warm-up %s of %s failed: %s", name, p.get("name", ""), str(ex)[:120])
    with ThreadPoolExecutor(1) as ex:  # one project after another: the first fetches of projects without a disk copy never run as a parallel burst
        list(ex.map(one, projects or list(api_context.PROJECTS.values()) or [api_context.CFG]))
    log.info("warm-up done")


def check_manager(projects, steer):
    """Warn when gh acts as someone else than a project's Manager (a gh call: run after the server listens)."""
    for p in projects:
        if steer and api_state.gh_user() and api_state.gh_user() != p["manager"]:
            log.warning("%s: gh is logged in as %s but the Manager is %s: protected labels set now would be flagged unauthorised", p["name"], api_state.gh_user(), p["manager"])


def serve(host, port, warm_up):
    """Listen first, then warm up in the background (PC-131): a restarted service answers the page at once from its disk copies, marked
    `warming` until warm_up() returns, instead of refusing connections for the whole warm-up. -> the server (the caller runs serve_forever)."""
    api_context.WARM.update(on=True, since=time.time())
    srv = server_handler.Server((host, port), server_handler.H)

    def go():
        try:
            warm_up()
        except Exception:
            log.exception("warm-up failed")
        finally:
            api_context.WARM["on"] = False
    threading.Thread(target=go, daemon=True).start()
    return srv


def load_token(path=None):
    """A fresh random token, or the one kept in `path` (created mode 0600 if missing)."""
    if not path:
        return secrets.token_urlsafe(16)
    p = pathlib.Path(path).expanduser()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(16))
        log.info("created token file %s", p)
    return p.read_text().strip()


def resolve(a):
    """Parsed command line (dict) -> (projects, default name). Single-project flags and --projects exclude each other."""
    if a.get("projects"):
        mixed = [f"--{k}" for k in api_context.PROJECT_KEYS if a.get(k) is not None]
        if mixed:
            raise ValueError(f"--projects replaces {', '.join(mixed)}: put them into the projects file (only --port, --lan, --read-only, --token-file, --simulate-orchestrator stay on the command line)")
        try:
            data = json.load(open(a["projects"]))
        except (OSError, ValueError) as ex:
            raise ValueError(f"--projects: cannot read {a['projects']}: {ex}")
        return api_context.projects_from_file(data)
    if not a.get("repo"):
        raise ValueError("give --repo OWNER/REPO or --projects FILE")
    p = api_context.project_from_flags(a)
    return [p], p["name"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--projects", help="JSON {default, projects:[{name, repo, project?, manager?, modules?, git?, code?, code_collect?, compliance?, phases?, meta?}]}: serve several projects (replaces the single-project flags)")
    ap.add_argument("--repo", help="OWNER/REPO")
    ap.add_argument("--manager", help="the one login whose protected labels count (default: the repo owner)")
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--lan", action="store_true", help="bind 0.0.0.0 (plain HTTP + token)")
    ap.add_argument("--project", type=int, help="GitHub Project number (owner = repo owner): Module, Priority hint, Status, token fields")
    ap.add_argument("--git", help="local clone: commit history for the timeline, docs/project/* for gaps, documents and events")
    ap.add_argument("--modules", help="JSON {modules:[{id,name,docs?}], assign:[{path,module}]}")
    ap.add_argument("--phases", help="extra timeline.json ({module, events:[...]}) merged into the timeline")
    ap.add_argument("--code", help="code snapshot directory: metrics.json and erd|dataflow|deps|js.svg (see README)")
    ap.add_argument("--compliance", help="the compliance check command (PC-108), run in the --git clone; it writes docs/compliance/compliance-report.md")
    ap.add_argument("--code-collect", help="the code snapshot's collector command, run by tools/code_snapshot_refresh.py when the snapshot is stale")
    ap.add_argument("--token-file", help="keep the access token in this file (created 0600 if missing), so the URL survives restarts")
    ap.add_argument("--cache-dir", help="keep the slow answers on disk here (default: <data dir>/cache) so that a restarted service shows data at once; 'none' = memory only")
    ap.add_argument("--read-only", action="store_true", help="refuse every action (default: actions are enabled)")
    ap.add_argument("--steer", action="store_true", help=argparse.SUPPRESS)  # actions are the default now; kept so that existing service files still start
    ap.add_argument("--simulate-orchestrator", action="store_true", help="demo buttons: 'takes it' and 'completes it'")
    ap.add_argument("--offline", metavar="BACKUP", help="run a read-only copy of one project from its backup (archive or folder, see docs/contracts/backup.md): no GitHub call at all")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if a.offline:
            mixed = [k for k in ("projects", "cache_dir", "simulate_orchestrator", *api_context.PROJECT_KEYS) if getattr(a, k) not in (None, False)]
            if mixed:
                raise ValueError(f"--offline takes the project from the backup: drop {', '.join('--' + x.replace('_', '-') for x in mixed)}")
            projects = [api_health.start_offline(a.offline)]
            default = projects[0]["name"]
        else:
            projects, default = resolve(vars(a))
    except ValueError as ex:
        ap.error(str(ex))
    api_context.register(projects, default)
    steer = not (a.read_only or a.offline)
    api_context.CFG.update(steer=steer, simulate=a.simulate_orchestrator)
    for p in projects:
        log.info("project %s: repo=%s project=%s manager=%s mode=%s", p["name"], p["repo"], p["project"], p["manager"], "STEERING" if steer else "READ-ONLY")
    if not a.offline:
        api_context.CACHE_DIR = None if a.cache_dir == "none" else pathlib.Path(a.cache_dir).expanduser() if a.cache_dir else store_layout.cache_dir(api_context.CACHE_SCHEMA, api_context.DATA_DIR)
    server_handler.H.token = load_token(a.token_file)
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    if a.lan:
        log.warning("--lan: plain HTTP, the token travels unencrypted; trusted network only")
    srv = serve(host, a.port, lambda: (check_manager(projects, steer), warm()))
    log.info("open http://%s:%d/?t=%s  (acting as the gh user; manager=%s)", host, a.port, "<token file>" if a.token_file else server_handler.H.token, ", ".join(f"{p['name']}={p['manager']}" for p in projects))
    srv.serve_forever()
