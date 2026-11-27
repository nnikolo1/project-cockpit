"""API group `actions`: steering actions (plan, execute, known effect and re-read), action ids that are applied at most once, and the page events."""
import logging
import json
import threading
import time

from ckp.core import environments as core_environments, health as core_health, tasks_model as core_tasks_model
from ckp.api import cache as api_cache, context as api_context, fetch as api_fetch, gh as api_gh, health as api_health, queue as api_queue, settings as api_settings, state as api_state

from ckp.api.result import from_status
log = logging.getLogger("cockpit")


TYPES = ["feature", "bug", "decision", "research", "doc", "devops", "test"]


# ---------- actions: plan (pure) -> execute ----------
def _date(v, what="date"):
    if not core_tasks_model.is_date(v):
        raise ValueError(f"bad {what}: {v!r}")
    return v


def _prio(v):
    p = f"P{str(v).lstrip('Pp')}"
    if p not in core_tasks_model.PRIO:
        raise ValueError(f"bad priority: {v!r}")
    return p


def _task(S, n):
    t = next((t for t in S["tasks"] if t["number"] == _int(n)), None)
    if not t:
        raise ValueError(f"no such task: {n!r}")
    return t


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ValueError(f"bad number: {v!r}")


def _edit(repo, t, add, remove):
    """gh commands that leave `add` effective and `remove` gone. A label that sits on the issue but was set by someone
    else (unauthorised) is removed first, so that re-adding it records the Manager as the actor."""
    eff = set(t["labels"]) - set(t["bad"])
    ops = []
    redo = [l for l in add if l in t["bad"]]
    if redo:
        ops.append(["issue", "edit", str(t["number"]), "-R", repo] + [x for l in redo for x in ("--remove-label", l)])
    add = [l for l in add if l not in eff]
    remove = [l for l in remove if l in t["labels"]]
    if add or remove:
        ops.append(["issue", "edit", str(t["number"]), "-R", repo] + [x for l in add for x in ("--add-label", l)] + [x for l in remove for x in ("--remove-label", l)])
    return ops


def _prio_due(t, prio, due, set_due):
    """(labels to add, labels to remove) giving task t priority `prio` (None = leave) and deadline `due` ('' = clear) when set_due."""
    eff = set(t["labels"]) - set(t["bad"])
    add, remove = [], []
    if prio:
        add, remove = core_tasks_model.swap_priority(eff, prio)
        remove += [p for p in core_tasks_model.PRIO if p in t["bad"] and p != prio]
    if set_due:
        dues = [l for l in t["labels"] if core_tasks_model.DUE.match(l)]
        if due:
            add.append(f"due:{due}")
            remove += [l for l in dues if l != f"due:{due}"]
        else:
            remove += dues
    return add, remove


EDITABLE = ("embryo", "released")   # the pre-WIP states: the only ones whose text may be edited (owner, 2026-11-25)


STALE_NOTE = "<!-- analysis: stale -->\nThe task text was edited in the Cockpit; its authority analysis is stale until the task is analysed again."


def _edit_text(form, t, repo):
    """Title and body of the issue from the editor. The provenance block and the filed-by marker are taken from the issue, never from the
    form (read-only there), and re-attached; labels are untouched. A saved edit records the authority analysis as stale (a comment)."""
    if t["stage"] not in EDITABLE or t.get("closed"):
        raise ValueError(f"{t['key']} is {'closed' if t.get('closed') else t['stage']}; only embryo and released tasks can be edited")
    title, text = (form.get("title") or "").strip(), core_tasks_model.split_text(form.get("body"))[0]
    if not title:
        raise ValueError("a title is required")
    if title == t["title"] and text == t["text"]:
        return [], f"{t['key']}: nothing changed"
    n = str(t["number"])
    full = title if t["key"].startswith("#") else f"[{t['key']}] {title}"
    ops = [["api", "-X", "PATCH", f"repos/{repo}/issues/{n}", "-f", f"title={full}", "-f", f"body={core_tasks_model.join_text(text, t['kept'])}"],
           ["issue", "comment", n, "-R", repo, "--body", STALE_NOTE]]
    return ops, f"{t['key']} text saved; its authority analysis is now stale"


