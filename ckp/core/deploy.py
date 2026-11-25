"""Deploy orchestration (PC-84, part B): pure; every effect goes through the store
functions or the injected adapter object `ad` (contract: docs/contracts/deploy-interfaces.md).

Adapter functions used beyond the contract's list (the contract does not name how the
core learns the facts, so the adapter supplies them): `ad.facts(project, tag, env) -> dict`,
`ad.changed_paths(project, tag) -> list[str]`, `ad.running_workers() -> list[str]`,
optionally `ad.lock_path` (default: `layout.deploy_lock()`).

A rollback is the same deploy with an older tag (PC-84 phase 2): when `facts["installed"]` is true the release folder of the tag
exists already, so the clone and test checks are skipped (that tree was checked when it was installed; the working clone is not
what is deployed) and fetch, selftest and install are skipped; the switch, restart, health check and record run as for a new tag.
"""
import logging

try:  # the store is written in a parallel branch; tests replace these two names
    from ckp.store import lock, releases
except ImportError:  # pragma: no cover
    lock = releases = None

log = logging.getLogger("ckp.deploy")

WORKER_FACING = (
    "dispatcher/proxy.py", "dispatcher/relay.py", "dispatcher/runtime.py",
    "dispatcher/image/", "dispatcher/agent.py",
)

_CHECKS = (  # (fact key, name, sentence when false, None counts as ok)
    ("clean", "clean", "the working clone has uncommitted or unpushed changes", False),
    ("tests_green", "tests", "the tests are not green", False),
    ("ahead_of_live", "ahead", "there is nothing new compared with the live release", False),
    ("ci_ok", "ci", "continuous integration is not green", True),
    ("problems_open", "problems", "heartbeat or backup problems are open", True),
    ("rollback_target", "rollback", "no previous release exists to roll back to", False),
)


def touches_workers(changed_paths):
    return [p for p in changed_paths if any(p.startswith(w) for w in WORKER_FACING)]


_INSTALLED_SKIPS = ("clean", "tests_green")   # facts of the working clone, not of an installed release


def readiness(facts):
    out = []
    for key, name, bad, none_ok in _CHECKS:
        v = facts.get(key)
        if facts.get("installed") and key in _INSTALLED_SKIPS:
            out.append({"name": name, "ok": True, "detail": "not needed: the release is installed already"})
            continue
        if v is None and none_ok:
            out.append({"name": name, "ok": True, "detail": "unknown"})
        else:
            ok = (not v) if key == "problems_open" else bool(v)
            out.append({"name": name, "ok": ok, "detail": "" if ok else bad})
    running = bool(facts.get("deploy_running"))
    out.append({"name": "no deploy running", "ok": not running,
                "detail": "another deploy is running" if running else ""})
    return out


def plan(changed_paths, running_workers):
    hit = touches_workers(changed_paths)
    if not hit:
        return {"mode": "hot", "reasons": ["no Worker-facing path changed"]}
    reasons = ["Worker-facing paths changed: " + ", ".join(hit)]
    if running_workers:
        reasons.append("held: Workers still running: " + ", ".join(running_workers))
    return {"mode": "held", "reasons": reasons}


def _result(ok, data, error=None, dry_run=True):
    return {"ok": ok, "data": data, "error": error, "dry_run": dry_run}


def deploy(project, tag, env, ad, dry_run=True):
    data = {"project": project, "tag": tag, "steps": []}

    def step(name, ok=True, detail=""):
        data["steps"].append({"step": name, "ok": ok, "detail": detail})
        (log.info if ok else log.error)("deploy %s %s: %s %s", project, tag, name, detail)

    def stop(error):
        step("stopped", False, error)
        return _result(False, data, error, dry_run)

    facts = ad.facts(project, tag, env)
    installed = bool(facts.get("installed"))
    data["installed"] = installed
    checks = readiness(facts)
    data["readiness"] = checks
    bad = [c for c in checks if not c["ok"]]
    if bad:
        return stop("not ready: " + "; ".join(c["name"] + ": " + c["detail"] for c in bad))
    step("readiness")

    running = list(ad.running_workers())
    pl = plan(ad.changed_paths(project, tag), running)
    data["plan"] = pl
    if pl["mode"] == "held" and running:
        return stop("; ".join(pl["reasons"]))
    step("plan", True, pl["mode"])

    if dry_run:
        step("dry run: nothing changed")
        return _result(True, data, None, True)

    lock_path = getattr(ad, "lock_path", None)
    if lock_path is None:
        from ckp.store import layout
        lock_path = layout.deploy_lock()
    try:
        with lock.exclusive(lock_path):
            return _run(project, tag, env, ad, data, step, installed)
    except lock.Busy:
        return stop("another deploy holds the lock")


def _run(project, tag, env, ad, data, step, installed=False):
    def fail(error, detail):
        step("failed", False, error)
        ad.record(project, tag, env, "failure", detail)
        return _result(False, data, error, False)

    if installed:
        step("install", True, "installed already: reused")
    else:
        try:
            tree = ad.fetch(tag)
            step("fetch", True, str(tree))
            ok, detail = ad.selftest(tree)
            step("selftest", ok, detail)
            if not ok:
                return fail("selftest failed", detail)
            releases.install_release(project, tag, tree)
            step("install")
        except Exception as e:  # nothing is switched yet: no rollback needed
            log.exception("deploy %s %s failed before the switch", project, tag)
            return fail("failed before the switch: %s" % e, str(e))

    try:
        releases.switch_current(project, tag)
        step("switch")
        ok, detail = ad.restart(project, env)
        step("restart", ok, detail)
        if ok:
            ok, detail = ad.health(env)
            step("health", ok, detail)
        if not ok:
            raise RuntimeError(detail)
    except Exception as e:
        log.exception("deploy %s %s failed after the switch; rolling back", project, tag)
        reason = str(e)
        try:
            back = releases.rollback(project)
            step("rollback", True, back)
            rok, rdetail = ad.restart(project, env)
            step("restart after rollback", rok, rdetail)
        except Exception as e2:
            step("rollback", False, str(e2))
            return fail("rollback failed: %s (after: %s)" % (e2, reason), reason)
        return fail("rolled back: " + reason, reason)

    ad.record(project, tag, env, "success", "deployed")
    step("record", True, "success")
    return _result(True, data, None, False)
