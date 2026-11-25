import plistlib

import pytest

from dispatcher import launchd as L

ARGS = dict(project="demo", repo="o/n", repo_dir="/r", token_file="/t", data_dir="/data", cockpit_dir="/cp")


def rel(tmp_path):
    """--cockpit-dir of a release folder: the production guard refuses a working clone."""
    return ["--cockpit-dir", str(tmp_path / "releases" / "cockpit" / "current")]


def _roundtrip(d):
    return plistlib.loads(plistlib.dumps(d))


def test_dispatcher_plist():
    d = _roundtrip(L.dispatcher_plist(**ARGS))
    assert d["Label"] == "com.project-cockpit.dispatcher.demo"
    assert d["StartInterval"] == 120 and d["AbandonProcessGroup"] is True   # Workers outlive the firing
    assert d["WorkingDirectory"] == "/cp" and d["EnvironmentVariables"]["PYTHONPATH"] == "/cp"
    assert "/opt/homebrew/bin" in d["EnvironmentVariables"]["PATH"] and "/usr/local/bin" in d["EnvironmentVariables"]["PATH"]
    a = d["ProgramArguments"]
    assert a[:3] == ["python3", "-m", "dispatcher.core"]
    for flag, val in (("--settings", "/data/demo/dispatcher-settings.json"), ("--socket", "/data/demo/proxy.sock"), ("--repo", "o/n"),
                      ("--repo-dir", "/r"), ("--token-file", "/t"), ("--data-dir", "/data/demo/processing"),
                      ("--usage-file", "/data/demo/proxy-usage.jsonl"), ("--area", "/data/demo/area")):
        assert a[a.index(flag) + 1] == val
    assert "--state" in a and "--image" not in a
    assert d["StandardOutPath"] == d["StandardErrorPath"]


def test_dispatcher_image_optional():
    a = L.dispatcher_plist(image="img:1", **ARGS)["ProgramArguments"]
    assert a[a.index("--image") + 1] == "img:1"


def test_quota_plist():
    d = _roundtrip(L.quota_plist("demo", data_dir="/data", cockpit_dir="/cp"))
    assert d["Label"] == "com.project-cockpit.quota.demo"
    assert d["StartInterval"] == 30
    a = d["ProgramArguments"]
    assert a[:3] == ["python3", "-m", "dispatcher.quota"]
    assert a[a.index("--history") + 1] == "/data/demo/quota-history.jsonl"


def test_socket_limit():
    with pytest.raises(ValueError):
        L.dispatcher_plist(**{**ARGS, "data_dir": "/d" * 60})


def test_render_writes_files_and_installs_nothing(tmp_path, capsys):
    L.render(["--project", "demo", "--repo", "o/n", "--repo-dir", str(tmp_path), "--token-file", str(tmp_path / "tok"), *rel(tmp_path), "--out", str(tmp_path / "o")])
    out = capsys.readouterr().out
    files = sorted((tmp_path / "o").glob("*.plist"))
    assert [f.name for f in files] == ["com.project-cockpit.dispatcher.demo.plist", "com.project-cockpit.quota.demo.plist"]
    assert "proxy" not in out.replace("proxy.sock", "")
    assert out.count("launchctl bootstrap") == 2 and all(str(f) in out for f in files)
    for f in files:
        assert plistlib.loads(f.read_bytes())["WorkingDirectory"].startswith("/")


def test_main_requires_render():
    with pytest.raises(SystemExit):
        L.main([])


def test_render_writes_an_absolute_python(tmp_path):
    out = tmp_path / "o"
    L.render(["--project", "demo", "--repo", "o/n", "--repo-dir", str(tmp_path), "--token-file", str(tmp_path / "tok"), *rel(tmp_path),
              "--out", str(out), "--python", "/opt/x/bin/python3"])
    for f in out.glob("*.plist"):
        assert plistlib.loads(f.read_bytes())["ProgramArguments"][0] == "/opt/x/bin/python3"
    L.render(["--project", "demo", "--repo", "o/n", "--repo-dir", str(tmp_path), "--token-file", str(tmp_path / "tok"), *rel(tmp_path), "--out", str(out)])
    assert all(plistlib.loads(f.read_bytes())["ProgramArguments"][0].startswith("/") for f in out.glob("*.plist"))
    with pytest.raises(SystemExit):
        L.render(["--project", "demo", "--repo", "o/n", "--repo-dir", str(tmp_path), "--token-file", str(tmp_path / "tok"), *rel(tmp_path), "--python", "python3"])



def test_label_names_the_environment_except_production():
    assert L.label("dispatcher", "demo") == "com.project-cockpit.dispatcher.demo"
    assert L.label("quota", "demo", "test") == "com.project-cockpit.test.quota.demo"


def test_a_test_environment_gets_its_own_labels_container_and_cp_env():
    d = L.dispatcher_plist(env="test", **ARGS)
    a = d["ProgramArguments"]
    assert d["Label"] == "com.project-cockpit.test.dispatcher.demo" and a[a.index("--container-name") + 1] == "cockpit-workers-test"
    assert d["EnvironmentVariables"]["CP_ENV"] == "test"
    assert L.quota_plist("demo", data_dir="/data", cockpit_dir="/cp", env="test")["Label"] == "com.project-cockpit.test.quota.demo"
    prod = L.dispatcher_plist(**ARGS)
    assert "--container-name" not in prod["ProgramArguments"] and prod["EnvironmentVariables"]["CP_ENV"] == "production"


def test_production_refuses_a_working_clone_and_keeps_the_current_link(tmp_path, capsys):
    clone = tmp_path / "clone"
    (clone / ".git").mkdir(parents=True)
    base = ["--project", "demo", "--repo", "o/n", "--repo-dir", str(tmp_path), "--token-file", str(tmp_path / "tok"), "--out", str(tmp_path / "o")]
    with pytest.raises(SystemExit) as e:
        L.render(base + ["--cockpit-dir", str(clone / "sub")])
    assert "release folder" in str(e.value)
    L.render(base + ["--cockpit-dir", str(clone), "--env", "test"])   # development and test may run from a clone
    assert (tmp_path / "o" / "com.project-cockpit.test.dispatcher.demo.plist").exists()
    rel_dir = tmp_path / "releases" / "cockpit"
    (rel_dir / "v2026.11.25-1").mkdir(parents=True)
    (rel_dir / "current").symlink_to("v2026.11.25-1")
    L.render(base + ["--cockpit-dir", str(rel_dir / "current")])
    d = plistlib.loads((tmp_path / "o" / "com.project-cockpit.dispatcher.demo.plist").read_bytes())
    assert d["WorkingDirectory"] == str(rel_dir / "current")   # the link, not the tag it points to: a deploy switches it
    with pytest.raises(SystemExit):
        L.render(base + ["--cockpit-dir", str(rel_dir / "current"), "--env", "../x"])
