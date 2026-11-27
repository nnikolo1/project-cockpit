"""The Dispatcher's decision logic: one firing (spec docs/specs/dispatcher-and-workers.md: 3a-3p, 4n, 6a, 6b).

Deterministic code on the host. launchd starts one firing every 2 minutes (3c):

    python3 -m dispatcher.core --settings FILE --state FILE --area DIR --socket SOCK --usage-file FILE ...

A firing (1) reads the settings, (2) looks after the running Workers (WIP on `started`, results, runaway and
start checks), (3) starts at most one new Worker, up to the ceiling (`workers`), when the host readings are under their thresholds
(PC-95, adaptive number of Workers), unless it must skip (proxy/container/relay, egress),
(4) writes the heartbeat line. The tasks, the processing log, the container and every probe are injected.
"""
import argparse
import datetime as dt
import json
import logging
import os
import shutil
import signal
import socket as socketlib
import subprocess
import sys
import time
from dataclasses import dataclass, field

from ckp.queue import runtime as rt
from ckp.queue.agent import ClaudeCode
from ckp.queue.host import _cmd, default_probes

log = logging.getLogger("dispatcher.core")

GIB = 1024 ** 3
DEFAULTS = {                        # 6a: the spec's values, used when the file or a key is missing
    "enabled": True, "interval_minutes": 2, "retries": 0, "workers": 6,
    "runaway_minutes": 40, "idle_minutes": 20, "start_minutes": 1,
    # PC-95: `workers` is the ceiling; another Worker starts only while every reading is on the right side of its threshold (3n: disk)
    "hold_pressure_level": 2, "max_swap_gib": 8, "min_free_memory_gib": 2, "min_free_disk_gib": 10, "max_container_memory_percent": 85,
}
PRESSURE_LEVELS = {"normal": 1, "warn": 2, "critical": 4}   # the host's memory pressure words (dispatcher.host) as levels
# PC-67: the projects the queue serves, [{"short", "repo", "repo_dir", "first"?}]; a list, so not one of the Settings page's numeric DEFAULTS.
# Absent or unusable = no key in the loaded settings = the one project of the command line (today's behaviour).


class Skip(Exception):
    """This firing starts nothing; the message is the recorded reason (3k, 3n, 3p, 4s)."""


class Held(Skip):
    """PC-95: a reading is over its threshold, so the number of Workers does not go up; the message names the readings."""


# --- settings (6a, 6b) ---------------------------------------------------------------------------------------
def load_settings(path):
    """The settings file merged over the defaults; a missing or unusable file, or a bad key, gives the default."""
    s = dict(DEFAULTS)
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        log.warning("settings %s not usable (%s); using defaults", path, e)
        return s
    if not isinstance(data, dict):
        log.warning("settings %s is not an object; using defaults", path)
        return s
    if "projects" in data:
        if valid_projects(data["projects"]):
            s["projects"] = data["projects"]
        else:
            log.warning("settings key projects=%r is unusable; only the project of the command line is served", data["projects"])
    for k, default in DEFAULTS.items():
        if k not in data:
            continue
        v = data[k]
        if isinstance(default, bool):
            ok = isinstance(v, bool)
        else:
            ok = isinstance(v, int) and not isinstance(v, bool) and v >= (0 if k == "retries" else 1)
        if ok:
            s[k] = v
        else:
            log.warning("settings key %s=%r is unusable; default %r", k, v, default)
    return s


def valid_projects(v):
    """A non-empty list of {"short", "repo", "repo_dir"} (strings, each short once; optional boolean "first")."""
    if not isinstance(v, list) or not v:
        return False
    for p in v:
        if not isinstance(p, dict) or not all(isinstance(p.get(k), str) and p[k] for k in ("short", "repo", "repo_dir")):
            return False
        if not isinstance(p.get("first", False), bool):
            return False
    return len({p["short"] for p in v}) == len(v)


# --- the adaptive number of Workers (PC-95) -------------------------------------------------------------------
def _gib(n):
    return f"{n / GIB:.1f}".removesuffix(".0") + " GiB"


