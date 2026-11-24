import os
import json
import pathlib
import re
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from dispatcher import runtime as rt  # noqa: E402
from dispatcher.agent import Agent, ClaudeCode  # noqa: E402


class Fake:
    """Injected `run`: records calls, answers from a script of (substring, (rc, out)) rules."""

    def __init__(self, *rules):
        self.rules, self.calls = list(rules), []

    def __call__(self, args, timeout):
        self.calls.append((args, timeout))
        line = " ".join(args)
        for key, reply in self.rules:
            if key in line:
                if isinstance(reply, Exception):
                    raise reply
                return reply
        return 0, ""


def make(tmp_path, *rules, **kw):
    cfg = rt.Config(area=str(tmp_path / "area"), socket="/tmp/p.sock", **kw)
    f = Fake(*rules)
    return rt.Runtime(cfg, run=f, spawn=lambda cmd, **k: ("spawned", cmd)), f


def test_timeout_is_a_distinct_error(tmp_path):
    r, _ = make(tmp_path, ("list", subprocess.TimeoutExpired("x", 1)))
    with pytest.raises(rt.ContainerTimeout):
        r.state()
    assert issubclass(rt.ContainerTimeout, rt.ContainerError)


def test_missing_tool_is_container_error(tmp_path):
    r, _ = make(tmp_path, ("list", FileNotFoundError("nope")))
    with pytest.raises(rt.ContainerError):
        r.state()


def test_every_call_has_a_time_limit(tmp_path):
    r, f = make(tmp_path, ("list", (0, "[]")))
    r.ensure_container()
    assert f.calls and all(isinstance(t, (int, float)) and t > 0 for _, t in f.calls)


def test_run_args_follow_4c(tmp_path):
    r, _ = make(tmp_path)
    a = r.run_args()
    assert a[:2] == ["run", "-d"] and "--init" in a
    assert a[a.index("--network") + 1] == "none"
    assert "--memory" in a and "--cpus" in a and "--ssh" not in a
    vols = [a[i + 1] for i, x in enumerate(a) if x == "-v"]
    assert vols == [f"{tmp_path}/area:/area", "/tmp/p.sock:/run/proxy.sock"]


def listing(status):
    return json.dumps([{"configuration": {"id": "cockpit-workers"}, "status": status}])


def test_ensure_container_cases(tmp_path):
    r, f = make(tmp_path, ("list", (0, listing("running"))))
    r.ensure_container()
    assert len(f.calls) == 1
    r, f = make(tmp_path, ("list", (0, listing("stopped"))))
    r.ensure_container()
    assert f.calls[1][0][1] == "start"
    r, f = make(tmp_path, ("list", (0, "[]")))
    r.ensure_container()
    assert f.calls[1][0][1] == "run"
    r, f = make(tmp_path, ("list", (0, "[]")), ("run", (1, "boom")))
    with pytest.raises(rt.ContainerError):
        r.ensure_container()


def test_state_errors(tmp_path):
    r, _ = make(tmp_path, ("list", (1, "bad")))
    with pytest.raises(rt.ContainerError):
        r.state()
    r, _ = make(tmp_path, ("list", (0, "not json")))
    with pytest.raises(rt.ContainerError):
        r.state()


def test_stats_none_when_unavailable(tmp_path):
    r, _ = make(tmp_path, ("stats", (0, '{"x":1}')))
    assert r.stats() == '{"x":1}'
    r, _ = make(tmp_path, ("stats", (1, "no")))
    assert r.stats() is None
    r, _ = make(tmp_path, ("stats", subprocess.TimeoutExpired("x", 1)))
    assert r.stats() is None


def test_build_image(tmp_path):
    r, f = make(tmp_path)
    r.build_image("/ctx")
    assert f.calls[0][0][1] == "build" and f.calls[0][1] == rt.BUILD_TIMEOUT
    args = f.calls[0][0]
    assert args[args.index("--dns") + 1] == "1.1.1.1"
    assert args[args.index("-f") + 1] == "/ctx/dispatcher/image/Containerfile"
    r, _ = make(tmp_path, ("build", (1, "fail")))
    with pytest.raises(rt.ContainerError):
        r.build_image("/ctx")


