import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from database import get_connection, get_ticker_registry
from time_engine import EXCHANGE_HOURS, is_exchange_holiday, is_trading_session, market_window_utc, ticker_exchange, ticker_exchange_from_suffix

logger = logging.getLogger(__name__)

# Live Yahoo marketState on these tracked index tickers stands in for exchange-holiday-aware
# open/closed status, since time_engine's weekday+hours heuristic has no holiday calendar.
MARKET_STATUS_PROXY: Dict[str, str] = {
    "NYSE": "^GSPC", "LSE": "^FTSE",
    "XETRA": "^GDAXI", "TSE": "^N225", "HKEX": "^HSI",
    "SSE": "000001.SS", "ASX": "^AXJO", "Euronext": "^FCHI",
}
OPEN_MARKET_STATES = {"REGULAR"}
PRE_MARKET_STATES = {"PRE", "PREPRE"}
POST_MARKET_STATES = {"POST", "POSTPOST"}


def is_exchange_open(exchange: str, include_premarket: bool = False) -> bool:
    """Market-open check, holiday-vetoed first via time_engine.is_exchange_holiday()
    (exchange_calendars — the one canonical holiday source, checked even for the 8 exchanges
    below with a live proxy). Beyond that veto, NYSE/LSE/XETRA/TSE/HKEX/SSE/ASX/Euronext are
    backed by the live Yahoo marketState cached from that exchange's proxy index ticker (see
    MARKET_STATUS_PROXY) — falls back to time_engine's weekday+hours heuristic for any other
    exchange, or if no market_state has been cached yet (e.g. right after a fresh install).
    With include_premarket=True, Yahoo's 'PRE'/'PREPRE' states also count as open — but only for
    exchanges that have a genuine extended-hours session modeled (i.e. a "premarket_open" entry
    in exchange_hours.json, currently NYSE only). Yahoo returns "PRE" for the entire gap since
    the previous close on exchanges with no real extended-hours session of their own (most
    non-US markets), so trusting it there misclassifies a market that has simply closed as
    "about to open" — this is what made the Markets page show Asia as "Pre-Market" long after
    HKEX/SSE/TSE had already finished their session for the day (found 2026-07-09)."""
    if is_exchange_holiday(exchange):
        return False

    proxy = MARKET_STATUS_PROXY.get(exchange)
    if proxy is None:
        return is_trading_session(exchange, include_premarket=include_premarket)

    honor_premarket = include_premarket and "premarket_open" in EXCHANGE_HOURS.get(exchange, {})

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT market_state FROM market_pulse_cache WHERE ticker = ?", (proxy,))
        row = cursor.fetchone()
    except Exception as e:
        logger.error("[MARKET PULSE] Failed to read market_state for %s: %s", proxy, e)
        return is_trading_session(exchange, include_premarket=honor_premarket)
    finally:
        if conn:
            conn.close()

    if row is None or row["market_state"] is None:
        return is_trading_session(exchange, include_premarket=honor_premarket)
    allowed_states = OPEN_MARKET_STATES | PRE_MARKET_STATES if honor_premarket else OPEN_MARKET_STATES
    return row["market_state"] in allowed_states