def admission(r, s):
    """Whether one more Worker may start: the readings `r` (pressure word; swap, free_memory, free_disk in bytes; container (used, limit)
    in bytes; a missing or None reading is unknown and never holds) against the thresholds of the settings `s`.
    -> (allowed, the readings as text, the reasons for holding as text, "" when allowed)."""
    seen, held = [], []
    p = r.get("pressure") or "unknown"
    seen.append(f"pressure {p}")
    lvl, limit = PRESSURE_LEVELS.get(p), s["hold_pressure_level"]
    if lvl is not None and lvl >= limit:
        word = next((k for k, v in PRESSURE_LEVELS.items() if v == limit), f"level {limit}")
        held.append(f"pressure {p} at or above {word}")
    for key, name, cap, over in (("swap", "swap", "max_swap_gib", True), ("free_memory", "free memory", "min_free_memory_gib", False),
                                 ("free_disk", "free disk", "min_free_disk_gib", False)):
        v = r.get(key)
        if v is None:
            seen.append(f"{name} unknown")
            continue
        seen.append(f"{name} {_gib(v)}")
        if (v > s[cap] * GIB) if over else (v < s[cap] * GIB):
            held.append(f"{name} {_gib(v)} {'above' if over else 'below'} {s[cap]} GiB")
    c = r.get("container")
    if c and c[1]:
        pct = 100 * c[0] / c[1]
        seen.append(f"container {_gib(c[0])} of {_gib(c[1])}")
        if pct > s["max_container_memory_percent"]:
            held.append(f"container memory {pct:.0f} % of its limit above {s['max_container_memory_percent']} %")
    else:
        seen.append("container memory unknown")
    return not held, ", ".join(seen), "; ".join(held)


# --- ranking (3a) --------------------------------------------------------------------------------------------
def rank_key(t):
    """P1 first (no label counts as P2), then the deadline, then what it unblocks, then must before nice."""
    return (t.priority, t.due or "9999-99-99", -t.unblocks, not t.must)


def filed_by_worker(task, worker_id):
    """2b: a finding is never worked on by the agent that filed it. A Worker's id is its user name (and the id of the task it works on, which is never the finding's own); an interactive
    session is bound by the policy text, not by this check."""
    return bool(task.filed_by) and worker_id is not None and task.filed_by == worker_id


def choose_task(tasks, active_ids, worker_id=None, first=()):
    """The best released task that is not blocked, not already being worked on and not filed by this Worker, or None.
    One global order over all projects: the tasks of the `first` projects (the Project cockpit) before all others, then rank_key."""
    ready = [t for t in tasks if not t.blocked and t.id not in active_ids and not filed_by_worker(t, worker_id)]
    return min(ready, key=lambda t: (getattr(t, "project", None) not in first, *rank_key(t))) if ready else None


INTERVAL_TOLERANCE = 30     # seconds: launchd's timing is not exact (3c)


# --- the state file: the Workers a firing leaves running for the next one (3o) -------------------------------
def load_state(path):
    try:
        with open(path) as f:
            st = json.load(f)
        if isinstance(st.get("workers"), dict):
            return st
    except (OSError, ValueError):
        pass
    return {"workers": {}}


def save_state(path, st):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, path)


# --- host sleep (3i) -----------------------------------------------------------------------------------------
def awake_seconds(start, end, sleeps):
    """Seconds from start to end minus the overlap with the recorded sleep intervals [(from, to), ...]."""
    total = max(end - start, 0)
    for a, b in sleeps:
        total -= max(0, min(end, b) - max(start, a))
    return total


# --- one firing ----------------------------------------------------------------------------------------------
@dataclass
class Probes:
    """Everything a firing reads from the outside world; tests inject fakes."""
    now: object = time.time
    free_disk: object = None            # () -> bytes free on the data volume
    proxy_last: object = None           # () -> {worker label: epoch seconds of its last request}; {} if the proxy cannot tell
    transcript_mtime: object = None     # (user) -> epoch seconds or None
    ensure_proxy: object = None         # () -> None; raises Skip/ContainerError if it cannot be started (3k)
    proxy_listening: object = None      # () -> bool (4d)
    start_proxy: object = None          # () -> pid of the detached proxy process; raises Skip (4d)
    stop_proxy: object = None           # (pid) -> bool, True when the process is gone and the socket no longer listens (4d)
    commits_ahead: object = None        # (repo, task_id) -> int, commits of worker/<task-id> beyond main (3e)
    heartbeat_parts: object = None      # () -> dict of the host and container readings (3g)
    resources: object = None            # () -> {"pressure", "swap", "free_memory", "container"} for `admission` (PC-95); None = all unknown