def plan_action(form, S, repo, simulate=False):
    ops, msg = _plan(form, S, repo, simulate)
    # new due:<date> labels must exist before they can be added
    ops = [["label", "create", x, "-R", repo] for x in sorted({x for op in ops for x in op if x.startswith("due:")})] + ops
    return ops, msg


def _plan(form, S, repo, simulate):
    """-> (ops, message). Each op is the argument list of one `gh` call; '{id}' in an op is the database id returned by the
    previous call. Raises ValueError for anything the framework rules forbid (checked here as well as in the page)."""
    a = form.get("action")
    openstage = ("embryo", "released", "wip")
    if a == "new":
        title = (form.get("title") or "").strip()
        mod, typ, cls = form.get("module") or "", form.get("type") or "task", form.get("cls") or "nice"
        if not title or typ not in TYPES or cls not in ("must", "nice"):
            raise ValueError("a title, a known type and must/nice are required")
        if mod and not any(m["id"] == mod and not m.get("virtual") for m in S["modules"]):
            raise ValueError(f"unknown module {mod!r}")
        labels = [f"type:{typ}", cls] + ([f"module:{mod}"] if mod else [])
        ops = [["label", "create", l, "-R", repo] for l in labels]  # tolerated if they exist
        ops.append(["api", "-X", "POST", f"repos/{repo}/issues", "-f", f"title={title}", "-f", f"body={form.get('desc') or ''}"] + [x for l in labels for x in ("-f", f"labels[]={l}")])
        if form.get("parent"):
            ops.append(["api", "-X", "POST", f"repos/{repo}/issues/{_task(S, form['parent'])['number']}/sub_issues", "-F", "sub_issue_id={id}"])
        return ops, f"task filed as an embryo: {title}"
    if a == "moddl":
        m, d = form.get("module"), form.get("d") or ""
        if not any(x["id"] == m and not x.get("virtual") for x in S["modules"]):
            raise ValueError(f"unknown module {m!r}")
        new = dict(S["modDeadline"])
        new.pop(m, None)
        if d:
            new[m] = _date(d)
        return [["variable", "set", "MODULE_DEADLINES", "--body", json.dumps(new, sort_keys=True), "-R", repo]], f"module deadline of {m} {'set to ' + d if d else 'cleared'}"
    t = _task(S, form.get("n"))
    n = str(t["number"])
    if a == "edit":
        return _edit_text(form, t, repo)
    if a == "release":
        if t["stage"] != "embryo":
            raise ValueError(f"{t['key']} is {t['stage']}; only embryos can be released")
        if t.get("analysis") == "stale":
            raise ValueError(f"{t['key']} was edited; its authority analysis is stale and must be redone before release")
        p, d = _prio(form.get("p")), form.get("d") or ""
        add, remove = _prio_due(t, p, d and _date(d), bool(d))
        return _edit(repo, t, ["released"] + add, remove), f"{t['key']} released as {p}" + (f", deadline {d}" if d else "")
    if a == "change":
        if t["stage"] not in ("released", "wip"):
            raise ValueError(f"{t['key']} is {t['stage']}; priority and deadline change only on released tasks")
        p = _prio(form["p"]) if form.get("p") else None
        d = form.get("d") if "d" in form else None
        return _edit(repo, t, *_prio_due(t, p, d and _date(d), d is not None)), f"{t['key']} updated"
    if a == "cancel":
        if t["stage"] not in openstage:
            raise ValueError(f"{t['key']} is {t['stage']}")
        return _edit(repo, t, ["cancelled"], []) + [["issue", "close", n, "-R", repo, "--reason", "not planned"]], f"{t['key']} cancelled"
    if a in ("block", "unblock"):
        b = _task(S, form.get("b"))
        if t["stage"] not in openstage or b["number"] == t["number"]:
            raise ValueError("cannot change blockers of this task")
        if (b["key"] in t["blockedBy"]) == (a == "block"):
            raise ValueError(f"{b['key']} is {'already' if a == 'block' else 'not'} a blocker of {t['key']}")
        path = f"repos/{repo}/issues/{n}/dependencies/blocked_by"
        if a == "block":
            return [["api", "-X", "POST", path, "-F", f"issue_id={b['dbid']}"]], f"{t['key']} blocked by {b['key']}"
        return [["api", "-X", "DELETE", f"{path}/{b['dbid']}"]], f"{t['key']} no longer blocked by {b['key']}"
    if a in ("accept", "dismiss"):
        p = next((p for p in S["proposals"] if p["n"] == t["number"] and p["id"] == str(form.get("id"))), None)
        if not p:
            raise ValueError("no such open proposal")
        ops = []
        if a == "accept":
            ops = _edit(repo, t, *_prio_due(t, f"P{p['value']}" if p["field"] == "priority" else None, p["value"] if p["field"] == "deadline" else None, p["field"] == "deadline"))
        verb = "Accepted" if a == "accept" else "Dismissed"
        ops.append(["issue", "comment", n, "-R", repo, "--body", f"<!-- pm:resolved {p['id']} -->\n{verb} by the Manager: {p['field']} -> {p['value']}."])
        return ops, f"proposal {verb.lower()} on {t['key']}"
    if a in ("take", "complete"):
        if not simulate:
            raise PermissionError("simulation is off (start with --simulate-orchestrator)")
        if a == "take":
            if t["stage"] != "released" or any(b for b in t["blockedBy"] if next(x for x in S["tasks"] if x["key"] == b)["stage"] != "merged"):
                raise ValueError(f"{t['key']} is not a released, unblocked task")
            return [["issue", "edit", n, "-R", repo, "--add-assignee", "@me"], ["issue", "comment", n, "-R", repo, "--body", "Orchestrator (simulated) takes this task."]], f"{t['key']} WIP"
        if t["stage"] != "wip":
            raise ValueError(f"{t['key']} is not WIP")
        return [["issue", "close", n, "-R", repo, "--reason", "completed"], ["issue", "comment", n, "-R", repo, "--body", "Orchestrator (simulated) completed this task."]], f"{t['key']} completed"
    raise ValueError(f"unknown action {a!r}")


