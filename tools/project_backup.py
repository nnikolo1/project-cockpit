#!/usr/bin/env python3
"""Daily backup of one project's Cockpit data into the folder the Drive desktop app syncs (PC-62; layout: docs/contracts/backup.md).

    python3 tools/project_backup.py --drive DIR [--project NAME] [--projects-file FILE] [--data-dir DIR] [--cache-dir DIR]
                                    [--transcripts DIR ...]

1. Mirrors the coding tool's session transcripts (each --transcripts folder) into <data dir>/<project>/transcripts/<folder name>/: new and
   grown files are copied, nothing is ever deleted, so a transcript the tool removes stays in the mirror.
2. Packs what the project's Cockpit has cached (its cache files, the listed files of its data folder incl. the mirror, the code snapshot, the
   phases file, the module map and the projects file) into one archive. Nothing that lies inside the project's clone is packed (the code is in
   the repository), and a file whose name marks a secret (token, credential, environment file, key) is refused.
3. Writes DRIVE/project_cockpits/<project>/<project>-YYYY-MM-DD.tar.gz create-only (a taken name gets -2, -3, ...): nothing in Drive is ever
   edited or deleted; the newest 30 are kept by the Apps Script tools/drive_retention.gs that the owner installs in Drive.
4. Writes the status record <data dir>/<project>/backup-status.json after every run (time, ok, archive, size, files, reason, last_ok).

Exit code: 0 backup written, 1 the backup failed (see the status record), 2 unusable arguments (no status written).
"""
import argparse
import datetime as dt
import fnmatch
import io
import json
import logging
import os
import pathlib
import re
import shutil
import sys
import tarfile
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import cockpit as C  # noqa: E402
import logic as L  # noqa: E402

log = logging.getLogger("project_backup")
DRIVE_FOLDER = "project_cockpits"
MIRROR = "transcripts"
# The files of the project's data folder that are packed (paths relative to it; `*` also matches `/`). Logs, sockets, rendered job files,
# task bundles (code) and the token files are not in it.
DATA_FILES = ("dispatcher-state.json", "dispatcher-settings.json", "cockpit-settings.json", "projects.json", C.PLOG_FILE, C.USAGE_FILE,
              C.QUOTA_FILE, C.TRANSCRIPT_FILE, C.AGGREGATE_FILE, L.BACKUP_STATUS_FILE, "cardmeta/*.json", "area/*/outbox/goodbye.json", MIRROR + "/*")
SECRET = re.compile(r"(^|[-_.])(tokens?|secrets?|credentials?|passwords?|passwd)($|[-_.])|^\.env|\.env$|^\.netrc$|^id_(rsa|dsa|ecdsa|ed25519)"
                    r"|\.(pem|key|p12|pfx|keychain)$", re.I)


def is_secret(name):
    """True when a file name marks a secret: such a file is never packed, whatever folder it is in."""
    return bool(SECRET.search(pathlib.PurePath(name).name))


def mirror_transcripts(src, dest):
    """Copy every *.jsonl under `src` into `dest` (same relative paths) when it is new there or has grown; never delete -> files copied."""
    src, n = pathlib.Path(src).expanduser(), 0
    for f in sorted(src.rglob("*.jsonl")):
        if not f.is_file() or f.is_symlink():
            continue
        t = dest / f.relative_to(src)
        try:
            have = t.stat() if t.exists() else None
            size = f.stat().st_size
            if have is not None and (size < have.st_size or (size == have.st_size and f.stat().st_mtime <= have.st_mtime)):
                if size < have.st_size:
                    log.warning("transcript %s shrank (%d < %d bytes): the mirror keeps the longer copy", t.relative_to(dest), size, have.st_size)
                continue
            t.parent.mkdir(parents=True, exist_ok=True)
            tmp = t.with_name(t.name + ".tmp")
            shutil.copy2(f, tmp)
            os.replace(tmp, t)
            n += 1
        except OSError as ex:
            log.warning("transcript %s not mirrored: %s", f.name, ex)
    return n


