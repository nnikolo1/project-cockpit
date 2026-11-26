"""tools/policy_harness.py on synthetic projects: each check passes on clean data and fails on its violation; the runner's exit code and
report; deduplication; the finding's text; no filing in a dry run (the filing function is replaced, never `gh`)."""
import datetime as dt
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import policy_harness as H  # noqa: E402

NOW = dt.datetime(2026, 11, 24, 12, 0, tzinfo=dt.timezone.utc)
FRESH, OLD = "2026-11-24T10:00:00Z", "2026-11-22T10:00:00Z"
MODS = ["module-a", "module-a/one"]


def card(kind, generated_at=FRESH, modules=None):
    return {"schema": 1, "kind": kind, "project": "demo", "generated_at": generated_at,
            "modules": modules if modules is not None else {m: {"own_files": 7, "sub_files": {}, "unsorted": []} for m in MODS}}


def write(tmp_path, cards=None, usage_ts=FRESH, backup_time=FRESH, projects=("demo",)):
    """A projects file and, per project, a clean data folder (a two-module map, the three cards, token files, a good backup);
    `cards` overrides card files by kind (None: leave the file out)."""
    (tmp_path / "m.json").write_text(json.dumps({"modules": [{"id": m, "name": m, "desc": "x"} for m in MODS], "assign": []}))
    (tmp_path / "projects.json").write_text(json.dumps({"projects": [{"name": n, "repo": f"o/{n}", "modules": str(tmp_path / "m.json")} for n in projects]}))
    for n in projects:
        d = tmp_path / n
        (d / "cardmeta").mkdir(parents=True)
        for k in ("footprint", "churn", "ambiguity"):
            c = (cards or {}).get(k, card(k))
            if c is not None:
                (d / "cardmeta" / f"{k}.json").write_text(json.dumps(dict(c, project=n)))
        for f in ("quota-history.jsonl", "transcript-usage.jsonl", "proxy-usage.jsonl"):
            (d / f).write_text(json.dumps({"ts": usage_ts}) + "\n")
        (d / "backup-status.json").write_text(json.dumps({"time": backup_time, "ok": True, "last_ok": backup_time}))


class Gh:
    """Stands in for the filing function and the open-findings listing."""
    def __init__(self, open_keys=()):
        self.filed, self.open = [], set(open_keys)

    def file(self, repo, title, body, check):
        self.filed.append(dict(repo=repo, title=title, body=body, check=check))
        return f"https://example.invalid/{repo}/issues/{len(self.filed)}"

    def keys(self, repo):
        return set(self.open)


def run(tmp_path, capsys, *extra, gh=None):
    code = H.main(["--projects-file", str(tmp_path / "projects.json"), "--data-dir", str(tmp_path), "--json", *extra], now=NOW,
                  file=(gh or Gh()).file, keys=(gh or Gh()).keys)
    return code, json.loads(capsys.readouterr().out)


def checks_of(rep):
    return sorted({(v["check"], v["subject"]) for p in rep["projects"] for v in p["violations"]})


def unsorted_card(n_unsorted, total):
    m = {"own_files": total - n_unsorted, "sub_files": {}, "unsorted": [{"path": f"f{i}.py", "candidates": ["module-a/one", "module-a/two"],
                                                                         "reason": "fits both"} for i in range(n_unsorted)]}
    return card("ambiguity", modules={"module-a": m, "module-a/one": {"own_files": 1}})


# --- each check: clean data passes, its violation fails ---------------------------------------------------------------------------------
def test_clean_project_has_no_violation_and_exit_0(tmp_path, capsys):
    write(tmp_path)
    code, rep = run(tmp_path, capsys)
    assert code == 0 and rep["count"] == 0 and rep["dry_run"] is True


@pytest.mark.parametrize("n, violated", [(3, False), (4, True)])  # 3 of 10 is exactly 30 percent: no violation ("more than")
def test_unsorted_share(tmp_path, capsys, n, violated):
    write(tmp_path, cards={"ambiguity": unsorted_card(n, 10)})
    code, rep = run(tmp_path, capsys)
    assert (("structure.unsorted-share", "module-a") in checks_of(rep)) is violated and code == int(violated)


def test_freshness(tmp_path, capsys):
    write(tmp_path, cards={"churn": card("churn", OLD)}, usage_ts=OLD)
    code, rep = run(tmp_path, capsys)
    assert code == 1 and {s for c, s in checks_of(rep) if c == "freshness.generated-24h"} == {"churn card data", "quota history", "transcript usage file"}


def test_footprint_missing_for_a_module(tmp_path, capsys):
    write(tmp_path, cards={"footprint": card("footprint", modules={"module-a": {}})})
    assert ("footprint.every-module", "module-a/one") in checks_of(run(tmp_path, capsys)[1])


