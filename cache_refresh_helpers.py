import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from config import load_config

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cache-refresh")
_lock = threading.Lock()
_pending = {}
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


def submit_cache_refresh(key, refresh, *, force=False):
    retry_seconds = load_config()["PERFORMANCE"]["CACHE_REFRESH_RETRY_SECONDS"]
    with _lock:
        now = time.monotonic()
        for old_key in list(_pending):
            if _pending[old_key].done():
                del _pending[old_key]
        for old_key in list(_retry_after):
            if _retry_after[old_key] <= now:
                del _retry_after[old_key]
        if key in _pending:
            return _pending[key]
        if not force and _retry_after.get(key, 0) > now:
            return None
        if len(_pending) >= 64:
            return None
        future = _executor.submit(_refresh, key, refresh, retry_seconds)
        _pending[key] = future
    future.add_done_callback(lambda completed: _forget_completed(key, completed))
    return future


def request_cache_refresh(key, refresh):
    return submit_cache_refresh(key, refresh)


def _forget_completed(key, future):
    with _lock:
        if _pending.get(key) is future:
            del _pending[key]
