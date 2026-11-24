"""The Dispatcher's decision logic: one firing (spec docs/specs/dispatcher-and-workers.md: 3a-3p, 4n, 6a, 6b).

Deterministic code on the host. launchd starts one firing every 2 minutes (3c):

    python3 -m dispatcher.core --settings FILE --state FILE --area DIR --socket SOCK --usage-file FILE ...

A firing (1) reads the settings, (2) looks after the running Workers (WIP on `started`, results, runaway and
start checks), (3) starts at most one new Worker unless it must skip (disk, proxy/container/relay, egress),
(4) writes the heartbeat line. The tasks, the processing log, the container and every probe are injected.
"""
import argparse
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

log = logging.getLogger("dispatcher.core")

GIB = 1024 ** 3
MIN_FREE = 10 * GIB                 # 3n
START_LIMIT = 60                    # seconds to write `started` after launch (3j)
DEFAULTS = {                        # 6a: the spec's values, used when the file or a key is missing
    "enabled": True, "interval_minutes": 2, "retries": 0, "workers": 3,
    "runaway_minutes": 40, "idle_minutes": 10,
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


def choose_task(tasks, active_ids):
    """The best released task that is not blocked and not already being worked on, or None."""
    ready = [t for t in tasks if not t.blocked and t.id not in active_ids]
    return min(ready, key=rank_key) if ready else None


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


# --- one firing ----------------------------------------------------------------------------------------------
@dataclass
class Probes:
    """Everything a firing reads from the outside world; tests inject fakes."""
    now: object = time.time
    free_disk: object = None            # () -> bytes free on the data volume
    proxy_counts: object = None         # () -> {worker label: requests so far}; {} if the proxy cannot tell
    transcript_mtime: object = None     # (user) -> epoch seconds or None
    alive: object = None                # (pid) -> bool
    kill: object = None                 # (pid) -> None
    ensure_proxy: object = None         # () -> None; raises Skip/ContainerError if it cannot be started (3k)
    ensure_relay: object = None         # () -> None; same, inside the container (3k)
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

    # -- bookkeeping --
    def _fail(self, st, user, w, reason):
        tid = w["task_id"]
        log.error("task %s failed: %s", tid, reason)
        self.tasks.set_state(tid, "failed")
        self.plog.failure(tid, w["folder"], reason)
        st["workers"].pop(user, None)

    def _last_record(self, user, w):
        """3o: the proxy's last request for this Worker, else its transcript's mtime, else the launch time."""
        t = w.get("last_request") or self.probes.transcript_mtime(user)
        return max(t or 0, w["launched"])

    # -- the running Workers (3e, 3i, 3j, 3o, 4n) --
    def manage(self, st):
        now = self.probes.now()
        counts = self.probes.proxy_counts()
        for user, w in list(st["workers"].items()):
            n = counts.get(user)
            if n is not None:
                if n > w.get("req_count", 0):
                    w["last_request"] = now      # the usage file has no times: seen at this firing
                w["req_count"] = n
            started, note, reason = self.runtime.read_outbox(w["task_id"])
            if started and not w["started"]:
                w["started"] = True
                self.tasks.set_state(w["task_id"], "wip")
                self.plog.event(w["task_id"], w["folder"], "wip")
            if not self.probes.alive(w["pid"]):
                self._finish(st, user, w, note, reason)
                continue
            age = now - w["launched"]
            limit, idle = self.settings["runaway_minutes"], self.settings["idle_minutes"]
            if not w["started"] and age > START_LIMIT:
                why = "no start"
            elif age > limit * 60:
                why = f"over {limit} minutes"
            elif w["started"] and now - self._last_record(user, w) > idle * 60:
                why = "no activity"
            else:
                continue
            log.warning("killing %s (task %s, pid %s): %s", user, w["task_id"], w["pid"], why)
            self.probes.kill(w["pid"])
            self._fail(st, user, w, why)

    def _finish(self, st, user, w, note, reason):
        """3e: the process has ended; unmerged if outcome done and the bundle holds a commit, else failed."""
        tid = w["task_id"]
        if note is None:
            return self._fail(st, user, w, reason)
        if note["outcome"] != "done":
            return self._fail(st, user, w, f"outcome {note['outcome']}")
        try:
            self.tasks.fetch_branch(tid, self.runtime.bundle_path(tid), note["branch"])
            ahead = self.probes.commits_ahead(w["repo"], tid)
        except Exception as e:  # a missing or broken bundle must fail this task, not the firing
            log.exception("fetching the branch of %s failed", tid)
            return self._fail(st, user, w, f"unusable bundle: {e}")
        if ahead < 1:
            return self._fail(st, user, w, "no commit")
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
            self.probes.ensure_relay()
        except rt.ContainerTimeout as e:
            raise Skip(f"container call over its time limit: {e}")
        except (rt.ContainerError, Skip) as e:
            raise Skip(f"cannot start proxy, container or relay: {e}")
        n = rt.clamp_workers(self.runtime.cfg, self.settings["workers"])
        free = [f"worker{i}" for i in range(1, n + 1) if f"worker{i}" not in st["workers"]]
        if not free:
            return "nothing to do (all Workers busy)"
        task = choose_task(self.tasks.released(), {w["task_id"] for w in st["workers"].values()})
        if task is None:
            return "nothing to do (no released task)"
        user = free[0]
        try:
            if not self.runtime.egress_blocked(user):
                raise Skip(f"egress check failed for {user}")
            folder = self.runtime.prepare_task(task.id, task.repo, f"# {task.title}\n\n{task.text}\n")
            proc = self.runtime.launch(user, task.id)
        except rt.ContainerTimeout as e:
            raise Skip(f"container call over its time limit: {e}")
        except rt.ContainerError as e:
            raise Skip(f"container call failed: {e}")
        except (OSError, ValueError) as e:  # e.g. the task folder already exists: this task cannot start
            log.exception("task %s cannot be prepared", task.id)
            self.tasks.set_state(task.id, "failed")
            self.plog.failure(task.id, os.path.join(self.runtime.cfg.area, task.id), f"cannot prepare: {e}")
            return f"{task.id} failed (cannot prepare)"
        st["workers"][user] = {
            "task_id": task.id, "repo": task.repo, "folder": folder, "pid": proc.pid, "launched": self.probes.now(),
            "started": False, "req_count": self.probes.proxy_counts().get(user, 0)}
        self.plog.event(task.id, folder, "launched")
        log.info("started %s for task %s (pid %s)", user, task.id, proc.pid)
        return f"started {user} on {task.id}"

    # -- the firing --
    def fire(self):
        st = load_state(self.state_path)
        try:
            self.manage(st)
            if not self.settings["enabled"]:
                did = "skipped: switched off in Settings"
            else:
                try:
                    did = self.start(st)
                except Skip as e:
                    log.warning("firing skipped: %s", e)
                    did = f"skipped: {e}"
        finally:
            save_state(self.state_path, st)
        line = self.heartbeat(st, did)
        self.plog.heartbeat(line)
        log.info(line)
        return did

    def heartbeat(self, st, did):
        """3g: the time, the Workers running, what the firing did, the host and container readings."""
        p = self.probes.heartbeat_parts()
        running = ", ".join(f"{u}={w['task_id']}" for u, w in sorted(st["workers"].items())) or "none"
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.probes.now()))
        return (f"{t} | running: {running} | {did} | memory pressure {p.get('memory_pressure', '?')}"
                f" | free disk {p.get('free_disk', '?')} | load {p.get('load', '?')} | power {p.get('power', '?')}"
                f" | container data {p.get('data_size', '?')} | container {p.get('container', 'not reported')}")


