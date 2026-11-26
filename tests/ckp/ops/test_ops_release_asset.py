"""The release asset (PC-142): a deploy of a new tag publishes the archive of the tagged tree as a GitHub Release; `--from-release` installs
from that asset instead of the clone. A real temporary git repository; the hosting service is faked by a runner that keeps the files."""
import hashlib
import pathlib
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from ckp import cli  # noqa: E402
from ckp.adapters import github  # noqa: E402
from ckp.adapters.deploy import GitDeployAdapter  # noqa: E402

TAG = "v2026.11.26-1"


def sh(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "sub").mkdir(parents=True)
    sh(r, "init", "-b", "main")
    sh(r, "config", "user.email", "t@t")
    sh(r, "config", "user.name", "t")
    (r / "a.txt").write_text("a")
    (r / "sub" / "b.txt").write_text("b")
    sh(r, "add", "-A")
    sh(r, "commit", "-m", "one")
    sh(r, "tag", TAG)
    return r


class Hosting:
    """`gh release create` keeps the uploaded files; `gh release download` copies them into --dir; anything else runs for real."""
    def __init__(self, tmp):
        self.store, self.calls = tmp / "hosted", []
        self.store.mkdir()

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        if args[:3] == ["gh", "release", "create"]:
            for f in args[args.index("--notes") + 2:]:
                shutil.copy(f, self.store)
            return "https://example.invalid/releases/" + args[3]
        if args[:3] == ["gh", "release", "download"]:
            dest = pathlib.Path(args[args.index("--dir") + 1])
            for i, a in enumerate(args):
                if a == "--pattern":
                    shutil.copy(self.store / args[i + 1], dest)
            return ""
        return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def test_publish_uploads_the_archive_of_the_tagged_tree_with_its_checksum(repo, tmp_path):
    host = Hosting(tmp_path)
    ad = GitDeployAdapter("p", "o/p", repo, "production", home=tmp_path / "home", run=host)
    assert ad.publish(TAG) == "https://example.invalid/releases/" + TAG
    arch = host.store / f"{TAG}.tar.gz"
    assert (host.store / f"{TAG}.tar.gz.sha256").read_text().split()[0] == hashlib.sha256(arch.read_bytes()).hexdigest()


def test_a_deploy_from_the_release_unpacks_the_published_tree(repo, tmp_path):
    host = Hosting(tmp_path)
    GitDeployAdapter("p", "o/p", repo, "production", home=tmp_path / "home", run=host).publish(TAG)
    tree = GitDeployAdapter("p", "o/p", repo, "production", home=tmp_path / "home", run=host, from_release=True).fetch(TAG)
    assert (tree / "sub" / "b.txt").read_text() == "b"
    assert any(c[:3] == ["gh", "release", "download"] for c in host.calls)


def test_a_wrong_checksum_is_refused_and_nothing_is_unpacked(repo, tmp_path):
    host = Hosting(tmp_path)
    GitDeployAdapter("p", "o/p", repo, "production", home=tmp_path / "home", run=host).publish(TAG)
    (host.store / f"{TAG}.tar.gz.sha256").write_text("0" * 64 + f"  {TAG}.tar.gz\n")
    with pytest.raises(ValueError, match="checksum"):
        github.download_release("o/p", TAG, tmp_path / "dl", run=host)
    assert not (tmp_path / "dl" / "tree").exists()


class FakeAdapter:
    """What cmd_deploy needs of the adapter, with a record of the publish call."""
    def __init__(self):
        self.published = []
        self.lock_path = None

    def publish(self, tag):
        self.published.append(tag)
        return "url"


def test_an_applied_deploy_of_a_new_tag_publishes_it_and_pings_its_heartbeat(repo, tmp_path, monkeypatch):
    from ckp.core import deploy as core_deploy, monitor
    from ckp.store import env as store_env
    store_env.save("p", {"production": {"port": 8800, "data_dir": "d", "label": "l"}})
    monkeypatch.setattr(cli.git_adapter, "push_tag", lambda d, t: None)
    monkeypatch.setattr(core_deploy, "deploy", lambda project, tag, env, ad, dry_run=True: {"ok": True, "data": {"tag": tag}, "dry_run": dry_run})
    pings = []
    monkeypatch.setattr(cli.notify, "ping", lambda url, ok=True: pings.append((url, ok)) or True)
    monitor.set_check("deploy-p", "https://hc.example.invalid/abc")
    ad = FakeAdapter()
    a = cli.build_parser().parse_args(["deploy", "--project", "p", "--repo", "o/p", "--repo-dir", str(repo), "--apply"])
    assert cli.cmd_deploy(a, adapter=ad) == 0
    assert ad.published == [sh(repo, "tag", "--list", "v*-*").split()[-1]] and pings == [("https://hc.example.invalid/abc", True)]
