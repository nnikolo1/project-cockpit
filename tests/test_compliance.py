"""Settings > Policies, compliance (PC-109): violations, the manual run, the last report link and date, on two synthetic projects."""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import cockpit as C
from ckp.api import context as api_context
import logic as L

ROOT = pathlib.Path(__file__).parent.parent
PAGE = (ROOT / "web" / "index.html").read_text()
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")

REPORT = """# Compliance report

Checked: {checked}

## Scope
- CLAUDE.md (found)

## Policies discovered
- "Stdlib only" (CLAUDE.md:4) -> local only

## Violations
{violations}
## Constitution
Version 2026-11-25, dated 2026-11-25
"""
V1 = """### CLAUDE.md:12
> Agents may rewrite
> the policy files.
- Rule: Local policy files are never overwritten automatically
- Reason: the passage allows agents to write policy files
"""
V2 = """### docs/rules.md
> Merge without review.
- Rule: Merging
- Reason: skips the verification before a merge
"""
# the check of the synthetic projects: writes the report with a fresh date, as PC-108's check does (an optional pause first)
CHECK = ("import os, sys, time, pathlib; time.sleep(float(sys.argv[1])); f = pathlib.Path(os.environ['COMPLIANCE_REPORT']); f.parent.mkdir(parents=True, exist_ok=True); "
         "f.write_text('# Compliance report\\n\\nChecked: %d\\n\\n## Violations\\nnone\\n' % time.time_ns())")


def projects(tmp_path, pause="0"):
    out = []
    for name in ("alpha", "beta"):
        (tmp_path / name).mkdir()
        out.append(dict(name=name, repo="o/" + name, git=str(tmp_path / name), compliance=[sys.executable, "-c", CHECK, pause]))
    return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(api_context, "DATA_DIR", tmp_path / "data")
    monkeypatch.setitem(C.CFG, "steer", True)
    def setup(pause="0"):
        ps = projects(tmp_path, pause)
        C.register(ps, "alpha")
        return ps
    yield setup
    C.register([], None)


def write_report(p, text):
    f = pathlib.Path(p["git"]) / L.COMPLIANCE_REPORT
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


def wait_ended(p, s=10):
    end = time.time() + s
    while C.compliance_of(p)["run"]["state"] == "running" and time.time() < end:
        time.sleep(0.05)
    return C.compliance_of(p)


def test_parse_reads_four_fields_and_the_date():
    r = L.parse_compliance(REPORT.format(checked="2026-11-25 10:00", violations=V1 + "\n" + V2))
    assert r["checked"] == "2026-11-25 10:00" and len(r["violations"]) == 2
    a, b = r["violations"]
    assert a == dict(file="CLAUDE.md", line=12, passage="Agents may rewrite the policy files.",
                     rule="Local policy files are never overwritten automatically", reason="the passage allows agents to write policy files")
    assert b["file"] == "docs/rules.md" and b["line"] is None and b["passage"] == "Merge without review." and b["rule"] == "Merging"


def test_parse_none_and_missing_section():
    assert L.parse_compliance(REPORT.format(checked="x", violations="none\n"))["violations"] == []
    assert L.parse_compliance("# empty") == dict(checked=None, violations=[])


def test_run_state():
    assert L.compliance_run_state(None, False) == "idle"
    assert L.compliance_run_state(dict(state="running"), True) == "running"
    assert L.compliance_run_state(dict(state="running"), False) == "interrupted"
    assert L.compliance_run_state(dict(state="ended", exit=0), False) == "done"
    assert L.compliance_run_state(dict(state="ended", exit=1), False) == "failed"


def test_tab_lists_violations_of_every_project_and_never_run(env):
    a, b = env()
    write_report(a, REPORT.format(checked="2026-11-25 10:00", violations=V1 + "\n" + V2))
    view = {p["name"]: p for p in C.build_compliance()["projects"]}
    assert set(view) == {"alpha", "beta"}
    assert [v["file"] for v in view["alpha"]["report"]["violations"]] == ["CLAUDE.md", "docs/rules.md"]
    assert view["alpha"]["report"]["checked"] == "2026-11-25 10:00" and view["alpha"]["configured"]
    assert view["beta"]["report"] is None and view["beta"]["run"]["state"] == "idle"   # "never run"