def execute(ops, run=api_gh.run_gh):
    """Run the planned gh calls in order; '{id}' is filled from the previous call's JSON; `label create` may fail (exists).
    -> each call's JSON answer ({} when none), for the known effect of the action (PC-135)."""
    last, outs = {}, []
    for op in ops:
        op = [x.replace("{id}", str(last.get("id", ""))) for x in op]
        try:
            out = run(op)
        except RuntimeError:
            if op[0] == "label":
                outs.append({})
                continue
            raise
        try:
            last = json.loads(out) if (out or "").strip().startswith("{") else {}
        except ValueError:
            last = {}
        outs.append(last)
    return outs


# ---------- after an action (PC-135): the known effect at once, then a re-read of only the touched issues ----------
REREAD_DELAY_S = 3   # the actions of a burst within this many seconds are re-read together: one call per TL_BATCH issues, not one per action


APPLY_WAIT_S = 1.0     # an action's answer waits at most this long for a full load that holds the state (a warm-up, a Refresh): actions never wait for it (PC-131)


def after_action(p, ops, outs, now=None, schedule=True, wait=APPLY_WAIT_S):
    """Lay the known effect of the gh calls that succeeded on the cached state (logic.apply_ops, no GitHub call) and queue a re-read of the
    touched issues. -> their numbers, or None when the cache holds no raw copy (an older copy: it counts as expired, the next look reloads it)
    or a full load holds the state longer than `wait` (the effect is then laid on it in the background once the load is stored)."""
    k, now = api_context.pk(p, "state"), now or time.time()
    lock = api_cache._key_lock(k)   # never between a full load's fetch and its store
    if not lock.acquire(timeout=-1 if wait is None else wait):
        log.info("%s: a full load holds the state; the effect of the action is applied when it is stored", p.get("name", ""))
        api_cache._deferred.add(k)

        def later():
            try:
                after_action(p, ops, outs, now, schedule, wait=None)
            except Exception:
                log.exception("the deferred known effect of an action could not be applied")
            finally:
                api_cache._deferred.discard(k)
        threading.Thread(target=later, daemon=True).start()
        return None
    try:
        t, S = api_cache._cache.get(k, (0, None))
        if S is None and (S := api_cache._disk_load(k)) is not None:
            t = api_cache._disk_time(k, api_gh.DISK_FRESH_S)
        if S is None:
            return None
        if "raw" not in S:
            api_cache._cache[k] = (0, S)
            return None
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        raw, dl, nums = core_tasks_model.apply_ops(S["raw"], S["modDeadline"], ops, outs, S["source"].get("user") or p["manager"], stamp)
        api_cache._store(k, t, api_state.state_from_raw(p, raw, dl, dict(S["source"], patchedAt=now, rereadNote=None)))
    finally:
        lock.release()
    if nums:
        with api_cache._reread_lock:
            first = k not in api_cache._reread
            api_cache._reread.setdefault(k, set()).update(nums)
        if first and schedule:
            threading.Timer(REREAD_DELAY_S, _reread_safe, (p,)).start()
    log.info("%s: action applied to the cached state; issue(s) %s re-read from GitHub in %d s", p.get("name", ""), nums, REREAD_DELAY_S)
    return nums


