import json

from ckp import cli
from ckp.core import tasks as core_tasks
from tests.ckp.core.test_core_tasks import Run, node


def run_cli(argv, run, capsys):
    a = cli.build_parser().parse_args(argv)
    code = a.func(a, run=run)
    return code, json.loads(capsys.readouterr().out)


def test_list(capsys):
    code, out = run_cli(["task", "list", "--project", "p", "--repo", "own/cockpit", "--short", "PC", "--state", "embryo"], Run([node(1), node(2, ["wip"])]), capsys)
    assert code == 0 and [t["id"] for t in out["data"]["tasks"]] == ["PC-1"]


def test_show_and_missing(capsys):
    base = ["--project", "p", "--repo", "own/cockpit", "--short", "PC"]
    code, out = run_cli(["task", "show", "PC-1", *base], Run([node(1, body="b")]), capsys)
    assert code == 0 and out["data"]["text"] == "b"
    code, out = run_cli(["task", "show", "PC-5", *base], Run([node(1)]), capsys)
    assert code == 1 and not out["ok"]


def test_file_and_comment(capsys):
    base = ["--project", "p", "--repo", "own/cockpit", "--short", "PC"]
    run = Run()
    code, out = run_cli(["task", "file", *base, "--title", "T", "--body", "B", "--label", "bug"], run, capsys)
    assert code == 0 and out["data"]["id"] == "PC-42"
    code, out = run_cli(["task", "file", *base, "--title", "T", "--label", "released"], run, capsys)
    assert code == 1 and not out["ok"]
    code, out = run_cli(["task", "comment", "PC-3", *base, "--text", "hi"], run, capsys)
    assert code == 0 and out["data"]["number"] == 3


def test_unknown_project_without_repo(capsys):
    code, out = run_cli(["task", "list", "--project", "nope"], Run(), capsys)
    assert code == 1 and "unknown project" in out["error"]


def test_no_release_subcommand():
    import pytest
    for sub in ("release", "cancel", "priority"):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["task", sub, "--project", "p"])


def test_main_exit_2_on_usage():
    assert cli.main(["task", "list"]) == 2
    assert core_tasks  # module imported by the CLI
