import pathlib
import sys
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from ckp.store import layout, lock  # noqa: E402


def test_deploy_lock_path(tmp_path):
    assert layout.deploy_lock(tmp_path) == tmp_path / "deploy.lock"


def test_exclusive_refuses_while_exclusive_or_shared_is_held(tmp_path):
    f = tmp_path / "l"
    with lock.exclusive(f):
        with pytest.raises(lock.Busy):
            with lock.exclusive(f, wait_s=0):
                pass
        with pytest.raises(lock.Busy):
            with lock.shared(f, wait_s=0):
                pass
    with lock.shared(f):
        with pytest.raises(lock.Busy):
            with lock.exclusive(f, wait_s=0):
                pass


def test_shared_locks_coexist(tmp_path):
    f = tmp_path / "l"
    with lock.shared(f):
        with lock.shared(f):
            pass


def test_lock_is_released_on_exit_and_on_error(tmp_path):
    f = tmp_path / "l"
    with pytest.raises(RuntimeError):
        with lock.exclusive(f):
            raise RuntimeError("x")
    with lock.exclusive(f, wait_s=0):
        pass


def test_wait_succeeds_when_the_holder_lets_go(tmp_path):
    f = tmp_path / "sub" / "l"
    got = []
    with lock.exclusive(f):
        t = threading.Timer(0.2, lambda: None)
        def waiter():
            with lock.exclusive(f, wait_s=5):
                got.append(1)
        th = threading.Thread(target=waiter)
        th.start()
        t.start()
        t.join()
        assert not got
    th.join(5)
    assert got == [1]
