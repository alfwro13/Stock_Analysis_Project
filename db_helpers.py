import logging
from typing import List, Optional

from config import load_config
from database import get_connection

logger = logging.getLogger(__name__)


def log_score_event(ticker: str, date: str, score: int, signal: str, close_price: Optional[float]) -> None:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO score_history (ticker, date, score, signal, close_price)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(ticker, date) DO UPDATE SET
                   score = excluded.score,
                   signal = excluded.signal,
                   close_price = COALESCE(excluded.close_price, score_history.close_price)""",
            (ticker, date, score, signal, close_price)
        )
        conn.commit()
    except Exception as e:
        logger.error("Failed to log score event for %s on %s: %s", ticker, date, e)
    finally:
        if conn:
            conn.close()


def get_universe_tickers() -> List[str]:
    """Respects FREETRADE_ONLY_MODE: returns only is_freetrade=1 tickers when enabled."""
    conn = None
    try:
        config_data = load_config()
        freetrade_only = config_data.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)
        conn = get_connection()
        cursor = conn.cursor()
        if freetrade_only:
            cursor.execute("SELECT ticker FROM market_universe WHERE is_freetrade = 1")
        else:
            cursor.execute("SELECT ticker FROM market_universe")
        return [row['ticker'] for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Failed to fetch universe tickers: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def get_mutual_fund_tickers(tickers: List[str]) -> set:
    """Subset of `tickers` classified MUTUALFUND in market_universe — these have no intraday
    trading (one NAV print per day), so Yahoo Finance always returns empty for 5m bars."""
    if not tickers:
        return set()
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(tickers))
        cursor.execute(
            f"SELECT ticker FROM market_universe WHERE quote_type = 'MUTUALFUND' "
            f"AND ticker IN ({placeholders})",
            tickers,
        )
        return {row["ticker"] for row in cursor.fetchall()}
    except Exception as e:
        logger.error("Failed to fetch mutual fund tickers: %s", e)
        return set()
    finally:
        if conn:
            conn.close()


def upsert_quant_signal(
    ticker: str,
    date: str,
    close_price: float,
    volume: int,
    rsi_14: Optional[float] = None,
    macd: Optional[float] = None,
    macd_signal: Optional[float] = None,
    macd_hist: Optional[float] = None,
    sma_50: Optional[float] = None,
    sma_200: Optional[float] = None,
    volume_surge: Optional[bool] = None,
    bullish_cross: Optional[bool] = None,
    ml_confidence_score: Optional[float] = None,
    sentiment_score: Optional[float] = None,
    var_95: Optional[float] = None,
    cvar_95: Optional[float] = None
) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        query = '''
            INSERT INTO quant_signals (
                ticker, date, close_price, volume, rsi_14, macd, macd_signal, macd_hist,
                sma_50, sma_200, volume_surge, bullish_cross,
                ml_confidence_score, sentiment_score, var_95, cvar_95
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET
                close_price       = excluded.close_price,
                volume            = excluded.volume,
                rsi_14            = excluded.rsi_14,
                macd              = excluded.macd,
                macd_signal       = excluded.macd_signal,
                macd_hist         = excluded.macd_hist,
                sma_50            = excluded.sma_50,
                sma_200           = excluded.sma_200,
                volume_surge      = excluded.volume_surge,
                bullish_cross     = excluded.bullish_cross,
                ml_confidence_score = COALESCE(excluded.ml_confidence_score, quant_signals.ml_confidence_score),
                sentiment_score   = COALESCE(excluded.sentiment_score, quant_signals.sentiment_score),
                var_95            = COALESCE(excluded.var_95, quant_signals.var_95),
                cvar_95           = COALESCE(excluded.cvar_95, quant_signals.cvar_95)
        '''

        cursor.execute(query, (
            ticker, date, close_price, volume, rsi_14, macd, macd_signal, macd_hist,
            sma_50, sma_200, volume_surge, bullish_cross,
            ml_confidence_score, sentiment_score, var_95, cvar_95
        ))

        conn.commit()
        return True
    except Exception as e:
        logger.error("Database insertion failed for quant_signal (%s on %s): %s", ticker, date, e)
        return False
    finally:
        if conn:
            conn.close()


def log_trap_phase(
    ticker: str,
    phase: str,
    scan_date: str,
    close_price: Optional[float],
    scan_ts: str,
) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            """INSERT OR IGNORE INTO trap_phase_history
               (ticker, phase, scan_date, scan_ts, close_price)
               VALUES (?, ?, ?, ?, ?)""",
            (ticker, phase, scan_date, scan_ts, close_price),
        )
        conn.commit()
    except Exception as e:
        logger.error("log_trap_phase failed for %s on %s: %s", ticker, scan_date, e)
    finally:
        if conn:
            conn.close()


def get_unresolved_trap_phases(cutoff_14d: str, cutoff_30d: str) -> list:
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT id, ticker, phase, scan_date, close_price,
                      direction_correct_14d, direction_correct_30d
               FROM trap_phase_history
               WHERE phase != 'NEUTRAL'
                 AND (
                   (direction_correct_14d IS NULL AND scan_date <= ?)
                   OR (direction_correct_30d IS NULL AND scan_date <= ?)
                 )
               ORDER BY scan_date""",
            (cutoff_14d, cutoff_30d),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_unresolved_trap_phases failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def update_trap_phase_actual(
    row_id: int,
    horizon: int,
    actual_price: float,
    actual_date: str,
    direction_correct: Optional[int],
) -> None:
    price_col   = f"actual_price_{horizon}d"
    date_col    = f"actual_date_{horizon}d"
    correct_col = f"direction_correct_{horizon}d"
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            f"UPDATE trap_phase_history SET {price_col}=?, {date_col}=?, {correct_col}=? WHERE id=?",
            (actual_price, actual_date, direction_correct, row_id),
        )
        conn.commit()
    except Exception as e:
        logger.error("update_trap_phase_actual failed for id %s horizon %sd: %s", row_id, horizon, e)
    finally:
        if conn:
            conn.close()


