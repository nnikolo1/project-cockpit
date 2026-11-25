import json
import pathlib
import sys
import threading
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import cockpit as C  # noqa: E402


def test_health_answers_without_a_token_and_says_whether_it_is_warming(monkeypatch):
    monkeypatch.setattr(C.H, "token", "secret", raising=False)
    srv = C.Server(("127.0.0.1", 0), C.H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        monkeypatch.setitem(C.WARM, "on", True)
        port = srv.server_address[1]
        r = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5))
        assert r == {"ok": True, "warming": True}
    finally:
        srv.shutdown()
