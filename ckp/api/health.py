"""API group `health`: the state of main (CI and the merge gate), the compliance check, the Environments view and the offline copy from a backup."""
import logging
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time

from ckp.core import environments as core_environments, health as core_health, rules as core_rules
from ckp.core import environments as core_environments
from ckp.store import io as store_io
from ckp.api import cache as api_cache, context as api_context, gh as api_gh

from ckp.api.result import File, fail, ok
log = logging.getLogger("cockpit")


# ---------- compliance check (Settings > Policies): the report is the check's (PC-108); the Cockpit shows it and starts the check ----------
_compliance_lock = threading.Lock()


def compliance_status_path(p):
    """The run status of the project's compliance check: <data dir>/<project name>/compliance-run.json (the page polls it)."""
    return api_context.DATA_DIR / (p.get("name") or p["repo"].split("/")[-1]) / "compliance-run.json"


def _write_json(f, data):
    store_io.atomic_write_json(f, data)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def compliance_of(p):
    """One project's compliance view: configured, the report (date, violations; None = never run), the run state."""
    try:
        status = json.loads(compliance_status_path(p).read_text())
    except (OSError, ValueError):
        status = None
    out = dict(name=p.get("name", ""), configured=bool(p.get("compliance") and p.get("git")), report=None, path=core_health.COMPLIANCE_REPORT,
               run=dict(status or {}, state=core_health.compliance_run_state(status, status and _alive(status.get("pid")))))
    f = compliance_report_file(p)
    if f:
        try:
            r = core_health.parse_compliance(f.read_text())
            out["report"] = dict(r, checked=r["checked"] or dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="minutes"))
        except (OSError, UnicodeDecodeError) as ex:
            out["error"] = f"report unreadable: {ex}"
    return out


def build_compliance():
    """The compliance view of every configured project (local files only, no GitHub)."""
    return dict(steering=api_context.CFG["steer"], projects=[compliance_of(p) for p in (api_context.PROJECTS or {api_context.CFG.get("name", ""): api_context.CFG}).values()])


def start_compliance(p):
    """Start the project's compliance check detached, in its clone; one run at a time per project. The status file records the run.
    ValueError when not configured or already running. The check itself writes the report; the Cockpit writes nothing into the project."""
    if not (p.get("compliance") and p.get("git")):
        raise ValueError("compliance check not configured: set the project's `compliance` command and `git` clone")
    name = p.get("name", "")
    with _compliance_lock:
        if compliance_of(p)["run"]["state"] == "running":
            raise ValueError(f"a compliance check of {name} is already running")
        f = compliance_status_path(p)
        f.parent.mkdir(parents=True, exist_ok=True)
        logf = open(f.with_suffix(".log"), "w")
        proc = subprocess.Popen(p["compliance"], cwd=pathlib.Path(p["git"]).expanduser(), stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True, env=dict(os.environ, COMPLIANCE_REPORT=core_health.COMPLIANCE_REPORT))
        rec = dict(state="running", pid=proc.pid, started=time.time())
        _write_json(f, rec)

    def wait():
        code = proc.wait()
        logf.close()
        _write_json(f, dict(rec, state="ended", exit=code, ended=time.time()))
        log.info("compliance check of %s ended with exit %s", name, code)

    threading.Thread(target=wait, daemon=True).start()
    log.info("compliance check of %s started (pid %d): %s", name, proc.pid, " ".join(p["compliance"]))
    return f"compliance check of {name} started"


def get_environments(p):
    """The Environments view of a project (PC-142): what `ckp env show` prints, and whether the page may offer the deploy buttons."""
    r = core_environments.view(p.get("name", ""))
    return dict(r["data"] or {}, error=r["error"], steering=api_context.CFG["steer"], configured=bool(p.get("git")))


def compliance_report_file(p):
    """The project's current report file, or None."""
    f = pathlib.Path(p["git"]).expanduser() / core_health.COMPLIANCE_REPORT if p.get("git") else None
    return f if f and f.is_file() else None


def local_gate(p):
    """The last plain merge gate run on main in the project's clone (logic.gate_result), or None."""
    if not p.get("git"):
        return None
    try:
        common = pathlib.Path(p["git"]) / api_gh.run_git(p, ["rev-parse", "--git-common-dir"], timeout=5).strip()
        return core_health.gate_result((common / core_health.GATE_LOG).read_text())
    except (OSError, subprocess.SubprocessError):
        return None


def get_ci(p):
    """The state of main for the Task Queue page (until the daily job reports a red main, PC-91): the newer of the last hosted run of
    the test workflow (hourly cache, served on its own so that it can never block the page) and the local merge gate's last run on
    main (PC-129). -> logic.ci_run(...) plus at, the local result, or dict(state="unknown", error)."""
    fetch = lambda: dict(core_health.ci_run(api_gh.run_gh(["run", "list", "-R", p["repo"], "--workflow", core_health.CI_WORKFLOW, "--branch", "main", "--limit", "1", "--json", core_health.CI_FIELDS])), at=time.time())
    try:
        hosted = api_cache.cached(api_context.pk(p, "ci"), fetch, api_gh.GH_REFRESH_S, swr=True, persist=True, disk_fresh=api_gh.DISK_FRESH_S, queue=True)
    except (RuntimeError, ValueError) as ex:
        log.warning("ci: last run of main unavailable (%s)", str(ex)[:80])
        hosted = dict(state="unknown", error=str(ex)[:200])
    return core_health.ci_latest(hosted, local_gate(p))


