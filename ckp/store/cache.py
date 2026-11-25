"""The on-disk copies of slow answers (the rebuildable caches): authoritative for nothing, safe to delete at any time. The shape is versioned
by the folder (`layout.cache_dir(schema)`), not by a wrapper in the file: the file holds the plain JSON value."""
import pathlib
import re

from ckp.store import io

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


class DiskCache:
    def __init__(self, directory):
        """`directory` None means no disk copies (every call is a no-op or a miss)."""
        self.dir = pathlib.Path(directory) if directory else None

    def path(self, key):
        return self.dir / (_UNSAFE.sub("_", key) + ".json") if self.dir else None

    def load(self, key):
        """The cached value, or None (no cache, no file, unreadable)."""
        return io.read_json(self.path(key)) if self.dir else None

    def save(self, key, value, default=None):
        """Write the value; raises OSError, TypeError or ValueError when it cannot be written (the caller logs)."""
        if self.dir:
            io.atomic_write_json(self.path(key), value, default=default)

    def mtime(self, key):
        """The file time of a copy, or None."""
        try:
            return self.path(key).stat().st_mtime if self.dir else None
        except OSError:
            return None

    def drop(self, prefix):
        """Delete every copy whose key starts with `prefix`."""
        if self.dir and self.dir.is_dir():
            for f in self.dir.glob(_UNSAFE.sub("_", prefix) + "*.json"):
                f.unlink(missing_ok=True)
