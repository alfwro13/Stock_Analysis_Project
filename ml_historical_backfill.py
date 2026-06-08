import time
import logging
from typing import List, Tuple
import pandas as pd
from indicators import (
    compute_rsi,
    compute_macd,
    compute_smas,
    compute_volume_sma,
    compute_volume_surge,
    compute_bullish_cross,
)

from database import get_connection
from data_engine import DataEngine
from yahoo_engine import yahoo_engine

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - ML_BACKFILL - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BLUE_CHIPS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "BRK-B", "LLY", "TSLA", 
    "AVGO", "JPM", "UNH", "V", "XOM", "MA", "JNJ", "PG", "HD", "COST", "MRK", 
    "ABBV", "CVX", "CRM", "AMD", "BAC", "PEP", "KO", "LIN", "TMO", "WMT", "MCD", 
    "DIS", "CSCO", "ACN", "ABT", "INTU", "QCOM", "IBM", "CAT", "VZ", "AMGN", 
    "TXN", "NOW", "PFE", "COP", "BA", "SPY", "QQQ", "DIA", "IWM"
]

def get_target_tickers() -> List[str]:
    """Union of portfolio/watchlist tickers + BLUE_CHIPS, deduplicated and capped at 250."""
    logger.info("Extracting user portfolio and watchlist tickers...")
    try:
        engine = DataEngine()
        user_tickers = engine.get_all_tickers()
    except Exception as e:
        logger.error(f"Failed to fetch user tickers from DataEngine: {e}")
        user_tickers = []

    combined_set = set(user_tickers).union(set(BLUE_CHIPS))
    cleaned_list = [t for t in combined_set if t and not t.startswith("0P")]
    final_tickers = sorted(cleaned_list)[:250]
    logger.info(f"Targeting {len(final_tickers)} unique tickers for historical backfill.")
    return final_tickers

def process_and_insert() -> None:
    """Download 2y of daily data per ticker, compute TA indicators, INSERT OR IGNORE into quant_signals."""
    tickers = get_target_tickers()
    if not tickers:
        logger.warning("No tickers found to backfill. Aborting.")
        return

    conn = None
    try:
        conn = get_connection()
        total_inserted = 0

        for i, ticker in enumerate(tickers):
            logger.info(f"[{i+1}/{len(tickers)}] Processing 2y historical data for {ticker}...")

            try:
                _result = yahoo_engine.get_price_history([ticker], period="2y", interval="1d")
                df = _result.get(ticker, pd.DataFrame())

                if df.empty:
                    logger.warning(f"No data returned for {ticker}. Skipping.")
                    continue

                df.dropna(subset=['Close', 'Volume'], inplace=True)

                if len(df) < 200:
                    logger.warning(f"Insufficient historical data for {ticker} (requires >= 200 days). Skipping.")
                    continue

                df['rsi_14'] = compute_rsi(df['Close'])
                df['macd'], df['macd_signal'], df['macd_hist'] = compute_macd(df['Close'])
                _smas = compute_smas(df['Close'], [50, 200])
                df['sma_50']  = _smas[50]
                df['sma_200'] = _smas[200]
                df['vol_sma_20']    = compute_volume_sma(df['Volume'])
                df['volume_surge']  = compute_volume_surge(df['Volume'], df['vol_sma_20'])
                df['bullish_cross'] = compute_bullish_cross(df['macd'], df['macd_signal'])

                # Drop NaN rows produced by the 200-day SMA warmup window.
                df.dropna(inplace=True)

                if df.empty:
                    logger.warning(f"Dataframe became empty after dropping NaNs for {ticker}. Skipping.")
                    continue

                records: List[Tuple] = []
                for index, row in df.iterrows():
                    date_str = index.strftime('%Y-%m-%d')

                    records.append((
                        ticker,
                        date_str,
                        float(row['Close']),
                        int(row['Volume']),
                        float(row['rsi_14']),
                        float(row['macd']),
                        float(row['macd_signal']),
                        float(row['macd_hist']),
                        float(row['sma_50']),
                        float(row['sma_200']),
                        int(row['volume_surge']),
                        int(row['bullish_cross'])
                    ))

                # INSERT OR IGNORE preserves current-day enriched rows written by the live scan.
                cursor = conn.cursor()
                query = """
                    INSERT OR IGNORE INTO quant_signals
                    (ticker, date, close_price, volume, rsi_14, macd, macd_signal, macd_hist, sma_50, sma_200, volume_surge, bullish_cross)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                cursor.executemany(query, records)
                conn.commit()
                # executemany rowcount = number of actually inserted rows (IGNORE-d rows not counted)
                inserted = cursor.rowcount
                total_inserted += inserted
                logger.info(f"Successfully backfilled {inserted} rows for {ticker}.")

            except Exception as e:
                logger.error(f"Error processing ticker {ticker}: {e}")
                conn.rollback()
            finally:
                time.sleep(0.5)

        logger.info(f"--- BACKFILL COMPLETE. Injected {total_inserted} new historical rows across {len(tickers)} assets. ---")

    except Exception as e:
        logger.error(f"Fatal error during historical backfill execution: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    logger.info("Initializing ML Historical Data Backfill script...")
    process_and_insert()