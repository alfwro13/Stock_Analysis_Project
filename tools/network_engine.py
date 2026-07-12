import json
import time
import random
import logging
import traceback
import threading
import queue as _queue_module
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from curl_cffi import requests as cffi_requests

from config import load_config, DATA_DIR
from notification_engine import notify, current_job_source

_IPV6_FAULT_FLAG = DATA_DIR / "ipv6_fault.flag"

logger = logging.getLogger(__name__)

# is_failing is a one-time latch — once True it stays True for the process lifetime; callers skip IPv6 on sight.
GLOBAL_IPV6_STATUS = {
    "is_failing": False,
    "last_error": "",
    "last_fail_time": 0.0,
}
_ipv6_status_lock = threading.Lock()
_latch_initialized = False
_latch_init_lock = threading.Lock()

_RATE_LIMIT_READY = threading.Event()
_RATE_LIMIT_READY.set()
_rate_limit_cb_lock = threading.Lock()
_RATE_LIMIT_COOLDOWN_SECS: float = 60.0

_routing_lock = threading.Lock()
_routing_counter = 0

_stats_queue: "_queue_module.Queue" = _queue_module.Queue()
_stats_writer_started = False
_stats_writer_init_lock = threading.Lock()

_CALL_LOG_RETENTION_DAYS = 8
_PRUNE_INTERVAL_SECS = 3600.0
_last_call_log_prune = 0.0

# yfinance often logs an ERROR (e.g. "possibly delisted", a 404 on an unsupported quoteSummary
# module) and just returns an empty result instead of raising — invisible to the except-block-based
# stat_status above. This filter counts those separately so the Yahoo API Usage panel can surface
# them without conflating them with actual request failures (429s/exceptions).
_yf_logged_error_local = threading.local()
_yf_noise_suppress_local = threading.local()
_yf_error_filter_installed = False
_yf_error_filter_lock = threading.Lock()


class _YfErrorNoiseFilter(logging.Filter):
    """Always counts ERROR-level yfinance log records (for the API Usage tracker), then — only
    while suppress_yf_delisted_noise(True) is active on the calling thread — demotes yfinance's
    "possibly delisted" line to DEBUG. That message is yfinance's generic wording for "empty
    result", which is a known false alarm for a thinly-traded ticker's intraday fetch (see
    yahoo_engine.get_intraday's gap-tracking); counting must happen before demotion so the stat
    still reflects the real event even though it's no longer written to the log file."""
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR:
            count = getattr(_yf_logged_error_local, "count", None)
            if count is not None:
                _yf_logged_error_local.count = count + 1
            if getattr(_yf_noise_suppress_local, "active", False) and "possibly delisted" in record.getMessage():
                record.levelno = logging.DEBUG
                record.levelname = "DEBUG"
        return True


def _ensure_yf_error_filter() -> None:
    global _yf_error_filter_installed
    if _yf_error_filter_installed:
        return
    with _yf_error_filter_lock:
        if _yf_error_filter_installed:
            return
        logging.getLogger("yfinance").addFilter(_YfErrorNoiseFilter())
        _yf_error_filter_installed = True


def suppress_yf_delisted_noise(active: bool) -> None:
    """Demotes yfinance's "possibly delisted" ERROR line to DEBUG for the current thread while
    active=True. Scope this narrowly around a call site where an empty result is an expected,
    already-handled condition (e.g. yahoo_engine.get_intraday's per-ticker gap tracking) — it
    must not be left active around a call where "possibly delisted" could mean a real delisting
    worth seeing in the log (e.g. the nightly historical/fundamentals fetch)."""
    _yf_noise_suppress_local.active = active


class _RateLimitedError(Exception):
    """Raised on HTTP 429; bypasses IPv6-fault handling and transient-retry logic."""


class _TransientHTTPError(Exception):
    """Raised after exhausting retries on a repeated Yahoo Finance 5xx; bypasses IPv6-fault handling like _RateLimitedError."""


def _select_interface(use_ipv4: bool, use_ipv6: bool) -> str:
    global _routing_counter
    if use_ipv4 and use_ipv6:
        with _routing_lock:
            result = "ipv4" if _routing_counter % 2 == 0 else "ipv6"
            _routing_counter += 1
        return result
    return "ipv6" if use_ipv6 else "ipv4"