def _reread_safe(p):
    try:
        reread(p)
    except Exception:
        log.exception("re-read after an action failed")


def reread(p, fetch=None):
    """Read the issues touched by the last actions afresh (one light call per TL_BATCH issues, counted in the hourly cap) and put them in the cached
    state. When GitHub cannot be asked (rate-limit pause, cap, error) the applied change stays and `source.rereadNote` says so."""
    k = api_context.pk(p, "state")
    with api_cache._reread_lock:
        nums = sorted(api_cache._reread.pop(k, ()))
    if not nums:
        return
    try:
        with api_gh._gh_queue:
            issues, events = (fetch or (lambda ns: api_fetch.fetch_issue_nodes(p, ns)))(nums)
        note = None
    except Exception as ex:
        issues, note = None, f"GitHub could not be asked about {', '.join(f'#{n}' for n in nums)} ({str(ex)[:150]}); the change is shown as applied here"
        log.warning("%s: re-read after an action failed: %s", p.get("name", ""), str(ex)[:150])
    with api_cache._key_lock(k):
        t, S = api_cache._cache.get(k, (0, None))
        if not S or "raw" not in S:
            return
        if issues is None:
            S = dict(S, source=dict(S["source"], rereadNote=note))
        else:
            with api_cache._reread_lock:   # an issue acted on again meanwhile keeps its newer applied change until its own re-read
                again = api_cache._reread.get(k, set())
            keep = [i for i in issues if i["number"] not in again]
            S = api_state.state_from_raw(p, core_tasks_model.replace_issues(S["raw"], keep, [e for e in events if e["number"] not in again]), S["modDeadline"],
                               dict(S["source"], rereadAt=time.time(), rereadNote=None))
            log.info("%s: issue(s) %s re-read from GitHub after an action", p.get("name", ""), [i["number"] for i in keep])
        api_cache._store(k, t, S)


# ---------- action ids: the page retries an action after a network failure; a repeat is never applied twice (PC-131) ----------
ACTS_KEEP, ACT_WAIT_S = 200, 120


_acts_lock, _acts_running = threading.Lock(), {}  # aid -> Event of an action that runs in this process right now


def acts_path():
    """The last ACTS_KEEP action ids with their state (started, or done with the answer): <data dir>/actions-seen.json. On disk, so that
    an action interrupted by a restart is recognised when the page sends it again."""
    return api_context.DATA_DIR / "actions-seen.json"


def _acts():
    try:
        return json.loads(acts_path().read_text())
    except (OSError, ValueError):
        return {}


def _acts_put(aid, rec):
    with _acts_lock:
        a = _acts()
        a[aid] = dict(rec, at=time.time())
        for k in sorted(a, key=lambda k: a[k]["at"])[:-ACTS_KEEP]:
            del a[k]
        try:
            api_health._write_json(acts_path(), a)
        except OSError as ex:
            log.warning("action ids not written to disk: %s", ex)


