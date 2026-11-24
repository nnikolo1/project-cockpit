"""Tiny in-container relay of 4f: TCP 127.0.0.1:PORT -> a Unix socket.

The socket is the host proxy's socket, mounted into the container with -v
(the runtime relays it over vsock).

    python3 relay.py --port 8089 --socket /run/proxy.sock
"""
import argparse
import asyncio
import logging

log = logging.getLogger("relay")


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except OSError as e:
        log.debug("pipe ended: %s", e)  # the peer closed first; normal


async def handle(client_r, client_w, socket_path):
    try:
        up_r, up_w = await asyncio.open_unix_connection(socket_path)
    except OSError as e:
        log.warning("cannot reach %s: %s", socket_path, e)
        client_w.close()
        return
    await asyncio.gather(pipe(client_r, up_w), pipe(up_r, client_w))
    up_w.close()
    client_w.close()


async def serve(port, socket_path, ready=None):
    srv = await asyncio.start_server(
        lambda r, w: handle(r, w, socket_path), "127.0.0.1", port)
    log.info("relaying 127.0.0.1:%d -> %s", port, socket_path)
    if ready is not None:
        ready.set_result(srv.sockets[0].getsockname()[1])
    async with srv:
        await srv.serve_forever()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--socket", required=True)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s relay %(message)s")
    asyncio.run(serve(a.port, a.socket))


if __name__ == "__main__":
    main()
