import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import cardmeta_collect as co  # noqa: E402


def ns(**kw):
    return argparse.Namespace(**{"map": None, "declared": None, "days": None, "only": None, **kw})


def test_plan_runs_all_three_in_order_and_passes_arguments():
    p = co.plan(ns(days=[7], map="m.json"), pathlib.Path("/r"), "demo", pathlib.Path("/o"))
    assert [n for n, _f, _a in p] == ["footprint", "churn", "ambiguity"]
    churn = dict((n, a) for n, _f, a in p)["churn"]
    assert churn[:6] == ["--repo", "/r", "--project", "demo", "--out-dir", "/o"] and "--map" in churn and churn[-2:] == ["--days", "7"]


def test_plan_only_filters():
    assert [n for n, _f, _a in co.plan(ns(only=["churn"]), pathlib.Path("/r"), "demo", pathlib.Path("/o"))] == ["churn"]
