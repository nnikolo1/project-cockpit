"""One command from "I have Python" to "the Cockpit runs for my projects", and its self-test (PC-100, finishing PC-97).

    python3 tools/install.py [--dry-run] [--self-test]

Interactive, in the owner's order (task PC-100, amendment 2026-11-25): (1) the latest Python on PATH, warned when it
is not the tested 3.14 (standing policy: never refused, unless older than the floor this code needs); (2) the GitHub
login (`gh auth status`) and the model credential file, reported found or not found by path and mode only: the file
is never read, copied or printed, and only its path is stored; (3) the parent folders of the owner's projects, whose
sub-folders with a GitHub remote are listed; (4) per project "install the Cockpit in this project?" (default no;
a yes also installs the pre-push hook that runs the merge gate, tools/pre_push.py);
(5) the viewer service, the daily job and the Dispatcher, each after a plain confirmation. Everything it would write
or run is listed as a plan and confirmed first; `--dry-run` prints the plan and stops. A second run finds what is
installed and offers only what is missing. It ends with the self-test: "ready" or the list of what is missing, each
with its fix. Steps whose tool does not exist yet print "planned: PC-nn" and are skipped, never faked.

Everything the steps touch (home folder, prompts, commands, PATH, OS, version, HTTP) comes through `Env`, so the
tests run the whole flow in a sandbox with recorders. Exit: 0 ready, 1 something missing, 2 stopped at a prerequisite.
"""
import argparse
import dataclasses
import json
import logging
import os
import pathlib
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dispatcher import launchd  # noqa: E402  (the Dispatcher's own job definitions, not a copy)

log = logging.getLogger("install")

TESTED = (3, 14)    # the owner's installed Python (standing policy "Supported platform"); follows it in the same change
FLOOR = (3, 10)     # older cannot even parse this repository
PORT = 8795         # cockpit.py's default
CONFIG = ".config/project-cockpit"          # relative to the home folder, as in docs/deployment.md
DATA = ".local/share/project-cockpit"
CRED_CANDIDATES = (f"{CONFIG}/model-token", f"{DATA}/model-token")  # where the owner keeps the `claude setup-token` output
DIR_CANDIDATES = ("src", "code", "projects", "Projects", "dev", "Developer", "git", "repos", "work")
VIEWER = "com.project-cockpit.viewer"
QUEUE_LABELS = [f"{launchd.PREFIX}.{kind}.{launchd.QUEUE}" for kind in ("quota", "dispatcher")]  # quota first
PLANNED = {"project_sync": ("PC-92", "tools/project_sync.py"), "daily job": ("PC-91", None)}
REMOTE = re.compile(r"github\.com[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?/*$")
FIX = {
    "git": "install git: `xcode-select --install` (macOS) or your package manager",
    "gh": "install the GitHub CLI: `brew install gh` (https://cli.github.com/)",
    "gh login": "log in as the Manager: `gh auth login`",
    "container": "install Apple's container tool (https://github.com/apple/container/releases), then `container system start`",
    "credential": "create the model credential: `claude setup-token`, save the printed token with `umask 077` to "
                  f"~/{CRED_CANDIDATES[0]}, then run again (or type its path when asked)",
}


def default_run(argv):
    """(exit code, stdout) of a command; a missing program is exit 127. Its output is never logged here."""
    try:
        r = subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=600)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def default_fetch(url):
    """True when the viewer answers at all (any HTTP status)."""
    try:
        urllib.request.urlopen(url, timeout=3)
        return True
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False


@dataclasses.dataclass
class Env:
    home: pathlib.Path
    ask: object = input
    run: object = default_run
    which: object = shutil.which
    fetch: object = default_fetch
    system: str = platform.system()
    version: tuple = tuple(sys.version_info[:2])
    path: str = os.environ.get("PATH", "")
    uid: int = os.getuid()
    root: pathlib.Path = ROOT

    def p(self, rel):
        return self.home / rel

    @property
    def projects_file(self):
        return self.p(f"{CONFIG}/projects.json")

    @property
    def install_file(self):
        return self.p(f"{CONFIG}/install.json")


def say(line):
    log.info("%s", line)


def ask(env, question, default=""):
    a = env.ask(f"{question} [{default}]: " if default else f"{question}: ").strip()
    return a or default


