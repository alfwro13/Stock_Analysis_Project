import time
import logging
import threading
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import pandas as pd

import notification_engine
from config import load_config, HISTORICAL_DIR
from database import get_connection, get_mutual_fund_tickers, get_ticker_registry
from utils import normalize_ticker, is_daily_bar_still_forming
from gilt_engine import GiltDataService
from yahoo_engine import yahoo_engine
from time_engine import is_trading_session, ticker_exchange

logger = logging.getLogger(__name__)

_STALE_ALERT_THRESHOLD_SECONDS = 1800

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


# Non-blocking lock prevents duplicate concurrent fetches without a check-then-set race.
_FETCH_LOCK = threading.Lock()

# Live Yahoo marketState on these tracked index tickers stands in for exchange-holiday-aware
# open/closed status, since time_engine's weekday+hours heuristic has no holiday calendar.
_MARKET_STATUS_PROXY: Dict[str, str] = {
    "NYSE": "^GSPC", "LSE": "^FTSE",
    "XETRA": "^GDAXI", "TSE": "^N225", "HKEX": "^HSI",
    "SSE": "000001.SS", "ASX": "^AXJO", "Euronext": "^FCHI",
}
_OPEN_MARKET_STATES = {"REGULAR"}
_PRE_MARKET_STATES = {"PRE", "PREPRE"}
_SPARKLINE_MAX_POINTS = 60


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


def is_exchange_open(exchange: str, include_premarket: bool = False) -> bool:
    """Exchange-holiday-aware market-open check for NYSE/LSE, backed by the live Yahoo
    marketState cached from that exchange's proxy index ticker (see _MARKET_STATUS_PROXY) —
    falls back to time_engine's weekday+hours heuristic for any other exchange, or if no
    market_state has been cached yet (e.g. right after a fresh install). With
    include_premarket=True, Yahoo's 'PRE'/'PREPRE' states also count as open, matching the
    premarket window time_engine.market_window_utc() grants via its own include_premarket flag."""
    proxy = _MARKET_STATUS_PROXY.get(exchange)
    if proxy is None:
        return is_trading_session(exchange, include_premarket=include_premarket)

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT market_state FROM market_pulse_cache WHERE ticker = ?", (proxy,))
        row = cursor.fetchone()
    except Exception as e:
        logger.error("[MARKET PULSE] Failed to read market_state for %s: %s", proxy, e)
        return is_trading_session(exchange, include_premarket=include_premarket)
    finally:
        if conn:
            conn.close()

    if row is None or row["market_state"] is None:
        return is_trading_session(exchange, include_premarket=include_premarket)
    allowed_states = _OPEN_MARKET_STATES | _PRE_MARKET_STATES if include_premarket else _OPEN_MARKET_STATES
    return row["market_state"] in allowed_states


def proxy_tickers_needing_refresh(max_age_seconds: int = 300) -> List[str]:
    """Which of the NYSE/LSE proxy tickers (see _MARKET_STATUS_PROXY) have a missing or stale
    market_state row — lets GET /api/system/market-status self-trigger a background refresh,
    the same needs_refresh pattern GET /api/market-pulse and the accounts endpoints already use.
    Without this, is_exchange_open() would only ever see fresh data when something else (the
    market-sentiment page's JS polling) happens to be fetching these tickers too — a caller that
    only ever polls market-status (e.g. Home Assistant) would keep falling back to the naive
    weekday/hours heuristic forever."""
    proxies = list(_MARKET_STATUS_PROXY.values())
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in proxies)
        cursor.execute(
            f"SELECT ticker, last_updated FROM market_pulse_cache WHERE ticker IN ({placeholders})",
            proxies,
        )
        last_updated_map = {row['ticker']: row['last_updated'] for row in cursor.fetchall()}
    except Exception as e:
        logger.error("[MARKET PULSE] Failed to check proxy ticker staleness: %s", e)
        return proxies
    finally:
        if conn:
            conn.close()

    now = time.time()
    return [
        t for t in proxies
        if now - last_updated_map.get(t, 0) > max_age_seconds
    ]