def _update_ipv6_status(failing: bool, error: str = "", fail_time: float = 0.0) -> None:
    with _ipv6_status_lock:
        GLOBAL_IPV6_STATUS["is_failing"] = failing
        GLOBAL_IPV6_STATUS["last_error"] = error
        GLOBAL_IPV6_STATUS["last_fail_time"] = fail_time


def _maybe_restore_latch() -> None:
    # Pre-sets the latch from the fault flag file so a restarted process silently skips IPv6 during an active fault window.
    global _latch_initialized
    with _latch_init_lock:
        if _latch_initialized:
            return
        _latch_initialized = True
    try:
        if not _IPV6_FAULT_FLAG.exists():
            return
        data = json.loads(_IPV6_FAULT_FLAG.read_text())
        fault_ts = data.get("timestamp", 0)
        age_secs = datetime.now(timezone.utc).timestamp() - fault_ts
        if age_secs < 3600:
            age_mins = int(age_secs // 60)
            _update_ipv6_status(
                failing=True,
                error=f"Latch restored from flag — last hard fault {age_mins}m ago.",
                fail_time=fault_ts,
            )
            logger.warning("IPv6 latch pre-set from flag file: last hard fault was %dm ago — skipping IPv6 this session.", age_mins)
    except Exception as e:
        logger.debug("Could not restore IPv6 latch from flag file: %s", e)


def _clear_yfinance_crumb() -> None:
    # YfData singleton binds crumb to old session cookies; clear it so the next fetch re-authenticates.
    try:
        from yfinance.data import YfData
        for inst in YfData._instances.values():
            with inst._cookie_lock:
                inst._crumb = None
                inst._cookie = None
        logger.debug("Cleared yfinance crumb cache after session change.")
    except Exception as e:
        logger.debug("Could not clear yfinance crumb cache: %s", e)


def wait_for_yahoo_rate_limit_reset(timeout: float = 65.0) -> None:
    """Block until the global Yahoo 429 cooldown has passed. Returns immediately when not rate-limited."""
    _RATE_LIMIT_READY.wait(timeout=timeout)


def _enter_yahoo_rate_limit(action_context: str) -> None:
    """Trip the global 429 circuit breaker. Only the first concurrent caller does work; others return immediately."""
    with _rate_limit_cb_lock:
        if not _RATE_LIMIT_READY.is_set():
            return
        _RATE_LIMIT_READY.clear()

    _clear_yfinance_crumb()
    logger.warning(
        "Yahoo Finance HTTP 429 during '%s' — pausing all Yahoo requests for %.0fs and resetting session.",
        action_context, _RATE_LIMIT_COOLDOWN_SECS,
    )

    def _reset() -> None:
        time.sleep(_RATE_LIMIT_COOLDOWN_SECS)
        _RATE_LIMIT_READY.set()
        logger.info("Yahoo Finance rate-limit cooldown complete — resuming requests.")

    threading.Thread(target=_reset, daemon=True).start()


def _trigger_fallback_alert(ipv6_address: str, action_context: str, error_summary: str, detailed_trace: str, config: dict) -> None:
    # Atomically check-and-set ensures only the first concurrent caller fires the alert; all others see is_failing=True and return.
    with _ipv6_status_lock:
        if GLOBAL_IPV6_STATUS["is_failing"]:
            logger.warning("IPv6 already disabled — suppressing duplicate alert for: %s", error_summary)
            return
        GLOBAL_IPV6_STATUS["is_failing"] = True
        GLOBAL_IPV6_STATUS["last_error"] = error_summary
        GLOBAL_IPV6_STATUS["last_fail_time"] = time.time()

    # Survives process restarts; read by _maybe_restore_latch on next startup.
    try:
        _IPV6_FAULT_FLAG.write_text(json.dumps({
            "timestamp": GLOBAL_IPV6_STATUS["last_fail_time"],
            "error": error_summary,
        }))
    except Exception as flag_e:
        logger.debug("Could not write IPv6 fault flag: %s", flag_e)

    msg = (
        f"Critical Fault on {ipv6_address} while accessing Yahoo Finance for '{action_context}'.\n"
        f"Summary: {error_summary}\n"
        f"Details:\n{detailed_trace}"
    )
    alert_msg = (
        f"🚨 **CRITICAL NETWORK FAULT: YAHOO FINANCE** 🚨\n\n"
        f"The custom IPv6 socket (`{ipv6_address}`) experienced a hard failure while fetching data for `{action_context}`.\n"
        f"**Error:** {error_summary}\n\n"
        f"🔄 *IPv6 interface permanently disabled for this session. All subsequent requests will use standard IPv4 routing.*\n\n"
        f"*(Full stack trace has been written to the SQLite system_notifications table.)*"
    )
    notify("network_fault", "Network Fault", msg, nextcloud_text=alert_msg, level="error")


def _patch_session_with_retries(
    session: cffi_requests.Session,
    action_context: str,
    timeout: int = 30,
    max_retries: int = 3,
) -> None:
    original_request = session.request
    base_delay = 2.0

    def wrapped_request(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        for attempt in range(max_retries + 1):
            try:
                response = original_request(method, url, **kwargs)
            except Exception as e:
                if isinstance(e, _RateLimitedError):
                    raise
                error_str = str(e)
                is_transient = (
                    type(e).__name__.lower() in {"timeout", "connectiontimeout", "connectionerror"}
                    or any(term in error_str.lower() for term in [
                        "timeout", "timed out", "connection reset", "curl: (28)", "(28)",
                    ])
                )
                if is_transient and attempt < max_retries:
                    sleep_time = (base_delay ** attempt) + random.uniform(0.5, 1.5)
                    logger.warning(
                        "Transient network error during '%s': %s. Retrying in %.2fs (Attempt %d/%d).",
                        action_context, error_str, sleep_time, attempt + 1, max_retries,
                    )
                    time.sleep(sleep_time)
                    continue
                raise
            else:
                if response.status_code == 429:
                    _enter_yahoo_rate_limit(action_context)
                    raise _RateLimitedError("HTTP 429. URL: %s" % url)
                if response.status_code >= 500:
                    if attempt < max_retries:
                        sleep_time = (base_delay ** attempt) + random.uniform(0.5, 1.5)
                        logger.warning(
                            "Transient network error during '%s': HTTP %d from Yahoo Finance. Retrying in %.2fs (Attempt %d/%d).",
                            action_context, response.status_code, sleep_time, attempt + 1, max_retries,
                        )
                        time.sleep(sleep_time)
                        continue
                    raise _TransientHTTPError(
                        "HTTP %d from Yahoo Finance during '%s' after %d attempts. URL: %s"
                        % (response.status_code, action_context, max_retries + 1, url)
                    )
                return response

    session.request = wrapped_request


def create_failover_session(ipv6_address: str, action_context: str, config: dict) -> cffi_requests.Session:
    # Impersonates Chrome to bypass Yahoo TLS fingerprinting; monkey-patches request to catch IPv6 bind faults and HTTP 429s.
    session = cffi_requests.Session(impersonate="chrome", interface=ipv6_address)
    session.fallback_triggered = False
    original_request = session.request

    def failover_request(method, url, **kwargs):
        # nonlocal required: without it Python treats `session`/`original_request` as locals (UnboundLocalError at first read). Has bitten us twice.
        nonlocal session, original_request
        global GLOBAL_IPV6_STATUS
        kwargs.setdefault("timeout", 30)
        max_retries = 3
        base_delay = 2.0

        for attempt in range(max_retries + 1):
            try:
                response = original_request(method, url, **kwargs)

                if response.status_code == 429:
                    _enter_yahoo_rate_limit(action_context)
                    raise _RateLimitedError("HTTP 429 on IPv6 interface. URL: %s" % url)

                if response.status_code >= 500:
                    if attempt < max_retries:
                        sleep_time = (base_delay ** attempt) + random.uniform(0.5, 1.5)
                        logger.warning(
                            "Transient network error during '%s': HTTP %d from Yahoo Finance. Retrying in %.2fs (Attempt %d/%d).",
                            action_context, response.status_code, sleep_time, attempt + 1, max_retries,
                        )
                        time.sleep(sleep_time)
                        continue
                    raise _TransientHTTPError(
                        "HTTP %d from Yahoo Finance during '%s' on IPv6 interface after %d attempts. URL: %s"
                        % (response.status_code, action_context, max_retries + 1, url)
                    )

                if not getattr(session, 'fallback_triggered', False):
                    _update_ipv6_status(failing=False)

                return response

            except Exception as e:
                if isinstance(e, (_RateLimitedError, _TransientHTTPError)):
                    raise

                error_str = str(e)

                if "Session is closed" in error_str:
                    logger.warning("Closed session detected. Rebuilding IPv6 session for '%s' (%s)...", action_context, ipv6_address)
                    session = cffi_requests.Session(impersonate="chrome", interface=ipv6_address)
                    original_request = session.request
                    continue

                # If we already fell back to standard routing and it STILL failed, we are completely offline or hard-banned.
                if getattr(session, 'fallback_triggered', False):
                    raise e

                # bind errors (errno 99) are a startup race — IPv6 interface may still be in DAD; retry before hard-faulting.
                is_transient = (
                    type(e).__name__.lower() in {"timeout", "connectiontimeout", "connectionerror"}
                    or any(term in error_str.lower() for term in [
                        "timeout", "timed out", "connection reset", "curl: (28)", "(28)",
                        "bind failed", "cannot assign requested address", "errno 99",
                    ])
                )
                if is_transient and attempt < max_retries:
                    sleep_time = (base_delay ** attempt) + random.uniform(0.5, 1.5)
                    logger.warning("Transient network error on IPv6 '%s': %s. Retrying in %.2fs (Attempt %d/%d).", ipv6_address, error_str, sleep_time, attempt + 1, max_retries)
                    time.sleep(sleep_time)
                    continue

                error_summary = "%s: %s" % (type(e).__name__, error_str)
                detailed_trace = (
                    "Target URL: %s %s\nAttempt: %d of %d\nStack Trace:\n%s"
                    % (method, url, attempt + 1, max_retries + 1, traceback.format_exc())
                )

                logger.error("Critical IPv6 Fault during '%s': %s\n%s", action_context, error_summary, detailed_trace)
                # duplicate concurrent callers are no-ops: latch check-and-set is atomic.
                _trigger_fallback_alert(ipv6_address, action_context, error_summary, detailed_trace, config)

                # Build fresh unbound session — mutating session.interface on a live curl_cffi is undocumented and may be silently ignored by libcurl.
                logger.warning("Exhausted IPv6 recovery options. Dropping custom interface %s and reverting to OS default routing (IPv4)...", ipv6_address)
                session.fallback_triggered = True
                _clear_yfinance_crumb()
                fallback_session = cffi_requests.Session(impersonate="chrome")
                try:
                    return fallback_session.request(method, url, **kwargs)
                except Exception as fallback_exc:
                    logger.error("[TOTAL FAILURE] IPv4 fallback also failed for '%s': %s", action_context, fallback_exc)
                    raise fallback_exc
                finally:
                    fallback_session.close()

    session.request = failover_request
    return session


def _ensure_stats_writer() -> None:
    global _stats_writer_started
    if _stats_writer_started:
        return
    with _stats_writer_init_lock:
        if _stats_writer_started:
            return
        _stats_writer_started = True

    def _writer() -> None:
        while True:
            try:
                call_time, date_str, interface, status, job_id, action_context, yf_errors = _stats_queue.get(timeout=60)
                _write_api_stat(date_str, interface, status, yf_errors)
                _write_call_log_entry(call_time, date_str, interface, status, job_id, action_context, yf_errors)
                _maybe_prune_call_log()
            except _queue_module.Empty:
                pass
            except Exception as e:
                logger.debug("Stats writer error: %s", e)

    threading.Thread(target=_writer, daemon=True).start()


def _write_call_log_entry(call_time: str, date_str: str, interface: str, status: str, job_id, action_context: str, yf_errors: int = 0) -> None:
    from database import get_connection
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO yahoo_api_call_log (call_time, date, interface, status, job_id, action_context, yf_logged_errors) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (call_time, date_str, interface, status, job_id, action_context, yf_errors),
        )
        conn.commit()
    except Exception as e:
        logger.debug("Could not write Yahoo API call log entry: %s", e)
    finally:
        if conn:
            conn.close()


def _maybe_prune_call_log() -> None:
    # Per-call rows would grow unbounded otherwise — keep only the window the detail chart covers.
    global _last_call_log_prune
    now = time.time()
    if now - _last_call_log_prune < _PRUNE_INTERVAL_SECS:
        return
    _last_call_log_prune = now
    from database import get_connection
    conn = None
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=_CALL_LOG_RETENTION_DAYS)).strftime("%Y-%m-%d")
        conn = get_connection()
        conn.execute("DELETE FROM yahoo_api_call_log WHERE date < ?", (cutoff,))
        conn.commit()
    except Exception as e:
        logger.debug("Could not prune Yahoo API call log: %s", e)
    finally:
        if conn:
            conn.close()


