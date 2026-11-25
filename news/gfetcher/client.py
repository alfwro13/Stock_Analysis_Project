import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import quote

import requests

from outbound_fetch_helpers import FetchRedirected, FetchRefused, fetch_body

from . import store

logger = logging.getLogger(__name__)

QUOTE_URL = "https://www.google.com/finance/quote/{symbol}:{exchange}?hl=en"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    # Skips the EU consent page that would otherwise replace the quote page.
    "Cookie": "CONSENT=YES+",
}
MIN_DELAY_SECONDS = 3.0
JITTER = (0.8, 1.4)
MAX_PAGE_BYTES = 5 * 1024 * 1024
COOLDOWN_STEPS = (timedelta(hours=1), timedelta(hours=6), timedelta(hours=24))
REFUSAL_STATUSES = (429, 503)
_SORRY_PATH = "/sorry/"
_UNUSUAL_TRAFFIC = "unusual traffic from your computer network"


class GoogleRefused(Exception):
    """Google asked us to stop (429/503 or its CAPTCHA page); the run must end and the cooldown has been set."""

    def __init__(self, reason: str, until: datetime):
        super().__init__(reason)
        self.reason = reason
        self.until = until


class BudgetSpent(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _retry_after_seconds(response) -> Optional[int]:
    value = (response.headers.get("Retry-After") or "").strip() if response is not None else ""
    return int(value) if value.isdigit() else None


def enter_cooldown(reason: str, retry_after_s: Optional[int], now: datetime) -> datetime:
    refusals = store.get_state().consecutive_refusals + 1
    wait = COOLDOWN_STEPS[min(refusals, len(COOLDOWN_STEPS)) - 1]
    if retry_after_s:
        wait = max(wait, timedelta(seconds=retry_after_s))
    until = (now + wait).replace(microsecond=0)
    store.set_cooldown(until, reason, refusals)
    logger.warning("Google News Fetcher: Google refused a request (%s); cooling down until %s.", reason, store.stamp(until))
    return until


class GoogleClient:
    """One instance per run, used only on the job thread: its sleeps never sit under a lock a web request needs."""

    def __init__(self, delay_seconds: float, max_requests_per_day: int,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.delay = max(float(delay_seconds), MIN_DELAY_SECONDS)
        self.remaining = max(0, max_requests_per_day - store.google_requests_since(_now() - timedelta(days=1)))
        self.requests_made = 0
        self._sleep = sleep
        self._clock = clock
        self._last: Optional[float] = None

    def _wait(self) -> None:
        if self._last is None:
            return
        pause = self._last + self.delay * random.uniform(*JITTER) - self._clock()
        if pause > 0:
            self._sleep(pause)

    def get_quote_page(self, ticker: str, symbol: str, exchange: str) -> Optional[str]:
        """The quote page HTML, or None on an error that does not mean Google refused us."""
        if self.remaining <= 0:
            raise BudgetSpent()
        self._wait()
        self.remaining -= 1
        self.requests_made += 1
        url = QUOTE_URL.format(symbol=quote(symbol, safe="."), exchange=quote(exchange, safe=""))
        started = self._clock()
        status: Optional[int] = None
        retry_after: Optional[int] = None
        outcome = "ok"
        html: Optional[str] = None
        try:
            html = fetch_body(url, HEADERS, max_bytes=MAX_PAGE_BYTES)
            status = 200
            if _UNUSUAL_TRAFFIC in html.lower():
                outcome = "captcha"
        except FetchRedirected as e:
            outcome = "captcha" if e.location and _SORRY_PATH in e.location else "error"
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            retry_after = _retry_after_seconds(e.response)
            outcome = "rate_limited" if status in REFUSAL_STATUSES else "error"
        except (requests.RequestException, FetchRefused, OSError) as e:
            outcome = "error"
            logger.warning("Google News Fetcher: request for %s failed: %s", ticker, type(e).__name__)
        finally:
            self._last = self._clock()
        now = _now()
        store.record_request(now, ticker, "quote_page", status, outcome, int((self._last - started) * 1000), retry_after)
        if outcome in ("captcha", "rate_limited"):
            reason = f"HTTP {status}" if outcome == "rate_limited" else "CAPTCHA page"
            raise GoogleRefused(reason, enter_cooldown(reason, retry_after, now))
        return html if outcome == "ok" else None