def get_intraday_points(ticker: str, max_points: int = _SPARKLINE_MAX_POINTS) -> List[List[float]]:
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
    """Returns all pulse data from DB for Jinja template pre-rendering."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT ticker, name, price, change_pts, change_pct, is_positive, last_updated FROM market_pulse_cache")
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
        cache[row['ticker']] = {
            "ticker": row['ticker'],
            "name": row['name'],
            "price": row['price'],
            "change_pts": row['change_pts'],
            "change_pct": row['change_pct'],
            "is_positive": bool(row['is_positive']),
            "is_stale": is_stale
        }
    return cache


def _select_active_pulse_tickers(config_data: dict) -> Dict[str, str]:
    """Static mode: today's is_pulse_tile picked list. Dynamic mode: markets_engine's own
    region-ordering logic, so Market Pulse can mirror what the Markets page currently shows.
    Both modes are capped by MARKET_PULSE_DESKTOP_COUNT (parameterizing the historically
    hardcoded 10-tile default). Deferred import of markets_engine avoids a circular import —
    markets_engine imports market_pulse for is_exchange_open/get_cached_pulse_from_db."""
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
    ignored_tickers = {normalize_ticker(t) for t in config_data.get("IGNORED_TICKERS", [])}

    pulse_index_tickers = _select_active_pulse_tickers(config_data)
    registry_by_ticker = {r["ticker"]: r for r in get_ticker_registry(enabled_only=True)}
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
    try:
        cursor = conn.cursor()

        if all_tickers:
            placeholders = ','.join('?' for _ in all_tickers)

            cursor.execute(f"SELECT ticker, name, price, change_pts, change_pct, is_positive, last_updated FROM market_pulse_cache WHERE ticker IN ({placeholders})", all_tickers)
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
    except Exception as e:
        logger.error("[MARKET PULSE] Failed to read pulse from DB: %s", e)
        return {"indexes": [], "assets": []}
    finally:
        conn.close()

    results: Dict[str, List[Dict[str, Any]]] = {"indexes": [], "assets": []}
    current_time: float = time.time()
    trading_now: bool = is_trading_session()

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
            needs_refresh: bool = False if not trading_now else (not has_data or age > int(refresh_rate))
            data_obj: Dict[str, Any] = {
                "ticker": t,
                "name": row['name'],
                "price": row['price'],
                "change_pts": row['change_pts'],
                "change_pct": row['change_pct'],
                "is_positive": bool(row['is_positive']),
                "is_stale": is_stale,
                "needs_refresh": needs_refresh,
                "sentiment_score": sentiment_scores.get(t, None),
                "invert_color": invert_color,
                "asset_type": asset_type,
                "is_pulse_mobile": is_pulse_mobile,
                "currency": currency,
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
            }

        if t in pulse_index_tickers:
            results["indexes"].append(data_obj)
        else:
            results["assets"].append(data_obj)

    return results


def upsert_live_price(ticker: str, name: str, price: Any, prev_close: Any, conn: Any = None) -> None:
    """Shares a price another engine already fetched for its own use instead of it being discarded; keeps an existing name if one is already on record."""
    if price is None or not prev_close:
        return
    change_pts = price - prev_close
    change_pct = (change_pts / prev_close) * 100.0
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                name = COALESCE(market_pulse_cache.name, excluded.name),
                price = excluded.price,
                change_pts = excluded.change_pts,
                change_pct = excluded.change_pct,
                is_positive = excluded.is_positive,
                last_updated = excluded.last_updated
        ''', (ticker, name, price, change_pts, change_pct, int(change_pts >= 0), time.time()))
        conn.commit()
    except Exception as e:
        logger.error("[MARKET PULSE] Failed to upsert live price for %s: %s", ticker, e)
    finally:
        if owns_conn and conn:
            conn.close()


def _maybe_alert_stale_ticker(ticker: str, prior_last_updated: float, now: float, conn: Any) -> None:
    """Fires a once-per-day notification when a held ticker's fetch has been failing for a
    while during its own market hours — otherwise a persistently-failing ticker (e.g. a genuine
    Yahoo Finance data gap) just sits silently stale forever with only a log line no one sees.
    Checked against the cache row's age *before* this call's own fetch attempt, so a ticker that
    has simply never been fetched yet (age 0) doesn't false-positive on its very first try."""
    if ticker in get_index_tickers():
        return
    if prior_last_updated <= 0 or (now - prior_last_updated) <= _STALE_ALERT_THRESHOLD_SECONDS:
        return
    exchange = ticker_exchange(ticker)
    if not is_trading_session(exchange):
        return

    today = datetime.now(timezone.utc).date().isoformat()
    cursor = conn.cursor()
    cursor.execute("SELECT state_date FROM alert_state WHERE engine = 'stale_price' AND ticker = ?", (ticker,))
    row = cursor.fetchone()
    if row and row["state_date"] == today:
        return

    cursor.execute(
        """INSERT INTO alert_state (engine, ticker, last_fired_utc, state_date)
           VALUES ('stale_price', ?, ?, ?)
           ON CONFLICT(engine, ticker) DO UPDATE SET last_fired_utc = excluded.last_fired_utc, state_date = excluded.state_date""",
        (ticker, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), today),
    )
    age_minutes = round((now - prior_last_updated) / 60)
    notification_engine.notify(
        "stale_price_alert", "Warning",
        f"{ticker}'s live price hasn't updated in {age_minutes} minutes despite {exchange} being open — the data fetch may be failing for this ticker.",
        level="warning", conn=conn,
    )


