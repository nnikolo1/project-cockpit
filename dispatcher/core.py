"""The Dispatcher's decision logic: one firing (spec docs/specs/dispatcher-and-workers.md: 3a-3p, 4n, 6a, 6b).

Deterministic code on the host. launchd starts one firing every 2 minutes (3c):

    python3 -m dispatcher.core --settings FILE --state FILE --area DIR --socket SOCK --usage-file FILE ...

A firing (1) reads the settings, (2) looks after the running Workers (WIP on `started`, results, runaway and
start checks), (3) starts at most one new Worker unless it must skip (disk, proxy/container/relay, egress),
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

from dispatcher import runtime as rt
from dispatcher.agent import ClaudeCode
from dispatcher.host import _cmd, default_probes

log = logging.getLogger("dispatcher.core")

GIB = 1024 ** 3
MIN_FREE = 10 * GIB                 # 3n
DEFAULTS = {                        # 6a: the spec's values, used when the file or a key is missing
    "enabled": True, "interval_minutes": 2, "retries": 0, "workers": 6,
    "runaway_minutes": 40, "idle_minutes": 20, "start_minutes": 1,
}


class Skip(Exception):
    """This firing starts nothing; the message is the recorded reason (3k, 3n, 3p, 4s)."""


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


# --- ranking (3a) --------------------------------------------------------------------------------------------
def rank_key(t):
    """P1 first (no label counts as P2), then the deadline, then what it unblocks, then must before nice."""
    return (t.priority, t.due or "9999-99-99", -t.unblocks, not t.must)


def filed_by_worker(task, worker_id):
    """2b: a finding is never worked on by the agent that filed it. A Worker's id is its user name (and the id of the task it works on, which is never the finding's own); an interactive
    session is bound by the policy text, not by this check."""
    return bool(task.filed_by) and worker_id is not None and task.filed_by == worker_id


def choose_task(tasks, active_ids, worker_id=None):
    """The best released task that is not blocked, not already being worked on and not filed by this Worker, or None."""
    ready = [t for t in tasks if not t.blocked and t.id not in active_ids and not filed_by_worker(t, worker_id)]
    return min(ready, key=rank_key) if ready else None


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

    # -- bookkeeping --
    def _fail(self, st, user, w, reason):
        tid = w["task_id"]
        tail = getattr(self.runtime, "worker_output_tail", lambda t: "")(tid)
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
            started, note, reason = self.runtime.read_outbox(w["task_id"])
            if started and not w["started"]:
                w["started"] = True
                self.tasks.set_state(w["task_id"], "wip")
                self.plog.event(w["task_id"], w["folder"], "wip")
            if not self.runtime.alive(w["pid"]):
                self._finish(st, user, w, note, reason)
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
            self.tasks.fetch_branch(tid, self.runtime.bundle_path(tid), f"worker/{tid}")
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
        """Starts at most one Worker; returns what the firing did. Raises Skip with the reason."""
        if self.probes.free_disk() < MIN_FREE:
            raise Skip(f"free disk below 10 GiB ({self.probes.free_disk() / GIB:.1f} GiB)")
        try:
            self.probes.ensure_proxy()
            self.runtime.ensure_container()
            self.runtime.ensure_relay(self.relay_path)
        except rt.ContainerTimeout as e:
            raise Skip(f"container call over its time limit: {e}")
        except (rt.ContainerError, Skip) as e:
            raise Skip(f"cannot start proxy, container or relay: {e}")
        n = rt.clamp_workers(self.runtime.cfg, self.settings["workers"])
        free = [f"worker{i}" for i in range(1, n + 1) if f"worker{i}" not in st["workers"]]
        if not free:
            return "nothing to do (all Workers busy)"
        active = {w["task_id"] for w in st["workers"].values()}
        released = self.tasks.released()
        for t in released:  # 3m: a folder in the area means the task was attempted before; never select it again
            if t.id not in active and os.path.exists(os.path.join(self.runtime.cfg.area, t.id)):
                log.info("task %s skipped: its folder already exists in the Workers' area", t.id)
                active.add(t.id)
        user = free[0]
        task = choose_task(released, active, user)
        if task is None:
            return "nothing to do (no released task)"
        try:
            if not self.runtime.egress_blocked(user):
                raise Skip(f"egress check failed for {user}")
            folder = self.runtime.prepare_task(task.id, self.repo_dir, f"# {task.title}\n\n{task.text}\n")
            try:
                proc = self.runtime.launch(user, task.id)
            except BaseException:  # no Worker started: the folder must not mark the task as attempted
                getattr(self.runtime, "discard_task", lambda t: None)(task.id)
                raise
        except rt.ContainerTimeout as e:
            raise Skip(f"container call over its time limit: {e}")
        except rt.ContainerError as e:
            raise Skip(f"container call failed: {e}")
        except (OSError, ValueError) as e:  # a problem of the Dispatcher, not a failure of the task: no state is set
            log.exception("task %s cannot be prepared", task.id)
            raise Skip(f"cannot prepare {task.id}: {e}")
        st["workers"][user] = {
            "task_id": task.id, "repo": self.repo_dir, "folder": folder, "pid": proc.pid, "launched": self.probes.now(),
            "started": False}
        self.plog.event(task.id, folder, "launched", worker=user)
        log.info("started %s for task %s (pid %s)", user, task.id, proc.pid)
        return f"started {user} on {task.id}"

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

    # -- the firing --
    def fire(self):
        st = load_state(self.state_path)
        slept = None
        try:
            slept = self._detect_sleep(st, self.probes.now())
            self.manage(st)
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
                except Skip as e:
                    log.warning("firing skipped: %s", e)
                    did = f"skipped: {e}"
            if did != "skipped: interval":
                st["last_fired"] = now
        finally:
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="One firing of the Dispatcher.")
    ap.add_argument("--settings", required=True, help="the settings JSON file (6a)")
    ap.add_argument("--state", required=True, help="the Dispatcher's state file (running Workers)")
    ap.add_argument("--area", required=True, help="the Workers' area (host folder)")
    ap.add_argument("--socket", required=True, help="the proxy's host socket")
    ap.add_argument("--token-file", required=True, help="the proxy's credential file")
    ap.add_argument("--usage-file", required=True, help="the proxy's JSONL usage file")
    ap.add_argument("--relay-path", default="/opt/relay.py", help="the relay script inside the image")
    ap.add_argument("--container-tool", default="container")
    ap.add_argument("--image", default="cockpit-worker:latest", help="the Workers' image (the test runs a stub image)")
    ap.add_argument("--repo", required=True, help="the task repository, owner/name")
    ap.add_argument("--repo-dir", required=True, help="the task repository's clone on the host")
    ap.add_argument("--data-dir", required=True, help="the processing log's data folder (outside any git repository)")
    ap.add_argument("--gh", default="gh", help="the GitHub CLI")
    ap.add_argument("--git", default="git", help="git")
    ap.add_argument("--call-timeout", type=int, default=60, help="seconds a gh or git call may take")
    a = ap.parse_args(argv)
    os.makedirs(a.area, exist_ok=True)  # the Workers' area is the Dispatcher's own folder: a first firing finds none
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    from dispatcher.plog import ProcessingLog
    from dispatcher.tasks import GitHubTasks

    s = load_settings(a.settings)
    cfg = rt.Config(area=a.area, socket=a.socket, container=a.container_tool, image=a.image, runaway_minutes=s["runaway_minutes"])
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
        heartbeat_parts=lambda: host_readings(a.area, runtime))
    tasks = GitHubTasks(a.repo, make_gh(a.gh, a.call_timeout), make_git(a.git, a.call_timeout), a.repo_dir)
    Dispatcher(s, a.state, runtime, tasks, ProcessingLog(a.data_dir), probes, relay_path=a.relay_path,
               repo_dir=a.repo_dir).fire()


if __name__ == "__main__":
    main()