def get_exchange_session_state(exchange: str) -> str:
    """4-state 'open'/'pre'/'post'/'closed' session status for one exchange. Holiday-vetoed
    first via time_engine.is_exchange_holiday() — same canonical check is_exchange_open() uses —
    so this sibling function can't disagree with it on a holiday. Beyond that veto, built on the
    same cached Yahoo marketState as is_exchange_open() (see MARKET_STATUS_PROXY) rather than a
    second lookup — Yahoo already reports 'POST'/'POSTPOST' for after-hours trading on these 8
    proxy-mapped exchanges, it just wasn't being surfaced past the open/pre/closed collapse
    is_exchange_open() does for its boolean callers. Exchanges with no proxy ticker (most
    non-US/UK/EU/Asia-majors) have no post-market concept in exchange_hours.json either, so they
    fall back to the existing open/closed-only time_engine heuristic — same limitation
    is_exchange_open() already has for those exchanges."""
    if is_exchange_holiday(exchange):
        return "closed"

    proxy = MARKET_STATUS_PROXY.get(exchange)
    if proxy is None:
        if is_trading_session(exchange):
            return "open"
        return "pre" if is_trading_session(exchange, include_premarket=True) else "closed"

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT market_state FROM market_pulse_cache WHERE ticker = ?", (proxy,))
        row = cursor.fetchone()
    except Exception as e:
        logger.error("[MARKET PULSE] Failed to read market_state for %s: %s", proxy, e)
        row = None
    finally:
        if conn:
            conn.close()

    state = row["market_state"] if row and row["market_state"] is not None else None
    if state is None:
        if is_trading_session(exchange):
            return "open"
        return "pre" if is_trading_session(exchange, include_premarket=True) else "closed"

    if state in OPEN_MARKET_STATES:
        return "open"
    if state in PRE_MARKET_STATES:
        honor_premarket = "premarket_open" in EXCHANGE_HOURS.get(exchange, {})
        return "pre" if honor_premarket else "closed"
    if state in POST_MARKET_STATES:
        return "post"
    return "closed"


def is_quote_settled(exchange: str, include_premarket: bool = False) -> bool:
    """True once `exchange` is open (is_exchange_open()) AND enough time has passed since its
    session open for Yahoo's free quote feed to be trustworthy — 0 minutes for most exchanges,
    but LSE's feed runs ~15-20 minutes behind in practice (see 'quote_delay_minutes' in
    exchange_hours.json). Any engine that reacts to a live quote the instant a market opens
    (not just on a slower fixed-interval scan well after open) must gate on this, not just
    is_exchange_open() — see accounts_engine.tickers_needing_refresh() and
    intraday_bottom_engine.run_scan(). `include_premarket` is forwarded to is_exchange_open()
    so a premarket-armed NYSE check isn't wrongly blocked by this gate (NYSE's own delay is 0
    anyway, but the open-time math below still needs a consistent 'is this session live' input)."""
    if not is_exchange_open(exchange, include_premarket=include_premarket):
        return False
    delay_minutes = EXCHANGE_HOURS.get(exchange, {}).get("quote_delay_minutes", 0)
    if delay_minutes <= 0:
        return True
    open_time, _close_time = market_window_utc(exchange, include_premarket=include_premarket)
    now = datetime.now(timezone.utc).time()
    open_minutes = open_time.hour * 60 + open_time.minute
    now_minutes = now.hour * 60 + now.minute
    return (now_minutes - open_minutes) >= delay_minutes


def build_registry_exchange_map(registry_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, str]:
    """ticker/future_ticker -> exchange for every enabled registry row, sharing the exchange
    between a dual-instrument row's spot and future ticker exactly as markets_engine.resolve_tile()
    already does for the spot/future swap itself — a future contract's settlement gate should
    track the same underlying exchange, not a separate (and unmodeled) futures-exchange concept.
    A caller that already fetched get_ticker_registry() for its own use (e.g.
    get_cached_pulse_from_db()'s registry_by_ticker) should pass registry_rows to avoid a repeat
    query."""
    if registry_rows is None:
        registry_rows = get_ticker_registry(enabled_only=True)
    exchange_map: Dict[str, str] = {}
    for row in registry_rows:
        exchange = row.get("exchange")
        if not exchange:
            continue
        exchange_map[row["ticker"]] = exchange
        if row.get("future_ticker"):
            exchange_map[row["future_ticker"]] = exchange
    return exchange_map


def build_registry_future_tickers(registry_rows: Optional[List[Dict[str, Any]]] = None) -> set:
    """Every enabled registry row's future_ticker. Tracked separately from
    build_registry_exchange_map()'s ticker->exchange map because a future contract's settlement
    gate must honor pre-market (it trades near-continuously and exists specifically to represent
    pre-market price movement) while its underlying spot instrument's gate must not — see
    is_ticker_quote_settled(). A caller that already fetched get_ticker_registry() for its own use
    should pass registry_rows to avoid a repeat query."""
    if registry_rows is None:
        registry_rows = get_ticker_registry(enabled_only=True)
    return {row["future_ticker"] for row in registry_rows if row.get("future_ticker")}