def yes(env, question, default=False):
    a = env.ask(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return a.startswith("y") if a else default


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_private(path, text):
    """Write a file the owner holds: mode 0600, folder created, replaced atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.chmod(0o600)
    tmp.replace(path)


def fmt(v):
    return ".".join(map(str, v))


# 1. Python

def find_python(env):
    """(version, path) of the latest python3.N on PATH; the running interpreter when none is newer."""
    best = (tuple(env.version), sys.executable)
    for d in filter(None, env.path.split(os.pathsep)):
        for f in sorted(pathlib.Path(d).glob("python3.*")) if pathlib.Path(d).is_dir() else ():
            m = re.fullmatch(r"python3\.(\d+)", f.name)
            if m and os.access(f, os.X_OK) and (3, int(m[1])) > best[0]:
                best = ((3, int(m[1])), str(f))
    return best


def check_python(env):
    """(python path or None, messages): never refused for another version, only below the floor."""
    v, exe = find_python(env)
    if v < FLOOR:
        return None, [f"Python {fmt(v)} is too old for this repository: install Python {fmt(TESTED)} "
                      "(https://www.python.org/downloads/ or `brew install python@3.14`), then run again"]
    return exe, [] if v == TESTED else [f"warning: Python {fmt(v)} ({exe}) is not the tested version {fmt(TESTED)}; using it anyway"]


# 2. Tools and credentials

def missing_tools(env):
    """[(what, fix)] of the tools and the GitHub login; the container tool only where the Dispatcher can run."""
    out = [(t, FIX[t]) for t in ("git", "gh") if not env.which(t)]
    if env.which("gh") and env.run(["gh", "auth", "status"])[0] != 0:
        out.append(("gh login", FIX["gh login"]))
    return out


def credential_state(path):
    """'found', 'not found' or 'not 0600': by stat only, the content is never opened."""
    if not path or not path.is_file():
        return "not found"
    return "found" if path.stat().st_mode & 0o077 == 0 else "not 0600"


def find_credential(env, saved):
    """The model credential's path, asked with the first existing usual location as default; (path, problem)."""
    cands = [pathlib.Path(saved)] if saved else []
    cands += [env.p(c) for c in CRED_CANDIDATES]
    default = next((c for c in cands if c.is_file()), cands[0])
    path = pathlib.Path(ask(env, "Model credential file (its path only is kept; the file stays where it is)", str(default))).expanduser()
    state = credential_state(path)
    say(f"model credential: {state} ({path})")
    if state == "not 0600":
        return path, ("model credential", f"make it private: `chmod 600 {path}`")
    return path, None if state == "found" else ("model credential", FIX["credential"])


# 3. Projects

def remote_of(repo_dir):
    """owner/name of the GitHub remote in the clone's .git/config (origin first), or None; no command is run."""
    try:
        text = (repo_dir / ".git" / "config").read_text()
    except OSError:
        return None
    found = {}
    for name, body in re.findall(r'\[remote "([^"]+)"\]([^\[]*)', text):
        m = re.search(r"^\s*url\s*=\s*(\S+)", body, re.M)
        r = m and REMOTE.search(m.group(1))
        if r:
            found[name] = f"{r[1]}/{r[2]}"
    return found.get("origin") or next(iter(found.values()), None)


def scan(parents):
    """[(folder, owner/name or None)] for every sub-folder of the parents that is a git clone, in name order."""
    out = []
    for parent in parents:
        if parent.is_dir():
            out += [(d, remote_of(d)) for d in sorted(parent.iterdir()) if (d / ".git").is_dir()]
    return out


def project_dirs(env, saved):
    """The parent folders, asked with the remembered ones or the usual places that exist as default."""
    default = saved or [str(env.p(c)) for c in DIR_CANDIDATES if env.p(c).is_dir()]
    a = ask(env, f"Where are your project folders? (one or more parent folders, separated by '{os.pathsep}')",
            os.pathsep.join(default))
    return [pathlib.Path(s.strip()).expanduser() for s in a.split(os.pathsep) if s.strip()]


def unique_name(repo, taken):
    name = repo.split("/")[1].lower()
    return name if name not in taken else repo.replace("/", "-").lower()


# 5. Services

def viewer_unit(env, python):
    """(path, content, status argv, load argvs, unload argvs) of the viewer service for this OS, or None."""
    args = [python, str(env.root / "cockpit.py"), "--projects", str(env.projects_file), "--token-file", str(env.p(f"{DATA}/token"))]
    logf = str(env.p(f"{DATA}/cockpit.log"))
    if env.system == "Darwin":
        path = env.p(f"Library/LaunchAgents/{VIEWER}.plist")
        body = plistlib.dumps(dict(Label=VIEWER, ProgramArguments=args, WorkingDirectory=str(env.root), RunAtLoad=True, KeepAlive=True,
                                   EnvironmentVariables={"PATH": launchd.JOB_PATH}, StandardOutPath=logf, StandardErrorPath=logf)).decode()
        dom = f"gui/{env.uid}"
        return path, body, ["launchctl", "print", f"{dom}/{VIEWER}"], [["launchctl", "bootstrap", dom, path]], [["launchctl", "bootout", f"{dom}/{VIEWER}"]]
    if env.system == "Linux":
        path = env.p(".config/systemd/user/project-cockpit.service")
        body = ("[Unit]\nDescription=Project cockpit\nAfter=network-online.target\n\n[Service]\n"
                f"ExecStart={' '.join(args)}\nWorkingDirectory={env.root}\nEnvironment=PATH=/usr/local/bin:/usr/bin:/bin\n"
                f"Restart=on-failure\nStandardOutput=append:{logf}\nStandardError=inherit\n\n[Install]\nWantedBy=default.target\n")
        return (path, body, ["systemctl", "--user", "is-active", "project-cockpit"],
                [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", "project-cockpit"]], [])
    return None


def queue_jobs(env, python, credential):
    """[(path, content, label)] of the quota collector and the Dispatcher of the one queue, quota first."""
    root = env.p(f"{DATA}/task-queue")
    out = root / "launchd"
    jobs = [launchd.quota_plist(None, cockpit_dir=env.root, python=python, queue_root=root),
            launchd.dispatcher_plist(None, None, None, str(credential), cockpit_dir=env.root, python=python, queue_root=root)]
    return [(out / f"{j['Label']}.plist", plistlib.dumps(j).decode(), j["Label"]) for j in jobs]


def loaded(env, label):
    return env.run(["launchctl", "print", f"gui/{env.uid}/{label}"])[0] == 0


def dispatcher_step(env, python, credential):
    """The step that writes and loads the one queue's two launchd jobs, or None when installed, declined or impossible here."""
    if env.system != "Darwin":
        return say("Dispatcher: needs macOS (launchd and Apple's container tool): skipped")
    if not env.which("container"):
        return say(f"Dispatcher: skipped, the container tool is missing: {FIX['container']}")
    try:
        jobs = queue_jobs(env, python, credential)
    except ValueError as e:  # the queue's socket path would be too long under this home folder
        return say(f"Dispatcher: skipped: {e}")
    todo = [j for j in jobs if not (j[0].is_file() and j[0].read_text() == j[1] and loaded(env, j[2]))]
    if not todo:
        return say("Dispatcher: already installed")
    if not yes(env, "Install the Dispatcher and its quota collector (launchd jobs of the one Task Queue)?", True):
        return None
    say("  by hand: the Workers' image (needs the Alpine `claude` binary): "
        "`container build --dns 1.1.1.1 -t cockpit-worker:latest -f dispatcher/image/Containerfile CONTEXT`")
    dom = f"gui/{env.uid}"
    runs = [a for p, _, label in todo for a in ([["launchctl", "bootout", f"{dom}/{label}"]] if loaded(env, label) else []) + [["launchctl", "bootstrap", dom, p]]]
    return Step("Dispatcher: write the two launchd jobs and load them, quota collector first",
                writes=[(p, c) for p, c, _ in todo], runs=runs, by_hand=[f"launchctl bootstrap {dom} {p}" for p, _, _ in todo])


# The plan

@dataclasses.dataclass
class Step:
    text: str
    writes: list = dataclasses.field(default_factory=list)   # [(path, content)]
    runs: list = dataclasses.field(default_factory=list)     # [argv]
    by_hand: list = dataclasses.field(default_factory=list)  # lines printed when a command fails


def execute(env, step):
    for path, content in step.writes:
        write_private(path, content)
    for argv in step.runs:
        rc, _ = env.run(argv)
        if rc != 0:
            say(f"failed ({rc}): {' '.join(map(str, argv))}")
            for line in step.by_hand or [" ".join(map(str, a)) for a in step.runs]:
                say(f"  run by hand: {line}")
            return False
    say(f"done: {step.text}")
    return True


def plan(env, python, credential, cfg):
    """Ask the per-project and per-service questions; return the steps for what is missing (nothing is done yet)."""
    steps, new = [], []
    pf = read_json(env.projects_file, {"projects": []})
    projects = pf.setdefault("projects", [])
    installed = {p.get("repo"): p for p in projects}
    say("Projects found:")
    found = scan(cfg["project_dirs"])
    for d, repo in found:
        say(f"  {d.name}: {repo or 'no GitHub remote, skipped'}" + (" (already installed)" if repo in installed else ""))
    if not found:
        say("  none")
    for d, repo in found:
        if not repo or repo in installed or not yes(env, f"Install the Cockpit in {repo} ({d})?"):
            continue
        name = unique_name(repo, {p.get("name") for p in projects})
        entry = {"name": name, "repo": repo, "git": str(d)}
        if (d / "docs/cockpit-modules.json").is_file():
            entry["modules"] = str(d / "docs/cockpit-modules.json")
        projects.append(entry)
        installed[repo] = entry
        pf.setdefault("default", name)
        new.append(entry)
        task, tool = PLANNED["project_sync"]
        if (env.root / tool).is_file():
            steps.append(Step(f"{name}: project_sync init", runs=[[python, env.root / tool, "init", "--repo-dir", d]]))
        else:
            say(f"  planned: {task}: `project_sync init` (the .cockpit/ folder) for {name} skipped")
        steps.append(Step(f"{name}: the kit labels in {repo} (kit/setup.sh, idempotent)", runs=[["bash", env.root / "kit/setup.sh", repo]]))
        steps.append(Step(f"{name}: the pre-push hook that runs the merge gate (tools/pre_push.py, idempotent, never overwrites a hook)",
                          runs=[[python, env.root / "tools/pre_push.py", "--install", "--repo", d]]))
    if new:
        steps.append(Step(f"register {', '.join(e['name'] for e in new)} in {env.projects_file}",
                          writes=[(env.projects_file, json.dumps(pf, indent=2) + "\n")]))
        for e in new:
            runs = [[python, env.root / "tools/code_snapshot_refresh.py", "--project", e["name"], "--projects-file", env.projects_file]]
            if "modules" in e:
                runs.append([python, env.root / "tools/cardmeta_collect.py", "--project", e["name"], "--repo", e["git"], "--map", e["modules"]])
            steps.append(Step(f"{e['name']}: first collectors run", runs=runs))
    unit = viewer_unit(env, python)
    if unit is None:
        say(f"viewer service: no service manager known for {env.system}; start `python3 cockpit.py --projects {env.projects_file}` yourself")
    else:
        path, body, status, load, unload = unit
        current = path.read_text() if path.is_file() else None
        up = env.run(status)[0] == 0
        if current == body and up:
            say("viewer service: already installed")
        elif current is None and env.fetch(f"http://127.0.0.1:{PORT}/"):
            say(f"viewer service: a viewer already answers on port {PORT} (a service of your own?): not installing a second one")
        elif yes(env, f"Install and start the viewer service ({path})?", True):
            steps.append(Step(f"viewer service: write {path} and start it", writes=[(path, body)],
                              runs=(unload if up else []) + load, by_hand=[" ".join(map(str, a)) for a in load]))
    say(f"daily upkeep job: planned: {PLANNED['daily job'][0]}: skipped (its steps run by hand, docs/deployment.md 5)")
    steps += filter(None, [dispatcher_step(env, python, credential)])
    saved = {"model_credential": str(credential), "project_dirs": [str(d) for d in cfg["project_dirs"]]}
    if read_json(env.install_file, None) != saved:
        steps.insert(0, Step(f"remember the credential path and the project folders in {env.install_file} (paths only)",
                             writes=[(env.install_file, json.dumps(saved, indent=2) + "\n")]))
    return steps


# Self-test

def self_test(env):
    """[(what, fix)] of everything missing for a running Cockpit; informational lines are logged."""
    miss = []
    python, msgs = check_python(env)
    for m in msgs:
        say(m)
    if not python:
        miss.append(("Python", msgs[0]))
    miss += missing_tools(env)
    saved = read_json(env.install_file, {})
    cred = saved.get("model_credential")
    state = credential_state(pathlib.Path(cred) if cred else None)
    if state != "found":
        miss.append(("model credential", f"make it private: `chmod 600 {cred}`" if state == "not 0600" else FIX["credential"]))
    projects = read_json(env.projects_file, {}).get("projects", [])
    if not projects:
        miss.append(("a project", "run `python3 tools/install.py` and answer yes for a project"))
    for p in projects:
        if p.get("git") and not pathlib.Path(p["git"]).expanduser().is_dir():
            miss.append((f"clone of {p['name']}", f"clone {p.get('repo')} to {p['git']} or fix {env.projects_file}"))
    unit = viewer_unit(env, python or "python3")
    if unit and env.run(unit[2])[0] != 0:
        miss.append(("viewer service", "run `python3 tools/install.py` and confirm the viewer service"))
    elif not env.fetch(f"http://127.0.0.1:{PORT}/"):
        miss.append(("viewer answering", f"start it (see the service log {env.p(DATA + '/cockpit.log')})"))
    if env.system == "Darwin":
        if not env.which("container"):
            miss.append(("container tool", FIX["container"]))
        for label in QUEUE_LABELS:
            if not loaded(env, label):
                miss.append((f"job {label}", "run `python3 tools/install.py` and confirm the Dispatcher"))
    say(f"planned: {PLANNED['daily job'][0]}: the daily upkeep job (not checked)")
    retention = read_json(env.p(".claude/settings.json"), {}).get("cleanupPeriodDays", 0)
    if not isinstance(retention, int) or retention < 3650:
        miss.append(("transcript retention", 'set "cleanupPeriodDays": 3650 in ~/.claude/settings.json'))
    for p in projects:
        rc, _ = env.run([python or "python3", env.root / "tools/metadata_gaps.py", "--project", p["name"], "--projects-file", env.projects_file])
        say(f"gap check {p['name']}: " + ("no gaps" if rc == 0 else f"gaps reported (`python3 tools/metadata_gaps.py --project {p['name']}` lists them)"))
    return miss


def report(miss):
    if not miss:
        say("ready")
        return 0
    say("missing:")
    for what, fix in miss:
        say(f"  - {what}: {fix}")
    return 1


def install(env, dry_run=False):
    python, msgs = check_python(env)
    for m in msgs:
        say(m)
    if not python:
        return 2
    say(f"python: {python}")
    tools = missing_tools(env)
    for what in ("git", "gh", "gh login"):
        say(f"{what}: {'not found' if any(t[0] == what for t in tools) else 'found'}")
    saved = read_json(env.install_file, {})
    credential, problem = find_credential(env, saved.get("model_credential"))
    stop = tools + ([problem] if problem else [])
    if stop:
        say("stopped, fix these first:")
        for what, fix in stop:
            say(f"  - {what}: {fix}")
        return 2
    cfg = {"project_dirs": project_dirs(env, saved.get("project_dirs"))}
    steps = plan(env, python, credential, cfg)
    if not steps:
        say("nothing to install: everything offered is in place")
    else:
        say("Plan:")
        for s in steps:
            say(f"  - {s.text}")
            for path, _ in s.writes:
                say(f"      write {path}")
            for argv in s.runs:
                say(f"      run {' '.join(map(str, argv))}")
        if dry_run:
            say("dry run: nothing was written or run")
            return 0
        if not yes(env, f"Carry out these {len(steps)} steps?", True):
            say("nothing done")
            return 1
        for s in steps:
            execute(env, s)
    return report(self_test(env))


def parser():
    ap = argparse.ArgumentParser(prog="tools/install.py", description="install the Cockpit for your projects (interactive, idempotent), then run the self-test")
    ap.add_argument("--dry-run", action="store_true", help="ask the questions, print the plan, write and run nothing")
    ap.add_argument("--self-test", action="store_true", help="only check: print ready or what is missing with its fix")
    return ap


def main(argv=None, env=None):
    a = parser().parse_args(argv)
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    env = env or Env(home=pathlib.Path.home())
    return report(self_test(env)) if a.self_test else install(env, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
