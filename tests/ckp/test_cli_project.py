import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from ckp import cli  # noqa: E402


def run_ok(args):
    return "true\n" if "rev-parse" in args else "git@github.com:o/n.git\n"


def call(argv, capsys, run=run_ok):
    a = cli.build_parser().parse_args(argv)
    code = cli.cmd_project(a, run=run)
    return code, json.loads(capsys.readouterr().out)


ADD = ["project", "add", "--name", "alpha", "--repo", "o/n", "--repo-dir", "/x", "--short", "AL", "--first", "3"]


def test_add_list_show_remove(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CP_HOME", str(tmp_path))
    code, out = call(ADD, capsys)
    assert code == 0 and out["data"]["project"]["first"] == 3
    code, out = call(["project", "list"], capsys)
    assert code == 0 and [p["name"] for p in out["data"]["projects"]] == ["alpha"]
    code, out = call(["project", "show", "alpha"], capsys)
    assert code == 0 and out["data"]["project"]["short"] == "AL"
    code, out = call(["project", "remove", "alpha"], capsys)
    assert code == 0
    code, out = call(["project", "show", "alpha"], capsys)
    assert code == 1 and not out["ok"]


def test_add_dry_run_and_failure_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CP_HOME", str(tmp_path))
    code, out = call(ADD + ["--dry-run"], capsys)
    assert code == 0 and out["dry_run"] and not (tmp_path / "projects.json").exists()
    code, out = call(ADD, capsys, run=lambda a: "false\n")
    assert code == 1 and not out["ok"]


def test_usage_error_is_exit_2():
    assert cli.main(["project", "add"]) == 2