def _inside(path, folder):
    return bool(folder) and pathlib.Path(path).expanduser().resolve().is_relative_to(pathlib.Path(folder).expanduser().resolve())


def plan(p, all_projects, data_dir, cache_dir, projects_file):
    """-> (members [(source path, archive path relative to the top folder)], manifest project dict, refused names)."""
    members, refused, entry = [], [], {k: p.get(k) for k in ("name", "repo", "manager", "project") if p.get(k)}

    def add(src, arc):
        if is_secret(src):
            refused.append(str(arc))
            log.warning("refused, the name marks a secret: %s", arc)
        elif src.is_file() and not src.is_symlink():
            members.append((src, arc))

    def add_tree(folder, arc):
        for f in sorted(pathlib.Path(folder).rglob("*")):
            add(f, f"{arc}/{f.relative_to(folder).as_posix()}")

    d = data_dir / p["name"]
    if d.is_dir():
        for f in sorted(d.rglob("*")):
            rel = f.relative_to(d).as_posix()
            if any(fnmatch.fnmatch(rel, pat) for pat in DATA_FILES):
                add(f, f"data/{p['name']}/{rel}")
    if p.get("meta") and pathlib.Path(p["meta"]).is_dir():  # card files kept elsewhere: offline they are read from data/<name>/cardmeta/
        for f in sorted(pathlib.Path(p["meta"]).glob("*.json")):
            add(f, f"data/{p['name']}/cardmeta/{f.name}")
    if cache_dir and cache_dir.is_dir():
        pre = lambda q: re.sub(r"[^A-Za-z0-9_.-]", "_", C.pk(q, ""))
        mine, longer = pre(p), [pre(q) for q in all_projects if len(pre(q)) > len(pre(p)) and pre(q).startswith(pre(p))]
        for f in sorted(cache_dir.glob(mine + "*.json")):
            if not any(f.name.startswith(x) for x in longer):
                add(f, f"cache/{f.name}")
    git = p.get("git")
    for key, arc in (("code", "files/code"), ("phases", "files/phases"), ("modules", "files/map")):
        src = pathlib.Path(p[key]).expanduser() if p.get(key) else None
        if src is None or not src.exists():
            continue
        if _inside(src, git):
            log.info("%s lies in the project's clone (in the repository): not packed", key)
            continue
        if src.is_dir():
            add_tree(src, arc)
            entry[key] = arc
        else:
            add(src, f"{arc}/{src.name}")
            entry[key] = f"{arc}/{src.name}"
            pages = src.parent / "modules"
            if key == "modules" and pages.is_dir() and not _inside(pages, git):
                add_tree(pages, f"{arc}/modules")
    if projects_file and projects_file.is_file() and not _inside(projects_file, git):
        add(projects_file, "files/projects.json")
    return members, entry, refused


def write_archive(members, manifest, top, tmpdir):
    """Pack the members and backup.json under the folder `top` into a gzip tar in tmpdir -> its path."""
    out = pathlib.Path(tmpdir) / (top + ".tar.gz")
    with tarfile.open(out, "w:gz") as tf:
        data = json.dumps(manifest, indent=1).encode()
        info = tarfile.TarInfo(f"{top}/{C.MANIFEST}")
        info.size, info.mtime, info.mode = len(data), manifest["created"], 0o600
        tf.addfile(info, io.BytesIO(data))
        for src, arc in members:
            tf.add(src, arcname=f"{top}/{arc}", recursive=False)
    return out


def place(archive, folder, stem):
    """Copy `archive` into `folder` as <stem>.tar.gz, create-only: a taken name gets -2, -3, ... -> the written path."""
    for i in range(1, 1000):
        dest = folder / (f"{stem}.tar.gz" if i == 1 else f"{stem}-{i}.tar.gz")
        try:
            with open(dest, "xb") as out, open(archive, "rb") as src:
                shutil.copyfileobj(src, out)
            return dest
        except FileExistsError:
            continue
    raise OSError(f"no free name for {stem} in {folder}")