# --- default probes (the host side; not exercised by the unit tests) ------------------------------------------
def _cmd(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def host_readings(volume, data_folder, runtime):
    """3g: memory pressure level, free disk, load average, power source, container data folder size, stats."""
    lvl = _cmd("sysctl", "-n", "kern.memorystatus_vm_pressure_level")
    batt = _cmd("pmset", "-g", "batt") or ""
    size = _cmd("du", "-sk", data_folder)
    return {
        "memory_pressure": {"1": "normal", "2": "warn", "4": "critical"}.get(lvl, lvl or "unknown"),
        "free_disk": f"{shutil.disk_usage(volume).free / GIB:.1f} GiB",
        "load": " ".join(f"{x:.2f}" for x in os.getloadavg()),
        "power": "AC" if "AC Power" in batt else ("battery" if "Battery Power" in batt else "unknown"),
        "data_size": f"{int(size.split()[0]) / 1024:.0f} MiB" if size else "unknown",
        "container": _stats_line(runtime),
    }


def _stats_line(runtime):
    try:
        raw = runtime.stats()
    except Exception:
        return "not reported"
    return " ".join(raw.split())[:300] if raw else "not reported"


def read_proxy_counts(path):
    """Requests per Worker label from the proxy's JSONL usage file; {} if there is no file."""
    counts = {}
    try:
        with open(path) as f:
            for line in f:
                try:
                    label = json.loads(line).get("worker")
                except ValueError:
                    continue
                if label:
                    counts[label] = counts.get(label, 0) + 1
    except OSError:
        pass
    return counts


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a child of this process that has ended is a zombie until reaped
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True


def commits_ahead(repo, task_id):
    p = subprocess.run(["git", "-C", repo, "rev-list", "--count", f"main..worker/{task_id}"],
                       capture_output=True, text=True, timeout=60)
    if p.returncode:
        raise ValueError(p.stderr.strip())
    return int(p.stdout.strip())


def transcript_mtime(runtime, user):
    code = ("import glob,os;print(max([os.path.getmtime(f) for f in "
            f"glob.glob('/home/{user}/.claude/projects/**/*.jsonl',recursive=True)] or [0]))")
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


def ensure_proxy(sock, token_file, usage_file, image_workers):
    """3k: start the proxy on its host socket if it is not listening."""
    if proxy_listening(sock):
        return
    cmd = [sys.executable, "-m", "dispatcher.proxy", "--socket", sock, "--token-file", token_file,
           "--usage-file", usage_file]
    for i in range(1, image_workers + 1):
        cmd += ["--worker", f"worker{i}={rt.dummy_token(f'worker{i}')}"]
    log.info("starting the proxy on %s", sock)
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(20):
        time.sleep(0.5)
        if proxy_listening(sock):
            return
    raise Skip("the proxy did not start listening")


def ensure_relay(runtime, relay_path):
    """3k: start the relay inside the container if its port does not answer."""
    port = runtime.cfg.relay_port
    probe = ("import socket;socket.create_connection(('127.0.0.1',%d),timeout=2).close()" % port)
    rc, _ = runtime.call("exec", runtime.cfg.name, "python3", "-c", probe)
    if rc == 0:
        return
    log.info("starting the relay in the container on port %d", port)
    rc, out = runtime.call("exec", "-d", runtime.cfg.name, "python3", relay_path, "--port", str(port),
                           "--socket", "/run/proxy.sock")
    if rc:
        raise rt.ContainerError(f"relay did not start: {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="One firing of the Dispatcher.")
    ap.add_argument("--settings", required=True, help="the settings JSON file (6a)")
    ap.add_argument("--state", required=True, help="the Dispatcher's state file (running Workers)")
    ap.add_argument("--area", required=True, help="the Workers' area (host folder)")
    ap.add_argument("--socket", required=True, help="the proxy's host socket")
    ap.add_argument("--token-file", required=True, help="the proxy's credential file")
    ap.add_argument("--usage-file", required=True, help="the proxy's JSONL usage file")
    ap.add_argument("--relay-path", default="/opt/relay.py", help="the relay script inside the image")
    ap.add_argument("--container-data", default=os.path.expanduser("~/Library/Application Support/com.apple.container"),
                    help="the container tool's data folder (size in the heartbeat)")
    ap.add_argument("--container-tool", default="container")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    from dispatcher.plog import ProcessingLog   # built by the other part; same signatures as the brief
    from dispatcher.tasks import GitHubTasks

    s = load_settings(a.settings)
    cfg = rt.Config(area=a.area, socket=a.socket, container=a.container_tool, runaway_minutes=s["runaway_minutes"])
    runtime = rt.Runtime(cfg)
    probes = Probes(
        free_disk=lambda: shutil.disk_usage(a.area).free,
        proxy_counts=lambda: read_proxy_counts(a.usage_file),
        transcript_mtime=lambda user: transcript_mtime(runtime, user),
        alive=pid_alive, kill=lambda pid: os.kill(pid, signal.SIGKILL),
        ensure_proxy=lambda: ensure_proxy(a.socket, a.token_file, a.usage_file, cfg.image_workers),
        ensure_relay=lambda: ensure_relay(runtime, a.relay_path),
        commits_ahead=commits_ahead,
        heartbeat_parts=lambda: host_readings(a.area, a.container_data, runtime))
    Dispatcher(s, a.state, runtime, GitHubTasks(), ProcessingLog(), probes).fire()


if __name__ == "__main__":
    main()
