"""The caches of the slow answers (memory and disk), stale-while-revalidate with a staggered refresh plan, and the bookkeeping of actions re-read from GitHub."""
import logging
import datetime as dt
import random
import threading
import time

from ckp.core import queue_view as core_queue_view
from ckp.store.cache import DiskCache
from ckp.api import context as api_context, gh as api_gh

log = logging.getLogger("cockpit")


_cache, _lock = {}, threading.RLock()  # _lock only guards the bookkeeping dicts; every key computes under its own lock, so a slow key never blocks the others


_locks, _refreshing = {}, set()


def _dc():
    return DiskCache(api_context.CACHE_DIR)


def _disk_path(key):
    return _dc().path(key)


def _disk_load(key):
    return _dc().load(key)


def _jsonable(o):
    """Dates (the commits' days) are kept on disk as ISO strings; logic.group_commits reads both forms."""
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _disk_save(key, v):
    if not api_context.CACHE_DIR or api_context.OFFLINE:  # the restored backup is never written to
        return
    try:
        _dc().save(key, v, default=_jsonable)
    except (OSError, TypeError, ValueError) as ex:
        log.warning("cache %s not written to disk: %s", key, ex)


def drop_cache(prefix):
    """Forget the memory and disk copies of every key starting with `prefix` (after an action: nothing older may be served)."""
    with _lock:
        for k in [k for k in _cache if k.startswith(prefix)]:
            del _cache[k]
    _dc().drop(prefix)


STAGGER_WINDOW_S = 900   # the GitHub refreshes of all projects and resources are spread over this window (owner: staggered offsets, no burst)


STAGGER_JITTER_S = 120   # plus a random jitter per key, so that slots do not line up again


GH_RESOURCES = ("state", "ci", "commits", "rulesprs")  # the queued GitHub resources of a project (cached(..., queue=True))


_started, _slots = time.time(), {}


def _slot(key):
    """(offset, jitter) of `key` in the staggered plan of every configured project's resources; a key outside the plan gets a slot of its own."""
    with _lock:
        if key not in _slots:
            keys = {api_context.pk(p, r) for p in (api_context.PROJECTS.values() or [api_context.CFG]) for r in GH_RESOURCES} | set(_slots) | {key}
            _slots.clear()
            _slots.update(core_queue_view.stagger_plan(keys, STAGGER_WINDOW_S, STAGGER_JITTER_S, random.Random()))
        return _slots[key]


_failures = {}  # key -> dict(at, error, until, rate_limited) of the last failed refresh that was answered with an older copy; removed by the next success


def cached(key, fn, ttl, swr=False, persist=False, disk_fresh=0, queue=False):
    """fn()'s value for `key`, recomputed when older than ttl seconds. swr (stale-while-revalidate): an expired copy is served at once and
    refreshed in a background thread. persist: the value (JSON) is also kept on disk; a copy from an earlier run counts as expired, unless it is
    younger than disk_fresh seconds (then a restart costs no refresh). queue: the refresh costs GitHub requests and runs inside the one-at-a-time queue."""
    t, v = _cache.get(key, (0, None))
    if v is None and persist:
        v = _disk_load(key)
        if v is not None:
            t = _disk_time(key, disk_fresh)
            _cache.setdefault(key, (t, v))
    if v is not None and (api_context.OFFLINE or time.time() - t <= ttl):
        return v
    if v is not None and swr and queue and time.time() < core_queue_view.refresh_due(t, ttl, _slot(key), _started):
        return v  # staggered: this key's refresh slot has not come yet
    if v is not None and swr:
        with _lock:
            if key in _refreshing:
                return v
            _refreshing.add(key)

        def go():
            try:
                _refresh(key, fn, ttl, persist, queue)
            except Exception as ex:
                log.warning("%s: background refresh failed: %s", key, str(ex)[:150])
            finally:
                _refreshing.discard(key)
        threading.Thread(target=go, daemon=True).start()
        return v
    return _refresh(key, fn, ttl, persist, queue)


def _disk_time(key, disk_fresh):
    """The time a disk copy counts as fetched: its file time when younger than disk_fresh, else 0 (expired)."""
    try:
        m = _disk_path(key).stat().st_mtime
    except (OSError, AttributeError):
        return 0
    return m if disk_fresh and time.time() - m <= disk_fresh else 0


def _refresh(key, fn, ttl, persist, queue=False):
    with _lock:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        t, v = _cache.get(key, (0, None))
        if v is not None and time.time() - t <= ttl:
            return v  # another thread refreshed it while this one waited
        try:
            if queue:
                with api_gh._gh_queue:
                    v = fn()
            else:
                v = fn()
        except Exception as ex:
            if v is None:
                raise
            until = getattr(ex, "until", None)
            _failures[key] = dict(at=time.time(), error=str(ex)[:300], until=until, rate_limited=bool(until) or core_queue_view.is_rate_limited(str(ex)))
            log.warning("%s: refresh failed, serving the last good copy (%.0f s old): %s", key, time.time() - t, str(ex)[:120])
            _cache[key] = ((until or time.time() + 15) - ttl, v)  # try again when GitHub is allowed again, else in about 15 s, not on every request
            return v
        _failures.pop(key, None)
        _cache[key] = (time.time(), v)
        if persist:
            _disk_save(key, v)
        return v


def refreshing(prefix):
    """True while a background refresh of a key starting with `prefix` runs: what is served may be older than its ttl."""
    return any(k.startswith(prefix) for k in list(_refreshing))


def _expire(key):
    """Make the copy of `key` (memory, else disk) count as expired but keep it for a failed refresh."""
    v = _cache[key][1] if key in _cache else _disk_load(key)
    if v is not None:
        _cache[key] = (0, v)


_reread, _reread_lock = {}, threading.Lock()   # state key -> issue numbers waiting for their re-read


def _key_lock(k):
    with _lock:
        return _locks.setdefault(k, threading.Lock())


def _store(k, t, S):
    """Replace the cached state, keeping the time of its full load (a patch never resets the hour)."""
    _cache[k] = (t, S)
    _disk_save(k, S)


_deferred = set()      # state keys with a known effect still waiting for a full load to finish