def applied_now(form, run, p=None):
    """True/False: the effect of the action can be read on the issue as it is on GitHub now; None: it cannot be told."""
    if form.get("action") not in core_tasks_model.CHECKABLE_ACTIONS:
        return None
    try:
        p = p or api_context.proj(form.get("project"))
        issue = json.loads(run(["issue", "view", str(_int(form.get("n"))), "-R", p["repo"], "--json", "state,labels,title,body"]))
    except (RuntimeError, ValueError, api_context.UnknownProject) as ex:
        log.warning("cannot read back the task of an interrupted %s: %s", form.get("action"), str(ex)[:100])
        return None
    return core_tasks_model.action_applied(form, issue)


def do_act(form, run=None, state=None, p=None):
    """-> (http status, body). With an action id (`aid`, sent by the page) the action is applied at most once: a repeat of a finished
    action gets the first answer (repeat=True); a repeat of one that is still running waits for it; a repeat of one interrupted by a
    restart first reads the task's current state on GitHub and is only run again when its effect is not there."""
    run = run or api_gh.run_gh
    aid = str(form.get("aid") or "")[:64]
    if not aid or not api_context.CFG["steer"]:
        return _logged_act(form, run, state, p)
    with _acts_lock:
        ev = _acts_running.get(aid)
    if ev:
        ev.wait(ACT_WAIT_S)
    rec = _acts().get(aid)
    if rec and rec.get("state") == "done":
        log.info("action %s repeated (id %s): answered as the first time, not applied again", form.get("action"), aid)
        return rec["code"], dict(rec["body"], repeat=True)
    if rec:
        if aid in _acts_running:
            return 409, dict(error="this action is still running; look at the task in a minute")
        done = applied_now(form, run, p)
        log.info("action %s (id %s) was interrupted by a restart; its effect on GitHub: %s", form.get("action"), aid, done)
        if done:
            body = dict(ok=True, msg=f"{form.get('action')} had already been applied before the Cockpit restarted; not applied again", repeat=True)
            _acts_put(aid, dict(state="done", code=200, body=body))
            return 200, body
        if done is None:
            return 409, dict(error="this action was interrupted by a restart of the Cockpit and its effect cannot be read back; look at the task before trying again")
    with _acts_lock:
        if aid in _acts_running:  # another repeat claimed it in the meantime
            return 409, dict(error="this action is still running; look at the task in a minute")
        ev = _acts_running[aid] = threading.Event()
    try:
        _acts_put(aid, dict(state="started", action=form.get("action")))
        code, body = _logged_act(form, run, state, p)
        _acts_put(aid, dict(state="done", code=code, body={k: v for k, v in body.items() if k != "state"}))  # the page's copy of the state (PC-135) is not kept for repeats
        return code, body
    finally:
        with _acts_lock:
            _acts_running.pop(aid, None)
        ev.set()


def _logged_act(form, run, state=None, p=None):
    """-> (http status, body). 403 when the app is read-only; acts on the project named in form["project"] (default project otherwise).
    Every action, whatever its outcome, leaves one line in the project's action log (PC-132)."""
    ctx, t0 = dict(p=p), time.monotonic()
    try:
        code, body = _do_act(form, run, state, ctx)
    except Exception as ex:  # an unexpected error is a failed action too: logged, and answered rather than dropping the connection
        log.exception("action %s failed", form.get("action"))
        code, body = 500, dict(error=f"{type(ex).__name__}: {ex}")
    outcome = "ok" if code == 200 else "failed" if code >= 500 else "refused"
    api_queue.log_action(ctx.get("p"), core_health.action_record(form, outcome, body.get("msg") or body.get("error"), (time.monotonic() - t0) * 1000,
                                             task=ctx.get("task"), version=api_queue.code_version(), secrets=api_context.secrets()))
    return code, body


