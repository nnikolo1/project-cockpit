import asyncio
import http.client
import json
import os
import socket
import tempfile
import threading

import pytest

from dispatcher import proxy, relay


class UnixConn(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost")
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(self.path)


@pytest.fixture
def stack():
    d = tempfile.mkdtemp(dir="/tmp")
    sock = os.path.join(d, "p.sock")
    calls = []
    state = {"status": 200, "chunks": [b'{"usage": {"input_tokens": 3, "output_tokens": 5}}']}

    def upstream(method, path, headers, body):
        calls.append((method, path, headers, body))
        return state["status"], [("content-type", "application/json"), ("content-length", "9")], iter(state["chunks"])

    usage = proxy.Usage(os.path.join(d, "u.jsonl"))
    srv = proxy.make_server(sock, "REAL", {"dummy-a": "a", "dummy-b": "b"}, usage, upstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield sock, calls, state, usage, d
    srv.shutdown()
    srv.server_close()


def post(sock, path, headers=None, method="POST"):
    c = UnixConn(sock)
    c.request(method, path, b"{}", headers or {})
    r = c.getresponse()
    return r.status, r.read()


def test_forwards_with_owner_credential_only(stack):
    sock, calls, _, usage, _ = stack
    st, body = post(sock, "/v1/messages?beta=true", {"Authorization": "Bearer dummy-a", "x-api-key": "dummy-a",
                                                    "anthropic-beta": "foo"})
    assert st == 200 and b"usage" in body
    _, path, h, _ = calls[0]
    assert path == "/v1/messages?beta=true"
    assert h["Authorization"] == "Bearer REAL"
    assert not [k for k in h if k.lower() == "x-api-key"]
    assert h["anthropic-beta"] == "foo,oauth-2025-04-20"
    assert usage.totals["a"]["output_tokens"] == 5


def test_beta_not_duplicated(stack):
    sock, calls, *_ = stack
    post(sock, "/v1/messages", {"x-api-key": "dummy-a", "anthropic-beta": "oauth-2025-04-20"})
    assert calls[0][2]["anthropic-beta"] == "oauth-2025-04-20"


def test_count_tokens_allowed_but_not_counted(stack):
    sock, calls, _, usage, _ = stack
    assert post(sock, "/v1/messages/count_tokens", {"x-api-key": "dummy-b"})[0] == 200
    assert usage.totals == {}


@pytest.mark.parametrize("path,method", [("/v1/models", "POST"), ("/v1/messages", "GET"), ("/", "POST")])
def test_other_requests_refused(stack, path, method):
    sock, calls, *_ = stack
    assert post(sock, path, {"x-api-key": "dummy-a"}, method)[0] == 403
    assert calls == []


def test_unknown_or_missing_token_refused(stack):
    sock, calls, *_ = stack
    assert post(sock, "/v1/messages", {"x-api-key": "nope"})[0] == 401
    assert post(sock, "/v1/messages")[0] == 401
    assert calls == []


def test_upstream_error_status_passed_and_not_counted(stack):
    sock, _, state, usage, _ = stack
    state["status"] = 429
    assert post(sock, "/v1/messages", {"x-api-key": "dummy-a"})[0] == 429
    assert usage.totals == {}


def test_upstream_failure_gives_502():
    d = tempfile.mkdtemp(dir="/tmp")
    sock = os.path.join(d, "p.sock")

    def boom(*a):
        raise ConnectionError("x")
    srv = proxy.make_server(sock, "REAL", {"d": "a"}, proxy.Usage(), boom)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    assert post(sock, "/v1/messages", {"x-api-key": "d"})[0] == 502
    srv.shutdown()
    srv.server_close()


def test_streaming_usage_summed_per_worker(stack):
    sock, _, state, usage, d = stack
    sse = (b'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":10,'
           b'"cache_read_input_tokens":4,"output_tokens":1}}}\n\n'
           b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":7}}\n\n')
    state["chunks"] = [sse[:50], sse[50:]]
    post(sock, "/v1/messages", {"x-api-key": "dummy-a"})
    post(sock, "/v1/messages", {"x-api-key": "dummy-a"})
    t = usage.totals["a"]
    assert (t["input_tokens"], t["output_tokens"], t["cache_read_input_tokens"]) == (20, 14, 8)
    lines = open(os.path.join(d, "u.jsonl")).read().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["worker"] == "a"


def test_extract_usage_garbage():
    assert proxy.extract_usage(b"not json at all") == {}
    assert proxy.extract_usage(b"data: {bad\n") == {}


def test_socket_path_limit():
    with pytest.raises(ValueError):
        proxy.make_server("/tmp/" + "x" * 100, "T", {}, proxy.Usage())


def test_relay_round_trip():
    d = tempfile.mkdtemp(dir="/tmp")
    sock = os.path.join(d, "e.sock")

    async def run():
        async def echo(r, w):
            w.write(b"echo:" + await r.read(5))
            await w.drain()
            w.close()
        us = await asyncio.start_unix_server(echo, sock)
        ready = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(relay.serve(0, sock, ready))
        port = await ready
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"hello")
        await w.drain()
        data = await r.read(100)
        w.close()
        task.cancel()
        us.close()
        return data
    assert asyncio.run(run()) == b"echo:hello"


def test_relay_unreachable_socket_closes_client():
    async def run():
        ready = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(relay.serve(0, "/nonexistent/s", ready))
        port = await ready
        r, w = await asyncio.open_connection("127.0.0.1", port)
        data = await r.read(10)
        task.cancel()
        return data
    assert asyncio.run(run()) == b""
