"""The daily backup (PC-62) on a made-up project (project-agnostic): the archive's place, name and content, create-only writing, the secret and
repository exclusions, the never-deleting transcript mirror, the status record, and the failure noticing (gap kind, Task Queue payload)."""
import datetime as dt
import json
import pathlib
import subprocess
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import backup_restore as R  # noqa: E402
import cockpit as C  # noqa: E402
import logic as L  # noqa: E402
import project_backup as B  # noqa: E402
import test_offline as O  # noqa: E402

NOW = dt.datetime(2026, 11, 24, 3, 0, tzinfo=dt.timezone.utc)


def setup(tmp, name="demo"):
    """A data folder, cache folder, clone, code snapshot and transcript folder of a made-up project -> (argv, paths)."""
    data, cache, clone, tr, drive = (tmp / x for x in ("data", "cache", "clone", "claude-projects/-somewhere-demo", "drive"))
    d = data / name
    for sub in ("processing", "cardmeta", "area/t1/outbox", "area/t1/inbox"):
        (d / sub).mkdir(parents=True)
    (d / "dispatcher-state.json").write_text(json.dumps(dict(workers={})))
    (d / "processing" / "processing.jsonl").write_text(json.dumps(dict(time="2026-11-24T01:00:00Z", task="#1", state="done")) + "\n")
    (d / "quota-history.jsonl").write_text('{"ts": "2026-11-24T01:00:00Z"}\n')
    (d / "cardmeta" / "churn.json").write_text("{}")
    (d / "area/t1/outbox/goodbye.json").write_text('{"outcome": "done"}')
    (d / "area/t1/outbox/result.bundle").write_text("code")
    (d / "area/t1/inbox/main.bundle").write_text("code")
    (d / "dispatcher.log").write_text("log")
    (d / "token").write_text("SECRET")
    (d / "proxy-token").write_text("SECRET")
    (O.make_backup(tmp / "ref", name) / "cache").rename(cache)  # the cache files of PC-63's generated backup: state, timeline, cardmeta
    (cache / f"{name}_x_state.json").write_text("{}")  # another project whose name starts with this one's
    (cache / "other_state.json").write_text("{}")
    code = tmp / "snapshot"
    code.mkdir()
    (code / "metrics.json").write_text(json.dumps(dict(modules={})))
    (code / ".env").write_text("KEY=SECRET")
    clone.mkdir()
    (clone / "map.json").write_text(json.dumps(dict(modules=[dict(id="core", name="Core")], assign=[])))
    tr.mkdir(parents=True)
    (tr / "s1.jsonl").write_text('{"text": "hello"}\n')
    (tr / "s1" / "subagents").mkdir(parents=True)
    (tr / "s1" / "subagents" / "a.jsonl").write_text("{}\n")
    drive.mkdir()
    pf = tmp / "projects.json"
    pf.write_text(json.dumps(dict(projects=[dict(name=name, repo=f"someone/{name}", git=str(clone), modules=str(clone / "map.json"), code=str(code)),
                                            dict(name=f"{name}_x", repo=f"someone/{name}_x"), dict(name="other", repo="someone/other")])))
    argv = ["--drive", str(drive), "--project", name, "--projects-file", str(pf), "--data-dir", str(data), "--cache-dir", str(cache), "--transcripts", str(tr)]
    return argv, dict(data=d, drive=drive, tr=tr)


def names(archive):
    with tarfile.open(archive) as tf:
        return {m.name.split("/", 1)[1] for m in tf.getmembers()}


def status(paths):
    return json.loads((paths["data"] / L.BACKUP_STATUS_FILE).read_text())


def test_daily_archive_holds_the_cached_data_and_restores_without_gaps(tmp_path):
    argv, paths = setup(tmp_path)
    assert B.main(argv, now=NOW) == 0
    arch = paths["drive"] / "project_cockpits" / "demo" / "demo-2026-11-24.tar.gz"
    got = names(arch)
    assert {"backup.json", "data/demo/dispatcher-state.json", "data/demo/processing/processing.jsonl", "data/demo/quota-history.jsonl",
            "data/demo/cardmeta/churn.json", "data/demo/area/t1/outbox/goodbye.json", "cache/demo_state.json", "cache/demo_timeline.json",
            "files/code/metrics.json", "files/projects.json", "data/demo/transcripts/-somewhere-demo/s1.jsonl",
            "data/demo/transcripts/-somewhere-demo/s1/subagents/a.jsonl"} <= got
    st = status(paths)
    assert st["ok"] and st["archive"] == str(arch) and st["size"] == arch.stat().st_size and st["files"] == len(got) and st["last_ok"] == st["time"]
    assert R.gaps(C.unpack_backup(arch, tmp_path / "restore")) == []
    assert R.restore_check(arch) == []  # a copy of the Cockpit runs from the archive alone (PC-63's restore test)


def test_secrets_repository_files_logs_and_other_projects_are_never_packed(tmp_path):
    argv, paths = setup(tmp_path)
    B.main(argv, now=NOW)
    got = names(next((paths["drive"] / "project_cockpits" / "demo").iterdir()))
    for x in ("data/demo/token", "data/demo/proxy-token", "files/code/.env", "data/demo/area/t1/outbox/result.bundle",
              "data/demo/area/t1/inbox/main.bundle", "data/demo/dispatcher.log", "cache/demo_x_state.json", "cache/other_state.json"):
        assert x not in got
    assert not any(n.startswith("files/map") for n in got)  # the module map lies in the clone: it is in the repository
    assert "modules" not in json.loads(tarfile.open(paths["drive"] / "project_cockpits/demo/demo-2026-11-24.tar.gz").extractfile(
        "demo-2026-11-24/backup.json").read())["project"]
    assert status(paths)["refused"] == ["files/code/.env"]  # the data folder's tokens are not even candidates; a stray one is refused by name