def _write_api_stat(date_str: str, interface: str, status: str, yf_errors: int = 0) -> None:
    is_ipv4 = 1 if interface == "ipv4" else 0
    is_ipv6 = 1 - is_ipv4
    is_429 = 1 if status == "429" else 0
    is_err = 1 if status == "error" else 0
    try:
        from database import get_connection
        conn = None
        try:
            conn = get_connection()
            conn.execute("""
                INSERT INTO yahoo_api_stats
                    (date, total_calls, ipv4_calls, ipv6_calls, rate_limit_429, other_errors, yfinance_logged_errors)
                VALUES (?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    total_calls            = total_calls + 1,
                    ipv4_calls             = ipv4_calls + ?,
                    ipv6_calls             = ipv6_calls + ?,
                    rate_limit_429         = rate_limit_429 + ?,
                    other_errors           = other_errors + ?,
                    yfinance_logged_errors = yfinance_logged_errors + ?
            """, (date_str, is_ipv4, is_ipv6, is_429, is_err, yf_errors,
                  is_ipv4, is_ipv6, is_429, is_err, yf_errors))
            conn.commit()
        finally:
            if conn:
                conn.close()
    except Exception as e:
        logger.debug("Could not write Yahoo API stat: %s", e)


def _increment_api_stat(interface: str, status: str, action_context: str = "", yf_errors: int = 0) -> None:
    now = datetime.now(timezone.utc)
    call_time = now.strftime("%Y-%m-%d %H:%M:%S")
    date_str = call_time[:10]
    _ensure_stats_writer()
    _stats_queue.put((call_time, date_str, interface, status, current_job_source(), action_context, yf_errors))