def fetch_and_save_pulse(tickers_to_fetch: List[str]) -> None:
    """Fetches live ticks from Yahoo Finance and saves to DB; UK10YG is sourced exclusively from FT.com."""
    if not _FETCH_LOCK.acquire(blocking=False):
        return

    conn = None
    try:
        index_tickers = get_index_tickers()
        handle_gilt: bool = False
        if "UK10YG" in tickers_to_fetch:
            handle_gilt = True
            tickers_to_fetch = [t for t in tickers_to_fetch if t != "UK10YG"]
            
        daily_dfs: dict = {}
        live_dfs: dict = {}

        if tickers_to_fetch:
            daily_dfs = yahoo_engine.get_price_history(tickers_to_fetch, period="5d", interval="1d")
            mutual_funds = get_mutual_fund_tickers(tickers_to_fetch)
            intraday_targets = [t for t in tickers_to_fetch if t not in mutual_funds]
            if intraday_targets:
                live_dfs = yahoo_engine.get_intraday(intraday_targets, period="2d", interval="2m", prepost=True)
                
        conn = get_connection()
        cursor = conn.cursor()
        current_time: float = time.time()

        # Pre-fetch existing cache rows for all tickers in one query to avoid N+1 lookups
        existing_cache: dict = {}
        existing_last_updated: dict = {}
        if tickers_to_fetch:
            placeholders = ','.join('?' for _ in tickers_to_fetch)
            cursor.execute(
                f"SELECT ticker, price, last_updated FROM market_pulse_cache WHERE ticker IN ({placeholders})",
                tickers_to_fetch,
            )
            for row in cursor.fetchall():
                existing_cache[row['ticker']] = row['price']
                existing_last_updated[row['ticker']] = row['last_updated']

        for ticker in tickers_to_fetch:
            try:
                t_daily: pd.DataFrame = daily_dfs.get(ticker, pd.DataFrame())
                t_live: pd.DataFrame = live_dfs.get(ticker, pd.DataFrame())

                if not t_daily.empty:
                    t_daily = t_daily.dropna(subset=['Close'])
                if not t_live.empty:
                    t_live = t_live.dropna(subset=['Close'])

                if t_daily.empty and ticker not in index_tickers:
                    fb = yahoo_engine.get_single_ticker_history(ticker, period="5d")
                    if fb is not None and not fb.empty:
                        fb = fb.dropna(subset=['Close'])
                        if not fb.empty:
                            t_daily = fb

                if t_daily.empty:
                    # No daily data at all — transient outage or genuinely invalid ticker.
                    _maybe_alert_stale_ticker(ticker, existing_last_updated.get(ticker, 0), current_time, conn)
                    price_in_cache = existing_cache.get(ticker)
                    in_cache = ticker in existing_cache
                    if in_cache and price_in_cache:
                        cursor.execute(
                            "UPDATE market_pulse_cache SET last_updated = ? WHERE ticker = ?",
                            (current_time, ticker)
                        )
                    elif in_cache:
                        cursor.execute(
                            "UPDATE market_pulse_cache SET last_updated = 0 WHERE ticker = ?",
                            (ticker,)
                        )
                    else:
                        name = index_tickers.get(ticker, ticker)
                        cursor.execute(
                            "INSERT INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (ticker, name, 0.0, 0.0, 0.0, 1, 0)
                        )
                    continue

                if t_live.empty:
                    # Daily-priced instrument (e.g. mutual fund) — use most recent daily close.
                    current_price = float(t_daily['Close'].iloc[-1])
                    prev_close = float(t_daily['Close'].iloc[-2]) if len(t_daily) >= 2 else current_price
                else:
                    current_price = float(t_live['Close'].iloc[-1])
                    if is_daily_bar_still_forming(t_daily.index[-1].date(), t_live.index[-1].date()) and len(t_daily) >= 2:
                        prev_close = float(t_daily['Close'].iloc[-2])
                    else:
                        prev_close = float(t_daily['Close'].iloc[-1])
                    
                change_pts: float = current_price - prev_close
                change_pct: float = (change_pts / prev_close) * 100.0 if not pd.isna(prev_close) and prev_close != 0 else 0.0

                if abs(change_pct) > 50.0:
                    logger.warning("Skipping %s: implausible daily change %.1f%% (possible split mismatch)", ticker, change_pct)
                    continue

                name: str = index_tickers.get(ticker, ticker)
                is_positive: int = int(change_pts >= 0)

                cursor.execute('''
                    INSERT INTO market_pulse_cache
                    (ticker, name, price, change_pts, change_pct, is_positive, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticker) DO UPDATE SET
                        name = excluded.name,
                        price = excluded.price,
                        change_pts = excluded.change_pts,
                        change_pct = excluded.change_pct,
                        is_positive = excluded.is_positive,
                        last_updated = excluded.last_updated
                ''', (ticker, name, current_price, change_pts, change_pct, is_positive, current_time))

                # Full replace, not append — the mini sparkline is inherently "today's session".
                # Skipped when t_live is empty (market closed) so the last session's line persists
                # instead of being wiped, per the Markets page's "flat/last-known when closed" spec.
                if not t_live.empty:
                    try:
                        cursor.execute("DELETE FROM market_pulse_sparkline WHERE ticker = ?", (ticker,))
                        sparkline_series = t_live['Close'].dropna()
                        if len(sparkline_series) > _SPARKLINE_MAX_POINTS:
                            step = len(sparkline_series) / _SPARKLINE_MAX_POINTS
                            sparkline_series = sparkline_series.iloc[[int(i * step) for i in range(_SPARKLINE_MAX_POINTS)]]
                        cursor.executemany(
                            "INSERT INTO market_pulse_sparkline (ticker, ts, price) VALUES (?, ?, ?)",
                            [(ticker, idx.timestamp(), float(val)) for idx, val in sparkline_series.items()],
                        )
                    except Exception as e:
                        logger.error("[MARKET PULSE] Failed to write sparkline for %s: %s", ticker, e)

                if ticker in _MARKET_STATUS_PROXY.values():
                    try:
                        state = yahoo_engine.get_market_state(ticker)
                        if state:
                            cursor.execute(
                                "UPDATE market_pulse_cache SET market_state = ? WHERE ticker = ?",
                                (state, ticker),
                            )
                    except Exception as e:
                        logger.error("[MARKET PULSE] Failed to fetch market_state for %s: %s", ticker, e)

            except Exception as e:
                logger.error(f"[MARKET PULSE BACKGROUND] Error processing {ticker}: {e}")
                
        if handle_gilt:
            try:
                gilt_service = GiltDataService()
                live_gilt_yield = gilt_service.fetch_live_ft_yield()
                parquet_path = HISTORICAL_DIR / "UK_GILT_BASELINE.parquet"
                
                if live_gilt_yield is None and parquet_path.exists():
                    try:
                        df_gilt_hist = pd.read_parquet(parquet_path)
                        if not df_gilt_hist.empty:
                            live_gilt_yield = float(df_gilt_hist['Close'].iloc[-1])
                            logger.info(f"Live FT scrape returned None. Falling back to Parquet value: {live_gilt_yield}")
                    except Exception as ex:
                        logger.error(f"Failed to read Parquet fallback for market pulse: {ex}")
                
                if live_gilt_yield is not None:
                    gilt_prev_close: float = live_gilt_yield
                    
                    if parquet_path.exists():
                        try:
                            df_gilt_hist = pd.read_parquet(parquet_path)
                            if len(df_gilt_hist) >= 2:
                                gilt_prev_close = float(df_gilt_hist['Close'].iloc[-2])
                            elif len(df_gilt_hist) == 1:
                                gilt_prev_close = float(df_gilt_hist['Close'].iloc[-1])
                        except Exception:
                            logger.debug("Could not parse gilt history close price, using default prev_close")

                    gilt_change_pts: float = live_gilt_yield - gilt_prev_close
                    gilt_change_pct: float = (gilt_change_pts / gilt_prev_close) * 100.0 if gilt_prev_close != 0.0 else 0.0
                    
                    gilt_name: str = index_tickers.get("UK10YG", "UK 10Y Gilt")
                    gilt_is_positive: int = int(gilt_change_pts >= 0)
                    
                    cursor.execute('''
                        INSERT OR REPLACE INTO market_pulse_cache 
                        (ticker, name, price, change_pts, change_pct, is_positive, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', ("UK10YG", gilt_name, live_gilt_yield, gilt_change_pts, gilt_change_pct, gilt_is_positive, current_time))
                else:
                    cursor.execute("SELECT price FROM market_pulse_cache WHERE ticker = 'UK10YG'")
                    existing_gilt = cursor.fetchone()
                    if existing_gilt is not None and existing_gilt['price']:
                        cursor.execute(
                            "UPDATE market_pulse_cache SET last_updated = ? WHERE ticker = 'UK10YG'",
                            (current_time,)
                        )
                    else:
                        cursor.execute("UPDATE market_pulse_cache SET last_updated = 0 WHERE ticker = 'UK10YG'")
            except Exception as ex:
                logger.error(f"[MARKET PULSE BACKGROUND] FT Gilt pipeline execution failed: {ex}")
                
        conn.commit()
    except Exception as e:
        logger.error(f"[MARKET PULSE BACKGROUND] Batch download failed: {e}")
    finally:
        if conn:
            conn.close()
        _FETCH_LOCK.release()