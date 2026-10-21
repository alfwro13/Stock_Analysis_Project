import math
import threading
import time
from collections import deque
from typing import Dict, Optional

from constants import (
    MCP_AUTH_FAILURE_WINDOW_SECONDS, MCP_AUTH_FAILURES_MAX, MCP_AUTH_LOCKOUT_SECONDS, MCP_RATE_CALLS_PER_MINUTE,
    MCP_RATE_CONCURRENT_CALLS, MCP_REFUSAL_LOG_SECONDS, MCP_THROTTLE_MAX_ENTRIES,
)

_MINUTE = 60
_SWEEP_SECONDS = 30
_lock = threading.Lock()
_calls: Dict[Optional[int], deque] = {}
_active: Dict[Optional[int], int] = {}
_failures: Dict[str, deque] = {}
_locked_until: Dict[str, float] = {}
_refusal_logged: Dict[tuple, float] = {}
_last_sweep = 0.0


def _now() -> float:
    return time.monotonic()


def _put(mapping: dict, key, value) -> None:
    if key not in mapping and len(mapping) >= MCP_THROTTLE_MAX_ENTRIES:
        del mapping[next(iter(mapping))]
    mapping[key] = value


def _sweep_locked(now: float) -> None:
    global _last_sweep
    if now - _last_sweep < _SWEEP_SECONDS:
        return
    _last_sweep = now
    for address in [a for a, until in _locked_until.items() if until <= now]:
        del _locked_until[address]
    for address in [a for a, times in _failures.items() if not times or now - times[-1] > MCP_AUTH_FAILURE_WINDOW_SECONDS]:
        del _failures[address]
    for key in [k for k, times in _calls.items() if not _active.get(k) and (not times or now - times[-1] >= _MINUTE)]:
        del _calls[key]
    for scope in [s for s, logged in _refusal_logged.items() if now - logged >= MCP_REFUSAL_LOG_SECONDS]:
        del _refusal_logged[scope]


def lockout_remaining(address: str) -> int:
    with _lock:
        now = _now()
        until = _locked_until.get(address)
        if until is None:
            return 0
        if until <= now:
            del _locked_until[address]
            return 0
        return math.ceil(until - now)


def record_failure(address: str) -> bool:
    """True when this failure is the one that locks the address out."""
    with _lock:
        now = _now()
        _sweep_locked(now)
        times = _failures.get(address)
        if times is None:
            times = deque()
            _put(_failures, address, times)
        times.append(now)
        while now - times[0] > MCP_AUTH_FAILURE_WINDOW_SECONDS:
            times.popleft()
        if len(times) < MCP_AUTH_FAILURES_MAX:
            return False
        del _failures[address]
        _put(_locked_until, address, now + MCP_AUTH_LOCKOUT_SECONDS)
        return True


def acquire(key_id: Optional[int]) -> int:
    """0 when the call may run (a slot is taken until release()), else the seconds to wait."""
    with _lock:
        now = _now()
        _sweep_locked(now)
        times = _calls.get(key_id)
        if times is None:
            times = deque()
            _put(_calls, key_id, times)
        while times and now - times[0] >= _MINUTE:
            times.popleft()
        if len(times) >= MCP_RATE_CALLS_PER_MINUTE:
            return max(1, math.ceil(_MINUTE - (now - times[0])))
        if _active.get(key_id, 0) >= MCP_RATE_CONCURRENT_CALLS:
            return 1
        times.append(now)
        _active[key_id] = _active.get(key_id, 0) + 1
        return 0


def release(key_id: Optional[int]) -> None:
    with _lock:
        if _active.get(key_id, 0) > 1:
            _active[key_id] -= 1
        else:
            _active.pop(key_id, None)


def first_refusal(scope: tuple) -> bool:
    """True when a 429 for this address or key has not been audit-logged within MCP_REFUSAL_LOG_SECONDS."""
    with _lock:
        now = _now()
        logged = _refusal_logged.get(scope)
        if logged is not None and now - logged < MCP_REFUSAL_LOG_SECONDS:
            return False
        _put(_refusal_logged, scope, now)
        return True


def reset() -> None:
    global _last_sweep
    with _lock:
        for state in (_calls, _active, _failures, _locked_until, _refusal_logged):
            state.clear()
        _last_sweep = 0.0
