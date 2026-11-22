"""I/O adapters of the boot-filling job. Each is a small class behind an interface that job.py uses and tests fake:
Hosting (issues), Quota (feed + refresh), Idle (transcripts), Workers (headless sessions), Judge (tie-break), Notifier.
Nothing here stores a token: gh and the assistant CLI run as the logged-in user."""
import glob
import json
import logging
import os
import pathlib
import subprocess
import sys
import time
from logging.handlers import TimedRotatingFileHandler

from . import core

log = logging.getLogger("bootfill")


def setup_logging(data_dir):
    """Two rotated logs (daily, 90 days): poll.log (one line per poll) and job.log (everything else)."""
    d = pathlib.Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for name, file in (("bootfill.poll", "poll.log"), ("bootfill", "job.log")):
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        if not any(isinstance(h, TimedRotatingFileHandler) for h in lg.handlers):
            h = TimedRotatingFileHandler(d / file, when="midnight", backupCount=90)
            h.setFormatter(fmt)
            lg.addHandler(h)
    logging.getLogger("bootfill.poll").propagate = False


def read_json(path, default):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except FileNotFoundError:
        return default


def write_json(path, obj):
    p = pathlib.Path(path)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True))
    os.replace(tmp, p)


def append_jsonl(path, rec):
    with open(path, "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def read_jsonl(path):
    try:
        return [json.loads(l) for l in pathlib.Path(path).read_text().splitlines() if l.strip()]
    except FileNotFoundError:
        return []


def run(cmd, timeout=300, cwd=None):
    """subprocess wrapper; raises RuntimeError with the stderr tail so that callers log a class and a reason."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{cmd[0]} timed out") from None
    if r.returncode:
        raise RuntimeError((r.stderr.strip() or f"{cmd[0]} exited {r.returncode}")[-300:])
    return r.stdout


class Quota:
    def __init__(self, feed_path, refresh_cmd, cwd=None):
        self.feed_path, self.refresh_cmd, self.cwd = feed_path, refresh_cmd, cwd  # cwd: the job's own headless folder, so its transcripts are recognisable

    def _load(self):
        """-> (parsed feed, observed epoch, age in seconds). The observation time is the status line's own `observed_at` when present,
        else the file's modification time; the age is measured from it."""
        p = pathlib.Path(self.feed_path).expanduser()
        raw = json.loads(p.read_text())
        obs = core.feed_observed_at(raw)
        if obs is None:
            obs = p.stat().st_mtime
        return core.parse_feed(raw), obs, max(time.time() - obs, 0.0)

    def peek(self):
        """The feed as the status line left it (no refresh call): -> (parsed quota, age in seconds). A window may be None (missing)."""
        parsed, _, age = self._load()
        return parsed, age

    def read(self):
        """One minimal headless call tries to refresh the status-line feed (it is stale while no interactive session runs), then the feed is
        read. `self.advanced` says whether the feed's observation time really moved after the call: the headless call usually does NOT
        update it, and the job must not claim a refresh that did not happen (ADR 0018)."""
        before = None
        try:
            before = self._load()[1]
        except (OSError, ValueError):
            pass
        if self.refresh_cmd:
            run(self.refresh_cmd, timeout=120, cwd=self.cwd)
        parsed, obs, self.last_age = self._load()
        self.advanced = bool(self.refresh_cmd) and (before is None or obs > before)
        return parsed


class Idle:
    """Newest transcript write by anyone but the job: paths matching `exclude` (fnmatch; the job's own headless folder and worker worktrees) are ignored."""

    def __init__(self, transcript_glob, exclude=()):
        self.glob, self.exclude = transcript_glob, list(exclude)

    def last_write(self):
        files = glob.glob(os.path.expanduser(self.glob), recursive=True)
        return max((os.path.getmtime(f) for f in files if not core.transcript_excluded(f, self.exclude)), default=None)


class Processes:
    """The process list (`ps`), parsed; raises RuntimeError when ps is unavailable so the caller can fall back and say so."""

    def __call__(self):
        return core.parse_ps(run(["ps", "-axo", "pid=,ppid=,command="], timeout=20))


class Gh:
    """Hosting client over the cockpit's own read and write path (cockpit.build_state, cockpit._edit, cockpit.execute):
    the release is exactly the cockpit's `released` label edit, plus a trail comment. It refuses to write unless gh runs as the Manager."""

    def __init__(self, projects):
        self.projects = {p["name"]: p for p in projects}

    def _cockpit(self):
        sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
        import cockpit
        return cockpit

    def tasks(self, project):
        C = self._cockpit()
        p = self.projects[project]
        S = C.build_state(dict(repo=p["repo"], manager=p["manager"], project=p.get("board"), git=None, modules=p.get("modules"), phases=None, code=None, name=project))
        for t in S["tasks"]:
            t["url_number"] = t["number"]
        return S["tasks"]

    def labels(self, project, number):
        C = self._cockpit()
        out = C.run_gh(["issue", "view", str(number), "-R", self.projects[project]["repo"], "--json", "labels", "--jq", "[.labels[].name]"])
        return json.loads(out)

    def release(self, project, task, comment):
        C = self._cockpit()
        p = self.projects[project]
        if C.gh_user() != p["manager"]:
            raise PermissionError(f"gh is logged in as {C.gh_user()}, not the Manager {p['manager']}")
        for lab in ("released", "cancelled"):  # never touch a task that already carries the label: _edit would unlabel and re-add it (ADR 0019)
            if lab in (task.get("labels") or []):
                raise RuntimeError(f"refused: {task['key']} already carries the {lab} label")
        ops = C._edit(p["repo"], task, ["released"], []) + [["issue", "comment", str(task["number"]), "-R", p["repo"], "--body", comment]]
        C.execute(ops)

    def assignees(self, project, number):
        """Assignee logins of the issue: the cockpit's signal for the stage `in progress`. Read only; the job never assigns or unassigns."""
        out = self._cockpit().run_gh(["issue", "view", str(number), "-R", self.projects[project]["repo"], "--json", "assignees", "--jq", "[.assignees[].login]"])
        return json.loads(out or "[]")

    def find_pr(self, project, branch):
        """Number of the newest pull request (any state) from `branch`, or None."""
        out = self._cockpit().run_gh(["pr", "list", "-R", self.projects[project]["repo"], "--head", branch, "--state", "all", "--json", "number", "--limit", "1"])
        rows = json.loads(out or "[]")
        return rows[0]["number"] if rows else None

    def comment(self, project, number, body):
        self._cockpit().run_gh(["issue", "comment", str(number), "-R", self.projects[project]["repo"], "--body", body])


class Judge:
    """Tie-break by one short headless model call; titles and keys only."""

    def __init__(self, cmd, cwd=None):
        self.cmd, self.cwd = cmd, cwd

    def __call__(self, tasks, running=None):
        """running=None: tie-break by value. running given: order `tasks` by how unlikely they are to get in the way of `running`."""
        prompt = core.judge_prompt(tasks) if running is None else core.conflict_prompt(running, tasks)
        out = run([x.replace("{prompt}", prompt) for x in self.cmd], timeout=180, cwd=self.cwd)
        return core.parse_judgement(out, [t["key"] for t in tasks])


class AuthorityJudge:
    """Authority-need judgement by one short headless model call: title, labels, signals and a capped, redacted excerpt only."""

    def __init__(self, cmd, limit, cwd=None):
        self.cmd, self.limit, self.cwd = cmd, limit, cwd

    def __call__(self, task, signals):
        prompt = core.authority_prompt(task, signals, self.limit)
        return core.parse_authority(run([x.replace("{prompt}", prompt) for x in self.cmd], timeout=180, cwd=self.cwd))


class Notifier:
    def __init__(self, cmd):
        self.cmd = cmd  # e.g. ["osascript", "-e", 'display notification "{message}" with title "{title}"']

    def __call__(self, title, message):
        if not self.cmd:
            return
        safe = message.replace('"', "'").replace("\\", "")[:200]
        run([x.replace("{title}", title).replace("{message}", safe) for x in self.cmd], timeout=20)


class Workers:
    """Headless assistant sessions, one git worktree and branch per task. The session gets the worktree as its working directory,
    a tool allowlist from the config, and the gh login (the Manager); it is told to open a DRAFT pull request and never to merge.
    The exit code lands in a file next to the log, so a later poll can tell finished from unfinished without a live parent."""

    def __init__(self, cfg, data_dir, env=None, rules=None):
        """env: `worker_env` (None = the default role marker; a mapping replaces it); rules: `worker_rules` (None = the default block)."""
        self.cfg, self.dir = cfg, pathlib.Path(data_dir) / "workers"
        self.env = dict(core.DEFAULT_WORKER_ENV if env is None else env)
        self.rules = rules
        self.dir.mkdir(parents=True, exist_ok=True)

    def start(self, entry, project_cfg):
        wid = f"{entry['project']}-{entry['number']}-{int(time.time())}"
        branch = f"{self.cfg.get('branch_prefix', 'boot/')}{entry['number']}"
        repo_dir = pathlib.Path(project_cfg["dir"]).expanduser()
        wt = repo_dir.parent / f"{repo_dir.name}-wt-boot-{entry['number']}"
        if not wt.exists():
            run(["git", "-C", str(repo_dir), "worktree", "add", str(wt), "-b", branch], timeout=60)
        links = core.find_symlinks(wt)  # ADR 0020: no worker in a worktree with symbolic links; nothing is changed or removed
        if links:
            raise RuntimeError("symbolic link(s) in the worktree, worker NOT started: " + ", ".join(f"{n} -> {t}" for n, t in links[:5])
                               + f". To fix: remove the link(s) from {wt} (or the branch) by hand and requeue the task")
        prompt = core.worker_prompt(entry, project_cfg, self.rules)
        cmd = core.worker_command(self.cfg, prompt)
        rc = self.dir / f"{wid}.rc"
        sh = "{ " + " ".join(_q(x) for x in cmd) + f" ; }} > {_q(str(self.dir / (wid + '.out')))} 2>&1 ; echo $? > {_q(str(rc))}"
        p = subprocess.Popen(["sh", "-c", sh], cwd=wt, start_new_session=True, env={**os.environ, **self.env})
        return dict(id=wid, pid=p.pid, rc=str(rc))

    def poll(self, h):
        """None while running, else the exit code."""
        if os.path.exists(h["rc"]):
            return int(open(h["rc"]).read().strip() or 1)
        try:
            os.kill(h["pid"], 0)
            return None
        except OSError:
            return 1  # process gone without an exit file: counts as unfinished


def _q(s):
    return "'" + s.replace("'", "'\\''") + "'"


class KeepAwake:
    """Holds the machine awake for a while by starting macOS `caffeinate -i -t SECONDS` (idle sleep only: the display may still sleep).
    Each armed poll renews the hold, so the chain of polls sustains itself and ends by itself a few minutes after the job is paused,
    unloaded or switched to dry run. A closed lid or a flat battery still puts the machine to sleep (a limit of caffeinate)."""

    def __init__(self, command="caffeinate"):
        self.command = command

    def hold(self, seconds):
        subprocess.Popen([self.command, "-i", "-t", str(int(seconds))], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)

