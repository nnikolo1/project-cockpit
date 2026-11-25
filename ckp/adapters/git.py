"""Git adapter of the deploy command (PC-84): tags, archives and changed paths of a local clone."""
import logging
import re
import tarfile
import tempfile
from pathlib import Path

from ckp.adapters import run as _run

log = logging.getLogger("ckp.adapters.git")


def _git(repo_dir, *args, run):
    return run(["git", "-C", str(repo_dir), *args])


def next_tag(repo_dir, today, *, run=_run):
    """The next `vYYYY.MM.DD-N` for `today` (a date): N is one more than the highest counter of that date's tags (1 when none)."""
    prefix = f"v{today:%Y.%m.%d}-"
    out = _git(repo_dir, "tag", "--list", prefix + "*", run=run)
    pat = re.compile(re.escape(prefix) + r"(\d+)$")
    counters = [int(m.group(1)) for t in out.split() if (m := pat.match(t))]
    tag_name = f"{prefix}{max(counters, default=0) + 1}"
    log.info("next tag for %s in %s: %s", today, repo_dir, tag_name)
    return tag_name


def tag(repo_dir, tag, sha, *, run=_run):
    """Create the tag on the commit `sha`; an existing tag makes git (and so this function) fail."""
    _git(repo_dir, "tag", tag, sha, run=run)
    log.info("tagged %s at %s in %s", tag, sha, repo_dir)


def push_tag(repo_dir, tag, *, run=_run):
    """Publish a release tag to the remote (the hosting service needs it for the deployment record and the release)."""
    _git(repo_dir, "push", "origin", tag, run=run)


def archive(repo_dir, tag, dest, *, run=_run):
    """`git archive` of the tag unpacked into `dest` (created when missing); returns `dest`."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tar = Path(tmp) / "tree.tar"
        _git(repo_dir, "archive", "--format=tar", "-o", str(tar), tag, run=run)
        with tarfile.open(tar) as t:
            t.extractall(dest, filter="data")
    log.info("archived %s of %s into %s", tag, repo_dir, dest)
    return dest


def changed_paths(repo_dir, old_tag, new_tag, *, run=_run):
    """The paths that differ between the tags (`git diff --name-only`); `old_tag` None: every path of `new_tag`."""
    if old_tag is None:
        out = _git(repo_dir, "ls-tree", "-r", "--name-only", new_tag, run=run)
    else:
        out = _git(repo_dir, "diff", "--name-only", old_tag, new_tag, run=run)
    return [p for p in out.splitlines() if p]