@dataclass
class Dispatcher:
    settings: dict
    state_path: str
    runtime: object                     # dispatcher.runtime.Runtime
    tasks: object                       # GitHubTasks
    plog: object                        # ProcessingLog
    probes: Probes
    actions: list = field(default_factory=list)
    relay_path: str = "/opt/relay.py"   # the relay script inside the image
    repo_dir: str = ""                  # the task repository's clone on the host (3d); the bundle is made from it
    projects: dict = None               # PC-67: short name -> clone of every project the queue serves; None = one project (repo_dir), today's behaviour
    first: tuple = ()                   # the projects ranked before all others

    # -- bookkeeping --
    @staticmethod
    def _kw(w):
        """The project argument of the runtime calls for a Worker's task: only when the queue serves several projects."""
        return {"project": w["project"]} if w.get("project") else {}

    def _fail(self, st, user, w, reason):
        tid = w["task_id"]
        tail = getattr(self.runtime, "worker_output_tail", lambda t, **kw: "")(tid, **self._kw(w))
        log.error("task %s failed: %s%s", tid, reason, f" | Worker output: {tail}" if tail else "")
        self.tasks.set_state(tid, "failed")
        self.plog.failure(tid, w["folder"], reason)
        st["workers"].pop(user, None)

    def _last_record(self, user, w):
        """3o: the proxy's last request for this Worker, else its transcript's mtime, else the launch time."""
        t = w.get("last_request") or self.probes.transcript_mtime(user)
        return max(t or 0, w["launched"])

    def _detect_sleep(self, st, now):
        """3i: a gap since `last_fired` over twice the interval plus a minute means the host slept (or the Dispatcher was
        down): record it (minus one normal interval), log it and return the minutes for the heartbeat; else None."""
        last, step = st.get("last_fired"), self.settings["interval_minutes"] * 60
        if not isinstance(last, (int, float)) or isinstance(last, bool) or now - last <= 2 * step + 60:
            return None
        sleeps = [s for s in st.get("sleeps", []) if s[1] >= now - 2 * self.settings["runaway_minutes"] * 60]
        sleeps.append([last + step, now])
        st["sleeps"] = sleeps
        minutes = round((now - last - step) / 60)
        log.warning("host slept (or the Dispatcher was down): %d minutes", minutes)
        self.plog.sleep(last + step, now, minutes)
        return minutes

    # -- the running Workers (3e, 3i, 3j, 3o, 4n) --
    def manage(self, st):
        now = self.probes.now()
        last = self.probes.proxy_last()
        sleeps = st.get("sleeps", [])
        for user, w in list(st["workers"].items()):
            if user in last:
                w["last_request"] = last[user]   # the `ts` of the proxy's last usage line for this Worker
            started, note, reason = self.runtime.read_outbox(w["task_id"], **self._kw(w))
            if started and not w["started"]:
                w["started"] = True
                self.tasks.set_state(w["task_id"], "wip")
                self.plog.event(w["task_id"], w["folder"], "wip")
            if not self.runtime.alive(w["pid"]):
                try:
                    self._finish(st, user, w, note, reason)
                except Exception:  # noqa: BLE001  a hosting-service failure (GitHub 500) retries next firing; it must not abort this one
                    log.exception("finishing %s (task %s) failed; retried at the next firing", user, w["task_id"])
                continue
            age = awake_seconds(w["launched"], now, sleeps)
            limit, idle = self.settings["runaway_minutes"], self.settings["idle_minutes"]
            if not w["started"] and age > self.settings["start_minutes"] * 60:
                why = "no start"
            elif age > limit * 60:
                why = f"over {limit} minutes"
            elif w["started"] and awake_seconds(self._last_record(user, w), now, sleeps) > idle * 60:
                why = "no activity"
            else:
                continue
            log.warning("killing %s (task %s, pid %s): %s", user, w["task_id"], w["pid"], why)
            self.runtime.kill(w["pid"])
            self._fail(st, user, w, why)

    def _finish(self, st, user, w, note, reason):
        """3e: the process has ended; unmerged if the bundle holds a commit and the Worker did not give up, else failed."""
        tid = w["task_id"]
        if note is None:
            return self._fail(st, user, w, reason)
        try:
            self.tasks.fetch_branch(tid, self.runtime.bundle_path(tid, **self._kw(w)), f"worker/{tid}")
            ahead = self.probes.commits_ahead(w["repo"], tid)
        except Exception:  # a missing or broken bundle holds no commit; it must fail this task, not the firing
            log.exception("fetching the branch of %s failed", tid)
            return self._fail(st, user, w, "no commit in the bundle")
        if ahead < 1:
            return self._fail(st, user, w, "no commit in the bundle")
        if note["outcome"] == "gave_up":  # the Worker's own declaration; the branch stays fetched for the owner
            return self._fail(st, user, w, "the Worker gave up")
        self.tasks.set_state(tid, "unmerged")
        self.plog.event(tid, w["folder"], "unmerged")
        log.info("task %s is unmerged (%d commits on worker/%s)", tid, ahead, tid)
        st["workers"].pop(user, None)

    # -- starting a Worker (3a, 3c, 3k, 3n, 3p, 4s) --
    def start(self, st):
        """Starts at most one Worker, up to the ceiling, when `admission` allows it; returns what the firing did. Raises Skip with
        the reason (Held when a reading is over its threshold). A reading never stops a running Worker (PC-95)."""
        n = rt.clamp_workers(self.runtime.cfg, self.settings["workers"])
        r = {**(self.probes.resources() if self.probes.resources else {}), "free_disk": self.probes.free_disk()}
        ok, seen, why = admission(r, self.settings)
        st["capacity"] = {"ceiling": n, "effective": n if ok else min(len(st["workers"]), n), "readings": seen, "held": why or None,
                          "at": self.probes.now()}
        if not ok:
            raise Held(why)
        try:
            self.probes.ensure_proxy()
            self.runtime.ensure_container()
            self.runtime.ensure_relay(self.relay_path)
        except rt.ContainerTimeout as e:
            raise Skip(f"container call over its time limit: {e}")
        except (rt.ContainerError, Skip) as e:
            raise Skip(f"cannot start proxy, container or relay: {e}")
        free = [f"worker{i}" for i in range(1, n + 1) if f"worker{i}" not in st["workers"]]
        if not free:
            return "nothing to do (all Workers busy)"
        active = {w["task_id"] for w in st["workers"].values()}
        released = self.tasks.released()
        if self.projects:   # a project that is not listed is never served, whatever the adapter returns
            released = [t for t in released if getattr(t, "project", None) in self.projects]
        for t in released:  # 3m: a folder in the area means the task was attempted before; never select it again
            if t.id not in active and os.path.exists(self.runtime.task_dir(t.id, t.project) if self.projects else os.path.join(self.runtime.cfg.area, t.id)):
                log.info("task %s skipped: its folder already exists in the Workers' area", t.id)
                active.add(t.id)
        user = free[0]
        task = choose_task(released, active, user, self.first)
        if task is None:
            return "nothing to do (no released task)"
        project = task.project if self.projects else None
        kw = {"project": project} if self.projects else {}
        repo_dir = self.projects[project] if self.projects else self.repo_dir   # the bundle is made from the task's own project's clone
        try:
            if not self.runtime.egress_blocked(user):
                raise Skip(f"egress check failed for {user}")
            folder = self.runtime.prepare_task(task.id, repo_dir, f"# {task.title}\n\n{task.text}\n", **kw)
            try:
                proc = self.runtime.launch(user, task.id, **kw)
            except BaseException:  # no Worker started: the folder must not mark the task as attempted
                getattr(self.runtime, "discard_task", lambda t, **k: None)(task.id, **kw)
                raise
        except rt.ContainerTimeout as e:
            raise Skip(f"container call over its time limit: {e}")
        except rt.ContainerError as e:
            raise Skip(f"container call failed: {e}")
        except (OSError, ValueError) as e:  # a problem of the Dispatcher, not a failure of the task: no state is set
            log.exception("task %s cannot be prepared", task.id)
            raise Skip(f"cannot prepare {task.id}: {e}")
        st["workers"][user] = {
            "task_id": task.id, "repo": repo_dir, "folder": folder, "pid": proc.pid, "launched": self.probes.now(),
            "started": False, **kw}
        self.plog.event(task.id, folder, "launched", worker=user)
        log.info("started %s for task %s (pid %s)", user, task.id, proc.pid)
        return f"started {user} on {task.id}: {seen}"

    # -- the proxy's lifetime (4d) --
    def manage_proxy(self, st):
        """Needed while a released task is not yet WIP or a Worker runs: start it then; stop it, when the Dispatcher started it, if not."""
        try:
            needed = bool(st["workers"]) or bool(self.tasks.released())
        except Exception:  # cannot tell: leave the proxy as it is
            log.exception("proxy: could not read the released tasks")
            return
        listening = self.probes.proxy_listening()
        if needed and not listening:
            try:
                st["proxy"] = {"pid": self.probes.start_proxy()}
            except Skip as e:
                log.warning("proxy not started: %s", e)
                return
            self.plog.event("-", "-", "proxy started")
            log.info("proxy started (pid %s)", st["proxy"]["pid"])
        elif not needed and "proxy" in st:
            pid = st["proxy"]["pid"]
            if not listening or self.probes.stop_proxy(pid):
                st.pop("proxy")
                self.plog.event("-", "-", "proxy stopped")
                log.info("proxy stopped (pid %s)", pid)
            else:
                log.warning("proxy (pid %s) did not stop", pid)

    def reconcile(self, st):
        """PC-98: no task stays `wip` without a Worker running it. A hosting-service failure is logged, never fatal to the firing."""
        try:
            for tid, what in self.tasks.reconcile_wip({w["task_id"] for w in st["workers"].values()}):
                log.warning("%s: %s", tid, what)
                self.plog.event(tid, "-", what)
        except Exception as e:   # noqa: BLE001
            log.warning("wip reconciliation skipped: %s", e)

    # -- the firing --
    def fire(self):
        st = load_state(self.state_path)
        slept = did = None
        try:
            slept = self._detect_sleep(st, self.probes.now())
            self.manage(st)
            self.reconcile(st)
            self.manage_proxy(st)
            now = self.probes.now()
            last = st.get("last_fired")
            if not self.settings["enabled"]:
                did = "skipped: switched off in Settings"
            elif isinstance(last, (int, float)) and now - last < self.settings["interval_minutes"] * 60 - INTERVAL_TOLERANCE:
                did = "skipped: interval"
                log.info("firing skipped: interval (%d s since the last firing, setting %s min)", now - last,
                         self.settings["interval_minutes"])
            else:
                try:
                    did = self.start(st)
                except Held as e:
                    log.warning("no Worker started: held: %s", e)
                    did = f"held: {e}"
                except Skip as e:
                    log.warning("firing skipped: %s", e)
                    did = f"skipped: {e}"
        finally:
            # every path that ran past the interval check counts as a firing, an aborted one included: otherwise the same
            # gap is detected as a "sleep" again on every later firing (issue 143)
            if did != "skipped: interval":
                st["last_fired"] = self.probes.now()
            save_state(self.state_path, st)
        line = self.heartbeat(st, did, slept)
        self.plog.heartbeat(line)
        log.info(line)
        return did

    def heartbeat(self, st, did, slept=None):
        """3g: the time, the Workers running, what the firing did, the host and container readings."""
        p = self.probes.heartbeat_parts()
        running = ", ".join(f"{u}={w['task_id']}" for u, w in sorted(st["workers"].items())) or "none"
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.probes.now()))
        if slept is not None:
            did = f"{did} | slept {slept} minutes"
        return (f"{t} | running: {running} | {did} | memory pressure {p.get('memory_pressure', '?')}"
                f" | free disk {p.get('free_disk', '?')} | load {p.get('load', '?')} | power {p.get('power', '?')}"
                f" | container data {p.get('data_size', '?')} | proxy {'running' if self.probes.proxy_listening() else 'off'} | container {p.get('container', 'not reported')}")