def test_worker_users_and_count(tmp_path):
    r, _ = make(tmp_path)
    assert rt.worker_user(r.cfg, 1) == "worker1"
    for bad in (0, r.cfg.image_workers + 1):
        with pytest.raises(ValueError):
            rt.worker_user(r.cfg, bad)
    assert rt.clamp_workers(r.cfg, 3) == 3
    assert rt.clamp_workers(r.cfg, 99) == r.cfg.image_workers
    assert rt.clamp_workers(r.cfg, 0) == 1
    assert rt.dummy_token("worker1") != rt.dummy_token("worker2")


def test_egress_check(tmp_path):
    r, f = make(tmp_path, ("python3", (0, "BLOCKED")))
    assert r.egress_blocked("worker1")
    assert "--user" in f.calls[0][0] and "worker1" in f.calls[0][0] and "1.1.1.1" in f.calls[0][0]
    for reply in ((0, "OPEN"), (1, "exec failed"), (0, "")):
        r, _ = make(tmp_path, ("python3", reply))
        assert not r.egress_blocked("worker1")


def test_egress_probe_script_runs_and_blocks():
    # the probe itself: connection refused on a closed local port counts as BLOCKED
    p = subprocess.run([sys.executable, "-c", rt.EGRESS_PROBE, "127.0.0.1", "1"], capture_output=True, text=True)
    assert p.stdout.strip() == "BLOCKED"


def git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    git("init", "-b", "main", cwd=d)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "c", cwd=d)
    return str(d)


def test_prepare_task_layout(tmp_path, repo):
    r, _ = make(tmp_path)
    base = pathlib.Path(r.prepare_task("T-1", repo, "# title\ntext"))
    assert base == tmp_path / "area" / "T-1"
    assert (base / "inbox" / "task.md").read_text() == "# title\ntext"
    assert (base / "inbox" / "main.bundle").stat().st_size > 0
    assert (base / "outbox").is_dir() and not list((base / "outbox").iterdir())
    with pytest.raises(FileExistsError):
        r.prepare_task("T-1", repo, "again")


def test_prepare_task_bad_repo(tmp_path):
    r, _ = make(tmp_path)
    with pytest.raises(rt.ContainerError):
        r.prepare_task("T-2", str(tmp_path / "nope"), "x")


def test_prepare_task_failure_removes_the_folder_it_made_and_a_retry_works(tmp_path, repo):
    r, _ = make(tmp_path)
    with pytest.raises(rt.ContainerError):
        r.prepare_task("T-4", str(tmp_path / "nope"), "x")
    assert not (tmp_path / "area" / "T-4").exists()
    assert pathlib.Path(r.prepare_task("T-4", repo, "x")).is_dir()


def test_prepare_task_never_removes_a_folder_that_existed(tmp_path, repo):
    r, _ = make(tmp_path)
    existing = tmp_path / "area" / "T-5"
    existing.mkdir(parents=True)
    (existing / "keep").write_text("k")
    with pytest.raises(FileExistsError):
        r.prepare_task("T-5", repo, "x")
    assert (existing / "keep").read_text() == "k"


@pytest.mark.parametrize("bad", ["", "../x", "a/b", ".hidden", "a b", None])
def test_task_id_rejected(tmp_path, bad):
    r, _ = make(tmp_path)
    with pytest.raises(ValueError):
        r.task_dir(bad)


def outbox(tmp_path, r, repo, note=None, started=False):
    base = pathlib.Path(r.prepare_task("T-3", repo, "x"))
    if started:
        (base / "outbox" / "started").write_text("")
    if note is not None:
        (base / "outbox" / "goodbye.json").write_text(note if isinstance(note, str) else json.dumps(note))
    return base


GOOD = {"outcome": "done", "summary": "s", "tokens": 12}


def test_read_outbox(tmp_path, repo):
    r, _ = make(tmp_path)
    outbox(tmp_path, r, repo)
    assert r.read_outbox("T-3") == (False, None, "no note")


def test_read_outbox_good_and_started(tmp_path, repo):
    r, _ = make(tmp_path)
    outbox(tmp_path, r, repo, GOOD, started=True)
    assert r.read_outbox("T-3") == (True, GOOD, None)
    assert r.bundle_path("T-3").endswith("T-3/outbox/result.bundle")


@pytest.mark.parametrize("note", [
    "not json", "[]", {**GOOD, "outcome": "maybe"}, {**GOOD, "summary": ""}, {**GOOD, "tokens": "many"},
    {**GOOD, "tokens": True}, {k: v for k, v in GOOD.items() if k != "summary"}])
def test_read_outbox_unusable(tmp_path, repo, note):
    r, _ = make(tmp_path)
    outbox(tmp_path, r, repo, note)
    assert r.read_outbox("T-3") == (False, None, "unusable note")


