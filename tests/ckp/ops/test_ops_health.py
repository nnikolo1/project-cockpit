import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from ckp.adapters.deploy import GitDeployAdapter  # noqa: E402


class Proc:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def health(self, port, path="/", **kw):
        self.calls.append((port, path))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def adapter(proc, wait=10):
    t = {"now": 0.0}
    ad = GitDeployAdapter("p", "o/p", "/r", "production", process=proc, health_wait_s=wait, health_interval_s=2,
                          sleep=lambda s: t.__setitem__("now", t["now"] + s), clock=lambda: t["now"])
    return ad


def test_health_polls_until_the_server_answers():
    proc = Proc([(False, "refused"), (False, "refused"), (True, "ok")])
    assert adapter(proc).health({"port": 8800}) == (True, "ok") and proc.calls == [(8800, "/health")] * 3


def test_health_gives_up_after_the_grace_window():
    proc = Proc([(False, "refused")])
    ok, detail = adapter(proc, wait=6).health({"port": 8800})
    assert not ok and detail == "refused" and len(proc.calls) == 4   # 0, 2, 4, 6 seconds