def batch_update_trap_phase_actuals(
    payloads: list[tuple[int, int, float, str, int]],
) -> None:
    """Single-transaction update; each payload is (row_id, horizon, actual_price, actual_date, direction_correct)."""
    if not payloads:
        return
    conn = None
    try:
        conn = get_connection()
        for row_id, horizon, actual_price, actual_date, direction_correct in payloads:
            price_col   = f"actual_price_{horizon}d"
            date_col    = f"actual_date_{horizon}d"
            correct_col = f"direction_correct_{horizon}d"
            conn.execute(
                f"UPDATE trap_phase_history SET {price_col}=?, {date_col}=?, {correct_col}=? WHERE id=?",
                (actual_price, actual_date, direction_correct, row_id),
            )
        conn.commit()
    except Exception as e:
        logger.error("batch_update_trap_phase_actuals failed (%d rows): %s", len(payloads), e)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def get_trap_phase_accuracy() -> dict:
    conn = None
    try:
        conn = get_connection()
        phases = [
            dict(r) for r in conn.execute(
                """SELECT
                    phase,
                    COUNT(*) AS total,
                    SUM(CASE WHEN direction_correct_14d IS NOT NULL THEN 1 ELSE 0 END) AS resolved_14d,
                    ROUND(AVG(CASE WHEN direction_correct_14d IS NOT NULL
                              THEN direction_correct_14d END) * 100, 1) AS accuracy_14d,
                    SUM(CASE WHEN direction_correct_30d IS NOT NULL THEN 1 ELSE 0 END) AS resolved_30d,
                    ROUND(AVG(CASE WHEN direction_correct_30d IS NOT NULL
                              THEN direction_correct_30d END) * 100, 1) AS accuracy_30d
                   FROM trap_phase_history
                   WHERE phase != 'NEUTRAL'
                   GROUP BY phase
                   ORDER BY phase"""
            ).fetchall()
        ]
        overall = dict(conn.execute(
            """SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN direction_correct_14d IS NOT NULL THEN 1 ELSE 0 END) AS resolved_14d,
                ROUND(AVG(CASE WHEN direction_correct_14d IS NOT NULL
                          THEN direction_correct_14d END) * 100, 1) AS accuracy_14d,
                SUM(CASE WHEN direction_correct_30d IS NOT NULL THEN 1 ELSE 0 END) AS resolved_30d,
                ROUND(AVG(CASE WHEN direction_correct_30d IS NOT NULL
                          THEN direction_correct_30d END) * 100, 1) AS accuracy_30d
               FROM trap_phase_history
               WHERE phase != 'NEUTRAL'"""
        ).fetchone())
        return {"phases": phases, "overall": overall}
    except Exception as e:
        logger.error("get_trap_phase_accuracy failed: %s", e)
        return {"phases": [], "overall": {}}
    finally:
        if conn:
            conn.close()
