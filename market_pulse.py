import time
import logging
from typing import List, Dict, Any, Optional

from config import load_config
from database import get_connection, get_ticker_registry
from db_helpers import resolve_live_price
from market_session_helpers import (
    MARKET_STATUS_PROXY,
    build_registry_exchange_map,
    build_registry_future_tickers,
    is_ticker_quote_settled,
)
from utils import normalize_ticker, ignored_tickers_set
from time_engine import is_trading_session

logger = logging.getLogger(__name__)


# Sourced from market_ticker_registry (single source of truth — see AGENTS.md central-engine
# rule) rather than a hardcoded dict, so the Markets page/Settings UI can add tickers with no
# code change. Two accessors because the two historical uses of the old INDEX_TICKERS dict have
# diverged in meaning now that the registry covers more than just Market Pulse's fixed tiles:
#   - get_index_tickers(): every enabled registry ticker (name lookups, "is this one of our own
#     tracked macro instruments" classification — e.g. the stale-alert exemption, the
#     /stock/{ticker} -> /index/{ticker} redirect).
#   - get_pulse_index_tickers(): only the is_pulse_tile=1 subset, ordered by pulse_sort_order —
#     this is what actually renders as a static Market Pulse tile, preserving today's exact
#     10-ticker set until markets_engine.select_pulse_tickers() adds dynamic-mode selection.
_index_tickers_cache: Optional[Dict[str, str]] = None
_pulse_index_tickers_cache: Optional[Dict[str, str]] = None


def get_index_tickers() -> Dict[str, str]:
    global _index_tickers_cache
    if _index_tickers_cache is None:
        try:
            rows = get_ticker_registry(enabled_only=True)
            _index_tickers_cache = {row["ticker"]: row["display_name"] for row in rows}
        except Exception as e:
            logger.error("[MARKET PULSE] Failed to load ticker registry: %s", e)
            _index_tickers_cache = {}
    return _index_tickers_cache


def get_pulse_index_tickers() -> Dict[str, str]:
    global _pulse_index_tickers_cache
    if _pulse_index_tickers_cache is None:
        try:
            rows = get_ticker_registry(enabled_only=True)
            pulse_rows = sorted((r for r in rows if r["is_pulse_tile"]), key=lambda r: r["pulse_sort_order"])
            _pulse_index_tickers_cache = {row["ticker"]: row["display_name"] for row in pulse_rows}
        except Exception as e:
            logger.error("[MARKET PULSE] Failed to load pulse ticker registry: %s", e)
            _pulse_index_tickers_cache = {}
    return _pulse_index_tickers_cache


def reload_ticker_registry() -> None:
    """Cache-bust after any market_ticker_registry write (registry CRUD, Settings save)."""
    global _index_tickers_cache, _pulse_index_tickers_cache
    _index_tickers_cache = None
    _pulse_index_tickers_cache = None


SPARKLINE_MAX_POINTS = 60


_DISPLAY_STALE_FLOOR_SECONDS = 300


def is_price_fresh(last_updated: float, price: float, refresh_rate: int) -> bool:
    """Display-only staleness check ('should the UI grey this out'), not a data-selection gate
    — see accounts_engine.current_price_map() for the latter, which compares timestamps
    directly instead of using an absolute cutoff. A cache row counts as fresh outside market
    hours as long as it has ever been populated; during market hours it must also be within a
    floor of 5 minutes (or 2x the refresh interval if that's larger) — a floor comfortably
    wider than the ~10-minute background scan that actually keeps the cache warm, so normal
    scan-to-scan gaps and occasional fetch latency don't flip the display to stale every cycle."""
    has_data = last_updated > 0 and price != 0.0
    if not has_data:
        return False
    if not is_trading_session():
        return True
    return (time.time() - last_updated) <= max(refresh_rate * 2, _DISPLAY_STALE_FLOOR_SECONDS)

def tickers_needing_refresh(tickers: List[str], max_age_seconds: int = 300) -> List[str]:
    """Which of the given tickers have a missing or stale market_pulse_cache row. Shared by
    proxy_tickers_needing_refresh() (the 8 exchange-state proxies) and any other caller that
    wants to self-trigger a background refresh for a fixed ticker list — the needs_refresh
    pattern GET /api/market-pulse and the accounts endpoints already use, generalized to an
    arbitrary ticker list so it isn't reimplemented per caller (see AGENTS.md rule 16)."""
    if not tickers:
        return []
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in tickers)
        cursor.execute(
            f"SELECT ticker, last_updated FROM market_pulse_cache WHERE ticker IN ({placeholders})",
            tickers,
        )
        last_updated_map = {row['ticker']: row['last_updated'] for row in cursor.fetchall()}
    except Exception as e:
        logger.error("[MARKET PULSE] Failed to check ticker staleness: %s", e)
        return list(tickers)
    finally:
        if conn:
            conn.close()

    now = time.time()
    return [
        t for t in tickers
        if now - last_updated_map.get(t, 0) > max_age_seconds
    ]


