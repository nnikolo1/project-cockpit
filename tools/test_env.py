#!/usr/bin/env python3
"""The test environment (PC-84): a throwaway clone and a temporary Cockpit home, made on demand and removed afterwards.

    python3 tools/test_env.py [--repo-dir DIR] [--keep]
    python3 tools/test_env.py ... --e2e-repo OWNER/THROWAWAY --token-file F [--image cockpit-worker:stub] [--prod-state FILE]

Steps, each printed as PASS or FAIL:
  1. clone: the committed HEAD of the working clone into <tmp>/repo (uncommitted changes are not tested: a release is a commit);
  2. tests: the merge gate (full tests, page code included) in that clone, with CP_HOME=<tmp>/home and CP_ENV=test, so no test
     can touch the production home;
  3. rehearsal: the release store of the deploy command in <tmp>/home: the tree is installed read-only, `current` is switched to it
     and the release imports from the link, as production will;
  4. e2e (only with --e2e-repo): the Dispatcher's failure paths against the throwaway repository with the stub of the model
     (tools/e2e_failure_paths.py), in the test's own container. The MacBook runs one container VM at a time: refused while the
     production Dispatcher has Workers running (its state file, --prod-state), production and test take turns.
`ckp deploy` runs it as its test gate. Exit status 0 all steps passed, 1 a step failed, 2 refused.
"""
import argparse
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ckp.adapters import git as git_adapter, process  # noqa: E402
from ckp.store import layout, releases  # noqa: E402

log = logging.getLogger("test_env")
PROJECT = "cockpit-test"          # the project name of the rehearsal release, inside the temporary home only
TAG = "v2000.01.01-1"             # a fixed tag: the temporary home holds no other release
SMOKE = "import ckp, logic, cockpit, dispatcher.core"


def _sh(args, **kw):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, **kw)


def step_clone(repo_dir, tmp):
    dest = tmp / "repo"
    r = _sh(["git", "clone", "-q", "--no-hardlinks", repo_dir, dest])
    if r.returncode:
        return False, r.stderr.strip()
    sha = _sh(["git", "-C", repo_dir, "rev-parse", "HEAD"]).stdout.strip()
    r = _sh(["git", "-C", dest, "checkout", "-q", "--detach", sha])
    return (r.returncode == 0, f"{dest} at {sha[:12]}" if r.returncode == 0 else r.stderr.strip())


def env_vars(tmp):
    """The variables of every process of the test environment: its own home and its name."""
    return dict(os.environ, CP_HOME=str(tmp / "home"), CP_ENV="test")


def step_tests(tmp, run=subprocess.run):
    repo = tmp / "repo"
    r = run([sys.executable, str(repo / "tools" / "merge_gate")], cwd=repo, env=env_vars(tmp))
    return r.returncode == 0, "merge gate PASS" if r.returncode == 0 else f"merge gate exited with {r.returncode}"


def step_rehearsal(tmp):
    home, tree = tmp / "home", tmp / "tree"
    git_adapter.archive(tmp / "repo", "HEAD", tree)
    releases.install_release(PROJECT, TAG, tree, home)
    releases.switch_current(PROJECT, TAG, home)
    link = home / "releases" / PROJECT / "current"
    r = _sh([sys.executable, "-c", SMOKE], cwd=link, env=dict(env_vars(tmp), PYTHONPATH=str(link)))
    return r.returncode == 0, f"{link} imports" if r.returncode == 0 else r.stderr.strip()[-500:]


def step_e2e(tmp, a, run=subprocess.run):
    repo = tmp / "repo"
    args = [sys.executable, str(repo / "tools" / "e2e_failure_paths.py"), "--repo", a.e2e_repo, "--cockpit", repo,
            "--base", tmp / "e2e", "--image", a.image, "--token-file", a.token_file]
    r = run([str(x) for x in args], cwd=repo, env=env_vars(tmp))
    return r.returncode == 0, "failure paths PASS" if r.returncode == 0 else f"failure paths exited with {r.returncode}"


def remove(tmp):
    """Remove the temporary folder; the release folders are read-only, so make them writable first."""
    for dp, _dn, _fn in os.walk(tmp):
        os.chmod(dp, 0o755)
    shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None, steps=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-dir", default=str(ROOT), help="the working clone whose HEAD is tested (default: this one)")
    ap.add_argument("--keep", action="store_true", help="keep the temporary folder and print its path")
    ap.add_argument("--e2e-repo", help="the throwaway repository of the failure-path test, owner/name (default: no e2e step)")
    ap.add_argument("--token-file", help="with --e2e-repo: the proxy's credential file")
    ap.add_argument("--image", default="cockpit-worker:stub", help="with --e2e-repo: the image with the stub of the model")
    ap.add_argument("--prod-state", default=str(layout.queue()["state"]), help="the production Dispatcher's state file")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s", stream=sys.stderr)
    if a.e2e_repo:
        if not a.token_file:
            ap.error("--e2e-repo needs --token-file")
        busy = process.running_workers(a.prod_state)
        if busy:
            print(f"refused: production has Workers running ({', '.join(busy)}); production and test take turns")
            return 2
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ckp-t-"))   # short: the e2e proxy socket path must stay under 104 bytes
    steps = steps or [("clone", lambda: step_clone(a.repo_dir, tmp)), ("tests", lambda: step_tests(tmp)),
                      ("rehearsal", lambda: step_rehearsal(tmp))] + ([("e2e", lambda: step_e2e(tmp, a))] if a.e2e_repo else [])
    ok = True
    try:
        for name, fn in steps:
            try:
                good, detail = fn()
            except Exception as e:  # noqa: BLE001 - a step that raises has failed; the next steps are not run
                good, detail = False, f"{type(e).__name__}: {e}"
            print(f"{'PASS' if good else 'FAIL'} {name}: {detail}")
            if not good:
                ok = False
                break
    finally:
        if a.keep:
            print(f"kept: {tmp}")
        else:
            remove(tmp)
    print("test environment: PASS" if ok else "test environment: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
