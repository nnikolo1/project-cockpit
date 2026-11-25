"""The one-command installer and its self-test (PC-100), generalized over any project names and any tree.

Step tests drive each step with injected prompts and functions; scenario tests run the whole flow in a sandbox (a
temporary home, fake GitHub and model credentials with recognisable secrets, a fake tree of project folders, the
service-manager and other commands replaced by a recorder); the no-leak test looks for the secrets everywhere.
No network, no real credentials, no service started.
Coverage (owner's machine, with pytest-cov): python3 -m pytest tests/tools/test_install.py tests/test_install_commands.py
--cov=install --cov-report=term-missing; the one line left uncovered is the `__main__` guard (run as a subprocess by
the drift test's --help check, outside the measured process).
"""
import json
import logging
import os
import pathlib
import shutil
import sys
import tempfile
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import install  # noqa: E402

MUTATING = ("bootstrap", "bootout", "enable", "daemon-reload", "setup.sh", "pre_push", "code_snapshot_refresh", "cardmeta_collect")


class Machine:
    """Recorder for every command: the service managers keep a set of loaded jobs, everything else succeeds."""

    def __init__(self, gh_secret, logged_in=True):
        self.calls, self.loaded, self.gh_secret, self.logged_in = [], set(), gh_secret, logged_in

    def run(self, argv):
        a = [str(x) for x in argv]
        self.calls.append(a)
        if a[:3] == ["gh", "auth", "status"]:
            return (0, f"Logged in to github.com account someone (token {self.gh_secret})") if self.logged_in else (1, "not logged in")
        if a[:2] == ["launchctl", "print"]:
            return (0, "state = running") if a[2].split("/")[-1] in self.loaded else (113, "")
        if a[:2] == ["launchctl", "bootstrap"]:
            self.loaded.add(pathlib.Path(a[3]).stem)
        if a[:2] == ["launchctl", "bootout"]:
            self.loaded.discard(a[2].split("/")[-1])
        if a[:3] == ["systemctl", "--user", "is-active"]:
            return (0, "active") if a[3] in self.loaded else (3, "inactive")
        if a[:3] == ["systemctl", "--user", "enable"]:
            self.loaded.add(a[-1])
        return 0, ""

    def mutations(self):
        return [c for c in self.calls if any(m in " ".join(c) for m in MUTATING)]


class Prompts:
    """Answers by the first rule whose key is in the question; an unexpected question fails the test."""

    def __init__(self, rules):
        self.rules, self.asked = dict(rules), []

    def __call__(self, question):
        self.asked.append(question)
        for key, answer in self.rules.items():
            if key in question:
                return answer
        raise AssertionError(f"unexpected question: {question}")


def git_clone(folder, remotes):
    (folder / ".git").mkdir(parents=True)
    (folder / ".git" / "config").write_text("[core]\n\tbare = false\n" + "".join(f'[remote "{n}"]\n\turl = {u}\n\tfetch = +refs/heads/*\n' for n, u in remotes.items()))
    (folder / "README.md").write_text("a project\n")


@pytest.fixture
def box():
    """A short temporary home (Unix socket paths of the queue must stay under 104 bytes) with the fake tree and secrets."""
    home = pathlib.Path(tempfile.mkdtemp(prefix="pc", dir="/tmp"))
    secrets = {"model": f"sk-model-{uuid.uuid4().hex}", "gh": f"gho_{uuid.uuid4().hex}"}
    cred = home / ".config/project-cockpit/model-token"
    cred.parent.mkdir(parents=True)
    cred.write_text(secrets["model"])
    cred.chmod(0o600)
    gh = home / ".config/gh/hosts.yml"
    gh.parent.mkdir(parents=True)
    gh.write_text(f"github.com:\n    oauth_token: {secrets['gh']}\n")
    src = home / "src"
    git_clone(src / "alpha", {"upstream": "https://example.org/x/y.git", "origin": "git@github.com:acme/alpha.git"})
    git_clone(src / "beta", {"origin": "https://github.com/acme/beta"})        # already installed
    git_clone(src / "localonly", {})
    (src / "notes").mkdir()
    (src / "notes" / "todo.txt").write_text("not a project\n")
    pf = home / ".config/project-cockpit/projects.json"
    pf.write_text(json.dumps({"default": "beta", "projects": [{"name": "beta", "repo": "acme/beta", "git": str(src / "beta")}]}))
    (home / ".claude").mkdir()
    (home / ".claude/settings.json").write_text('{"cleanupPeriodDays": 3650}')
    yield home, secrets
    shutil.rmtree(home)