def proxy_tickers_needing_refresh(max_age_seconds: int = 300) -> List[str]:
    """Which of the NYSE/LSE proxy tickers (see MARKET_STATUS_PROXY) have a missing or stale
    market_state row — lets GET /api/system/market-status self-trigger a background refresh.
    Without this, is_exchange_open() would only ever see fresh data when something else (the
    market-sentiment page's JS polling) happens to be fetching these tickers too — a caller that
    only ever polls market-status (e.g. Home Assistant) would keep falling back to the naive
    weekday/hours heuristic forever."""
    return tickers_needing_refresh(list(MARKET_STATUS_PROXY.values()), max_age_seconds)


def registry_tickers_needing_refresh(tickers: List[str], max_age_seconds: int = 300) -> List[str]:
    """Same staleness-by-age check as tickers_needing_refresh(), but additionally gates an
    already-cached ticker on is_ticker_quote_settled() for its own registry exchange. Deliberately
    a separate function rather than a branch inside tickers_needing_refresh(): that function is
    also used by proxy_tickers_needing_refresh() for the exchange-state proxy tickers themselves,
    which must stay ungated on settlement — they're what is_quote_settled() depends on to know an
    exchange is open at all, so gating them on it would be circular. A ticker missing a cached row
    entirely is refreshed unconditionally, same bootstrap exception as tickers_needing_refresh()."""
    if not tickers:
        return []
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in tickers)
        cursor.execute(
            f"SELECT ticker, last_updated FROM market_pulse_cache WHERE ticker IN ({placeholders})",
            tickers,
        )
        last_updated_map = {row['ticker']: row['last_updated'] for row in cursor.fetchall()}
    except Exception as e:
        logger.error("[MARKET PULSE] Failed to check registry ticker staleness: %s", e)
        return list(tickers)
    finally:
        if conn:
            conn.close()

    registry_exchange_map = build_registry_exchange_map()
    registry_future_tickers = build_registry_future_tickers()
    now = time.time()
    stale = []
    for t in tickers:
        if t not in last_updated_map:
            stale.append(t)
            continue
        if now - last_updated_map[t] <= max_age_seconds:
            continue
        if is_ticker_quote_settled(t, registry_exchange_map=registry_exchange_map, registry_future_tickers=registry_future_tickers):
            stale.append(t)
    return stale


def get_intraday_points(ticker: str, max_points: int = SPARKLINE_MAX_POINTS) -> List[List[float]]:
    """Today's-session sparkline points for the Markets page, written by fetch_and_save_pulse.
    Returns [[ts, price], ...] ordered oldest-first; empty when the ticker has never been fetched."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT ts, price FROM market_pulse_sparkline WHERE ticker = ? ORDER BY ts DESC LIMIT ?",
            (ticker, max_points),
        )
        rows = cursor.fetchall()
        return [[row["ts"], row["price"]] for row in reversed(rows)]
    except Exception as e:
        logger.error("[MARKET PULSE] Failed to read sparkline for %s: %s", ticker, e)
        return []
    finally:
        if conn:
            conn.close()


def get_all_cached_pulse() -> Dict[str, Dict[str, Any]]:
    """Returns all pulse data from DB for Jinja template pre-rendering. A ticker's
    market_pulse_cache row can go stuck (no background job keeps it warm outside market hours,
    or a ticker is newly held before its first live fetch), so its price is only trusted while
    it isn't stuck more than resolve_live_price()'s gap behind stock_signals' own last update —
    same protection accounts_engine.current_price_map() already applies to the P&L math, applied
    here to what's actually displayed. A ticker falling back loses its change_pct/change_pts too,
    since those are computed relative to the same stuck snapshot and would misleadingly imply a
    day-over-day move for the fallback price that never happened."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT c.ticker, c.name, c.price, c.change_pts, c.change_pct, c.is_positive, c.last_updated, "
            "c.extended_price, c.extended_change_pts, c.extended_change_pct, c.extended_session, "
            "s.current_price AS fallback_price, s.last_updated AS fallback_last_updated "
            "FROM market_pulse_cache c LEFT JOIN stock_signals s ON s.ticker = c.ticker"
        )
        rows = cursor.fetchall()
    except Exception as e:
        logger.error("[MARKET PULSE] Failed to read pulse cache: %s", e)
        return {}
    finally:
        conn.close()

    config_data = load_config()
    refresh_rate: int = int(config_data.get("UI_PREFERENCES", {}).get("REFRESH_RATE", 60))

    cache: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        is_stale = not is_price_fresh(row['last_updated'], row['price'], refresh_rate)
        price, used_fallback = resolve_live_price(row['price'], row['last_updated'], row['fallback_price'], row['fallback_last_updated'])
        cache[row['ticker']] = {
            "ticker": row['ticker'],
            "name": row['name'],
            "price": price,
            "change_pts": None if used_fallback else row['change_pts'],
            "change_pct": None if used_fallback else row['change_pct'],
            "is_positive": bool(row['is_positive']),
            "is_stale": is_stale,
            "extended_price": row['extended_price'],
            "extended_change_pts": row['extended_change_pts'],
            "extended_change_pct": row['extended_change_pct'],
            "extended_session": row['extended_session'],
        }
    return cache


