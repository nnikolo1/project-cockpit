"""The Result every API entry function returns: {"ok", "error", "data"}, plus `status`, the HTTP status the server answers with (200 or the
refusal's: 400, 403, 404, 409, 502, 503). `data` is the JSON body of a success; a `File` for an answer that is not JSON."""
import collections


File = collections.namedtuple("File", "body ctype")   # bytes or text of a non-JSON answer and its content type


def ok(data=None, status=200):
    return dict(ok=True, error=None, data=data, status=status)


def fail(error, status=400):
    return dict(ok=False, error=str(error), data=None, status=status)


def from_status(code, body):
    """The (http status, body) pair an action handler answers with -> a Result: 200 is a success carrying the body, anything else a refusal."""
    return ok(body, code) if code == 200 else fail(body.get("error"), code)
