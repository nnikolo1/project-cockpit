"""The Workers' container, folders and launch (spec docs/specs/dispatcher-and-workers.md: 3p, 4a-4e, 4i-4s).

Every call to the container tool goes through one injectable function, `run(args, timeout)`, which returns
`(returncode, output)` or raises `subprocess.TimeoutExpired`; a timeout surfaces as `ContainerTimeout`, so the
Dispatcher can skip the firing and record the reason (3p). The proxy and the relay (4f-4h) are not here.
"""
import json
import logging
import os
import re
import signal
import subprocess
from dataclasses import dataclass

log = logging.getLogger("dispatcher.runtime")

CALL_TIMEOUT = 60        # seconds for any single call to the container tool (3p)
START_TIMEOUT = 300      # first start of the container can pull/unpack the image
BUILD_TIMEOUT = 1800
TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
OUTCOMES = ("done", "gave_up")
INSTRUCTION = (  # 4r
    "Read {inbox}/task.md. Work in your own clone ({clone}). Write {outbox}/started first. "
    "At the end write {outbox}/goodbye.json (outcome, summary, branch, tokens) and {outbox}/result.bundle "
    "(a git bundle of your branch).")
# 4i: Claude Code's non-essential traffic off; skipWebFetchPreflight is set in the image's settings.json
SWITCHES = {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
    "ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_ARTIFACT": "1",
    "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL": "1",
}
EGRESS_PROBE = (  # prints BLOCKED only if the connection fails; any other outcome counts as a leak or an error
    "import socket,sys\n"
    "try:\n socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=5).close(); print('OPEN')\n"
    "except OSError:\n print('BLOCKED')\n")


class ContainerError(Exception):
    """A call to the container tool failed."""


class ContainerTimeout(ContainerError):
    """A call to the container tool exceeded its time limit (3p): skip the firing, record the reason."""


@dataclass
class Config:
    area: str                           # host folder: the Workers' area, mounted at /area
    socket: str                         # host Unix socket of the proxy (< 104 bytes), mounted at /run/proxy.sock
    container: str = "container"        # path of the tool
    name: str = "cockpit-workers"
    image: str = "cockpit-worker:latest"
    memory: str = "4G"                  # 4c: 4-6 GB
    cpus: str = "2"
    image_workers: int = 6              # number of workerN users in the image (4a); keep equal to image/Containerfile
    relay_port: int = 8089              # where the relay of 4f listens inside the container
    data_folder: str = os.path.expanduser("~/Library/Application Support/com.apple.container")  # size shown in the heartbeat
    runaway_minutes: int = 40           # 3i; the in-guest backstop is twice this (4e)


def default_run(args, timeout):
    """Run the tool; on timeout subprocess kills the child and raises TimeoutExpired."""
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


def worker_user(cfg, n):
    """The unprivileged in-image user of Worker n (4a, 4j)."""
    if not 1 <= n <= cfg.image_workers:
        raise ValueError(f"worker {n} is outside 1..{cfg.image_workers} (the users defined in the image)")
    return f"worker{n}"


def clamp_workers(cfg, requested):
    """4a: the Settings value, capped at the number of workerN users in the image."""
    n = max(1, min(int(requested), cfg.image_workers))
    if n != requested:
        log.warning("worker count %s adjusted to %d (image has %d users)", requested, n, cfg.image_workers)
    return n


def dummy_token(user):
    """The per-Worker dummy token (4i, 4g): not a secret, it only tells the proxy the Workers apart."""
    return f"dummy-{user}"


def check_task_id(task_id):
    if not isinstance(task_id, str) or not TASK_ID.match(task_id):
        raise ValueError(f"unusable task id {task_id!r}")
    return task_id