def make_env(home, machine, prompts, system="Darwin", version=(3, 14), tools=("git", "gh", "container")):
    return install.Env(home=home, ask=prompts, run=machine.run, which=lambda t: f"/usr/bin/{t}" if t in tools else None,
                       fetch=lambda url: bool(machine.loaded), system=system, version=version, path="", uid=501)


YES = {"credential": "", "project folders": "", "Install the Cockpit in": "y", "viewer": "", "Dispatcher": "", "Carry out": ""}


def snapshot(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.fixture
def out(caplog):
    caplog.set_level(logging.INFO, logger="install")
    return lambda: [r.getMessage() for r in caplog.records if r.name == "install"]


# 1. Step tests

@pytest.mark.parametrize("version,ok,warned", [((3, 14), True, False), ((3, 13), True, True), ((3, 15), True, True), ((3, 9), False, False)])
def test_python_version_warns_never_refuses_above_the_floor(version, ok, warned, tmp_path):
    exe, msgs = install.check_python(install.Env(home=tmp_path, version=version, path=""))
    assert bool(exe) is ok
    assert any("not the tested version 3.14" in m for m in msgs) is warned
    if not ok:
        assert "install Python 3.14" in msgs[0]


def test_python_latest_on_path_is_used(tmp_path):
    for n in ("python3.11", "python3.16", "python3-config", "python3.x"):
        f = tmp_path / n
        f.write_text("")
        f.chmod(0o755)
    v, exe = install.find_python(install.Env(home=tmp_path, version=(3, 12), path=f"{tmp_path}{os.pathsep}/nonexistent"))
    assert v == (3, 16) and exe == str(tmp_path / "python3.16")


def test_missing_tools_name_their_fix(tmp_path):
    m = Machine("x", logged_in=False)
    env = install.Env(home=tmp_path, run=m.run, which=lambda t: "/bin/gh" if t == "gh" else None)
    miss = dict(install.missing_tools(env))
    assert set(miss) == {"git", "gh login"} and "gh auth login" in miss["gh login"]
    env.which = lambda t: None
    assert set(dict(install.missing_tools(env))) == {"git", "gh"}


def test_credential_lookup_by_path_and_mode_only(tmp_path, out):
    cred = tmp_path / "elsewhere" / "tok"
    cred.parent.mkdir()
    cred.write_text("secret")
    cred.chmod(0o000)   # unreadable: the lookup never opens it
    env = install.Env(home=tmp_path, ask=Prompts({"credential": str(cred)}))
    assert install.find_credential(env, None) == (cred, None)
    cred.chmod(0o644)
    path, problem = install.find_credential(env, None)
    assert problem == ("model credential", f"make it private: `chmod 600 {cred}`")
    env.ask = Prompts({"credential": ""})
    path, problem = install.find_credential(env, None)
    assert path == tmp_path / install.CRED_CANDIDATES[0] and "claude setup-token" in problem[1]
    assert "model credential: not found" in out()[-1]


def test_credential_default_is_the_saved_or_first_existing_path(tmp_path):
    second = tmp_path / install.CRED_CANDIDATES[1]
    second.parent.mkdir(parents=True)
    second.write_text("t")
    p = Prompts({"credential": ""})
    install.find_credential(install.Env(home=tmp_path, ask=p), None)
    assert f"[{second}]" in p.asked[0]
    install.find_credential(install.Env(home=tmp_path, ask=p), "/saved/path")
    assert f"[{second}]" in p.asked[1]   # the saved path no longer exists, the existing one wins


def test_projects_found_by_their_remotes(tmp_path):
    names = [uuid.uuid4().hex[:8] for _ in range(3)]
    git_clone(tmp_path / "p" / names[0], {"origin": f"https://github.com/o/{names[0]}.git"})
    git_clone(tmp_path / "p" / names[1], {"fork": f"git@github.com:o/{names[1]}", "origin": "ssh://host/x"})
    git_clone(tmp_path / "q" / names[2], {})
    (tmp_path / "q" / "plain").mkdir()
    found = install.scan([tmp_path / "p", tmp_path / "q", tmp_path / "absent"])
    assert sorted((d.name, r) for d, r in found) == sorted([(names[0], f"o/{names[0]}"), (names[1], f"o/{names[1]}"), (names[2], None)])


def test_project_folders_default_to_usual_places(tmp_path):
    (tmp_path / "code").mkdir()
    p = Prompts({"project folders": ""})
    assert install.project_dirs(install.Env(home=tmp_path, ask=p), None) == [tmp_path / "code"]
    p.rules["project folders"] = f"~/a{os.pathsep} /b "
    assert install.project_dirs(install.Env(home=tmp_path, ask=p), ["/x"]) == [pathlib.Path("~/a").expanduser(), pathlib.Path("/b")]


def test_per_project_confirmation_defaults_to_no(box, out):
    home, _ = box
    m = Machine("x")
    rules = dict(YES, **{"Install the Cockpit in": ""})
    steps = install.plan(make_env(home, m, Prompts(rules)), sys.executable, home / "c", {"project_dirs": [home / "src"]})
    assert not any("alpha" in s.text for s in steps)
    assert "  alpha: acme/alpha" in out() and "  beta: acme/beta (already installed)" in out() and "  localonly: no GitHub remote, skipped" in out()


def test_plan_lists_every_write_and_command(box, out):
    home, _ = box
    m = Machine("x")
    rc = install.install(make_env(home, m, Prompts(YES)), dry_run=True)
    lines = out()
    assert rc == 0 and "Plan:" in lines
    plan = lines[lines.index("Plan:"):]
    for f in ("install.json", "projects.json", f"{install.VIEWER}.plist", "com.project-cockpit.dispatcher.queue.plist", "com.project-cockpit.quota.queue.plist"):
        assert any(l.strip().startswith("write ") and l.endswith(f) for l in plan), f
    assert any("setup.sh acme/alpha" in l for l in plan)
    assert any("planned: PC-92" in l for l in lines) and any("planned: PC-91" in l for l in lines)


def test_unknown_os_has_no_service_unit(tmp_path):
    assert install.viewer_unit(install.Env(home=tmp_path, system="Plan9"), "py") is None


def test_failed_command_prints_the_by_hand_lines(tmp_path, out):
    env = install.Env(home=tmp_path, run=lambda a: (5, ""))
    assert not install.execute(env, install.Step("x", writes=[(tmp_path / "f", "c")], runs=[["launchctl", "bootstrap", "d", "p"]]))
    assert (tmp_path / "f").stat().st_mode & 0o777 == 0o600
    assert "  run by hand: launchctl bootstrap d p" in out()


# 2. Scenario tests

@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_full_run_all_yes(box, out, system):
    home, _ = box
    m = Machine("x")
    rc = install.install(make_env(home, m, Prompts(YES), system=system))
    assert rc == 0 and out()[-1] == "ready"
    pf = json.loads((home / ".config/project-cockpit/projects.json").read_text())
    assert [p["repo"] for p in pf["projects"]] == ["acme/beta", "acme/alpha"] and pf["default"] == "beta"
    assert ["bash", str(ROOT / "kit/setup.sh"), "acme/alpha"] in m.calls
    assert [c[1:] for c in m.calls if "pre_push.py" in " ".join(c)] == [[str(ROOT / "tools/pre_push.py"), "--install", "--repo", str(home / "src/alpha")]]
    assert not any("acme/beta" in " ".join(c) or "localonly" in " ".join(c) for c in m.mutations())
    saved = json.loads((home / ".config/project-cockpit/install.json").read_text())
    assert saved == {"model_credential": str(home / ".config/project-cockpit/model-token"), "project_dirs": [str(home / "src")]}
    boots = [pathlib.Path(c[3]).stem for c in m.calls if c[:2] == ["launchctl", "bootstrap"]]
    if system == "Darwin":
        assert boots == [install.VIEWER, "com.project-cockpit.quota.queue", "com.project-cockpit.dispatcher.queue"]
    else:
        assert boots == [] and "project-cockpit" in m.loaded and any("Dispatcher: needs macOS" in l for l in out())


def test_declined_project_untouched(box, out):
    home, _ = box
    before = snapshot(home)
    m = Machine("x")
    rules = dict(YES, **{"Install the Cockpit in": "n", "viewer": "n", "Dispatcher": "n"})
    rc = install.install(make_env(home, m, Prompts(rules)))
    after = snapshot(home)
    assert m.mutations() == []
    assert {k: v for k, v in after.items() if k.startswith("src/")} == {k: v for k, v in before.items() if k.startswith("src/")}
    assert after[".config/project-cockpit/projects.json"] == before[".config/project-cockpit/projects.json"]
    assert set(after) - set(before) == {".config/project-cockpit/install.json"}
    assert rc == 1 and any("viewer service" in l for l in out())


def test_dry_run_writes_and_runs_nothing(box, out):
    home, _ = box
    before = snapshot(home)
    m = Machine("x")
    p = Prompts(YES)
    assert install.main(["--dry-run"], env=make_env(home, m, p)) == 0
    assert snapshot(home) == before and m.mutations() == []
    assert out()[-1] == "dry run: nothing was written or run" and not any("Carry out" in q for q in p.asked)


def test_second_run_changes_nothing(box, out):
    home, _ = box
    m = Machine("x")
    install.install(make_env(home, m, Prompts(YES)))
    before, ncalls = snapshot(home), len(m.mutations())
    p = Prompts({"credential": "", "project folders": ""})   # any other question fails the test
    assert install.install(make_env(home, m, p)) == 0
    assert snapshot(home) == before and len(m.mutations()) == ncalls
    assert "nothing to install: everything offered is in place" in out()


def test_second_run_offers_only_what_is_missing(box, out):
    home, _ = box
    m = Machine("x")
    install.install(make_env(home, m, Prompts(YES)))
    git_clone(home / "src" / "gamma", {"origin": "https://github.com/acme/gamma.git"})
    m.loaded.discard("com.project-cockpit.quota.queue")
    p = Prompts(YES)
    assert install.install(make_env(home, m, p)) == 0
    asked = [q for q in p.asked if "Install" in q]
    assert len(asked) == 2 and "acme/gamma" in asked[0] and "Dispatcher" in asked[1]
    assert sum(1 for c in m.calls if c[:2] == ["launchctl", "bootstrap"]) == 4   # 3 first run, the quota job again


def test_changed_viewer_unit_is_reloaded(box):
    home, _ = box
    m = Machine("x")
    install.install(make_env(home, m, Prompts(YES)))
    (home / f"Library/LaunchAgents/{install.VIEWER}.plist").write_text("old")
    install.install(make_env(home, m, Prompts(YES)))
    assert m.calls[-12:] and ["launchctl", "bootout", f"gui/501/{install.VIEWER}"] in m.calls


@pytest.mark.parametrize("problem,fix", [("credential", "claude setup-token"), ("gh login", "gh auth login"), ("gh", "brew install gh"), ("git", "xcode-select")])
def test_missing_prerequisite_stops_cleanly(box, out, problem, fix):
    home, _ = box
    if problem == "credential":
        (home / ".config/project-cockpit/model-token").unlink()
    tools = tuple(t for t in ("git", "gh", "container") if t != problem)
    m = Machine("x", logged_in=problem != "gh login")
    before = snapshot(home)
    assert install.install(make_env(home, m, Prompts({"credential": ""}), tools=tools)) == 2
    assert snapshot(home) == before and m.mutations() == []
    assert "stopped, fix these first:" in out() and any(fix in l for l in out())


def test_missing_container_tool_skips_the_dispatcher(box, out):
    home, _ = box
    m = Machine("x")
    assert install.install(make_env(home, m, Prompts(YES), tools=("git", "gh"))) == 1
    assert any("Dispatcher: skipped, the container tool is missing" in l for l in out())
    assert "  - container tool: " + install.FIX["container"] in out()


def test_old_python_stops_with_the_message(box, out):
    home, _ = box
    m = Machine("x")
    assert install.install(make_env(home, m, Prompts({}), version=(3, 9))) == 2
    assert m.calls == [] and "install Python 3.14" in out()[0]


def test_other_python_warns_and_installs(box, out):
    home, _ = box
    assert install.install(make_env(home, Machine("x"), Prompts(YES), version=(3, 13))) == 0
    assert "not the tested version 3.14" in out()[0]


def test_self_test_lists_what_is_missing_with_fixes(tmp_path, out):
    m = Machine("x", logged_in=False)
    env = make_env(tmp_path, m, Prompts({}), tools=("gh",))
    assert install.main(["--self-test"], env=env) == 1
    missing = [l for l in out() if l.startswith("  - ")]
    for what in ("git", "gh login", "model credential", "a project", "viewer service", "container tool",
                 "job com.project-cockpit.quota.queue", "job com.project-cockpit.dispatcher.queue", "transcript retention"):
        assert any(l.startswith(f"  - {what}: ") and len(l) > len(what) + 8 for l in missing), what
    assert m.mutations() == []


def test_self_test_finds_a_lost_clone_and_a_silent_viewer(box, out):
    home, _ = box
    m = Machine("x")
    env = make_env(home, m, Prompts(YES), system="Linux")
    install.install(env)
    shutil.rmtree(home / "src" / "beta")
    env.fetch = lambda url: False
    assert install.report(install.self_test(env)) == 1
    assert any(l.startswith("  - clone of beta:") for l in out()) and any(l.startswith("  - viewer answering:") for l in out())


def test_self_test_flags_an_open_credential(box, out):
    home, _ = box
    m = Machine("x")
    install.install(make_env(home, m, Prompts(YES)))
    (home / ".config/project-cockpit/model-token").chmod(0o644)
    assert install.report(install.self_test(make_env(home, m, Prompts({})))) == 1
    assert any("chmod 600" in l for l in out())


def test_carry_out_declined_does_nothing(box, out):
    home, _ = box
    m = Machine("x")
    before = snapshot(home)
    assert install.install(make_env(home, m, Prompts(dict(YES, **{"Carry out": "n"})))) == 1
    assert snapshot(home) == before and m.mutations() == [] and out()[-1] == "nothing done"


# 3. No leak

def test_secrets_never_leak(box, out):
    home, secrets = box
    m = Machine(secrets["gh"])
    p = Prompts(YES)
    install.install(make_env(home, m, p))
    install.install(make_env(home, m, Prompts(YES)), dry_run=True)
    install.main(["--self-test"], env=make_env(home, m, Prompts({})))
    texts = out() + p.asked + [" ".join(c) for c in m.calls]
    owner_held = {home / ".config/project-cockpit/model-token", home / ".config/gh/hosts.yml"}
    texts += [f.read_text(errors="replace") for f in home.rglob("*") if f.is_file() and f not in owner_held]
    texts += [f.read_text(errors="replace") for f in ROOT.rglob("*") if f.is_file() and ".git" not in f.parts and "__pycache__" not in f.parts]
    for s in secrets.values():
        assert not any(s in t for t in texts)


def test_branches_of_the_plan(box, out, tmp_path):
    home, _ = box
    git_clone(home / "src" / "delta", {"origin": "https://github.com/acme/delta.git"})
    (home / "src" / "delta" / "docs").mkdir()
    (home / "src" / "delta" / "docs" / "cockpit-modules.json").write_text("{}")
    (home / "src" / "broken" / ".git").mkdir(parents=True)               # a clone without a config: no remote
    root = tmp_path / "cockpit"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "project_sync.py").write_text("")                  # the day PC-92 lands, its init is run
    env = make_env(home, Machine("x"), Prompts({"acme/delta": "y", **YES, "Install the Cockpit in": "n"}), system="Plan9")
    env.root = root
    texts = [" ".join(map(str, a)) for s in install.plan(env, "py", home / "c", {"project_dirs": [home / "src"]}) for a in s.runs]
    assert any("project_sync.py init --repo-dir" in t for t in texts) and any("cardmeta_collect.py --project delta" in t for t in texts)
    assert "  broken: no GitHub remote, skipped" in out() and any("no service manager known for Plan9" in l for l in out())
    install.plan(env, "py", home / "c", {"project_dirs": [home / "absent"]})
    lines = out()
    assert lines[len(lines) - 1 - lines[::-1].index("Projects found:") + 1] == "  none"


