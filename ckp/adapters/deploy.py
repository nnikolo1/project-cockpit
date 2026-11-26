"""The adapter object of the deploy command: puts the git, GitHub and process adapters and the release store behind the interface that
`ckp.core.deploy.deploy` calls (docs/contracts/deploy-interfaces.md). The integrator's piece of PC-84 phase 1: no logic of its own beyond
the facts it reports; every outside effect goes through the injected modules (`git`, `github`, `process`, `releases`, `layout`)."""
import logging
import pathlib
import tempfile

from ckp.adapters import git as _git, github as _github, process as _process, run as _run
from ckp.store import layout as _layout, releases as _releases

log = logging.getLogger("ckp.deploy")

SMOKE = "import ckp, logic, cockpit, dispatcher.core"   # the release tree must import


class GitDeployAdapter:
    def __init__(self, project, repo, repo_dir, env_name, home=None, *, git=_git, github=_github, process=_process, releases=_releases,
                 layout=_layout, run=_run, tests_green=None, health_wait_s=90, health_interval_s=2, sleep=None, clock=None,
                 from_release=False, problems=None):
        """`repo`: OWNER/NAME on the hosting service; `repo_dir`: the working clone; `env_name`: the environment being deployed;
        `tests_green`: a bool or a callable returning one (default: the test environment, tools/test_env.py, on the clone's HEAD);
        `from_release`: fetch the tree from the tag's release asset instead of the clone (PC-142); `problems`: a callable returning the
        open heartbeat and backup problems (default: ckp.core.monitor.problems), asked only for a release that is not installed yet."""
        self.project, self.repo, self.repo_dir, self.env_name, self.home = project, repo, repo_dir, env_name, home
        self.git, self.github, self.process, self.releases, self.layout, self.run = git, github, process, releases, layout, run
        self._tests_green = tests_green
        self.from_release, self._problems = from_release, problems
        import time
        self.health_wait_s, self.health_interval_s = health_wait_s, health_interval_s
        self.sleep, self.clock = sleep or time.sleep, clock or time.monotonic
        self.lock_path = layout.deploy_lock(home)

    # -- what the core asks for before it changes anything --
    def _sha(self, ref):
        return self.run(["git", "-C", str(self.repo_dir), "rev-parse", ref]).strip()

    def _tests_ok(self):
        t = self._tests_green
        if callable(t):
            return bool(t())
        if t is not None:
            return bool(t)
        try:
            self.run(["python3", str(self.repo_dir) + "/tools/test_env.py", "--repo-dir", str(self.repo_dir)])
            return True
        except Exception as e:  # noqa: BLE001 - a failing gate is a fact, not an error
            log.warning("test environment failed: %s", e)
            return False

    def facts(self, project, tag, env):
        try:
            dirty = bool(self.run(["git", "-C", str(self.repo_dir), "status", "--porcelain", "--untracked-files=no"]).strip())
            pushed = self._sha("HEAD") == self._sha("origin/main")
        except Exception as e:  # noqa: BLE001
            log.warning("git facts unavailable: %s", e)
            dirty, pushed = True, False
        live = self.releases.current(project, self.home)
        try:
            ahead = live is None or self._sha(f"{tag}^{{commit}}") != self._sha(f"{live}^{{commit}}")
        except Exception:  # noqa: BLE001
            ahead = True
        installed = self.releases.release_path(project, tag, self.home).is_dir()   # a rollback: no gate run, the tree was checked then
        return dict(clean=not dirty and pushed, tests_green=installed or self._tests_ok(), ahead_of_live=ahead, deploy_running=False,
                    ci_ok=None, problems_open=None if installed else self._problems_open(), installed=installed,
                    rollback_target=live is None or self.releases.release_path(project, live, self.home).is_dir())

    def _problems_open(self):
        """True when heartbeat or backup problems are open (a rollback is never held by them: not asked for an installed release)."""
        try:
            if self._problems is None:
                from ckp.core import monitor
                return bool(monitor.problems(self.home))
            return bool(self._problems())
        except Exception as e:  # noqa: BLE001 - an unreadable monitor state is unknown, not a refusal
            log.warning("monitoring problems unknown: %s", e)
            return None

    def _ref(self, tag):
        """The tag when it exists, else HEAD (a dry run plans a tag that is created only when the deploy is applied)."""
        try:
            self._sha(f"{tag}^{{commit}}")
            return tag
        except Exception:  # noqa: BLE001
            return "HEAD"

    def changed_paths(self, project, tag):
        return self.git.changed_paths(self.repo_dir, self.releases.current(project, self.home), self._ref(tag), run=self.run)

    def running_workers(self):
        return self.process.running_workers(self.layout.per_project(self.home or self.layout.DATA_DIR, self.project)["state"])

    # -- the steps of a deploy --
    def fetch(self, tag):
        dest = tempfile.mkdtemp(prefix="ckp-release-")
        if self.from_release:
            return self.github.download_release(self.repo, tag, dest, run=self.run)
        return self.git.archive(self.repo_dir, tag, dest, run=self.run)

    def publish(self, tag):
        """The release asset (PC-142): a GitHub Release of the tag with the archive of the tagged tree and its checksum. Returns the URL."""
        with tempfile.TemporaryDirectory(prefix="ckp-asset-") as tmp:
            arch = self.git.archive_file(self.repo_dir, tag, pathlib.Path(tmp) / self.github.asset_name(tag), run=self.run)
            return self.github.create_release(self.repo, tag, f"Release {tag} of {self.project} (archive of the tagged tree).", arch, run=self.run)

    def selftest(self, tree):
        try:
            self.run(["env", f"PYTHONPATH={tree}", "python3", "-c", SMOKE])
            return True, "the release tree imports"
        except Exception as e:  # noqa: BLE001
            return False, f"import check failed: {e}"

    def restart(self, project, env):
        return self.process.restart_service(env["label"], run=self.run)

    def health(self, env):
        """Poll /health until the server answers or the grace window is over (a restarted viewer needs seconds to listen)."""
        deadline, last = self.clock() + self.health_wait_s, (False, "no check made")
        while True:
            last = self.process.health(env["port"], "/health")
            if last[0] or self.clock() >= deadline:
                return last
            self.sleep(self.health_interval_s)

    def record(self, project, tag, env, state, detail):
        try:
            self.github.record_deployment(self.repo, tag, self.env_name, state, detail, run=self.run)
        except Exception as e:  # noqa: BLE001 - the record of a deploy must not undo it
            log.warning("deployment record not written: %s %s", e, (getattr(e, "stderr", "") or "")[:300])