_registry_exchange_cache: Optional[Dict[str, str]] = None


def cached_registry_exchange_map() -> Dict[str, str]:
    """Process-wide registry exchange map for per-ticker loops over thousands of tickers; an empty map (failed registry read) is never cached, and market_pulse.reload_ticker_registry() resets it after a registry write."""
    global _registry_exchange_cache
    if _registry_exchange_cache is None:
        exchange_map = build_registry_exchange_map()
        if not exchange_map:
            return exchange_map
        _registry_exchange_cache = exchange_map
    return _registry_exchange_cache


def reset_registry_exchange_cache() -> None:
    global _registry_exchange_cache
    _registry_exchange_cache = None


def resolve_ticker_exchange(
    ticker: str,
    currency: str = "",
    registry_exchange_map: Optional[Dict[str, str]] = None,
    *,
    suffix_only: bool = False,
) -> str:
    """The exchange to gate `ticker`'s quote freshness on. Prefers market_ticker_registry's own
    `exchange` column (indexes/commodities/FX tracked by the Markets page/Market Pulse — the
    authoritative source per AGENTS.md's central-engine rule), falling back to
    time_engine.ticker_exchange(ticker, currency) for ordinary equities that have no registry
    row. suffix_only=True falls back to the ticker suffix alone (plain tickers are NYSE, never
    the operator's HOME_EXCHANGE) for callers that key a ticker's own price history to its
    exchange. Callers refreshing many tickers at once should build registry_exchange_map()
    themselves and pass it in to avoid a repeat query per ticker."""
    if registry_exchange_map is None:
        registry_exchange_map = build_registry_exchange_map()
    exchange = registry_exchange_map.get(ticker)
    if exchange:
        return exchange
    if suffix_only:
        return ticker_exchange_from_suffix(ticker)
    return ticker_exchange(ticker, currency)


def is_ticker_quote_settled(
    ticker: str,
    currency: str = "",
    registry_exchange_map: Optional[Dict[str, str]] = None,
    registry_future_tickers: Optional[set] = None,
) -> bool:
    """is_quote_settled() resolved against `ticker`'s own exchange rather than a caller-supplied
    one — the single canonical per-ticker settlement check, shared by every needs_refresh path
    (accounts_engine.tickers_needing_refresh(), get_cached_pulse_from_db(),
    registry_tickers_needing_refresh()) instead of each reimplementing its own exchange
    resolution. See AGENTS.md rule 16/17. A registry row's future_ticker honors pre-market (see
    build_registry_future_tickers()) so a futures tile isn't stuck requiring its underlying spot
    exchange's regular session — the exact session during which resolve_tile() shows the spot
    ticker instead, which left futures tickers refreshing only once a session, then frozen for the
    rest of the day including the pre-market window they're meant to represent (found 2026-07-13).
    A genuinely active pre/post-market session (Yahoo's own marketState, via
    get_exchange_session_state()) also counts as settled for an ordinary ticker even when it isn't
    a registry future — this is what keeps the Pre-Market/After Hours display (see
    yahoo_engine.get_quote_snapshot()) actually live rather than frozen on last session's cache row
    until the next regular open (found 2026-07-17). This is deliberately narrower than changing
    is_quote_settled() itself, which alert-firing engines (Crash & Moonshot, AI Contagion) also
    call and must keep its existing regular/premarket-only semantics for."""
    if registry_future_tickers is None:
        registry_future_tickers = build_registry_future_tickers()
    honor_premarket = ticker in registry_future_tickers
    exchange = resolve_ticker_exchange(ticker, currency, registry_exchange_map)
    if is_quote_settled(exchange, include_premarket=honor_premarket):
        return True
    return get_exchange_session_state(exchange) in ("pre", "post")
