import logging
import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, List, Optional

import pandas as pd

import notification_engine
from config import HISTORICAL_DIR
from database import get_connection, get_mutual_fund_tickers
from db_helpers import upsert_fx_quote
from gilt_engine import GiltDataService
from market_pulse import SPARKLINE_MAX_POINTS, get_index_tickers
from market_session_helpers import (
    POST_MARKET_STATES,
    PRE_MARKET_STATES,
    build_registry_exchange_map,
    is_exchange_open,
    resolve_ticker_exchange,
)
from time_engine import is_trading_session, ticker_exchange
from utils import is_daily_bar_still_forming
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

_STALE_ALERT_THRESHOLD_SECONDS = 1800

# Non-blocking lock prevents duplicate concurrent fetches without a check-then-set race.
_FETCH_LOCK = threading.Lock()


def upsert_live_price(ticker: str, name: str, price: Any, prev_close: Any, conn: Any = None) -> None:
    """Shares a price another engine already fetched for its own use instead of it being discarded; keeps an existing name if one is already on record."""
    if price is None or not prev_close:
        return
    if ticker.endswith("=X") and (not math.isfinite(float(price)) or price <= 0):
        return
    change_pts = price - prev_close
    change_pct = (change_pts / prev_close) * 100.0
    owns_conn = conn is None
    try:
        if owns_conn:
            conn = get_connection()
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
        if ticker.endswith("=X"):
            upsert_fx_quote(ticker, float(price), time.time(), conn=conn)
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

        registry_exchange_map = build_registry_exchange_map()
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

                # Session (regular/pre/post) always comes from Yahoo's own marketState, never guessed from the clock.
                snapshot = yahoo_engine.get_quote_snapshot(ticker)
                market_state: Optional[str] = None
                extended_price: Optional[float] = None
                extended_change_pts: Optional[float] = None
                extended_change_pct: Optional[float] = None
                extended_session: Optional[str] = None
                skip_price_update = False

                if snapshot and snapshot.get('regular_price') is not None:
                    current_price = float(snapshot['regular_price'])
                    market_state = snapshot.get('market_state')

                    if snapshot.get('regular_change') is not None and snapshot.get('regular_change_pct') is not None:
                        change_pts = float(snapshot['regular_change'])
                        change_pct = float(snapshot['regular_change_pct'])
                    else:
                        prev_close = snapshot.get('regular_previous_close')
                        prev_close = float(prev_close) if prev_close is not None else float(t_daily['Close'].iloc[-1])
                        change_pts = current_price - prev_close
                        change_pct = (change_pts / prev_close) * 100.0 if prev_close else 0.0

                    if market_state in PRE_MARKET_STATES and snapshot.get('pre_market_price') is not None:
                        extended_price = float(snapshot['pre_market_price'])
                        extended_change_pts = float(snapshot['pre_market_change']) if snapshot.get('pre_market_change') is not None else None
                        extended_change_pct = float(snapshot['pre_market_change_pct']) if snapshot.get('pre_market_change_pct') is not None else None
                        extended_session = 'pre'
                    elif market_state in POST_MARKET_STATES and snapshot.get('post_market_price') is not None:
                        extended_price = float(snapshot['post_market_price'])
                        extended_change_pts = float(snapshot['post_market_change']) if snapshot.get('post_market_change') is not None else None
                        extended_change_pct = float(snapshot['post_market_change_pct']) if snapshot.get('post_market_change_pct') is not None else None
                        extended_session = 'post'
                elif t_live.empty:
                    # Daily-priced instrument (e.g. mutual fund) — use most recent daily close.
                    # No live intraday feed exists for these, so there's no prepost tick to leak.
                    current_price = float(t_daily['Close'].iloc[-1])
                    prev_close = float(t_daily['Close'].iloc[-2]) if len(t_daily) >= 2 else current_price
                    change_pts = current_price - prev_close
                    change_pct = (change_pts / prev_close) * 100.0 if not pd.isna(prev_close) and prev_close != 0 else 0.0
                else:
                    # Quote snapshot failed but a live intraday feed exists — its last bar was fetched
                    # with prepost=True, so it's only safe to treat as "the price" while the exchange
                    # is confirmed in regular session right now. Outside regular hours that same feed
                    # can only be a pre/post-market tick, which must never land in the settled
                    # price/change_pts/change_pct columns (see AGENTS.md "never mix session data").
                    exchange = resolve_ticker_exchange(ticker, registry_exchange_map=registry_exchange_map)
                    if not is_exchange_open(exchange):
                        logger.warning(
                            "Skipping price/change update for %s: quote snapshot unavailable and %s "
                            "isn't in regular session — the only available tick could be pre/post-market.",
                            ticker, exchange,
                        )
                        skip_price_update = True
                    else:
                        current_price = float(t_live['Close'].iloc[-1])
                        if is_daily_bar_still_forming(t_daily.index[-1].date(), t_live.index[-1].date(), True) and len(t_daily) >= 2:
                            prev_close = float(t_daily['Close'].iloc[-2])
                            prev_close_date = t_daily.index[-2].date()
                        else:
                            prev_close = float(t_daily['Close'].iloc[-1])
                            prev_close_date = t_daily.index[-1].date()

                        # Yahoo's daily chart-history endpoint can silently drop rows out of the
                        # middle of the requested window for some symbols (seen on ^KS200: period="5d"
                        # returned only 2 rows with a 6-day gap between them, even once the endpoint
                        # "caught up" and its last row again matched today) — checking only the
                        # feed's last date isn't enough once it re-includes today, since the row
                        # actually used as prev_close can still be several sessions further back.
                        # When that row is implausibly old next to the live feed, prefer the
                        # quoteSummary endpoint's own previousClose (a separate Yahoo endpoint, not
                        # similarly affected) instead.
                        if (t_live.index[-1].date() - prev_close_date).days > 3:
                            info = yahoo_engine.get_ticker_info(ticker)
                            info_prev_close = info.get("regularMarketPreviousClose") if info else None
                            if info_prev_close:
                                prev_close = float(info_prev_close)

                        change_pts = current_price - prev_close
                        change_pct = (change_pts / prev_close) * 100.0 if not pd.isna(prev_close) and prev_close != 0 else 0.0

                if not skip_price_update:
                    if ticker.endswith("=X") and (not math.isfinite(float(current_price)) or current_price <= 0):
                        continue
                    if abs(change_pct) > 50.0:
                        logger.warning("Skipping %s: implausible daily change %.1f%% (possible split mismatch)", ticker, change_pct)
                        continue

                    name: str = index_tickers.get(ticker, ticker)
                    is_positive: int = int(change_pts >= 0)

                    cursor.execute('''
                        INSERT INTO market_pulse_cache
                        (ticker, name, price, change_pts, change_pct, is_positive, last_updated,
                         market_state, extended_price, extended_change_pts, extended_change_pct, extended_session)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(ticker) DO UPDATE SET
                            name = excluded.name,
                            price = excluded.price,
                            change_pts = excluded.change_pts,
                            change_pct = excluded.change_pct,
                            is_positive = excluded.is_positive,
                            last_updated = excluded.last_updated,
                            market_state = COALESCE(excluded.market_state, market_pulse_cache.market_state),
                            extended_price = excluded.extended_price,
                            extended_change_pts = excluded.extended_change_pts,
                            extended_change_pct = excluded.extended_change_pct,
                            extended_session = excluded.extended_session
                    ''', (ticker, name, current_price, change_pts, change_pct, is_positive, current_time,
                          market_state, extended_price, extended_change_pts, extended_change_pct, extended_session))
                    if ticker.endswith("=X"):
                        upsert_fx_quote(ticker, float(current_price), time.time(), conn=conn)

                # Full replace, not append — the mini sparkline is inherently "today's session".
                # Skipped when t_live is empty (market closed) so the last session's line persists
                # instead of being wiped, per the Markets page's "flat/last-known when closed" spec.
                if not t_live.empty:
                    try:
                        cursor.execute("DELETE FROM market_pulse_sparkline WHERE ticker = ?", (ticker,))
                        sparkline_series = t_live['Close'].dropna()
                        if len(sparkline_series) > SPARKLINE_MAX_POINTS:
                            step = len(sparkline_series) / SPARKLINE_MAX_POINTS
                            sparkline_series = sparkline_series.iloc[[int(i * step) for i in range(SPARKLINE_MAX_POINTS)]]
                        cursor.executemany(
                            "INSERT INTO market_pulse_sparkline (ticker, ts, price) VALUES (?, ?, ?)",
                            [(ticker, idx.timestamp(), float(val)) for idx, val in sparkline_series.items()],
                        )
                    except Exception as e:
                        logger.error("[MARKET PULSE] Failed to write sparkline for %s: %s", ticker, e)

            except Exception as e:
                logger.error("[MARKET PULSE BACKGROUND] Error processing %s: %s", ticker, e)
                
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
                            logger.info("Live FT scrape returned None. Falling back to Parquet value: %s", live_gilt_yield)
                    except Exception as ex:
                        logger.error("Failed to read Parquet fallback for market pulse: %s", ex)
                
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
                logger.error("[MARKET PULSE BACKGROUND] FT Gilt pipeline execution failed: %s", ex)
                
        conn.commit()
    except Exception as e:
        logger.error("[MARKET PULSE BACKGROUND] Batch download failed: %s", e)
    finally:
        if conn:
            conn.close()
        _FETCH_LOCK.release()
