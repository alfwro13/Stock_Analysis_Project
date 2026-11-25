import logging
import random
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import quote

import requests

from constants import GFETCHER_MIN_DELAY_SECONDS
from outbound_fetch_helpers import FetchRedirected, FetchRefused, fetch_body
from tools.network_engine import network_routing

from . import store

logger = logging.getLogger(__name__)

QUOTE_URL = "https://www.google.com/finance/quote/{symbol}:{exchange}?hl=en"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    # Skips the EU consent page that would otherwise replace the quote page.
    "Cookie": "CONSENT=YES+",
}
MIN_DELAY_SECONDS = GFETCHER_MIN_DELAY_SECONDS
# The gap before each request is drawn from [delay, delay * DELAY_SPREAD], so requests never tick at a fixed rate.
DELAY_SPREAD = 2.0
MAX_PAGE_BYTES = 5 * 1024 * 1024
COOLDOWN_STEPS = (timedelta(hours=1), timedelta(hours=6), timedelta(hours=24))
REFUSAL_STATUSES = (429, 503)
IPV4 = "ipv4"
IPV6 = "ipv6"
_SORRY_PATH = "/sorry/"
_UNUSUAL_TRAFFIC = "unusual traffic from your computer network"


@dataclass(frozen=True)
class Route:
    name: str
    family: int
    source_address: Optional[str] = None


class GoogleRefused(Exception):
    """Every route is cooling down after Google refused it (429/503 or its CAPTCHA page); the run must end."""

    def __init__(self, reason: str, until: Optional[datetime]):
        super().__init__(reason)
        self.reason = reason
        self.until = until


class BudgetSpent(Exception):
    pass


class NoRoute(Exception):
    """Every route failed to connect this run (no refusal, so no cooldown)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def configured_routes() -> list[Route]:
    """IPv4 and/or IPv6 as Settings > Advanced Network routes Yahoo, IPv6 bound to its configured address."""
    routing = network_routing()
    routes = []
    if routing.use_ipv4:
        routes.append(Route(IPV4, socket.AF_INET))
    if routing.use_ipv6:
        routes.append(Route(IPV6, socket.AF_INET6, routing.ipv6_address))
    return routes or [Route(IPV4, socket.AF_INET)]


def _retry_after_seconds(response) -> Optional[int]:
    value = (response.headers.get("Retry-After") or "").strip() if response is not None else ""
    return int(value) if value.isdigit() else None


def enter_cooldown(interface: str, reason: str, retry_after_s: Optional[int], now: datetime) -> datetime:
    refusals = store.get_state(interface).consecutive_refusals + 1
    wait = COOLDOWN_STEPS[min(refusals, len(COOLDOWN_STEPS)) - 1]
    if retry_after_s:
        wait = max(wait, timedelta(seconds=retry_after_s))
    until = (now + wait).replace(microsecond=0)
    store.set_cooldown(interface, until, reason, refusals)
    logger.warning("Google News Fetcher: Google refused a request over %s (%s); %s cools down until %s.",
                   interface, reason, interface, store.stamp(until))
    return until


class GoogleClient:
    """One instance per run, used only on the job thread (its sleeps never sit under a lock a web request needs); alternates the usable routes request by request."""

    def __init__(self, delay_seconds: float, max_requests_per_day: int, routes: Optional[list[Route]] = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        now = _now()
        self.delay = max(float(delay_seconds), MIN_DELAY_SECONDS)
        self.remaining = max(0, max_requests_per_day - store.google_requests_since(now - timedelta(days=1)))
        self.requests_made = 0
        self._sleep = sleep
        self._clock = clock
        self._last: Optional[float] = None
        self._turn = 0
        states = store.get_states()
        self._refusals_before = {name: state.consecutive_refusals for name, state in states.items()}
        self.cooling: dict = {}
        self.active: list[Route] = []
        for route in routes if routes is not None else configured_routes():
            state = states.get(route.name)
            if state and state.cooldown_until and state.cooldown_until > now:
                self.cooling[route.name] = (state.cooldown_reason, state.cooldown_until)
            else:
                self.active.append(route)
        self.succeeded: set = set()
        self.dropped: set = set()
        self.refused_now: list = []

    def refusal(self) -> GoogleRefused:
        reason = "; ".join(f"{name}: {why}" for name, (why, _) in sorted(self.cooling.items()))
        until = min((u for _, u in self.cooling.values()), default=None)
        return GoogleRefused(reason or "no usable route", until)

    def _wait(self) -> None:
        if self._last is None:
            return
        pause = self._last + random.uniform(self.delay, self.delay * DELAY_SPREAD) - self._clock()
        if pause > 0:
            self._sleep(pause)

    def get_quote_page(self, ticker: str, symbol: str, exchange: str) -> Optional[str]:
        """The quote page HTML, or None when this request failed (the run goes on over the routes still usable)."""
        if not self.active:
            raise self.refusal() if self.cooling else NoRoute()
        if self.remaining <= 0:
            raise BudgetSpent()
        route = self.active[self._turn % len(self.active)]
        self._turn += 1
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
            html = fetch_body(url, HEADERS, max_bytes=MAX_PAGE_BYTES, family=route.family, source_address=route.source_address)
            status = 200
            if _UNUSUAL_TRAFFIC in html.lower():
                outcome = "captcha"
        except FetchRedirected as e:
            outcome = "captcha" if e.location and _SORRY_PATH in e.location else "error"
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            retry_after = _retry_after_seconds(e.response)
            outcome = "rate_limited" if status in REFUSAL_STATUSES else "error"
        except (requests.ConnectionError, requests.Timeout, OSError) as e:
            outcome = "unreachable"
            logger.warning("Google News Fetcher: %s route unreachable for %s (%s); not used again this run.",
                           route.name, ticker, type(e).__name__)
        except (requests.RequestException, FetchRefused) as e:
            outcome = "error"
            logger.warning("Google News Fetcher: request for %s over %s failed: %s", ticker, route.name, type(e).__name__)
        finally:
            self._last = self._clock()
        now = _now()
        store.record_request(now, ticker, route.name, "quote_page", status, outcome, int((self._last - started) * 1000), retry_after)
        if outcome in ("captcha", "rate_limited"):
            reason = f"HTTP {status}" if outcome == "rate_limited" else "CAPTCHA page"
            until = enter_cooldown(route.name, reason, retry_after, now)
            self.cooling[route.name] = (reason, until)
            self.refused_now.append({"route": route.name, "reason": reason, "until": until})
            self.active.remove(route)
            if not self.active:
                raise self.refusal()
            return None
        if outcome == "unreachable":
            self.dropped.add(route.name)
            self.active.remove(route)
            if not self.active and self.cooling:
                raise self.refusal()
            return None
        if outcome == "ok":
            self.succeeded.add(route.name)
            return html
        return None

    def finish(self) -> list[str]:
        """A route that answered without being refused this run starts its next refusal from the first cooldown step again; returns the routes that had been refused before (recovered)."""
        now = _now()
        recovered = []
        for name in sorted(self.succeeded - set(self.cooling)):
            store.mark_success(name, now)
            if self._refusals_before.get(name):
                recovered.append(name)
        return recovered