# --- default probes (the host side; not exercised by the unit tests) ------------------------------------------
def host_readings(volume, runtime, host=None):
    """3g: memory pressure level, free disk, load average, power source, container data folder size, stats."""
    host = host or default_probes()
    size = _cmd("du", "-sk", runtime.cfg.data_folder)
    return {
        "memory_pressure": host.memory_pressure(),
        "free_disk": f"{shutil.disk_usage(volume).free / GIB:.1f} GiB",
        "load": " ".join(f"{x:.2f}" for x in os.getloadavg()),
        "power": host.power_source(),
        "data_size": f"{int(size.split()[0]) / 1024:.0f} MiB" if size else "unknown",
        "container": _stats_line(runtime),
    }


def resource_readings(runtime, host=None):
    """PC-95: the readings `admission` weighs besides the free disk: memory pressure, swap in use, free memory, the container's memory."""
    host = host or default_probes()
    return {"pressure": host.memory_pressure(), "swap": host.swap_used(), "free_memory": host.free_memory(), "container": runtime.memory()}


def _stats_line(runtime):
    try:
        raw = runtime.stats()
    except Exception:
        return "not reported"
    return " ".join(raw.split())[:300] if raw else "not reported"


def read_proxy_last(path):
    """The time (epoch seconds) of each Worker label's last request, from the `ts` of the proxy's JSONL usage file; {} if there is no file."""
    last = {}
    try:
        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    label, t = rec.get("worker"), dt.datetime.fromisoformat(rec["ts"]).timestamp()
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
                if label and t > last.get(label, 0):
                    last[label] = t
    except OSError:
        pass
    return last


