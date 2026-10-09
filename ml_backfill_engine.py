import logging
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import pandas as pd

from data_engine import DataEngine, load_or_fetch_daily_history
from yahoo_engine import yahoo_engine
from indicators import QUANT_HISTORY_COLUMNS, compute_quant_indicator_frame
from database import get_connection, log_notification

logger = logging.getLogger(__name__)

# GUI name: "Historical Data Backfill & Sync". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.


def download_spy_benchmark() -> Optional[pd.DataFrame]:
    """
    Loads SPY OHLCV data for relative strength calculations.
    Called once before the main ticker loop. Returns None on failure.
    Shared with quant_engine.py's daily scan — the canonical source for rel_strength_5d/20d.
    """
    try:
        spy = load_or_fetch_daily_history("SPY")
        if spy is None:
            spy = pd.DataFrame()
        if spy.empty:
            logger.warning("SPY download returned empty. Relative strength will be skipped.")
            return None
        spy = spy[['Close']].copy()
        spy['spy_ret_5d']  = spy['Close'].pct_change(5)
        spy['spy_ret_20d'] = spy['Close'].pct_change(20)
        logger.info(
            'SPY benchmark downloaded: %s rows (%s to %s)', len(spy), spy.index[0].date(), spy.index[-1].date()
        )
        return spy
    except Exception as e:
        logger.warning('SPY download failed: %s. Relative strength will be skipped.', e)
        return None


def get_target_tickers() -> List[str]:
    """
    Combines user portfolio/watchlist tickers with a randomly sampled
    cross-section of the market universe to prevent Mega-Cap bias.
    """
    logger.info("Extracting user portfolio and watchlist tickers...")
    try:
        engine       = DataEngine()
        user_tickers = engine.get_all_tickers()
    except Exception as e:
        logger.error('Failed to fetch user tickers from DataEngine: %s', e)
        user_tickers = []

    logger.info("Dynamically sampling market universe to ensure balanced training distribution...")
    conn = None
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ticker FROM market_universe ORDER BY RANDOM() LIMIT 500")
        universe_sample = [row[0] for row in cursor.fetchall()]
    except Exception as e:
        logger.warning('Failed to sample from market_universe: %s', e)
        universe_sample = []
    finally:
        if conn:
            conn.close()

    combined_set  = set(user_tickers).union(set(universe_sample))
    cleaned_list  = [t for t in combined_set if t and not t.startswith("0P")]
    final_tickers = sorted(cleaned_list)[:600]

    logger.info('Targeting %s unique tickers for historical backfill.', len(final_tickers))
    return final_tickers


def sync_ticker_metadata(tickers: List[str]) -> None:
    """
    Ensures sector metadata is available in ticker_metadata.
    Idempotent — only fetches missing tickers.
    """
    logger.info('Syncing metadata for %s tickers...', len(tickers))
    conn = None
    try:
        conn   = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT ticker FROM ticker_metadata")
        existing_tickers = {row[0] for row in cursor.fetchall()}
        missing_tickers  = [t for t in tickers if t not in existing_tickers]

        if not missing_tickers:
            logger.info("All ticker metadata is already up to date.")
            return

        records: List[Tuple[str, str, float, float]] = []
        for ticker in missing_tickers:
            try:
                info   = yahoo_engine.get_ticker_info(ticker) or {}
                sector = info.get('sector', 'Unknown')
                beta   = info.get('beta', 1.0)
                mcap   = info.get('marketCap', 0.0)
                records.append((
                    ticker, sector,
                    float(beta) if beta else 1.0,
                    float(mcap) if mcap else 0.0
                ))
            except Exception as e:
                logger.warning('Failed to fetch metadata for %s: %s', ticker, e)
                records.append((ticker, 'Unknown', 1.0, 0.0))
            time.sleep(0.1)

        if records:
            cursor.executemany("""
                INSERT OR REPLACE INTO ticker_metadata (ticker, sector, beta, market_cap)
                VALUES (?, ?, ?, ?)
            """, records)
            conn.commit()
            logger.info('Injected metadata for %s new tickers.', len(records))
    finally:
        if conn:
            conn.close()


_QUANT_HISTORY_UPSERT = """
    INSERT INTO quant_signals
    (ticker, date, close_price, volume, rsi_14, macd, macd_signal,
     macd_hist, sma_50, sma_200, volume_surge, bullish_cross,
     mom_1m, mom_3m, mom_6m, mom_12m_skip1m,
     atr_pct, hist_vol_20, rel_strength_5d, rel_strength_20d)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(ticker, date) DO UPDATE SET
        close_price      = excluded.close_price,
        volume           = excluded.volume,
        rsi_14           = excluded.rsi_14,
        macd             = excluded.macd,
        macd_signal      = excluded.macd_signal,
        macd_hist        = excluded.macd_hist,
        sma_50           = excluded.sma_50,
        sma_200          = excluded.sma_200,
        volume_surge     = excluded.volume_surge,
        bullish_cross    = excluded.bullish_cross,
        mom_1m           = excluded.mom_1m,
        mom_3m           = excluded.mom_3m,
        mom_6m           = excluded.mom_6m,
        mom_12m_skip1m   = excluded.mom_12m_skip1m,
        atr_pct          = excluded.atr_pct,
        hist_vol_20      = excluded.hist_vol_20,
        rel_strength_5d  = excluded.rel_strength_5d,
        rel_strength_20d = excluded.rel_strength_20d
"""