def _do_act(form, run, state, ctx):
    if not api_context.CFG["steer"]:
        log.warning("refused action %s: read-only", form.get("action"))
        try:
            ctx["p"] = ctx["p"] or api_context.proj(form.get("project"))
        except api_context.UnknownProject:
            pass
        return 403, dict(error="read-only: this is an offline copy from a backup" if api_context.OFFLINE else "read-only: this instance was started with --read-only")
    try:
        p = ctx["p"] = ctx["p"] or api_context.proj(form.get("project"))
        if form.get("action") == "settings":  # a local file, not a GitHub operation; same guard, same errors
            return 200, dict(ok=True, msg=api_settings.save_settings(p, form))
        if form.get("action") == "cardsettings":  # same: a local file, same guard
            return 200, dict(ok=True, msg=api_settings.save_card_settings(p, form))
        if form.get("action") == "compliance":  # starts the project's compliance check; same guard (the UI is the owner's alone)
            return 200, dict(ok=True, msg=api_health.start_compliance(p))
        if form.get("action") == "deploy":  # starts `ckp deploy` detached (PC-142); the command does the checks, the page only asks
            r = core_environments.start_deploy(p.get("name", ""), p.get("repo"), p.get("git"), str(form.get("env") or "production"), str(form.get("mode") or "check"))
            return (200, dict(ok=True, msg=r["msg"])) if r["ok"] else (400, dict(error=r["error"]))
        why = api_state.steer_block(p)
        if why:  # owner: the write side refuses while GitHub cannot be read; nothing is queued for later
            log.warning("refused action %s on %s: %s", form.get("action"), p.get("name", ""), why)
            return 503, dict(error=f"not done, and not queued: {why}")
        S = state or api_state.get_state(p=p)  # the cached state: an action does not bypass the hour (owner F-701)
        ctx["task"] = next((t["key"] for t in S["tasks"] if str(t["number"]) == str(form.get("n", "")).lstrip("#")), None)
        ops, msg = plan_action(form, S, p["repo"], api_context.CFG["simulate"])
        outs = execute(ops, run)
    except api_context.UnknownProject as ex:
        return 404, dict(error=ex.args[0])
    except PermissionError as ex:
        return 403, dict(error=str(ex))
    except ValueError as ex:
        log.warning("refused %s: %s", form.get("action"), ex)
        return 400, dict(error=str(ex))
    except RuntimeError as ex:
        log.error("action %s failed: %s", form.get("action"), ex)
        return 502, dict(error=f"GitHub: {ex}")
    log.info("action ok on %s: %s -> %s", p.get("name", ""), form.get("action"), msg)
    try:
        nums = after_action(p, ops, outs) if ops else []
    except Exception:  # the action itself succeeded: its answer must not fail; the next look reloads the state
        log.exception("the known effect of %s could not be applied to the cached state", form.get("action"))
        if api_context.pk(p, "state") in api_cache._cache:
            api_cache._expire(api_context.pk(p, "state"))
        nums = None
    # the page's copy at once (PC-135): the served state with the known effect, when the cache holds one (no GitHub call)
    return 200, dict(ok=True, msg=msg, issues=nums or [], **({"state": api_state.get_state_served(p)} if nums is not None and ops and state is None else {}))


def page_events(form):
    """POST /api/event {project, events: [...]}: the failures a page reports (each with its page id; the ones it kept while the server could not be
    reached come in one batch) are appended to the project's action log. -> (http status, body); the invalid ones are counted, not logged."""
    try:
        p = api_context.proj(form.get("project") or None)
    except api_context.UnknownProject as ex:
        return 404, dict(error=ex.args[0])
    evs = form.get("events")
    if not isinstance(evs, list) or not evs or len(evs) > 50:
        return 400, dict(error="events: a list of 1 to 50 events")
    bad = 0
    for ev in evs:
        try:
            rec = core_health.page_record(ev if isinstance(ev, dict) else {}, p.get("name", ""), version=api_queue.code_version(), secrets=api_context.secrets())
        except ValueError:
            bad += 1
            continue
        log.warning("page %s reports %s on %s: %s", rec["page"], rec["kind"], rec["action"] or "-", rec["msg"])
        api_queue.log_action(p, rec)
    return 200, dict(ok=True, logged=len(evs) - bad, refused=bad)


# ---------- API entry points (Result) ----------
def act(ctx, form):
    """POST /api/act: one action on a project (steering). The status is the action's (403 read-only, 400 refused, 502 GitHub, ...)."""
    return from_status(*do_act(form, run=ctx.run))


def event(ctx, form):
    """POST /api/event: the failures a page reports, appended to the project's action log."""
    return from_status(*page_events(form))
