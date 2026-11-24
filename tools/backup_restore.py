#!/usr/bin/env python3
"""Restore test of a Cockpit backup (PC-63), for any project: unpack the backup into a temporary folder, start the Cockpit offline on it
(`cockpit.py --offline`, with a `gh` on PATH that only records being called) and request every pane's data. Fails (exit 1) when a pane's data
is not in the backup (a gap), a pane answers with no data, an action is not refused, or anything tried to reach GitHub.

    python3 tools/backup_restore.py BACKUP          # archive (tar, any compression) or folder; layout: docs/contracts/backup.md
    python3 tools/backup_restore.py --gaps BACKUP   # only the gap checker: which pane's data is not in the backup (no server started)
"""
import argparse
import json
import logging
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import cockpit as C  # noqa: E402
import logic as L  # noqa: E402

log = logging.getLogger("backup_restore")
FAKE_GH = "#!/bin/sh\necho \"$@\" >> \"$(dirname \"$0\")/gh-called\"\necho 'offline restore test: no GitHub call allowed' >&2\nexit 1\n"


def gaps(backup_root):
    """The gap checker over an unpacked backup: [{pane, api, missing}] for every pane whose data is not in it."""
    p, _ = C.open_backup(backup_root)
    root = pathlib.Path(backup_root).resolve()
    saved = C.DATA_DIR, C.CACHE_DIR
    C.DATA_DIR, C.CACHE_DIR = root / "data", root / "cache"
    try:
        return L.backup_gaps(C.backup_inventory(p))
    finally:
        C.DATA_DIR, C.CACHE_DIR = saved


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(port, token, path, data=None):
    """-> (http status, parsed JSON or None)."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers={"Cookie": f"t={token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code, body = r.status, r.read()
    except urllib.error.HTTPError as ex:
        code, body = ex.code, ex.read()
    try:
        return code, json.loads(body)
    except ValueError:
        return code, None


def restore_check(backup, timeout=30):
    """Unpack, start offline, request every pane -> list of problems (empty = the backup restores a working copy)."""
    problems = []
    with tempfile.TemporaryDirectory(prefix="cockpit-restore-") as tmp:
        tmp = pathlib.Path(tmp)
        root = C.unpack_backup(backup, tmp / "backup")
        problems += [f"gap: {g['pane']} ({g['api']}) lacks {'; '.join(g['missing'])}" for g in gaps(root)]
        (tmp / "bin").mkdir()
        (tmp / "bin" / "gh").write_text(FAKE_GH)
        (tmp / "bin" / "gh").chmod(0o755)
        port, token = _free_port(), "restore-test"
        (tmp / "token").write_text(token)
        env = dict(os.environ, PATH=f"{tmp / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
        with open(tmp / "cockpit.log", "w") as out:
            proc = subprocess.Popen([sys.executable, str(ROOT / "cockpit.py"), "--offline", str(root), "--port", str(port), "--token-file", str(tmp / "token")],
                                    env=env, stdout=out, stderr=subprocess.STDOUT)
        try:
            info = None
            end = time.time() + timeout
            while time.time() < end and proc.poll() is None:
                try:
                    code, j = _get(port, token, "/api/projects")
                    info = j.get("offline") if code == 200 and j else None
                    break
                except OSError:
                    time.sleep(0.1)
            if info is None:
                return problems + [f"the offline Cockpit did not start: {(tmp / 'cockpit.log').read_text()[-800:]}"]
            seen = set()
            for pane, api, _ in L.OFFLINE_PANES:
                if api in seen:
                    continue
                seen.add(api)
                code, j = _get(port, token, api)
                why = f"HTTP {code}: {(j or {}).get('error')}" if code != 200 else L.pane_problem(api, j)
                if why:
                    problems.append(f"{pane} ({api}): {why}")
                if api == "/api/code" and code == 200:
                    for m in (j or {}).get("mods", []):
                        if not m.get("virtual"):
                            c, mj = _get(port, token, "/api/moduledoc?id=" + urllib.request.quote(m["id"]))
                            if c != 200:
                                problems.append(f"module page {m['id']}: HTTP {c}: {(mj or {}).get('error')}")
            code, _ = _get(port, token, "/api/act", json.dumps(dict(action="release", n=1, p=1)).encode())
            if code != 403:
                problems.append(f"steering is not off: an action got HTTP {code}")
            _, j = _get(port, token, "/api/projects")
            n = ((j or {}).get("offline") or {}).get("ghAttempts")
            if n:
                problems.append(f"the Cockpit tried to reach GitHub {n} time(s)")
            if (tmp / "bin" / "gh-called").exists():
                problems.append(f"gh was run: {(tmp / 'bin' / 'gh-called').read_text().strip()[:300]}")
        finally:
            proc.terminate()
            proc.wait(10)
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("backup", help="the backup: an archive or a folder holding backup.json")
    ap.add_argument("--gaps", action="store_true", help="only list the panes whose data is not in the backup")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if a.gaps:
        with tempfile.TemporaryDirectory(prefix="cockpit-gaps-") as tmp:
            found = [f"{g['pane']} ({g['api']}) lacks {'; '.join(g['missing'])}" for g in gaps(C.unpack_backup(a.backup, tmp))]
    else:
        found = restore_check(a.backup)
    for x in found:
        log.error("%s", x)
    log.info("%s", "every pane has its data offline" if not found else f"{len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