@contextmanager
def yahoo_connection_boundary(action_context: str):
    _maybe_restore_latch()
    _ensure_yf_error_filter()
    config = load_config()
    ipv6_addr = config.get("YAHOO_IPV6_ADDRESS", "").strip()
    use_ipv4 = config.get("YAHOO_USE_IPV4", True)
    # Backward compat: if YAHOO_USE_IPV6 absent, infer from whether IPv6 addr is set
    use_ipv6 = config.get("YAHOO_USE_IPV6", bool(ipv6_addr)) and bool(ipv6_addr)

    # IPv6 permanent latch: if IPv6 hard-failed this session, disable it
    if GLOBAL_IPV6_STATUS["is_failing"]:
        use_ipv6 = False
        if not use_ipv4:
            use_ipv4 = True  # safety: IPv6-only mode but IPv6 is dead

    interface = _select_interface(use_ipv4, use_ipv6)

    if interface == "ipv6" and not use_ipv4:
        # IPv6-only: use existing failover session (retains hard-fail latch + emergency IPv4 fallback)
        session = create_failover_session(ipv6_addr, action_context, config)
    elif interface == "ipv6":
        # Dual mode: plain IPv6 session — round-robin handles failures naturally, no failover needed
        session = cffi_requests.Session(impersonate="chrome", interface=ipv6_addr)
        _patch_session_with_retries(session, action_context)
    else:
        session = cffi_requests.Session(impersonate="chrome")
        _patch_session_with_retries(session, action_context)

    stat_status = "success"
    _yf_logged_error_local.count = 0
    try:
        yield session
    except _RateLimitedError:
        stat_status = "429"
        raise
    except Exception:
        stat_status = "error"
        raise
    finally:
        session.close()
        yf_errors = _yf_logged_error_local.count
        _yf_logged_error_local.count = None
        _increment_api_stat(interface, stat_status, action_context, yf_errors)
