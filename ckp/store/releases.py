"""Releases on disk: <home>/releases/<project>/<tag>/, a `current` link and a `previous` link (contract docs/contracts/deploy-interfaces.md)."""
import logging
import os
import pathlib
import re
import shutil
import stat

from ckp.store import layout

log = logging.getLogger(__name__)
_TAG = re.compile(r"^v(\d{4})\.(\d{2})\.(\d{2})-(\d+)$")
_WRITE = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH


def _root(project, home=None):
    if not project or "/" in project or "\\" in project or project in (".", ".."):
        raise ValueError(f"unusable project name {project!r}")
    return (pathlib.Path(home).expanduser() if home else layout.home()) / "releases" / project


def _tag_key(tag):
    m = _TAG.match(tag)
    return tuple(int(g) for g in m.groups()) if m else None


def release_path(project, tag, home=None):
    if not _TAG.match(str(tag)):
        raise ValueError(f"unusable tag {tag!r}")
    return _root(project, home) / tag


def list_releases(project, home=None):
    """Tags on disk, newest first (by date and counter); folders that are not tags are ignored."""
    root = _root(project, home)
    if not root.is_dir():
        return []
    tags = [p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink() and _tag_key(p.name)]
    return sorted(tags, key=_tag_key, reverse=True)


def _link_target(project, name, home):
    try:
        return pathlib.Path(os.readlink(_root(project, home) / name)).name
    except OSError:
        return None


def current(project, home=None):
    return _link_target(project, "current", home)


def previous(project, home=None):
    return _link_target(project, "previous", home)


def _set_link(root, name, tag):
    tmp = root / f".{name}.{os.getpid()}.tmp"
    tmp.unlink(missing_ok=True)
    os.symlink(tag, tmp)  # relative target: the folder keeps working when its home is moved
    os.replace(tmp, root / name)


def _make_read_only(path):
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            p = os.path.join(dirpath, f)
            if not os.path.islink(p):
                os.chmod(p, os.stat(p).st_mode & ~_WRITE)
    for dirpath, _dirs, _files in os.walk(path, topdown=False):
        os.chmod(dirpath, os.stat(dirpath).st_mode & ~_WRITE)


def install_release(project, tag, tree, home=None):
    """Copy the unpacked `tree` to the release folder and make it read-only. An existing tag is never overwritten."""
    dest = release_path(project, tag, home)
    if dest.exists():
        raise FileExistsError(f"release {project} {tag} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{tag}.{os.getpid()}.tmp"
    try:
        shutil.copytree(tree, tmp, symlinks=True)
        os.rename(tmp, dest)
        tmp = dest  # from here a failure cleans up the final folder
        _make_read_only(dest)
    except BaseException:
        log.error("install of release %s %s failed", project, tag, exc_info=True)
        if tmp.exists():
            for dp, _dn, _fn in os.walk(tmp):
                os.chmod(dp, 0o755)
            shutil.rmtree(tmp, ignore_errors=True)
        raise
    log.info("installed release %s %s", project, tag)
    return dest


def switch_current(project, tag, home=None):
    """Point `current` at `tag` atomically; the old target becomes `previous`. The tag must exist."""
    if not release_path(project, tag, home).is_dir():
        raise LookupError(f"release {project} {tag} does not exist")
    root = _root(project, home)
    old = current(project, home)
    if old and old != tag:
        _set_link(root, "previous", old)
    _set_link(root, "current", tag)
    log.info("switched %s: %s -> %s", project, old, tag)


def rollback(project, home=None):
    """Switch back to `previous` and return its tag; LookupError when there is none."""
    prev = previous(project, home)
    if not prev:
        raise LookupError(f"no previous release for {project}")
    switch_current(project, prev, home)
    log.warning("rolled back %s to %s", project, prev)
    return prev