def commits_ahead(repo, task_id):
    p = subprocess.run(["git", "-C", repo, "rev-list", "--count", f"main..worker/{task_id}"],
                       capture_output=True, text=True, timeout=60)
    if p.returncode:
        raise ValueError(p.stderr.strip())
    return int(p.stdout.strip())


def transcript_mtime(runtime, user, agent=None):
    agent = agent or ClaudeCode()
    code = ("import glob,os;print(max([os.path.getmtime(f) for f in "
            f"glob.glob('{agent.activity_glob(user)}',recursive=True)] or [0]))")
    try:
        rc, out = runtime.exec(user, "python3", "-c", code)
        return float(out.splitlines()[-1]) or None if rc == 0 and out else None
    except (rt.ContainerError, ValueError):
        return None


def proxy_listening(sock):
    s = socketlib.socket(socketlib.AF_UNIX)
    s.settimeout(3)
    try:
        s.connect(sock)
        return True
    except OSError:
        return False
    finally:
        s.close()


def start_proxy(sock, token_file, usage_file, image_workers, host=None):
    """Start the proxy detached from the firing, kept awake by the host helper; returns its pid (also its process group)."""
    host = host or default_probes()
    cmd = [*host.keep_awake_prefix(), sys.executable, "-m", "dispatcher.proxy", "--socket", sock, "--token-file", token_file,
           "--usage-file", usage_file]
    for i in range(1, image_workers + 1):
        cmd += ["--worker", f"worker{i}={rt.dummy_token(f'worker{i}')}"]
    log.info("starting the proxy on %s", sock)
    pid = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True).pid
    for _ in range(20):
        time.sleep(0.5)
        if proxy_listening(sock):
            return pid
    raise Skip("the proxy did not start listening")


