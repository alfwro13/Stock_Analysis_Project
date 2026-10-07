import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from config import load_config

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cache-refresh")
# Callers blocked on a result must never queue behind bulk background refreshes.
_awaited_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cache-refresh-awaited")
_lock = threading.RLock()
_pending = {}
_awaited_keys = set()
_retry_after = {}


def _refresh(key, refresh, retry_seconds):
    failed = True
    try:
        result = refresh()
        failed = result is None or result is False
        return result
    except Exception:
        failed = True
        logger.exception("Cache refresh failed for %s", key)
        return None
    finally:
        with _lock:
            if failed:
                _retry_after[key] = time.monotonic() + retry_seconds
            else:
                _retry_after.pop(key, None)


def _submit(key, refresh, *, force, awaited):
    retry_seconds = load_config()["PERFORMANCE"]["CACHE_REFRESH_RETRY_SECONDS"]
    with _lock:
        now = time.monotonic()
        for old_key in list(_pending):
            if _pending[old_key].done():
                del _pending[old_key]
                _awaited_keys.discard(old_key)
        for old_key in list(_retry_after):
            if _retry_after[old_key] <= now:
                del _retry_after[old_key]
        existing = _pending.get(key)
        # A still-queued background job is cancelled and resubmitted so an awaiting caller skips the queue.
        if existing is not None and (key in _awaited_keys or not awaited or not existing.cancel()):
            return existing
        if not force and _retry_after.get(key, 0) > now:
            return None
        if not awaited and len(_pending) - len(_awaited_keys) >= 64:
            return None
        future = (_awaited_executor if awaited else _executor).submit(_refresh, key, refresh, retry_seconds)
        _pending[key] = future
        if awaited:
            _awaited_keys.add(key)
    future.add_done_callback(lambda completed: _forget_completed(key, completed))
    return future


def submit_cache_refresh(key, refresh, *, force=False):
    return _submit(key, refresh, force=force, awaited=True)


def request_cache_refresh(key, refresh):
    return _submit(key, refresh, force=False, awaited=False)


def _forget_completed(key, future):
    with _lock:
        if _pending.get(key) is future:
            del _pending[key]
            _awaited_keys.discard(key)