def test_footprint_card_missing(tmp_path, capsys):
    write(tmp_path, cards={"footprint": None})
    assert ("footprint.every-module", "footprint card") in checks_of(run(tmp_path, capsys)[1])


def test_backup_stale_or_missing(tmp_path, capsys):
    write(tmp_path, backup_time=OLD)
    assert ("backup.fresh", "backup") in checks_of(run(tmp_path, capsys)[1])
    (tmp_path / "demo" / "backup-status.json").unlink()
    assert ("backup.fresh", "backup") in checks_of(run(tmp_path, capsys)[1])


def test_registry_entries_are_complete():
    ids = [c.id for c in H.CHECKS]
    assert len(ids) == len(set(ids))
    for c in H.CHECKS:
        assert c.title and "owner, 20" in c.policy and c.threshold and "{subject}" in c.remediation and callable(c.run)


# --- the runner -------------------------------------------------------------------------------------------------------------------------
def test_all_projects_and_text_report(tmp_path, capsys):
    write(tmp_path, backup_time=OLD, projects=("demo", "other"))
    code = H.main(["--projects-file", str(tmp_path / "projects.json"), "--data-dir", str(tmp_path), "--all"], now=NOW, file=Gh().file, keys=Gh().keys)
    out = capsys.readouterr().out
    assert code == 1 and "demo | backup.fresh | backup" in out and "other | backup.fresh | backup" in out and "dry run: nothing filed" in out


def test_unknown_project_gives_exit_2(tmp_path, capsys):
    write(tmp_path)
    assert H.main(["--projects-file", str(tmp_path / "projects.json"), "--project", "nope"], now=NOW) == 2


def test_dry_run_files_nothing(tmp_path, capsys):
    write(tmp_path, backup_time=OLD)
    gh = Gh()
    code, rep = run(tmp_path, capsys, gh=gh)
    assert code == 1 and gh.filed == [] and not any("task" in v for p in rep["projects"] for v in p["violations"])


def test_file_tasks_files_one_per_violation_and_deduplicates(tmp_path, capsys):
    write(tmp_path, backup_time=OLD, cards={"ambiguity": unsorted_card(5, 10)})
    gh = Gh(open_keys={("backup.fresh", "demo", "backup")})
    code, rep = run(tmp_path, capsys, "--file-tasks", gh=gh)
    vs = {v["check"]: v for v in rep["projects"][0]["violations"]}
    assert code == 1 and vs["backup.fresh"].get("duplicate") and vs["structure.unsorted-share"]["task"].endswith("/issues/1")
    assert [f["check"] for f in gh.filed] == ["structure.unsorted-share"] and gh.filed[0]["repo"] == "o/demo"


def test_filing_errors_are_reported_not_raised(tmp_path, capsys):
    write(tmp_path, backup_time=OLD)
    def broken(*a):
        raise OSError("gh missing")
    code = H.main(["--projects-file", str(tmp_path / "projects.json"), "--data-dir", str(tmp_path), "--json", "--file-tasks"], now=NOW, file=broken, keys=Gh().keys)
    assert code == 1 and "gh missing" in json.loads(capsys.readouterr().out)["projects"][0]["violations"][0]["error"]


# --- the finding's text -----------------------------------------------------------------------------------------------------------------
def test_finding_text_of_the_analysis_check():
    v = dict(check="structure.unsorted-share", project="demo", subject="module-a", evidence=["4 of 10 files unsorted", "`f0.py`: candidates x"])
    title, body = H.finding(v, "policy-harness", "2026-11-24")
    assert "module-a" in title and "demo" in title
    assert "Work (an analysis):" in body and "suggestions for the owner" in body and "do not change the module map" in body
    assert "more than 30 percent" in body and "owner, 2026-11-24" in body and "UNSORTED_SHARE = 0.3" in body
    assert "- 4 of 10 files unsorted" in body and "<!-- filed-by: policy-harness -->" in body
    assert {tuple(m.groups()) for m in H.MARKER.finditer(body)} == {H.key(v)}


def test_file_task_creates_a_labelled_issue_and_a_trail_comment():
    calls = []
    url = H.file_task("o/demo", "T", "B", "backup.fresh", run=lambda args: calls.append(args) or "https://example.invalid/1")
    assert url == "https://example.invalid/1" and calls[0][:3] == ["issue", "create", "-R"]
    assert "policy-finding" in calls[0] and "author:job" in calls[0] and "released" not in calls[0]
    assert calls[1][:3] == ["issue", "comment", url] and "backup.fresh" in calls[1][-1]


def test_open_keys_reads_the_markers():
    v = dict(check="freshness.generated-24h", project="demo", subject="quota history", evidence=["x"])
    body = H.finding(v, "policy-harness", "2026-11-24")[1]
    assert H.open_keys("o/demo", run=lambda args: json.dumps([{"body": body}, {"body": "no marker"}])) == {H.key(v)}
