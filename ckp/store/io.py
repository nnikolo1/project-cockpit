"""The Cockpit's file operations: one atomic write, tolerant reads, JSON-lines append and read. Replaces the copies of the temporary-file
write that were spread over the server (cache, settings, generated files)."""
import json
import os
import pathlib


def atomic_write_text(path, text, mode=None):
    """Write `text` to `path` through a temporary file in the same folder and a rename, so a reader never sees half a file. `mode`: file
    permissions of a new file (for example 0o600 for settings)."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644 if mode is None else mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def atomic_write_json(path, data, mode=None, indent=None, default=None):
    """`atomic_write_text` of `data` as JSON (`default`: the encoder for values json cannot write, for example dates)."""
    atomic_write_text(path, json.dumps(data, indent=indent, default=default), mode)


def read_text(path, default=None):
    """The text of a file, or `default` when it is missing or unreadable."""
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return default


def read_json(path, default=None):
    """The JSON value of a file, or `default` when the file is missing, unreadable or not JSON."""
    t = read_text(path)
    if t is None:
        return default
    try:
        return json.loads(t)
    except ValueError:
        return default


def append_jsonl(path, record, default=None):
    """Append one record as a line to a JSON-lines file (created with its folder when missing)."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=default) + "\n")


def read_jsonl(path):
    """The records of a JSON-lines file; a line that is not a JSON object is skipped. A missing file gives an empty list."""
    out = []
    for line in (read_text(path) or "").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out
