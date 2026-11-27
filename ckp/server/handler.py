"""The HTTP handler: the token and cookie check, the routing through ckp.server.routes, JSON and file responses, the request log."""
import json
import logging
import re
import secrets
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ckp.api import context as api_context, gh as api_gh
from ckp.api.result import File
from ckp.server import routes as server_routes

log = logging.getLogger("cockpit")


def redact(line):
    """Keep the access token out of the request log."""
    return re.sub(r"([?&]t=)[^ &]+", r"\1<token>", line)


class Server(ThreadingHTTPServer):
    request_queue_size = 128  # the page opens about ten connections at once; the default backlog of 5 dropped some of them
    daemon_threads = True


class H(BaseHTTPRequestHandler):
    token = ""

    def log_message(self, fmt, *a):
        log.info("%s %s", self.address_string(), redact(fmt % a))

    def send(self, code, body=b"", ctype="application/json", headers=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return "t" in c and secrets.compare_digest(c["t"].value, self.token)

    def answer(self, route, ctx, q, rest, *form):
        """Call the route's API function and send its Result: a success as it is (JSON, or a file with its content type), a refusal as {"error"}."""
        r = route.fn(ctx, *form, **{k: read(q, rest) for k, read in route.params.items()})
        if not r["ok"]:
            return self.send(r["status"], dict(error=r["error"]))
        d = r["data"]
        return self.send(r["status"], d.body, d.ctype) if isinstance(d, File) else self.send(r["status"], d)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        route, rest = server_routes.match("GET", u.path)
        if route and route.public:
            return self.answer(route, api_context.Context(), q, rest)
        if q.get("t", [""])[0] and secrets.compare_digest(q["t"][0], self.token):
            return self.send(303, headers=[("Set-Cookie", f"t={self.token}; HttpOnly; SameSite=Strict; Path=/"), ("Location", u.path)])
        if not self.authed():
            return self.send(403, dict(error="forbidden"))
        try:
            if route and not route.project:
                return self.answer(route, api_context.Context(), q, rest)
            name_q = q.get("project", [""])[0]
            if u.path.startswith("/report/") and not name_q:  # files a report page loads carry no query: take the page's own
                name_q = parse_qs(urlparse(self.headers.get("Referer", "")).query).get("project", [""])[0]
            p = api_context.proj(name_q or None)
            if route is None:
                return self.send(404, dict(error="not found"))
            self.answer(route, api_context.Context(p=p), q, rest)
        except api_context.UnknownProject as ex:
            self.send(404, dict(error=ex.args[0]))
        except Exception as ex:
            log.exception("GET %s failed", u.path)
            self.send(502, dict(error=str(ex) + ("" if isinstance(ex, api_gh.GitHubUnavailable) else " (no earlier copy to show; Refresh tries again now)")))

    def do_POST(self):
        u = urlparse(self.path)
        route, rest = server_routes.match("POST", u.path)
        if not self.authed() or route is None:
            return self.send(403, dict(error="forbidden"))
        try:
            form = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self.send(400, dict(error="bad JSON"))
        if not form.get("project") and parse_qs(u.query).get("project"):
            form["project"] = parse_qs(u.query)["project"][0]
        if not isinstance(form, dict):
            return self.send(400, dict(error="bad JSON"))
        self.answer(route, api_context.Context(), {}, rest, form)


api_context.SECRET_SOURCES.append(lambda: (H.token,))   # the access token never reaches the action log or a page event