# ---------- offline copy from a backup (PC-63; layout: docs/contracts/backup.md) ----------
MANIFEST = "backup.json"


BACKUP_PATHS = ("modules", "git", "code", "phases")  # project settings that name a file or folder inside the backup


def unpack_backup(src, dest):
    """Unpack the backup archive `src` (tar, any compression) into the folder `dest`, or copy it when `src` is a folder -> the backup root
    (the folder holding backup.json: dest itself or its one subfolder). Members outside dest, links and devices are refused (ValueError)."""
    src, dest = pathlib.Path(src).expanduser(), pathlib.Path(dest)
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
    else:
        with tarfile.open(src) as tf:
            for m in tf.getmembers():
                if not (m.isfile() or m.isdir()) or m.name.startswith("/") or ".." in pathlib.PurePosixPath(m.name).parts:
                    raise ValueError(f"{src}: refused archive member {m.name!r}")
            tf.extractall(dest, filter="data")
    if (dest / MANIFEST).is_file():
        return dest
    subs = [d for d in dest.iterdir() if (d / MANIFEST).is_file()]
    if len(subs) != 1:
        raise ValueError(f"{src}: no {MANIFEST} at the top of the backup")
    return subs[0]


def open_backup(root):
    """The backup folder -> (project dict with its paths inside the backup, created epoch). ValueError when the manifest is unusable."""
    root = pathlib.Path(root).resolve()
    try:
        m = json.loads((root / MANIFEST).read_text())
        e = dict(m["project"])
    except (OSError, ValueError, KeyError, TypeError) as ex:
        raise ValueError(f"{root / MANIFEST}: unusable manifest: {ex}")
    (p,), _ = api_context.projects_from_file(dict(projects=[{k: v for k, v in e.items() if k not in BACKUP_PATHS + ("meta", "stats_cache")}]))
    for k in BACKUP_PATHS:
        if e.get(k):
            f = (root / e[k]).resolve()
            if not f.is_relative_to(root):
                raise ValueError(f"{MANIFEST}: {k} {e[k]!r} lies outside the backup")
            p[k] = str(f) if f.exists() else None
    created = m.get("created")
    return p, created if isinstance(created, (int, float)) else (root / MANIFEST).stat().st_mtime


def backup_inventory(p):
    """The items logic.OFFLINE_PANES asks for that are present for project p (cache folder, data folder, snapshot, clone, module map)."""
    have = set()
    pre = re.sub(r"[^A-Za-z0-9_.-]", "_", api_context.pk(p, ""))
    if api_context.CACHE_DIR and api_context.CACHE_DIR.is_dir():
        have |= {"cache:" + f.stem[len(pre):] for f in api_context.CACHE_DIR.glob(pre + "*.json")}
    d = api_context.DATA_DIR / p["name"]
    if d.is_dir():
        have |= {"data:" + f.relative_to(d).as_posix() for f in d.rglob("*") if f.is_file()}
    if p.get("code") and (pathlib.Path(p["code"]) / "metrics.json").is_file():
        have.add("code")
    if p.get("git") and pathlib.Path(p["git"]).is_dir():
        have.add("git")
    try:
        if not core_rules.rules_config(api_context.load_modules(p)) and "cache:rules" not in have:
            have.add("norules")
    except (OSError, ValueError):
        pass
    return have


def offline_info():
    """What the page's banner and the restore test need in an offline copy: the time of the data, the refused GitHub calls and the gaps."""
    return dict(created=api_context.OFFLINE["created"], ghAttempts=api_context.OFFLINE.get("gh_attempts", 0), gaps=core_health.backup_gaps(backup_inventory(api_context.proj())))


def start_offline(src):
    """--offline: unpack the backup (an archive into a temporary folder; a folder is used in place, never written to) and point the data and cache
    folders into it -> the project. No GitHub call is made from here on."""
    root = pathlib.Path(src).expanduser()
    if not root.is_dir():
        root = unpack_backup(root, tempfile.mkdtemp(prefix="cockpit-offline-"))
    elif not (root / MANIFEST).is_file():
        raise ValueError(f"{root}: no {MANIFEST} in this folder")
    p, created = open_backup(root)
    api_context.DATA_DIR, api_context.CACHE_DIR = root.resolve() / "data", root.resolve() / "cache"
    api_context.OFFLINE.update(root=str(root), created=created, gh_attempts=0)
    gaps = core_health.backup_gaps(backup_inventory(p))
    log.info("offline copy of %s from %s (data from %s); %s", p["name"], root, time.strftime("%Y-%m-%d %H:%M", time.localtime(created)),
             "every pane has its data" if not gaps else "gaps: " + "; ".join(f"{g['pane']} lacks {', '.join(g['missing'])}" for g in gaps))
    return p


# ---------- API entry points (Result) ----------
def health(ctx):
    """GET /health: only that the server answers and whether it is still warming up (the deploy's health check, PC-84)."""
    return ok(dict(ok=True, warming=bool(api_context.WARM["on"])))


def ci(ctx):
    """GET /api/ci: the state of main."""
    return ok(get_ci(ctx.p))


def environments(ctx):
    """GET /api/environments: the Environments view of the project."""
    return ok(get_environments(ctx.p))


def compliance(ctx):
    """GET /api/compliance: the compliance view of every project."""
    return ok(build_compliance())


def compliance_report(ctx):
    """GET /compliance-report: the project's current report file as plain text."""
    f = compliance_report_file(ctx.p)
    return ok(File(f.read_bytes(), "text/plain; charset=utf-8")) if f else fail("no compliance report yet", 404)
