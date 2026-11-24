import asyncio
import os
import tempfile

from dispatcher import relay


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