def stop_proxy(pid, sock):
    """Terminate the proxy's process group, then check that the socket no longer listens."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(20):
        if not proxy_listening(sock):
            return True
        time.sleep(0.5)
    return False


def ensure_proxy(sock, token_file, usage_file, image_workers):
    """3k: start the proxy on its host socket if it is not listening."""
    if not proxy_listening(sock):
        start_proxy(sock, token_file, usage_file, image_workers)


def _run(args, cwd, timeout):
    """Run a command with a time limit; its stdout, or a RuntimeError carrying the stderr."""
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f"{args[0]} {args[1] if len(args) > 1 else ''} failed ({p.returncode}): {p.stderr.strip()[:300]}")
    return p.stdout


def make_gh(tool, timeout):
    return lambda args: _run([tool, *args], None, timeout)


def make_git(tool, timeout):
    return lambda args, cwd: _run([tool, *args], cwd, timeout)


def build_tasks(s, repo, repo_dir, gh, git):
    """The task source and the project table: ProjectsTasks over the projects of the settings when more than one is listed; else the one
    project of the command line through GitHubTasks (today's path). -> (tasks, {short: clone} or None, tuple of the `first` projects)."""
    from dispatcher import tasks as T
    listed = s.get("projects") or []
    if len(listed) > 1:
        first = tuple(p["short"] for p in listed if p.get("first"))
        log.info("one queue for %d projects: %s", len(listed), ", ".join(p["short"] for p in listed))
        return (T.ProjectsTasks([{k: p[k] for k in ("short", "repo", "repo_dir")} for p in listed], gh, git, first=first),
                {p["short"]: p["repo_dir"] for p in listed}, first)
    if len(listed) == 1:
        repo, repo_dir = listed[0]["repo"], listed[0]["repo_dir"]
    return T.GitHubTasks(repo, gh, git, repo_dir), None, ()


def main(argv=None):
    ap = argparse.ArgumentParser(description="One firing of the Dispatcher.")
    ap.add_argument("--data-root", help="the shared processing-data root of one queue for all projects; every folder and file flag below that is not given is taken from it")
    ap.add_argument("--settings", help="the settings JSON file (6a)")
    ap.add_argument("--state", help="the Dispatcher's state file (running Workers)")
    ap.add_argument("--area", help="the Workers' area (host folder)")
    ap.add_argument("--socket", help="the proxy's host socket")
    ap.add_argument("--token-file", required=True, help="the proxy's credential file")
    ap.add_argument("--usage-file", help="the proxy's JSONL usage file")
    ap.add_argument("--relay-path", default="/opt/relay.py", help="the relay script inside the image")
    ap.add_argument("--container-tool", default="container")
    ap.add_argument("--container-name", default="cockpit-workers", help="the Workers' container (a test run must use a name of its own)")
    ap.add_argument("--image", default="cockpit-worker:latest", help="the Workers' image (the test runs a stub image)")
    ap.add_argument("--repo", help="the task repository, owner/name (not needed when the settings list the projects)")
    ap.add_argument("--repo-dir", help="the task repository's clone on the host (not needed when the settings list the projects)")
    ap.add_argument("--data-dir", help="the processing log's data folder (outside any git repository)")
    ap.add_argument("--synopsis-dir", help="the projects' session synopses, <project>.md (default with --data-root: its synopsis folder; else none is copied)")
    ap.add_argument("--gh", default="gh", help="the GitHub CLI")
    ap.add_argument("--git", default="git", help="git")
    ap.add_argument("--call-timeout", type=int, default=60, help="seconds a gh or git call may take")
    a = ap.parse_args(argv)
    if a.data_root:
        from dispatcher import launchd
        root = launchd.queue_paths(a.data_root)
        for flag, key in (("settings", "settings"), ("state", "state"), ("area", "area"), ("socket", "socket"), ("usage_file", "usage"), ("data_dir", "processing")):
            if getattr(a, flag) is None:
                setattr(a, flag, str(root[key]))
        if a.synopsis_dir is None:
            a.synopsis_dir = os.path.join(str(root["dir"]), "synopsis")
    missing = [f"--{f.replace('_', '-')}" for f in ("settings", "state", "area", "socket", "usage_file", "data_dir") if getattr(a, f) is None]
    if missing:
        ap.error(f"required without --data-root: {', '.join(missing)}")
    os.makedirs(a.area, exist_ok=True)  # the Workers' area is the Dispatcher's own folder: a first firing finds none
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    from dispatcher.plog import ProcessingLog

    s = load_settings(a.settings)
    if not s.get("projects") and not (a.repo and a.repo_dir):
        ap.error("--repo and --repo-dir are required when the settings do not list the projects")
    cfg = rt.Config(area=a.area, socket=a.socket, container=a.container_tool, name=a.container_name, image=a.image, runaway_minutes=s["runaway_minutes"],
                    synopsis_dir=a.synopsis_dir)
    runtime = rt.Runtime(cfg)
    probes = Probes(
        free_disk=lambda: shutil.disk_usage(a.area).free,
        proxy_last=lambda: read_proxy_last(a.usage_file),
        transcript_mtime=lambda user: transcript_mtime(runtime, user),
        ensure_proxy=lambda: ensure_proxy(a.socket, a.token_file, a.usage_file, cfg.image_workers),
        proxy_listening=lambda: proxy_listening(a.socket),
        start_proxy=lambda: start_proxy(a.socket, a.token_file, a.usage_file, cfg.image_workers),
        stop_proxy=lambda pid: stop_proxy(pid, a.socket),
        commits_ahead=commits_ahead,
        heartbeat_parts=lambda: host_readings(a.area, runtime),
        resources=lambda: resource_readings(runtime))
    tasks, projects, first = build_tasks(s, a.repo, a.repo_dir, make_gh(a.gh, a.call_timeout), make_git(a.git, a.call_timeout))
    project_of = None
    if projects:
        from dispatcher.tasks import project_of   # PC-67 adapter; not imported on the single-project path
    multi = dict(projects=projects, first=first) if projects else {}   # the single-project call stays exactly as it was
    Dispatcher(s, a.state, runtime, tasks, ProcessingLog(a.data_dir, **({"project_of": project_of} if projects else {})), probes,
               relay_path=a.relay_path, repo_dir=a.repo_dir or "", **multi).fire()


if __name__ == "__main__":
    main()