def get_cached_change_pct(ticker: str) -> Optional[float]:
    """Last-known change_pct for one ticker — persists past market close, so a benchmark whose
    exchange is currently shut still has a meaningful figure instead of nothing at all."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT change_pct FROM market_pulse_cache WHERE ticker = ?", (ticker,))
        row = cursor.fetchone()
        return row['change_pct'] if row and row['change_pct'] is not None else None
    except Exception as e:
        logger.error("[MARKET PULSE] Failed to read cached change_pct for %s: %s", ticker, e)
        return None
    finally:
        if conn:
            conn.close()


def _select_active_pulse_tickers(config_data: dict) -> Dict[str, str]:
    """Static mode: today's is_pulse_tile picked list. Dynamic mode: markets_engine's own
    region-ordering logic, so Market Pulse can mirror what the Markets page currently shows.
    Both modes are capped by MARKET_PULSE_DESKTOP_COUNT (parameterizing the historically
    hardcoded 10-tile default). Deferred import of markets_engine avoids a circular import —
    markets_engine imports market_pulse for get_cached_pulse_from_db/get_intraday_points."""
    ui_prefs = config_data.get("UI_PREFERENCES", {})
    desktop_count = int(ui_prefs.get("MARKET_PULSE_DESKTOP_COUNT", 10))
    if not ui_prefs.get("MARKET_PULSE_DYNAMIC", False):
        return dict(list(get_pulse_index_tickers().items())[:desktop_count])

    try:
        import markets_engine
        mobile_count = int(ui_prefs.get("MARKET_PULSE_MOBILE_COUNT", 8))
        selection = markets_engine.select_pulse_tickers(dynamic=True, desktop_count=desktop_count, mobile_count=mobile_count)
        index_tickers = get_index_tickers()
        return {t: index_tickers.get(t, t) for t in selection["desktop"]}
    except Exception as e:
        logger.error("[MARKET PULSE] Dynamic ticker selection failed, falling back to static: %s", e)
        return dict(list(get_pulse_index_tickers().items())[:desktop_count])


def get_cached_pulse_from_db(asset_tickers: List[str], refresh_rate: int) -> Dict[str, List[Dict[str, Any]]]:
    """Returns cached pulse prices (with staleness flag + latest FinBERT sentiment) split into indexes vs. assets."""
    if asset_tickers is None:
        asset_tickers = []

    asset_tickers = [normalize_ticker(t) for t in asset_tickers]

    config_data = load_config()
    ignored_tickers = ignored_tickers_set(config_data)

    pulse_index_tickers = _select_active_pulse_tickers(config_data)
    registry_rows = get_ticker_registry(enabled_only=True)
    registry_by_ticker = {r["ticker"]: r for r in registry_rows}
    seen: set = set(pulse_index_tickers.keys())
    requested_assets: List[str] = []
    for t in asset_tickers:
        if t not in seen and t not in ignored_tickers:
            seen.add(t)
            requested_assets.append(t)
    all_tickers: List[str] = list(pulse_index_tickers.keys()) + requested_assets
    
    conn = get_connection()
    rows: List[Any] = []
    sentiment_scores: Dict[str, float] = {}
    equity_currency_map: Dict[str, str] = {}
    fallback_price_map: Dict[str, Any] = {}
    try:
        cursor = conn.cursor()

        if all_tickers:
            placeholders = ','.join('?' for _ in all_tickers)

            cursor.execute(
                f"SELECT ticker, name, price, change_pts, change_pct, is_positive, last_updated, "
                f"extended_price, extended_change_pts, extended_change_pct, extended_session "
                f"FROM market_pulse_cache WHERE ticker IN ({placeholders})", all_tickers
            )
            rows = cursor.fetchall()

            query = f"""
                SELECT ticker, sentiment_score
                FROM quant_signals
                WHERE ticker IN ({placeholders})
                AND sentiment_score IS NOT NULL
                AND date = (
                    SELECT MAX(date) FROM quant_signals qs
                    WHERE qs.ticker = quant_signals.ticker
                        AND qs.sentiment_score IS NOT NULL
                )
            """
            cursor.execute(query, all_tickers)
            sentiment_rows = cursor.fetchall()

            for s_row in sentiment_rows:
                sentiment_scores[s_row['ticker']] = s_row['sentiment_score']

        if requested_assets:
            placeholders = ','.join('?' for _ in requested_assets)
            cursor.execute(
                f"SELECT ticker, currency, current_price, last_updated FROM stock_signals WHERE ticker IN ({placeholders})",
                requested_assets
            )
            signals_rows = cursor.fetchall()
            equity_currency_map = {r['ticker']: r['currency'] for r in signals_rows}
            fallback_price_map = {r['ticker']: (r['current_price'], r['last_updated']) for r in signals_rows}
    except Exception as e:
        logger.error("[MARKET PULSE] Failed to read pulse from DB: %s", e)
        return {"indexes": [], "assets": []}
    finally:
        conn.close()

    results: Dict[str, List[Dict[str, Any]]] = {"indexes": [], "assets": []}
    current_time: float = time.time()
    registry_exchange_map = build_registry_exchange_map(registry_rows)
    registry_future_tickers = build_registry_future_tickers(registry_rows)

    db_map: Dict[str, Any] = {row['ticker']: row for row in rows}

    for t in all_tickers:
        registry_row = registry_by_ticker.get(t)
        invert_color = bool(registry_row["invert_color"]) if registry_row else False
        asset_type = registry_row["asset_type"] if registry_row else None
        is_pulse_mobile = bool(registry_row["is_pulse_mobile"]) if registry_row else True
        currency = registry_row["currency"] if registry_row else None

        if t in db_map:
            row = db_map[t]
            age = current_time - row['last_updated']
            has_data = row['last_updated'] > 0 and row['price'] != 0.0
            is_stale: bool = not is_price_fresh(row['last_updated'], row['price'], refresh_rate)
            settled = is_ticker_quote_settled(t, equity_currency_map.get(t, ''), registry_exchange_map, registry_future_tickers)
            needs_refresh: bool = False if not settled else (not has_data or age > int(refresh_rate))
            fallback = fallback_price_map.get(t)
            price, used_fallback = resolve_live_price(row['price'], row['last_updated'], fallback[0] if fallback else None, fallback[1] if fallback else None)
            data_obj: Dict[str, Any] = {
                "ticker": t,
                "name": row['name'],
                "price": price,
                "change_pts": None if used_fallback else row['change_pts'],
                "change_pct": None if used_fallback else row['change_pct'],
                "is_positive": bool(row['is_positive']),
                "is_stale": is_stale,
                "needs_refresh": needs_refresh,
                "sentiment_score": sentiment_scores.get(t, None),
                "invert_color": invert_color,
                "asset_type": asset_type,
                "is_pulse_mobile": is_pulse_mobile,
                "currency": currency,
                "extended_price": row['extended_price'],
                "extended_change_pts": row['extended_change_pts'],
                "extended_change_pct": row['extended_change_pct'],
                "extended_session": row['extended_session'],
            }
        else:
            data_obj = {
                "ticker": t,
                "name": pulse_index_tickers.get(t, t),
                "price": 0.0,
                "change_pts": 0.0,
                "change_pct": 0.0,
                "is_positive": True,
                "is_stale": True,
                "needs_refresh": True,
                "sentiment_score": sentiment_scores.get(t, None),
                "invert_color": invert_color,
                "asset_type": asset_type,
                "is_pulse_mobile": is_pulse_mobile,
                "currency": currency,
                "extended_price": None,
                "extended_change_pts": None,
                "extended_change_pct": None,
                "extended_session": None,
            }

        if t in pulse_index_tickers:
            results["indexes"].append(data_obj)
        else:
            results["assets"].append(data_obj)

    return results