_QUANT_FLAG_COLUMNS = frozenset({'volume_surge', 'bullish_cross'})


def quant_history_records(ticker: str, df: pd.DataFrame, spy_df: Optional[pd.DataFrame]) -> List[Tuple]:
    df = df.dropna(subset=['Close', 'Volume', 'High', 'Low'])

    if len(df) < 252:
        logger.warning("Skipping %s: insufficient data (%d rows < 252).", ticker, len(df))
        return []

    indicators = compute_quant_indicator_frame(df, spy_df)[list(QUANT_HISTORY_COLUMNS)].dropna()
    return [
        (ticker, index.strftime('%Y-%m-%d'), float(df.at[index, 'Close']), int(df.at[index, 'Volume']),
         *(int(row[column]) if column in _QUANT_FLAG_COLUMNS else float(row[column]) for column in QUANT_HISTORY_COLUMNS))
        for index, row in indicators.iterrows()
    ]


def rebuild_quant_history(ticker: str, df: pd.DataFrame, from_date: str) -> int:
    records = [r for r in quant_history_records(ticker, df, download_spy_benchmark()) if r[1] >= from_date]
    if not records:
        return 0
    conn = None
    try:
        conn = get_connection()
        conn.executemany(_QUANT_HISTORY_UPSERT, records)
        conn.commit()
    finally:
        if conn:
            conn.close()
    return len(records)


def run_historical_backfill(tickers: Optional[List[str]] = None) -> None:
    """Fundamental features are NOT stored in quant_signals — joined from stock_signals at training/inference time."""
    if tickers is None:
        tickers = get_target_tickers()
    if not tickers:
        logger.warning("No tickers found to backfill. Aborting.")
        return

    sync_ticker_metadata(tickers)

    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    start_idx = 0

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT last_processed_ticker FROM quant_scan_states "
            "WHERE scan_type = 'ml_backfill' AND status = 'IN_PROGRESS' "
            "ORDER BY scan_date DESC LIMIT 1"
        )
        state = cursor.fetchone()

        if state:
            last_ticker = state['last_processed_ticker']
            if last_ticker and last_ticker in tickers:
                start_idx = tickers.index(last_ticker) + 1
                resume_ticker = tickers[start_idx] if start_idx < len(tickers) else 'END'
                logger.info("Resuming ML Backfill from %s (skipping %d already-processed tickers).", resume_ticker, start_idx)
                log_notification("Info", f"Resuming ML Historical Backfill from {resume_ticker}.")
        else:
            cursor.execute(
                "INSERT INTO quant_scan_states (scan_date, scan_type, last_processed_ticker, status) VALUES (?, ?, ?, ?)",
                (today_str, 'ml_backfill', '', 'IN_PROGRESS')
            )
            conn.commit()

        spy_df = download_spy_benchmark()

        log_notification("Info", f"ML Historical Backfill initiated for {len(tickers)} assets.")

        total_inserted = 0
        total_tickers  = len(tickers)

        for i in range(start_idx, total_tickers):
            ticker = tickers[i]
            logger.info("[%d/%d] Processing 2y historical data for %s...", i + 1, total_tickers, ticker)

            try:
                df = load_or_fetch_daily_history(ticker)
                if df is None:
                    df = pd.DataFrame()

                if df.empty:
                    continue

                records = quant_history_records(ticker, df, spy_df)
                if not records:
                    continue

                cursor.executemany(_QUANT_HISTORY_UPSERT, records)
                conn.commit()
                total_inserted += cursor.rowcount

                cursor.execute(
                    "UPDATE quant_scan_states SET last_processed_ticker = ? WHERE scan_type = 'ml_backfill' AND status = 'IN_PROGRESS'",
                    (ticker,)
                )
                conn.commit()

            except Exception as e:
                logger.error("Error processing ticker %s: %s", ticker, e)
                conn.rollback()
            finally:
                time.sleep(0.5)

            processed = i + 1
            if total_tickers >= 4 and processed % max(1, total_tickers // 4) == 0 and processed < total_tickers:
                pct = int((processed / total_tickers) * 100)
                log_notification("Info", f"ML Historical Backfill Progress: {pct}% ({processed}/{total_tickers} tickers processed).")

        cursor.execute(
            "UPDATE quant_scan_states SET status = 'COMPLETED' WHERE scan_type = 'ml_backfill' AND status = 'IN_PROGRESS'"
        )
        conn.commit()

        logger.info("ML Backfill complete. Injected/updated %d historical rows.", total_inserted)
        log_notification("Success", f"ML Backfill completed. Injected/Updated {total_inserted:,} data points.")

    except Exception as e:
        logger.error("Fatal error during historical backfill: %s", e)
        log_notification("Error", f"ML Historical Backfill failed: {str(e)}")
    finally:
        if conn:
            conn.close()
