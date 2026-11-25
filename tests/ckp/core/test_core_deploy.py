import contextlib
import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from ckp.core import deploy as d  # noqa: E402

GOOD = dict(clean=True, tests_green=True, ahead_of_live=True, deploy_running=False,
            ci_ok=True, problems_open=False, rollback_target=True)


class Busy(Exception):
    pass


class FakeAd:
    def __init__(self, facts=None, changed=("README.md",), running=(), selftest=(True, "ok"),
                 restarts=((True, "r"),), health=(True, "up")):
        self.f = dict(GOOD, **(facts or {}))
        self.changed, self.running = list(changed), list(running)
        self.st, self.rs, self.hl = selftest, list(restarts), health
        self.calls, self.records = [], []
        self.lock_path = "LOCK"

    def facts(self, p, t, e): return self.f
    def changed_paths(self, p, t): return self.changed
    def running_workers(self): return self.running
    def fetch(self, t): self.calls.append("fetch"); return "TREE"
    def selftest(self, tree): self.calls.append("selftest"); return self.st
    def restart(self, p, e):
        self.calls.append("restart")
        return self.rs.pop(0) if len(self.rs) > 1 else self.rs[0]
    def health(self, e): self.calls.append("health"); return self.hl
    def record(self, p, t, e, state, detail): self.records.append((state, detail))


@pytest.fixture
def store(monkeypatch):
    log = []
    s = SimpleNamespace(log=log, held=False, fail_install=False)

    def install(p, t, tree):
        log.append("install")
        if s.fail_install:
            raise OSError("disk")

    def switch(p, t): log.append("switch")
    def rollback(p): log.append("rollback"); return "v0"

    @contextlib.contextmanager
    def exclusive(path, wait_s=30):
        if s.held:
            raise Busy()
        log.append("lock")
        yield

    monkeypatch.setattr(d, "releases", SimpleNamespace(install_release=install, switch_current=switch, rollback=rollback))
    monkeypatch.setattr(d, "lock", SimpleNamespace(exclusive=exclusive, Busy=Busy))
    return s


def test_touches_workers_and_plan():
    paths = ["dispatcher/proxy.py", "dispatcher/image/Dockerfile", "cockpit.py"]
    assert d.touches_workers(paths) == paths[:2]
    assert d.plan(["cockpit.py"], ["t1"])["mode"] == "hot"
    held = d.plan(paths, ["PC-1"])
    assert held["mode"] == "held" and "PC-1" in " ".join(held["reasons"])


def test_readiness_names_every_false_condition():
    bad = {k: (not v) for k, v in GOOD.items()}
    res = d.readiness(bad)
    assert all(not r["ok"] and r["detail"] for r in res)
    assert all(r["ok"] for r in d.readiness(GOOD))
    assert all(r["ok"] for r in d.readiness(dict(GOOD, ci_ok=None, problems_open=None)))


def test_dry_run_changes_nothing(store):
    ad = FakeAd()
    r = d.deploy("p", "v1", "prod", ad)
    assert r["ok"] and r["dry_run"] and store.log == [] and ad.calls == [] and ad.records == []


def test_not_ready_stops(store):
    r = d.deploy("p", "v1", "prod", FakeAd(facts={"tests_green": False}), dry_run=False)
    assert not r["ok"] and "tests" in r["error"] and store.log == []


def test_held_with_running_workers_stops(store):
    r = d.deploy("p", "v1", "prod", FakeAd(changed=["dispatcher/relay.py"], running=["PC-9"]), dry_run=False)
    assert not r["ok"] and "PC-9" in r["error"] and store.log == []


def test_held_without_workers_proceeds(store):
    r = d.deploy("p", "v1", "prod", FakeAd(changed=["dispatcher/relay.py"]), dry_run=False)
    assert r["ok"] and r["data"]["plan"]["mode"] == "held"


def test_happy_path(store):
    ad = FakeAd()
    r = d.deploy("p", "v1", "prod", ad, dry_run=False)
    assert r["ok"] and r["error"] is None and not r["dry_run"]
    assert store.log == ["lock", "install", "switch"]
    assert ad.calls == ["fetch", "selftest", "restart", "health"]
    assert ad.records == [("success", "deployed")]
    assert [s["step"] for s in r["data"]["steps"]][-3:] == ["restart", "health", "record"]


def test_lock_busy(store):
    store.held = True
    ad = FakeAd()
    r = d.deploy("p", "v1", "prod", ad, dry_run=False)
    assert not r["ok"] and "lock" in r["error"] and ad.calls == []


def test_selftest_failure_installs_nothing(store):
    ad = FakeAd(selftest=(False, "boom"))
    r = d.deploy("p", "v1", "prod", ad, dry_run=False)
    assert not r["ok"] and "selftest" in r["error"]
    assert "install" not in store.log and ad.records[0][0] == "failure"


def test_install_failure_before_switch(store):
    store.fail_install = True
    r = d.deploy("p", "v1", "prod", FakeAd(), dry_run=False)
    assert not r["ok"] and "rollback" not in store.log and "switch" not in store.log


def test_health_failure_rolls_back(store):
    ad = FakeAd(health=(False, "503"))
    r = d.deploy("p", "v1", "prod", ad, dry_run=False)
    assert not r["ok"] and r["error"].startswith("rolled back") and "503" in r["error"]
    assert store.log[-1] == "rollback" and ad.calls.count("restart") == 2
    assert ad.records[0][0] == "failure"


def test_restart_failure_rolls_back(store):
    ad = FakeAd(restarts=[(False, "no job"), (True, "ok")])
    r = d.deploy("p", "v1", "prod", ad, dry_run=False)
    assert r["error"].startswith("rolled back") and "health" not in ad.calls


def test_an_installed_release_skips_the_clone_checks_and_the_install(store):
    ad = FakeAd(facts=dict(installed=True, clean=False, tests_green=False))
    r = d.deploy("p", "v2026.11.24-1", {}, ad, dry_run=False)
    assert r["ok"] and r["data"]["installed"], r
    assert ad.calls == ["restart", "health"] and "install" not in store.log and store.log[-1] == "switch"
    assert not d.deploy("p", "v2026.11.24-1", {}, FakeAd(facts=dict(installed=True, ahead_of_live=False)), dry_run=True)["ok"]
    assert not d.deploy("p", "v2026.11.24-1", {}, FakeAd(facts=dict(clean=False)), dry_run=True)["ok"]