@pytest.mark.ui_action("server:compliance")
def test_button_starts_the_command_shows_running_then_date_changes_on_a_second_run(env):
    a, b = env(pause="0.6")
    code, body = C.do_act(dict(action="compliance", project="beta"))
    assert code == 200 and "beta" in body["msg"]
    assert C.compliance_of(b)["run"]["state"] == "running"
    assert C.do_act(dict(action="compliance", project="beta"))[0] == 400          # one run at a time per project
    assert C.do_act(dict(action="compliance", project="alpha"))[0] == 200         # another project may run at once
    first = wait_ended(b)
    assert first["run"]["state"] == "done" and first["report"]["checked"] and first["report"]["violations"] == []
    wait_ended(a)
    assert C.do_act(dict(action="compliance", project="beta"))[0] == 200
    second = wait_ended(b)
    assert second["run"]["state"] == "done" and second["report"]["checked"] != first["report"]["checked"]   # the new run replaced the report
    assert (pathlib.Path(b["git"]) / L.COMPLIANCE_REPORT).is_file() and sorted(pathlib.Path(b["git"]).rglob("*")) == [
        pathlib.Path(b["git"]) / "docs", pathlib.Path(b["git"]) / "docs" / "compliance", pathlib.Path(b["git"]) / L.COMPLIANCE_REPORT]


def test_failed_and_interrupted_runs_are_shown(env):
    a, _ = env()
    a["compliance"] = [sys.executable, "-c", "raise SystemExit(3)"]
    assert C.do_act(dict(action="compliance", project="alpha"))[0] == 200
    r = wait_ended(a)["run"]
    assert r["state"] == "failed" and r["exit"] == 3
    C._write_json(C.compliance_status_path(a), dict(state="running", pid=2 ** 22 + 12345, started=time.time()))
    assert C.compliance_of(a)["run"]["state"] == "interrupted"


@pytest.mark.ui_action("server:compliance/refused")
def test_refusals(env, monkeypatch):
    a, _ = env()
    monkeypatch.setitem(C.CFG, "steer", False)
    assert C.do_act(dict(action="compliance", project="alpha"))[0] == 403   # read-only
    monkeypatch.setitem(C.CFG, "steer", True)
    a["compliance"] = None
    code, body = C.do_act(dict(action="compliance", project="alpha"))
    assert code == 400 and "not configured" in body["error"] and not C.compliance_of(a)["configured"]


def test_projects_file_takes_the_command():
    ps, _ = C.projects_from_file({"projects": [{"name": "x", "repo": "o/x", "git": "/tmp/x", "compliance": "python3 check.py --all"}]})
    assert ps[0]["compliance"] == ["python3", "check.py", "--all"]
    with pytest.raises(ValueError, match="compliance"):
        C.projects_from_file({"projects": [{"name": "x", "repo": "o/x", "compliance": 5}]})


# ---------- the page ----------
def run_node(code):
    esc = re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0)
    block = PAGE[PAGE.index("/* compliance:start */"):PAGE.index("/* compliance:end */")]
    r = subprocess.run(["node", "-e", esc + "const hm=t=>'HH:MM';const tmTxt=hm;" + block + code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def page(comp, armed=None):
    return run_node("let UI=%s;let COMP=%s;console.log(vCompliance())" % (json.dumps(dict(armed=armed)), json.dumps(comp)))


@node
def test_page_shows_violations_link_date_and_never_run(env):
    a, _ = env()
    write_report(a, REPORT.format(checked="2026-11-25 10:00", violations=V1))
    html = page(C.build_compliance())
    alpha, beta = html.split('data-comp="alpha"')[1].split('data-comp="beta"')
    assert "<b>1</b> violations" in alpha and 'href="/compliance-report?project=alpha"' in alpha and "2026-11-25 10:00" in alpha
    assert "never run" in beta and "<b>0</b> violations" in beta
    v = html.split("data-violation")[1]
    assert "CLAUDE.md:12" in v and "Agents may rewrite the policy files." in v and "never overwritten automatically" in v and "allows agents" in v
    assert html.count("Run compliance check") == 2


@node
def test_page_confirmation_running_state_and_read_only(env):
    a, b = env()
    comp = C.build_compliance()
    armed = page(comp, armed="compliance:beta")
    assert "Run the compliance check on <b>beta</b> now?" in armed and "Confirm" in armed and armed.count("Run compliance check") == 1
    comp["projects"][1]["run"] = dict(state="running", started=1)
    assert "Check running since HH:MM" in page(comp) and "disabled" in page(comp).split('data-comp="beta"')[1]
    comp["steering"] = False
    assert "Run compliance check" not in page(comp)


def test_report_link_serves_the_report(env):
    a, b = env()
    write_report(a, "# r\n")
    assert C.compliance_report_file(a).read_text() == "# r\n" and C.compliance_report_file(b) is None