def test_self_test_names_an_old_python(tmp_path, out):
    miss = install.self_test(make_env(tmp_path, Machine("x"), Prompts({}), version=(3, 9)))
    assert ("Python", out()[0]) in miss and "install Python 3.14" in out()[0]


def test_default_run_and_fetch():
    assert install.default_run([sys.executable, "-c", "print('x')"]) == (0, "x\n")
    assert install.default_run(["/nonexistent/program"])[0] == 127
    import http.server
    import threading

    class NotFound(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/ok":
                self.send_response(200)
                self.end_headers()
            else:
                self.send_error(404)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), NotFound)
    threading.Thread(target=lambda: [srv.handle_request() for _ in range(2)], daemon=True).start()
    assert install.default_fetch(f"http://127.0.0.1:{srv.server_port}/ok")
    assert install.default_fetch(f"http://127.0.0.1:{srv.server_port}/")   # any HTTP answer counts
    srv.server_close()
    assert not install.default_fetch(f"http://127.0.0.1:{srv.server_port}/")


def test_a_viewer_of_your_own_is_left_alone(box, out):
    home, _ = box
    m = Machine("x")
    m.loaded.add("org.example.own-viewer")        # answers on the port under another label
    steps = install.plan(make_env(home, m, Prompts(YES)), sys.executable, home / "c", {"project_dirs": []})
    assert not any("viewer" in s.text for s in steps) and any("not installing a second one" in l for l in out())


def test_too_long_home_skips_the_dispatcher_with_the_reason(tmp_path, out):
    home = tmp_path / ("h" * 60)
    home.mkdir()
    assert install.dispatcher_step(make_env(home, Machine("x"), Prompts({})), sys.executable, home / "c") is None
    assert any(l.startswith("Dispatcher: skipped: socket path") for l in out())