def test_secret_names():
    for n in ("token", "proxy-token", ".env", "prod.env", ".env.local", "credentials.json", "id_ed25519", "server.pem", "api_secret.txt"):
        assert B.is_secret(n), n
    for n in ("transcript-usage.jsonl", "quota-history.jsonl", "demo_state.json", "metrics.json", "goodbye.json", "4f1c.jsonl"):
        assert not B.is_secret(n), n


def test_a_taken_name_gets_a_suffix_and_nothing_in_drive_is_changed(tmp_path):
    argv, paths = setup(tmp_path)
    B.main(argv, now=NOW)
    folder = paths["drive"] / "project_cockpits" / "demo"
    first = (folder / "demo-2026-11-24.tar.gz").read_bytes()
    assert B.main(argv, now=NOW) == 0 and B.main(argv, now=NOW) == 0
    assert sorted(f.name for f in folder.iterdir()) == ["demo-2026-11-24-2.tar.gz", "demo-2026-11-24-3.tar.gz", "demo-2026-11-24.tar.gz"]
    assert (folder / "demo-2026-11-24.tar.gz").read_bytes() == first


def test_the_transcript_mirror_never_deletes_and_follows_growth(tmp_path):
    argv, paths = setup(tmp_path)
    B.main(argv, now=NOW)
    (paths["tr"] / "s1.jsonl").unlink()  # the coding tool removed an old transcript
    (paths["tr"] / "s1" / "subagents" / "a.jsonl").write_text("{}\n{}\n")
    B.main(argv, now=NOW + dt.timedelta(days=1))
    m = paths["data"] / "transcripts" / "-somewhere-demo"
    assert (m / "s1.jsonl").read_text() == '{"text": "hello"}\n' and (m / "s1/subagents/a.jsonl").read_text() == "{}\n{}\n"
    assert "data/demo/transcripts/-somewhere-demo/s1.jsonl" in names(paths["drive"] / "project_cockpits/demo/demo-2026-11-25.tar.gz")


def test_a_failed_run_writes_a_failure_record_and_keeps_the_last_good_time(tmp_path):
    argv, paths = setup(tmp_path)
    B.main(argv, now=NOW)
    good = status(paths)["time"]
    (paths["drive"]).rename(tmp_path / "unmounted")
    assert B.main(argv, now=NOW + dt.timedelta(days=1)) == 1
    st = status(paths)
    assert st["ok"] is False and "does not exist" in st["reason"] and st["last_ok"] == good and st["archive"] is None


def test_unusable_arguments_exit_2(tmp_path):
    argv, _ = setup(tmp_path)
    assert B.main(argv[:2] + ["--project", "nope"] + argv[4:], now=NOW) == 2


# ---------- failure noticing: the gap kind and the Task Queue payload (pure, generic) ----------
def rec(ok=True, at="2026-11-24T03:00:00Z", last_ok="2026-11-24T03:00:00Z", reason=None):
    return json.dumps(dict(schema=1, time=at, ok=ok, archive="x", size=1, files=1, reason=reason, last_ok=last_ok))


def gaps(text, now=NOW):
    return [(w, a) for w, a in L.backup_status_gaps(text, now)]


def test_backup_gap_kinds_missing_failed_stale_and_clean():
    assert gaps(rec()) == []
    assert [w for w, _ in gaps(None)] == ["no backup recorded: no daily backup has run, or its status record is unreadable"]
    assert [w for w, _ in gaps("{not json")] == [w for w, _ in gaps(None)]
    failed = gaps(rec(ok=False, reason="OSError: no Drive"))
    assert failed[0][0] == "last backup failed at 2026-11-24T03:00:00Z: OSError: no Drive" and len(failed) == 1
    later = NOW + dt.timedelta(hours=30)
    assert gaps(rec(), later) == [("last successful backup is older than 24 hours", 30.0)]
    assert [w for w, _ in gaps(rec(ok=False, last_ok=None), later)][1] == "no successful backup recorded"


def test_the_gap_checker_reports_kind_backup():
    base = dict(modules=None, cards={}, metrics=None, usage={}, policies={})
    kinds = lambda **kw: [g for g in L.metadata_gaps("p", NOW, **base, **kw) if g["kind"] == "backup"]
    assert kinds() == []  # not asked: not checked
    assert len(kinds(backup=dict(text=None))) == 1 and kinds(backup=dict(text=rec())) == []
    g = kinds(backup=dict(text=rec(ok=False, reason="r")))[0]
    assert g["module"] is None and g["project"] == "p" and "failed" in g["what"]


def test_the_task_queue_payload_carries_the_last_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    (tmp_path / "p").mkdir()
    assert C.get_queue(dict(name="p", repo="o/p"))["backup"] is None
    (tmp_path / "p" / L.BACKUP_STATUS_FILE).write_text(rec(ok=False, reason="r"))
    b = C.get_queue(dict(name="p", repo="o/p"))["backup"]
    assert b["ok"] is False and b["reason"] == "r" and b["time"] == "2026-11-24T03:00:00Z"


def test_the_page_shows_the_last_backup():
    page = (ROOT / "web" / "index.html").read_text()
    assert "bkLine(L.backup)" in page and "Daily backup: " in page


def test_retention_script_keeps_a_count_not_an_age():
    gs = (ROOT / "tools" / "drive_retention.gs").read_text()
    assert "const KEEP = 30;" in gs and "project_cockpits" in gs and "slice(KEEP)" in gs and "getLastUpdated" not in gs


def test_the_script_runs_from_the_command_line(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "project_backup.py"), "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "--drive" in r.stdout
