"""The adapter object of the deploy command: puts the git, GitHub and process adapters and the release store behind the interface that
`ckp.core.deploy.deploy` calls (docs/contracts/deploy-interfaces.md). The integrator's piece of PC-84 phase 1: no logic of its own beyond
the facts it reports; every outside effect goes through the injected modules (`git`, `github`, `process`, `releases`, `layout`)."""
import logging
import tempfile

from ckp.adapters import git as _git, github as _github, process as _process, run as _run
from ckp.store import layout as _layout, releases as _releases

log = logging.getLogger("ckp.deploy")

SMOKE = "import ckp, logic, cockpit, dispatcher.core"   # the release tree must import


class GitDeployAdapter:
    def __init__(self, project, repo, repo_dir, env_name, home=None, *, git=_git, github=_github, process=_process, releases=_releases,
                 layout=_layout, run=_run, tests_green=None):
        """`repo`: OWNER/NAME on the hosting service; `repo_dir`: the working clone; `env_name`: the environment being deployed;
        `tests_green`: a bool or a callable returning one (default: run tools/merge_gate in the clone)."""
        self.project, self.repo, self.repo_dir, self.env_name, self.home = project, repo, repo_dir, env_name, home
        self.git, self.github, self.process, self.releases, self.layout, self.run = git, github, process, releases, layout, run
        self._tests_green = tests_green
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
            self.run([str(self.repo_dir) + "/tools/merge_gate"])
            return True
        except Exception as e:  # noqa: BLE001 - a failing gate is a fact, not an error
            log.warning("merge gate failed: %s", e)
            return False

    def facts(self, project, tag, env):
        try:
            dirty = bool(self.run(["git", "-C", str(self.repo_dir), "status", "--porcelain"]).strip())
            pushed = self._sha("HEAD") == self._sha("origin/main")
        except Exception as e:  # noqa: BLE001
            log.warning("git facts unavailable: %s", e)
            dirty, pushed = True, False
        live = self.releases.current(project, self.home)
        try:
            ahead = live is None or self._sha(f"{tag}^{{commit}}") != self._sha(f"{live}^{{commit}}")
        except Exception:  # noqa: BLE001
            ahead = True
        return dict(clean=not dirty and pushed, tests_green=self._tests_ok(), ahead_of_live=ahead, deploy_running=False, ci_ok=None,
                    problems_open=None, rollback_target=live is None or self.releases.release_path(project, live, self.home).is_dir())

    def changed_paths(self, project, tag):
        return self.git.changed_paths(self.repo_dir, self.releases.current(project, self.home), tag, run=self.run)

    def running_workers(self):
        return self.process.running_workers(self.layout.per_project(self.home or self.layout.DATA_DIR, self.project)["state"])

    # -- the steps of a deploy --
    def fetch(self, tag):
        dest = tempfile.mkdtemp(prefix="ckp-release-")
        return self.git.archive(self.repo_dir, tag, dest, run=self.run)

    def selftest(self, tree):
        try:
            self.run(["env", f"PYTHONPATH={tree}", "python3", "-c", SMOKE])
            return True, "the release tree imports"
        except Exception as e:  # noqa: BLE001
            return False, f"import check failed: {e}"

    def restart(self, project, env):
        return self.process.restart_service(env["label"], run=self.run)

    def health(self, env):
        return self.process.health(env["port"])

    def record(self, project, tag, env, state, detail):
        try:
            self.github.record_deployment(self.repo, tag, self.env_name, state, detail, run=self.run)
        except Exception as e:  # noqa: BLE001 - the record of a deploy must not undo it
            log.warning("deployment record not written: %s", e)
