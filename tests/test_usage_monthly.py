"""Token Usage "Per project, by month" (PC-113): synthetic projects, folders and months, no project names."""
import datetime as dt
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import cockpit as C
import logic as L

PAGE = (pathlib.Path(__file__).parent.parent / "web" / "index.html").read_text()
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def tr(ts, source, n=1, msg=None, kind=None):
    rec = dict(ts=ts, source=source, session="s-" + source, agent=None, message=msg or ts + source,
               input_tokens=n, output_tokens=n, cache_creation_input_tokens=n, cache_read_input_tokens=n)
    if kind:
        rec["kind"] = kind
    return json.dumps(rec)


def px(ts, n=1):
    return json.dumps(dict(ts=ts, worker="w", input_tokens=n, output_tokens=n, cache_creation_input_tokens=n, cache_read_input_tokens=n))


def agg(day, n):
    return json.dumps(dict(day=day, model="m", tokens=n, messages=1, sessions=1, source="stats-cache"))


def proj(name, start=None, usage=None, plog=None, sources=None):
    return dict(name=name, dir=name, sources=sources, start=start, usage=usage, plog=plog)


def rows(rep):
    return {p["name"]: {r["month"]: r for r in p["rows"]} for p in rep["projects"]}


@pytest.mark.parametrize("today,months", [(dt.date(2026, 11, 25), ["2026-11", "2026-10", "2026-09"]),
                                          (dt.date(2027, 2, 1), ["2027-02", "2027-01", "2026-12"]),
                                          (dt.date(2027, 1, 31), ["2027-01", "2026-12", "2026-11"])])
def test_the_months_shown_are_the_current_and_the_two_before(today, months):
    rep = L.monthly_by_project([proj("alpha", usage="")], [], [], today)
    assert rep["months"] == months and [r["month"] for r in rep["projects"][0]["rows"]] == months


def test_a_project_that_started_later_shows_nothing_earlier():
    rep = L.monthly_by_project([proj("alpha"), proj("beta", start="2027-01", usage=px("2026-12-03T10:00:00Z"))],
                               [tr("2026-12-05T10:00:00Z", "-src-beta")], [], dt.date(2027, 1, 10))
    r = rows(rep)["beta"]
    assert r["2026-12"] == dict(month="2026-12", state="before_start") and r["2026-11"]["state"] == "before_start"
    assert r["2027-01"]["state"] == "data" and r["2027-01"]["total"] == 0


def test_interactive_and_unattended_are_separate_and_add_up_to_the_sum():
    rep = L.monthly_by_project([proj("alpha", usage=px("2026-11-02T10:00:00Z", 3)), proj("beta")],
                               [tr("2026-11-03T10:00:00Z", "-src-alpha", 2), tr("2026-11-04T10:00:00Z", "-src-alpha", 5, kind="headless"),
                                tr("2026-10-04T10:00:00Z", "-src-beta", 1)], [], dt.date(2026, 11, 25))
    r = rows(rep)
    a = r["alpha"]["2026-11"]
    assert (a["interactive"], a["unattended"], a["total"]) == (8, 32, 40) and a["interactive"] + a["unattended"] == a["total"]
    assert a["input_tokens"] == 10 and a["last_day"] == "2026-11-04"
    b = r["beta"]
    assert b["2026-11"]["state"] == "data" and b["2026-11"]["unattended"] == 0   # no proxy file: zero, not "no data"
    assert b["2026-10"]["interactive"] == 4


def test_no_data_only_when_no_source_exists():
    assert rows(L.monthly_by_project([proj("alpha")], [None], [None], dt.date(2026, 11, 25)))["alpha"]["2026-11"]["state"] == "no_data"
    assert rows(L.monthly_by_project([proj("alpha")], [""], [None], dt.date(2026, 11, 25)))["alpha"]["2026-11"]["state"] == "data"


def test_the_aggregate_month_is_marked_and_goes_to_the_only_project_started():
    a = "\n".join([agg("2026-09-10", 100), agg("2026-10-10", 50), agg("2026-11-03", 7), agg("2026-11-04", 9)])
    rep = L.monthly_by_project([proj("alpha"), proj("beta", start="2026-11")], [tr("2026-11-04T10:00:00Z", "-src-alpha")], [a, a],
                               dt.date(2026, 11, 25))
    r = rows(rep)
    sep = r["alpha"]["2026-09"]
    assert sep["aggregate"] == 100 and sep["total"] == 100 and sep["interactive"] == 0 and sep["input_tokens"] == 0  # no split; files read twice count once
    assert r["alpha"]["2026-10"]["aggregate"] == 50 and r["beta"]["2026-10"]["state"] == "before_start"
    assert r["alpha"]["2026-11"]["aggregate"] == 0    # a day with transcripts ignores its aggregate; two projects live: not this one's
    assert r["unassigned"]["2026-11"]["aggregate"] == 7
    assert "incl. daily aggregate, no split" in PAGE[PAGE.index("function projectsCard()"):]


def test_an_unmapped_folder_lands_in_unassigned_and_the_mapping_is_a_setting():
    t = [tr("2026-11-03T10:00:00Z", "-src-gamma", 1), tr("2026-11-03T11:00:00Z", "-src-alpha--claude-worktrees-x", 1),
         tr("2026-11-03T12:00:00Z", "-src-alpha-beta", 1), tr("2026-11-03T13:00:00Z", "special", 1)]
    r = rows(L.monthly_by_project([proj("alpha"), proj("alpha-beta", sources=["special"])], t, [], dt.date(2026, 11, 25)))
    assert r["alpha"]["2026-11"]["interactive"] == 4                       # its worktree included
    assert r["alpha-beta"]["2026-11"]["interactive"] == 8                  # the longest directory wins; the explicit source too
    assert r["unassigned"]["2026-11"]["interactive"] == 4
    assert "unassigned" not in rows(L.monthly_by_project([proj("alpha")], [t[1]], [], dt.date(2026, 11, 25)))


def test_project_settings_are_validated():
    ps, _ = C.projects_from_file({"projects": [{"name": "a", "repo": "o/a", "start_month": "2026-11", "transcript_sources": ["-x-a"]}]})
    assert ps[0]["start_month"] == "2026-11" and ps[0]["transcript_sources"] == ["-x-a"]
    for bad in ({"start_month": "2026-13"}, {"start_month": 202611}, {"transcript_sources": "-x-a"}, {"transcript_sources": [""]}):
        with pytest.raises(ValueError):
            C.projects_from_file({"projects": [dict({"name": "a", "repo": "o/a"}, **bad)]})


@node
def test_the_card_states_the_period_and_the_months():
    code = "\n".join([re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0), re.search(r"^const USEF=.*$", PAGE, re.M).group(0),
                      PAGE[PAGE.index("function projectsCard(){"):PAGE.index("/* The order of the Token Usage cards")],
                      "var USE=%s;process.stdout.write(projectsCard())" % json.dumps(dict(monthly=L.monthly_by_project(
                          [proj("alpha", usage=""), proj("beta"), proj("gamma", start="2026-11")], [None], [agg("2026-09-01", 5)], dt.date(2026, 11, 25))))])
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "<h2>Per project, by month</h2>" in out and "November 2026, October 2026, September 2026" in out
    assert "before the project started" in out and "incl. daily aggregate, no split" in out
