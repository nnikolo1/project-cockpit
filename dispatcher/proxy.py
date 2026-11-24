"""Host reverse proxy of 4g/4h: listens only on a Unix socket, forwards only POST /v1/messages
(and /v1/messages/count_tokens) to the model API, strips any client credential, adds the owner's
subscription token, tells the Workers apart by their own dummy token and records token usage.

    python3 -m dispatcher.proxy --socket S --token-file F --worker label=dummy [...] --usage-file U

The token is read from the file F (outside git); it is never logged or printed.
"""
import argparse
import http.client
import json
import logging
import os
import socketserver
import threading
from http.server import BaseHTTPRequestHandler

log = logging.getLogger("proxy")

UPSTREAM_HOST = "api.anthropic.com"
ALLOWED = {"/v1/messages", "/v1/messages/count_tokens"}
DROP = {"host", "x-api-key", "authorization", "connection", "content-length", "accept-encoding"}
DROP_RESPONSE = {"transfer-encoding", "connection", "content-length", "content-encoding"}
OAUTH_BETA = "oauth-2025-04-20"
SOCKET_PATH_LIMIT = 104
USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def https_upstream(method, path, headers, body):
    """Default upstream: returns (status, [(k, v)], iterable of byte chunks)."""
    c = http.client.HTTPSConnection(UPSTREAM_HOST, timeout=120)
    c.request(method, path, body, headers)
    r = c.getresponse()

    def chunks():
        while chunk := r.read(1024):
            yield chunk
    return r.status, r.getheaders(), chunks()


def worker_label(headers, workers):
    """The label of the Worker whose dummy token the request carries, or None."""
    cred = headers.get("x-api-key") or ""
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        cred = cred or auth[7:]
    return workers.get(cred.strip())


def extract_usage(raw):
    """Token usage from a complete response body: plain JSON or an SSE stream. {} if none."""
    text = raw.decode("utf-8", "replace")
    usage = {}
    try:
        obj = json.loads(text)
        return {k: obj["usage"][k] for k in USAGE_FIELDS if k in (obj.get("usage") or {})}
    except (ValueError, AttributeError, TypeError):
        pass
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:])
        except ValueError:
            continue
        u = (ev.get("message") or {}).get("usage") if ev.get("type") == "message_start" else \
            ev.get("usage") if ev.get("type") == "message_delta" else None
        for k in USAGE_FIELDS:
            if u and k in u:
                usage[k] = u[k]  # later (cumulative) values replace earlier ones
    return usage


class Usage:
    """Per-Worker running totals; every request is also appended to a JSONL file (if given)."""

    def __init__(self, path=None):
        self.path = path
        self.totals = {}
        self._lock = threading.Lock()

    def add(self, label, usage):
        with self._lock:
            t = self.totals.setdefault(label, dict.fromkeys(USAGE_FIELDS, 0))
            for k in USAGE_FIELDS:
                t[k] += usage.get(k) or 0
            if self.path:
                with open(self.path, "a") as f:
                    f.write(json.dumps({"worker": label, **{k: usage.get(k) or 0 for k in USAGE_FIELDS}}) + "\n")


def make_handler(token, workers, usage, upstream):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _reply(self, status, label=None):
            log.info("%s %s %s worker=%s", status, self.command, self.path.split("?", 1)[0], label)
            self.send_response(status)
            self.send_header("content-length", "0")
            self.send_header("connection", "close")
            self.end_headers()
            self.close_connection = True

        def handle_any(self):
            path = self.path.split("?", 1)[0]
            body = self.rfile.read(int(self.headers.get("content-length") or 0))
            label = worker_label(self.headers, workers)
            if self.command != "POST" or path not in ALLOWED:
                return self._reply(403, label)
            if label is None:
                return self._reply(401)
            h = {k: v for k, v in self.headers.items() if k.lower() not in DROP}
            h["Authorization"] = "Bearer " + token
            betas = [b for b in h.get("anthropic-beta", "").split(",") if b]
            if OAUTH_BETA not in betas:
                betas.append(OAUTH_BETA)
            h["anthropic-beta"] = ",".join(betas)
            try:
                status, resp_headers, chunks = upstream("POST", self.path, h, body)
            except OSError as e:
                log.warning("upstream failed path=%s worker=%s: %s", path, label, type(e).__name__)
                return self._reply(502, label)
            log.info("%s POST %s worker=%s", status, path, label)
            self.send_response(status)
            for k, v in resp_headers:
                if k.lower() not in DROP_RESPONSE:
                    self.send_header(k, v)
            self.send_header("connection", "close")
            self.end_headers()
            seen = bytearray()
            for chunk in chunks:
                seen += chunk
                self.wfile.write(chunk)
                self.wfile.flush()
            self.close_connection = True
            if status == 200 and path == "/v1/messages":
                u = extract_usage(bytes(seen))
                if u:
                    usage.add(label, u)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = handle_any

    return H


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        r, _ = super().get_request()
        return r, ("unix", 0)


def make_server(socket_path, token, workers, usage, upstream=https_upstream):
    if len(socket_path.encode()) >= SOCKET_PATH_LIMIT:
        raise ValueError("socket path must be shorter than %d bytes" % SOCKET_PATH_LIMIT)
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    srv = Server(socket_path, make_handler(token, workers, usage, upstream))
    os.chmod(socket_path, 0o600)
    return srv


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--socket", required=True)
    p.add_argument("--token-file", required=True)
    p.add_argument("--worker", action="append", default=[], metavar="LABEL=DUMMY_TOKEN")
    p.add_argument("--usage-file")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s proxy %(message)s")
    with open(a.token_file) as f:
        token = f.read().strip()
    workers = {d: l for l, _, d in (w.partition("=") for w in a.worker)}
    srv = make_server(a.socket, token, workers, Usage(a.usage_file))
    log.info("listening on %s for %d workers", a.socket, len(workers))
    srv.serve_forever()


if __name__ == "__main__":
    main()
