"""Offline copy from a backup (PC-63): the gap checker, the offline switches of the Cockpit and the restore test, on a generated backup of a
made-up project (project-agnostic). Set COCKPIT_BACKUP=<archive or folder> to run the restore test on a real backup as well."""
import io
import json
import os
import pathlib
import sys
import tarfile
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "tools"))
import cockpit as C
from ckp.api import context as api_context
import logic as L
import backup_restore as R

CREATED = 1764000000


def make_backup(root, name="demo"):
    """A complete backup of a made-up project, in the layout of docs/contracts/backup.md."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "backup.json").write_text(json.dumps(dict(created=CREATED, project=dict(name=name, repo=f"someone/{name}", modules="files/modules.json", code="files/code"))))
    files = root / "files"
    (files / "code").mkdir(parents=True)
    (files / "modules.json").write_text(json.dumps(dict(modules=[dict(id="core", name="Core")], assign=[dict(path="*", module="core")])))
    (files / "code" / "metrics.json").write_text(json.dumps(dict(generated_at="2026-11-24", modules=dict(core=dict(name="Core", metrics={})))))
    at = CREATED - 60
    state = dict(today="2026-11-24", tasks=[], questions=[], modules=[], modDeadline={}, proposals=[], events=[], unauthorised=0,
                 source=dict(name=name, repo=f"someone/{name}", steering=True, simulate=False, project=None, manager="someone", user="someone", fetched="10:00:00", fetchedAt=at))
    cache = root / "cache"
    cache.mkdir()
    for what, v in dict(state=state, timeline=dict(entries=[], total=0, activity=[], stateAt=at), cardmeta=dict(dir="")).items():
        (cache / f"{name}_{what}.json").write_text(json.dumps(v))
    data = root / "data" / name
    (data / "processing").mkdir(parents=True)
    (data / "dispatcher-state.json").write_text(json.dumps(dict(workers={})))
    (data / "processing" / "processing.jsonl").write_text(json.dumps(dict(time="2026-11-24T10:00:00Z", task="#1", state="done")) + "\n")
    (data / "proxy-usage.jsonl").write_text("")
    return root


def pack(folder, archive):
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(folder, arcname="backup")
    return archive


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(api_context, "OFFLINE", dict(root="x", created=CREATED, gh_attempts=0))
    C._cache.clear()
    yield C.OFFLINE
    C._cache.clear()


# ---------- the gap checker (pure) ----------
def test_every_pane_is_listed_with_its_request():
    panes = {p for p, _, _ in L.OFFLINE_PANES}
    for want in ("tasks and states", "ranking", "timeline", "open questions", "rules", "task queue (local part)", "token usage", "settings", "code"):
        assert want in panes
    assert all(api.startswith("/api/") for _, api, _ in L.OFFLINE_PANES)


def test_gaps_name_the_pane_and_what_is_missing():
    full = {"cache:state", "cache:timeline", "norules", "data:dispatcher-state.json", "data:proxy-usage.jsonl", "code", "cache:cardmeta"}
    assert L.backup_gaps(full) == []
    g = {x["pane"]: x["missing"] for x in L.backup_gaps(full - {"cache:state", "code"})}
    assert set(g) == {"tasks and states", "ranking", "open questions", "timeline", "code"}
    assert g["timeline"] == ["cache:state"] and g["code"] == ["code"]
    assert {x["pane"] for x in L.backup_gaps(full - {"norules"})} == {"rules", "rules' open pull requests"}


def test_pane_problem_flags_missing_data_and_steering():
    assert L.pane_problem("/api/state", dict(tasks=[], source=dict(steering=False))) is None
    assert L.pane_problem("/api/state", dict(error="x")) == "no task state"
    assert L.pane_problem("/api/state", dict(tasks=[], source=dict(steering=True))) == "steering is on"
    assert L.pane_problem("/api/settings", dict(steering=True)) == "steering is on"
    assert L.pane_problem("/api/code", dict(wired=False)) == "code snapshot not wired"
    assert L.pane_problem("/api/rulesprs", dict(configured=True, available=False, error="e")).startswith("open pull requests")
    assert L.pane_problem("/api/questions", dict(projects=[dict(error="boom")])) == "project unreadable: boom"


# ---------- the offline switches ----------
def test_offline_makes_no_github_call(offline, monkeypatch):
    monkeypatch.setattr(C.subprocess, "run", lambda *a, **k: pytest.fail("gh was run"))
    with pytest.raises(C.GitHubUnavailable):
        C.run_gh(["api", "user"])
    assert offline["gh_attempts"] == 1


def test_offline_serves_an_old_copy_as_is(offline):
    C._cache["k"] = (0, "old")
    assert C.cached("k", lambda: pytest.fail("refreshed"), 10, persist=True) == "old"


def test_offline_never_writes_the_backup(offline, tmp_path, monkeypatch):
    monkeypatch.setattr(api_context, "CACHE_DIR", tmp_path)
    C._disk_save("k", 1)
    assert list(tmp_path.iterdir()) == []


def test_offline_actions_are_refused(offline, monkeypatch):
    monkeypatch.setitem(C.CFG, "steer", False)
    code, body = C.do_act(dict(action="settings"))
    assert code == 403 and "offline copy" in body["error"]


def test_offline_state_has_steering_off(offline, tmp_path, monkeypatch):
    root = make_backup(tmp_path / "b")
    monkeypatch.setattr(api_context, "CACHE_DIR", root / "cache")
    p, created = C.open_backup(root)
    assert created == CREATED and p["code"] == str((root / "files" / "code").resolve())
    assert C.get_state_served(p)["source"]["steering"] is False


def test_unpack_refuses_members_outside_the_folder(tmp_path):
    bad = tmp_path / "bad.tar"
    with tarfile.open(bad, "w") as tf:
        ti = tarfile.TarInfo("../escape.txt")
        ti.size = 1
        tf.addfile(ti, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="refused"):
        C.unpack_backup(bad, tmp_path / "out")


def test_manifest_paths_must_stay_inside_the_backup(tmp_path):
    root = make_backup(tmp_path / "b")
    m = json.loads((root / "backup.json").read_text())
    m["project"]["code"] = "../../elsewhere"
    (root / "backup.json").write_text(json.dumps(m))
    with pytest.raises(ValueError, match="outside the backup"):
        C.open_backup(root)


def test_gap_checker_on_a_backup(tmp_path):
    root = make_backup(tmp_path / "b")
    assert R.gaps(root) == []
    (root / "files" / "code" / "metrics.json").unlink()
    assert [g["pane"] for g in R.gaps(root)] == ["code"]


# ---------- the restore test ----------
def test_restore_of_a_complete_backup(tmp_path):
    archive = pack(make_backup(tmp_path / "b"), tmp_path / "backup.tar.gz")
    assert R.restore_check(archive) == []


def test_restore_fails_when_a_pane_has_no_data(tmp_path):
    root = make_backup(tmp_path / "b")
    (root / "cache" / "demo_state.json").unlink()
    problems = R.restore_check(root)
    assert any(x.startswith("gap: tasks and states") for x in problems)
    assert any(x.startswith("tasks and states (/api/state): HTTP 502") for x in problems)
    assert any("tried to reach GitHub" in x for x in problems)


@pytest.mark.skipif(not os.environ.get("COCKPIT_BACKUP"), reason="set COCKPIT_BACKUP to a real backup to check it")
def test_restore_of_a_real_backup():
    assert R.restore_check(os.environ["COCKPIT_BACKUP"]) == []
