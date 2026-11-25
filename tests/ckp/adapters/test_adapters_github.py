import hashlib
import json
import subprocess

import pytest

from ckp.adapters import github


class Fake:
    def __init__(self, *replies):
        self.calls, self.replies = [], list(replies)

    def __call__(self, args):
        self.calls.append(list(args))
        r = self.replies.pop(0) if self.replies else ""
        if isinstance(r, Exception):
            raise r
        return r


def test_create_release_without_asset():
    run = Fake("https://example.invalid/rel/v1\n")
    assert github.create_release("o/r", "v2026.11.25-1", "notes", run=run) == "https://example.invalid/rel/v1"
    assert run.calls[0][:5] == ["gh", "release", "create", "v2026.11.25-1", "--repo"]
    assert "o/r" in run.calls[0] and "notes" in run.calls[0]


def test_create_release_uploads_asset_with_checksum(tmp_path):
    asset = tmp_path / "rel.tar.gz"
    asset.write_bytes(b"payload")
    run = Fake("url")
    github.create_release("o/r", "v1", "n", asset, run=run)
    sums = tmp_path / "rel.tar.gz.sha256"
    assert sums.read_text() == f"{hashlib.sha256(b'payload').hexdigest()}  rel.tar.gz\n"
    assert run.calls[0][-2:] == [str(asset), str(sums)]


def test_create_release_failure_propagates():
    with pytest.raises(subprocess.CalledProcessError):
        github.create_release("o/r", "v1", "n", run=Fake(subprocess.CalledProcessError(1, "gh")))


def test_record_deployment_creates_then_sets_status():
    run = Fake(json.dumps({"id": 42}), "{}")
    github.record_deployment("o/r", "v1", "production", "success", "ok " * 100, run=run)
    assert run.calls[0][2] == "repos/o/r/deployments" and "ref=v1" in run.calls[0] and "environment=production" in run.calls[0]
    assert run.calls[1][2] == "repos/o/r/deployments/42/statuses" and "state=success" in run.calls[1]
    assert all(len(a) <= 140 + len("description=") for c in run.calls for a in c)


def test_record_deployment_failure_on_create_stops():
    run = Fake(subprocess.CalledProcessError(1, "gh"))
    with pytest.raises(subprocess.CalledProcessError):
        github.record_deployment("o/r", "v1", "production", "failure", "d", run=run)
    assert len(run.calls) == 1


def test_latest_deployment_none_when_there_is_none():
    assert github.latest_deployment("o/r", "production", run=Fake("[]")) is None


def test_latest_deployment_with_status():
    dep = [{"id": 7, "ref": "v1", "environment": "production", "created_at": "2026-11-25T10:00:00Z"}]
    run = Fake(json.dumps(dep), json.dumps([{"state": "success", "description": "done"}]))
    got = github.latest_deployment("o/r", "production", run=run)
    assert got == {"id": 7, "ref": "v1", "environment": "production", "state": "success", "description": "done",
                   "created_at": "2026-11-25T10:00:00Z"}
    assert "environment=production" in run.calls[0][2]


def test_latest_deployment_without_status_has_state_none():
    dep = [{"id": 7, "ref": "v1", "environment": "production"}]
    got = github.latest_deployment("o/r", "production", run=Fake(json.dumps(dep), "[]"))
    assert got["state"] is None