class Runtime:
    def __init__(self, cfg, run=default_run, spawn=subprocess.Popen):
        self.cfg, self._run, self._spawn = cfg, run, spawn

    # --- the container tool ------------------------------------------------------------------------------
    def call(self, *args, timeout=CALL_TIMEOUT):
        cmd = [self.cfg.container, *args]
        log.debug("container call: %s", " ".join(cmd))
        try:
            rc, out = self._run(cmd, timeout)
        except subprocess.TimeoutExpired:
            log.error("container call timed out after %ss: %s", timeout, " ".join(cmd))
            raise ContainerTimeout(f"`container {args[0]}` exceeded {timeout}s")
        except OSError as e:
            raise ContainerError(f"cannot run the container tool: {e}")
        return rc, out

    def exec(self, user, *cmd, env=None, workdir=None, timeout=CALL_TIMEOUT):
        opts = ["--user", user]
        if workdir:
            opts += ["--workdir", workdir]
        for k, v in (env or {}).items():
            opts += ["--env", f"{k}={v}"]
        return self.call("exec", *opts, self.cfg.name, *cmd, timeout=timeout)

    # --- the container (4b, 4c, 3k) ----------------------------------------------------------------------
    def state(self):
        """'running', another status string, or None when the container does not exist."""
        rc, out = self.call("list", "--all", "--format", "json")
        if rc:
            raise ContainerError(f"container list failed: {out}")
        try:
            items = json.loads(out or "[]")
        except ValueError:
            raise ContainerError(f"container list returned unusable output: {out[:200]}")
        for it in items:
            if self.cfg.name in (it.get("configuration", {}).get("id"), it.get("id")):
                return str(it.get("status", "unknown"))
        return None

    def run_args(self):
        c = self.cfg
        return ["run", "-d", "--name", c.name, "--init", "--network", "none", "--memory", c.memory,
                "--cpus", c.cpus, "-v", f"{c.area}:/area", "-v", f"{c.socket}:/run/proxy.sock",
                c.image, "tail", "-f", "/dev/null"]

    def ensure_container(self):
        """Start the one long-lived container if it is not running; raises ContainerError if it cannot."""
        st = self.state()
        if st == "running":
            return
        if st is None:
            log.info("creating container %s from %s", self.cfg.name, self.cfg.image)
            rc, out = self.call(*self.run_args(), timeout=START_TIMEOUT)
        else:
            log.info("starting container %s (was %s)", self.cfg.name, st)
            rc, out = self.call("start", self.cfg.name, timeout=START_TIMEOUT)
        if rc:
            raise ContainerError(f"container did not start: {out}")

    def stats(self):
        """Raw JSON text of the container's resource use (3g), or None if the tool cannot report it."""
        try:
            rc, out = self.call("stats", "--format", "json", "--no-stream", self.cfg.name)
        except ContainerError:
            return None
        return out if rc == 0 else None

    def build_image(self, context):
        """Build the image from the Containerfile in `context` (see image/Containerfile)."""
        rc, out = self.call("build", "-t", self.cfg.image, "-f", os.path.join(context, "Containerfile"), context,
                            timeout=BUILD_TIMEOUT)
        if rc:
            raise ContainerError(f"image build failed: {out[-500:]}")

    # --- egress check (4s) -------------------------------------------------------------------------------
    def egress_blocked(self, user, host="1.1.1.1", port=443):
        """True only if the probe, run as the Worker's user, positively reports the connection failed."""
        rc, out = self.exec(user, "python3", "-c", EGRESS_PROBE, host, str(port), timeout=30)
        blocked = rc == 0 and out.strip().endswith("BLOCKED")
        log.log(logging.INFO if blocked else logging.ERROR, "egress check as %s to %s:%s: rc=%s %s",
                user, host, port, rc, out[:200])
        return blocked

    # --- the per-task folder (4q, 3d) --------------------------------------------------------------------
    def task_dir(self, task_id):
        return os.path.join(self.cfg.area, check_task_id(task_id))

    def prepare_task(self, task_id, repo, task_text):
        """Create <area>/<task-id>/{inbox,outbox}, write inbox/main.bundle (current main of `repo`) and task.md."""
        base = self.task_dir(task_id)
        inbox, outbox = os.path.join(base, "inbox"), os.path.join(base, "outbox")
        os.makedirs(inbox)  # FileExistsError if the folder is taken: ids never overwrite each other
        os.makedirs(outbox)
        os.chmod(outbox, 0o777)  # bind mounts show up root-owned in the guest; workerN must be able to write
        p = subprocess.run(["git", "-C", repo, "bundle", "create", os.path.join(inbox, "main.bundle"), "main"],
                           capture_output=True, text=True, timeout=CALL_TIMEOUT)
        if p.returncode:
            raise ContainerError(f"git bundle of main failed: {p.stderr.strip()}")
        with open(os.path.join(inbox, "task.md"), "w") as f:
            f.write(task_text)
        log.info("task folder ready: %s", base)
        return base

    def read_outbox(self, task_id):
        """(started, note, reason): `note` is the parsed good-bye note, or None with `reason` 'no note' or
        'unusable note' (5b)."""
        out = os.path.join(self.task_dir(task_id), "outbox")
        started = os.path.exists(os.path.join(out, "started"))
        path = os.path.join(out, "goodbye.json")
        if not os.path.exists(path):
            return started, None, "no note"
        try:
            with open(path) as f:
                note = json.load(f)
            ok = (isinstance(note, dict) and note.get("outcome") in OUTCOMES
                  and isinstance(note.get("summary"), str) and isinstance(note.get("branch"), str)
                  and note["branch"] and isinstance(note.get("tokens"), (int, dict))
                  and not isinstance(note["tokens"], bool))
        except (OSError, ValueError):
            ok = False
        if not ok:
            log.warning("unusable good-bye note: %s", path)
            return started, None, "unusable note"
        return started, note, None

    def bundle_path(self, task_id):
        return os.path.join(self.task_dir(task_id), "outbox", "result.bundle")

    # --- launching a Worker (4e, 4i, 4k, 4l, 4r) ---------------------------------------------------------
    def clone_main(self, user, task_id):
        """Clone main.bundle into <task-id>/clone on the container's own disk, no hardlinks (4k, 4l)."""
        check_task_id(task_id)
        home = f"/home/{user}/{task_id}"
        rc, out = self.exec(user, "git", "clone", "--no-hardlinks", f"/area/{task_id}/inbox/main.bundle",
                            f"{home}/clone", timeout=120)
        if rc:
            raise ContainerError(f"clone for {task_id} failed: {out[-300:]}")
        return f"{home}/clone"

    def launch(self, user, task_id):
        """Start the Worker; returns the host-side process handle (killing it ends the guest process, which
        the Dispatcher uses for 3i/3j). The in-guest `timeout` is the backstop of 4e."""
        clone = self.clone_main(user, task_id)
        base = f"/area/{task_id}"
        env = dict(SWITCHES, ANTHROPIC_BASE_URL=f"http://127.0.0.1:{self.cfg.relay_port}",
                   ANTHROPIC_AUTH_TOKEN=dummy_token(user), CLAUDE_CONFIG_DIR=f"/home/{user}/.claude",
                   HOME=f"/home/{user}")
        prompt = INSTRUCTION.format(inbox=f"{base}/inbox", outbox=f"{base}/outbox", clone=clone)
        backstop = 2 * self.cfg.runaway_minutes * 60
        cmd = [self.cfg.container, "exec", "--user", user, "--workdir", clone]
        for k, v in env.items():
            cmd += ["--env", f"{k}={v}"]
        cmd += [self.cfg.name, "timeout", str(backstop), "claude", "-p", prompt]
        log.info("launching %s for task %s in %s", user, task_id, clone)
        return self._spawn(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # --- the Worker's host-side process (3i/3j): whether it has ended, and kill ---------------------------
    def alive(self, pid):
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

    def kill(self, pid):
        os.kill(pid, signal.SIGKILL)

    def ensure_relay(self, relay_path):
        """3k: start the relay inside the container if its port does not answer."""
        port = self.cfg.relay_port
        probe = ("import socket;socket.create_connection(('127.0.0.1',%d),timeout=2).close()" % port)
        rc, _ = self.call("exec", self.cfg.name, "python3", "-c", probe)
        if rc == 0:
            return
        log.info("starting the relay in the container on port %d", port)
        rc, out = self.call("exec", "-d", self.cfg.name, "python3", relay_path, "--port", str(port),
                            "--socket", "/run/proxy.sock")
        if rc:
            raise ContainerError(f"relay did not start: {out}")
