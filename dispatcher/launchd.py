"""The two LaunchAgent property lists: the Dispatcher (3c) and the quota collector. The proxy is started by the Dispatcher (4d).

Pure rendering (plistlib); the command line only writes the files and prints the install commands, it installs nothing.

    python3 -m dispatcher.launchd render --project NAME --repo OWNER/NAME --repo-dir DIR --token-file F [--out DIR]
"""
import argparse
import pathlib
import plistlib
import sys

from dispatcher import runtime as rt

COCKPIT_DIR = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = pathlib.Path("~/.local/share/project-cockpit").expanduser()  # as cockpit.DATA_DIR
SOCKET_LIMIT = 104  # bytes, 4f
INTERVAL = 120      # seconds, 3c
QUOTA_INTERVAL = 30  # seconds, the quota collector
PREFIX = "com.project-cockpit"


def paths(project, data_dir=DATA_DIR):
    """The per-project files, under <data dir>/<project>/ (the folder the Cockpit reads: settings_path, USAGE_FILE, PLOG_FILE, AREA_DIR)."""
    d = pathlib.Path(data_dir) / project
    return dict(dir=d, settings=d / "dispatcher-settings.json", state=d / "dispatcher-state.json", area=d / "area",
                socket=d / "proxy.sock", usage=d / "proxy-usage.jsonl", quota=d / "quota-history.jsonl", processing=d / "processing", launchd=d / "launchd")


def check_socket(sock):
    if len(str(sock).encode()) >= SOCKET_LIMIT:
        raise ValueError(f"socket path {sock} is {len(str(sock).encode())} bytes; a Unix socket path must be shorter than {SOCKET_LIMIT}")
    return str(sock)


# launchd starts jobs with a bare PATH (/usr/bin:/bin:...): python3, gh, git and the container tool live elsewhere.
JOB_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def _env(cockpit_dir):
    return {"PYTHONPATH": str(cockpit_dir), "PATH": JOB_PATH}


def dispatcher_plist(project, repo, repo_dir, token_file, image=None, data_dir=DATA_DIR, cockpit_dir=COCKPIT_DIR, python="python3"):
    """3c: the Dispatcher job, started every 2 minutes with the arguments of dispatcher.core.main()."""
    p = paths(project, data_dir)
    args = [python, "-m", "dispatcher.core", "--settings", p["settings"], "--state", p["state"], "--area", p["area"],
            "--socket", check_socket(p["socket"]), "--token-file", token_file, "--usage-file", p["usage"],
            "--repo", repo, "--repo-dir", repo_dir, "--data-dir", p["processing"]]
    if image:
        args += ["--image", image]
    return dict(Label=f"{PREFIX}.dispatcher.{project}", ProgramArguments=[str(a) for a in args], StartInterval=INTERVAL,
                WorkingDirectory=str(cockpit_dir), EnvironmentVariables=_env(cockpit_dir),
                StandardOutPath=str(p["dir"] / "dispatcher.log"), StandardErrorPath=str(p["dir"] / "dispatcher.log"))


def quota_plist(project, data_dir=DATA_DIR, cockpit_dir=COCKPIT_DIR, python="python3"):
    """The quota collector, one reading every 30 seconds into <data dir>/<project>/quota-history.jsonl."""
    p = paths(project, data_dir)
    args = [python, "-m", "dispatcher.quota", "--history", p["quota"]]
    return dict(Label=f"{PREFIX}.quota.{project}", ProgramArguments=[str(a) for a in args], StartInterval=QUOTA_INTERVAL,
                WorkingDirectory=str(cockpit_dir), EnvironmentVariables=_env(cockpit_dir),
                StandardOutPath=str(p["dir"] / "quota.log"), StandardErrorPath=str(p["dir"] / "quota.log"))


def render(argv=None):
    ap = argparse.ArgumentParser(prog="dispatcher.launchd render", description="write the two LaunchAgent files; installs nothing")
    ap.add_argument("--project", required=True)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--repo-dir", required=True)
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--out", help="default: <data dir>/<project>/launchd")
    ap.add_argument("--image")
    a = ap.parse_args(argv)
    out = pathlib.Path(a.out or paths(a.project)["launchd"]).expanduser().resolve()
    absolute = lambda s: str(pathlib.Path(s).expanduser().resolve())
    token, repo_dir = absolute(a.token_file), absolute(a.repo_dir)
    out.mkdir(parents=True, exist_ok=True)
    for plist in (dispatcher_plist(a.project, a.repo, repo_dir, token, a.image), quota_plist(a.project)):
        f = out / f"{plist['Label']}.plist"
        f.write_bytes(plistlib.dumps(plist))
        print(f)
    print("To install (by hand):")
    for plist in (quota_plist(a.project), dispatcher_plist(a.project, a.repo, repo_dir, token)):
        print(f"  launchctl bootstrap gui/$(id -u) {out / (plist['Label'] + '.plist')}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "render":
        sys.exit("usage: python3 -m dispatcher.launchd render --project NAME --repo OWNER/NAME --repo-dir DIR --token-file F [--out DIR]")
    render(argv[1:])


if __name__ == "__main__":
    main()