def test_clone_without_hardlinks(tmp_path):
    r, f = make(tmp_path)
    assert r.clone_main("worker2", "T-4") == "/home/worker2/T-4/clone"
    args = f.calls[0][0]
    assert "--no-hardlinks" in args and "/area/T-4/inbox/main.bundle" in args and "worker2" in args
    r, _ = make(tmp_path, ("clone", (1, "bad")))
    with pytest.raises(rt.ContainerError):
        r.clone_main("worker2", "T-4")


def test_launch_command(tmp_path):
    r, f = make(tmp_path)
    kind, cmd = r.launch("worker1", "T-5")
    assert kind == "spawned"
    line = " ".join(cmd)
    assert cmd[1:5] == ["exec", "--user", "worker1", "--workdir"] and cmd[5] == "/home/worker1/T-5/clone"
    assert cmd[cmd.index("timeout") + 1] == str(2 * 40 * 60)       # 4e: twice the runaway limit
    for sw in ClaudeCode.SWITCHES:
        assert f"{sw}={ClaudeCode.SWITCHES[sw]}" in line
    assert cmd[-3:-1] == ["claude", "-p"]
    assert "CLAUDE_CONFIG_DIR=/home/worker1/.claude" in line
    assert "ANTHROPIC_BASE_URL=http://127.0.0.1:8089" in line
    assert f"ANTHROPIC_AUTH_TOKEN={rt.dummy_token('worker1')}" in line
    assert "/area/T-5/inbox/task.md" in line and "/area/T-5/outbox/started" in line
    assert "goodbye.json" in line and "result.bundle" in line
    assert f.calls and f.calls[0][0][1] == "exec"  # the clone ran first
    assert "sudo" not in line and "--dangerously-skip-permissions" not in line


def test_containerfile_settings_allowlist():
    text = (pathlib.Path(rt.__file__).parent / "image" / "Containerfile").read_text()
    raw = re.search(r"^ARG WORKER_SETTINGS='(.*)'$", text, re.M).group(1)
    s = json.loads(raw)
    assert s["skipWebFetchPreflight"] is True
    assert s["permissions"]["allow"] == ["Edit", "Write", "Bash(git:*)", "Bash(python3:*)", "Bash(pytest:*)", "Read", "Glob", "Grep", "WebSearch"]
    assert s["permissions"]["deny"] == ["WebFetch"]
    assert '"$WORKER_SETTINGS"' in text


def test_launch_aborts_when_clone_fails(tmp_path):
    r, _ = make(tmp_path, ("clone", (1, "bad")))
    with pytest.raises(rt.ContainerError):
        r.launch("worker1", "T-5")


def test_alive_for_self_and_dead(tmp_path):
    import os
    r, _ = make(tmp_path)
    assert r.alive(os.getpid())
    assert not r.alive(2 ** 22 + 12345)


def test_claude_code_adapter():
    a = ClaudeCode()
    assert a.command("do it") == ["claude", "-p", "do it"]
    env = a.env("worker1", "http://127.0.0.1:1", "tok")
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:1" and env["ANTHROPIC_AUTH_TOKEN"] == "tok"
    assert env["CLAUDE_CONFIG_DIR"] == "/home/worker1/.claude"
    assert set(ClaudeCode.SWITCHES) <= set(env)
    assert a.activity_glob("worker1") == "/home/worker1/.claude/projects/**/*.jsonl"


def test_launch_uses_the_given_agent(tmp_path):
    class Other(Agent):
        def command(self, prompt): return ["other", prompt]
        def env(self, user, base_url, token): return {"OTHER_URL": base_url}
        def activity_glob(self, user): return "/x"
    r, _ = make(tmp_path)
    _, cmd = r.launch("worker1", "T-5", Other())
    line = " ".join(cmd)
    assert "OTHER_URL=http://127.0.0.1:8089" in line and "ANTHROPIC" not in line and "claude" not in cmd[-2:]
    assert cmd[-2] == "other"


def test_worker_output_is_kept_and_its_tail_readable(tmp_path):
    r, _ = make(tmp_path)
    assert r.worker_output_tail("T-9") == ""
    r.launch("worker1", "T-9")
    with open(os.path.join(r.task_dir("T-9"), "worker.log"), "a") as f:
        f.write("x" * 700 + "API Error: 429")
    tail = r.worker_output_tail("T-9", 100)
    assert tail.endswith("API Error: 429") and len(tail) == 100
