import plistlib

import pytest

from dispatcher import launchd as L

ARGS = dict(project="demo", repo="o/n", repo_dir="/r", token_file="/t", data_dir="/data", cockpit_dir="/cp")


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
    L.render(["--project", "demo", "--repo", "o/n", "--repo-dir", str(tmp_path), "--token-file", str(tmp_path / "tok"), "--out", str(tmp_path / "o")])
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
    L.render(["--project", "demo", "--repo", "o/n", "--repo-dir", str(tmp_path), "--token-file", str(tmp_path / "tok"),
              "--out", str(out), "--python", "/opt/x/bin/python3"])
    for f in out.glob("*.plist"):
        assert plistlib.loads(f.read_bytes())["ProgramArguments"][0] == "/opt/x/bin/python3"
    L.render(["--project", "demo", "--repo", "o/n", "--repo-dir", str(tmp_path), "--token-file", str(tmp_path / "tok"), "--out", str(out)])
    assert all(plistlib.loads(f.read_bytes())["ProgramArguments"][0].startswith("/") for f in out.glob("*.plist"))
    with pytest.raises(SystemExit):
        L.render(["--project", "demo", "--repo", "o/n", "--repo-dir", str(tmp_path), "--token-file", str(tmp_path / "tok"), "--python", "python3"])