def write_status(path, rec):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=1))
    os.replace(tmp, path)


def run(p, all_projects, drive, data_dir, cache_dir, projects_file, transcripts, now):
    """One backup -> the status record (also written to the data folder)."""
    d = data_dir / p["name"]
    prev = L.backup_status(C._read_text(d / L.BACKUP_STATUS_FILE)) or {}
    rec = dict(schema=1, time=now.strftime("%Y-%m-%dT%H:%M:%SZ"), ok=False, archive=None, size=None, files=None, reason=None, refused=[],
               last_ok=prev.get("last_ok"))
    try:
        if not drive.is_dir():
            raise OSError(f"the Drive folder {drive} does not exist (is the Drive desktop app running?)")
        for t in transcripts:
            src = pathlib.Path(t).expanduser()
            if not src.is_dir():
                raise OSError(f"transcript folder {src} does not exist")
            log.info("transcripts: %d file(s) mirrored from %s", mirror_transcripts(src, d / MIRROR / src.name), src.name)
        members, entry, rec["refused"] = plan(p, all_projects, data_dir, cache_dir, projects_file)
        if not members:
            raise ValueError("nothing to back up: no cached data found for the project")
        stem = f"{p['name']}-{now.strftime('%Y-%m-%d')}"
        target = drive / DRIVE_FOLDER / p["name"]
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="cockpit-backup-") as tmp:
            arch = write_archive(members, dict(created=int(now.timestamp()), project=entry), stem, tmp)
            dest = place(arch, target, stem)
        rec.update(ok=True, archive=str(dest), size=dest.stat().st_size, files=len(members) + 1, last_ok=rec["time"])
        log.info("backup of %s written: %s (%d files, %d bytes)", p["name"], dest, rec["files"], rec["size"])
    except (OSError, ValueError, tarfile.TarError) as ex:
        rec["reason"] = f"{type(ex).__name__}: {ex}"[:300]
        log.error("backup of %s failed: %s", p["name"], rec["reason"])
    try:
        write_status(d / L.BACKUP_STATUS_FILE, rec)
    except OSError as ex:
        log.error("status record not written: %s", ex)
    return rec


def main(argv=None, now=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drive", required=True, help="the folder the Drive desktop app syncs (its 'My Drive'); project_cockpits/ is made in it")
    ap.add_argument("--project", help="project name (default: the default of the projects file)")
    ap.add_argument("--projects-file", default="~/.config/project-cockpit/projects.json", help="the Cockpit's --projects file (default: %(default)s)")
    ap.add_argument("--data-dir", default=str(C.DATA_DIR), help="the Cockpit's data folder (default: %(default)s)")
    ap.add_argument("--cache-dir", help="the Cockpit's --cache-dir (default: <data dir>/cache)")
    ap.add_argument("--transcripts", action="append", default=[], metavar="DIR",
                    help="a folder of the coding tool's session transcripts of this project, mirrored and backed up (repeatable)")
    a = ap.parse_args(argv)
    now = now or dt.datetime.now(dt.timezone.utc)
    pf = pathlib.Path(a.projects_file).expanduser()
    try:
        projects, default = C.projects_from_file(json.loads(pf.read_text()))
        p = next((x for x in projects if x["name"] == (a.project or default)), None)
        if p is None:
            raise ValueError(f"unknown project {a.project!r}; configured: {', '.join(x['name'] for x in projects)}")
    except (OSError, ValueError) as ex:
        log.error("%s", ex)
        return 2
    data_dir = pathlib.Path(a.data_dir).expanduser()
    cache_dir = pathlib.Path(a.cache_dir).expanduser() if a.cache_dir else data_dir / "cache"
    rec = run(p, projects, pathlib.Path(a.drive).expanduser(), data_dir, cache_dir, pf, a.transcripts, now)
    return 0 if rec["ok"] else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sys.exit(main())
