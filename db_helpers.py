import logging
from datetime import datetime, timezone
from typing import List, Optional

from config import load_config
from database import get_connection

logger = logging.getLogger(__name__)

LIVE_PRICE_OVERRIDE_MAX_GAP_SECONDS = 6 * 3600


def parse_utc_epoch(stored_utc: Optional[str]) -> float:
    """Parses a `"%Y-%m-%d %H:%M:%S"` UTC timestamp (SQLite storage format) to a Unix epoch."""
    if not stored_utc:
        return 0.0
    try:
        return datetime.strptime(stored_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


def resolve_live_price(
    live_price: Optional[float],
    live_last_updated_epoch: Optional[float],
    fallback_price: Optional[float],
    fallback_last_updated_utc: Optional[str],
    max_gap_seconds: int = LIVE_PRICE_OVERRIDE_MAX_GAP_SECONDS,
) -> "tuple[Optional[float], bool]":
    """Picks between a live-cache price (e.g. `market_pulse_cache`) and a slower-cadence fallback
    (e.g. `stock_signals.current_price`), trusting the live price only while it isn't stuck more
    than `max_gap_seconds` behind the fallback's own last write. A live price that predates the
    fallback by more than the gap means the live source has stopped updating for this ticker
    while the fallback kept moving — trusting it then risks showing a long-stale, possibly wildly
    wrong number instead of falling back to the slower-but-current source. See
    accounts_engine.current_price_map()'s original docstring for the full reasoning (a bare
    "whichever timestamp is newer" comparison flips sources every night purely by winning a
    timestamp race, even when the live cache is still correctly holding the day's real close).
    A missing fallback (no row for this ticker in the fallback source) always keeps the live
    price — there's nothing to compare against, so the gap-check can't apply.

    Returns `(price, used_fallback)` rather than a bare price — callers that also display a
    change_pts/change_pct computed relative to the live price need to know when the price they're
    showing came from the fallback instead, so they can drop those now-inconsistent figures rather
    than inferring the switch by comparing the returned value back against the live price (which
    would silently miss the case where both sources happen to agree on the exact same number)."""
    if fallback_price is None:
        return live_price, False
    if live_price is None or not live_last_updated_epoch:
        return fallback_price, True
    fallback_epoch = parse_utc_epoch(fallback_last_updated_utc)
    if fallback_epoch - live_last_updated_epoch <= max_gap_seconds:
        return live_price, False
    return fallback_price, True


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


def get_portfolio_watchlist_tickers() -> List[str]:
    """Union of Portfolio + Watchlist tickers, uppercased/sorted, ignored-ticker-filtered — the
    shared scope-union logic for any engine offering a Portfolio+Watchlist scope (Pairs Spread
    Monitor, Predicted Movers). Local imports avoid a circular import: database.py imports this
    module at module level, and accounts_engine.py imports database.py at module level."""
    from accounts_engine import get_combined_holdings
    from database import get_watchlist_tickers
    from utils import ignored_tickers_set, is_excluded_from_yahoo_fetch

    ignored = ignored_tickers_set(load_config())
    tickers: set = set()
    tickers.update(get_combined_holdings().keys())
    tickers.update(get_watchlist_tickers())
    return sorted(
        t.upper() for t in tickers
        if t and not is_excluded_from_yahoo_fetch(t, ignored)
    )


def get_mutual_fund_tickers(tickers: List[str]) -> set:
    """Subset of `tickers` classified MUTUALFUND — these have no intraday trading (one NAV
    print per day), so Yahoo Finance always returns empty for 5m bars. Checks asset_profiles
    and stock_signals as well as market_universe: a portfolio-only ticker bought via a
    Built-in Account (rather than imported from a Freetrade CSV export) never gets a
    market_universe row, and only picks up its quote_type via the nightly quant scan
    (stock_signals) and fundamentals profiler (asset_profiles) — which may not have run yet
    for a ticker at all (e.g. a just-closed position). Yahoo's own OEIC/mutual-fund symbol
    scheme always starts with "0P" (e.g. "0P0001RI3X.L"), so that prefix is matched directly
    without a DB round trip, guaranteeing correct classification even before any table has a row."""
    if not tickers:
        return set()
    prefix_matches = {t for t in tickers if t.startswith('0P')}
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(tickers))
        cursor.execute(
            f"SELECT ticker FROM market_universe WHERE quote_type = 'MUTUALFUND' AND ticker IN ({placeholders}) "
            f"UNION SELECT ticker FROM asset_profiles WHERE quote_type = 'MUTUALFUND' AND ticker IN ({placeholders}) "
            f"UNION SELECT ticker FROM stock_signals WHERE quote_type = 'MUTUALFUND' AND ticker IN ({placeholders})",
            tickers * 3,
        )
        return prefix_matches | {row["ticker"] for row in cursor.fetchall()}
    except Exception as e:
        logger.error("Failed to fetch mutual fund tickers: %s", e)
        return prefix_matches
    finally:
        if conn:
            conn.close()


def filter_equity_tickers(tickers: List[str]) -> List[str]:
    """Subset of `tickers` NOT classified as a non-equity quote_type (ETF, MUTUALFUND, etc.) in
    stock_signals or asset_profiles. A ticker with no quote_type recorded anywhere is treated as
    equity (kept) — callers that need certainty should already have a profiled quote_type before
    reaching here. Used to keep equity-only scans (earnings dates, insider Form 4 filings) from
    hammering Yahoo for instrument types that structurally never have that data."""
    if not tickers:
        return tickers
    conn = None
    try:
        conn = get_connection()
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker FROM stock_signals WHERE ticker IN ({placeholders}) AND quote_type IS NOT NULL AND quote_type != 'EQUITY' "
            f"UNION SELECT ticker FROM asset_profiles WHERE ticker IN ({placeholders}) AND quote_type IS NOT NULL AND quote_type != 'EQUITY'",
            tickers * 2,
        ).fetchall()
        non_equity = {row["ticker"] for row in rows}
        if non_equity:
            logger.info("Excluded %d non-equity tickers: %s", len(non_equity), sorted(non_equity))
        return [t for t in tickers if t not in non_equity]
    except Exception as e:
        logger.debug("Could not filter non-equity tickers: %s", e)
        return tickers
    finally:
        if conn:
            conn.close()


def get_next_earnings_dates(tickers: List[str]) -> dict:
    """{ticker: {"company_name", "next_earnings_date"}} from stock_signals for `tickers` — the
    single shared read of the value written nightly by the Quant Scan (quant_signals.py) and
    weekly by the Universe Fundamentals sync, both from Yahoo's earningsTimestamp field. Callers
    checking "does this ticker have earnings soon" must use this instead of re-deriving the date
    via a live yahoo_engine.get_ticker_info()/get_earnings_dates() call."""
    if not tickers:
        return {}
    conn = None
    try:
        conn = get_connection()
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, company_name, next_earnings_date FROM stock_signals WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
        return {
            row["ticker"]: {"company_name": row["company_name"], "next_earnings_date": row["next_earnings_date"]}
            for row in rows
        }
    except Exception as e:
        logger.error("Failed to fetch next earnings dates: %s", e)
        return {}
    finally:
        if conn:
            conn.close()


def get_ticker_currency_map(tickers: List[str], conn) -> dict:
    """{ticker: currency} from stock_signals for `tickers`, using a caller-supplied connection
    (e.g. a job's already-open conn). Raw currency as stored (GBp/GBP not normalized) — callers
    needing pence/pounds collapsed into one bucket must do that themselves."""
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"SELECT ticker, currency FROM stock_signals WHERE ticker IN ({placeholders})",
        tickers,
    ).fetchall()
    return {r["ticker"]: r["currency"] for r in rows}


def get_company_names(tickers: List[str]) -> dict:
    """{ticker: company_name} from stock_signals for `tickers`. Opens its own connection —
    for batch-enriching a result set at the API/page layer, not inside an already-open job txn."""
    if not tickers:
        return {}
    conn = None
    try:
        conn = get_connection()
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, company_name FROM stock_signals WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
        return {r["ticker"]: r["company_name"] for r in rows if r["company_name"]}
    except Exception as e:
        logger.error("Failed to fetch company names: %s", e)
        return {}
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
    rsi: Optional[float] = None,
    ema_distance: Optional[float] = None,
    bull_trap_vol_ratio: Optional[float] = None,
    cap_vol_zscore: Optional[float] = None,
    wyckoff_bb_width: Optional[float] = None,
) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            """INSERT OR IGNORE INTO trap_phase_history
               (ticker, phase, scan_date, scan_ts, close_price,
                rsi, ema_distance, bull_trap_vol_ratio, cap_vol_zscore, wyckoff_bb_width)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, phase, scan_date, scan_ts, close_price,
             rsi, ema_distance, bull_trap_vol_ratio, cap_vol_zscore, wyckoff_bb_width),
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


def log_pattern_detection(
    ticker: str,
    pattern_family: str,
    pattern_type: str,
    phase: str,
    scan_date: str,
    close_price: Optional[float],
    scan_ts: str,
    measured_target: Optional[float] = None,
    volume_confirms: Optional[bool] = None,
    rsi_divergence: Optional[bool] = None,
    pattern_r2: Optional[float] = None,
    prior_trend_pct: Optional[float] = None,
) -> bool:
    """Returns True if a new row was inserted, False if one already existed for this
    (ticker, scan_date, pattern_family, pattern_type) or on failure — lets callers (the
    historical backfill) count genuinely new rows logged."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.execute(
            """INSERT OR IGNORE INTO pattern_detection_history
               (ticker, pattern_family, pattern_type, phase, scan_date, scan_ts, close_price,
                measured_target, volume_confirms, rsi_divergence, pattern_r2, prior_trend_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, pattern_family, pattern_type, phase, scan_date, scan_ts, close_price,
             measured_target,
             None if volume_confirms is None else int(volume_confirms),
             None if rsi_divergence is None else int(rsi_divergence),
             pattern_r2, prior_trend_pct),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("log_pattern_detection failed for %s on %s: %s", ticker, scan_date, e)
        return False
    finally:
        if conn:
            conn.close()


def get_unresolved_pattern_detections(cutoff_14d: str, cutoff_30d: str) -> list:
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT id, ticker, pattern_family, pattern_type, phase, scan_date, close_price,
                      direction_correct_14d, direction_correct_30d
               FROM pattern_detection_history
               WHERE phase = 'CONFIRMED'
                 AND (
                   (direction_correct_14d IS NULL AND scan_date <= ?)
                   OR (direction_correct_30d IS NULL AND scan_date <= ?)
                 )
               ORDER BY scan_date""",
            (cutoff_14d, cutoff_30d),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_unresolved_pattern_detections failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def batch_update_pattern_detection_actuals(
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
                f"UPDATE pattern_detection_history SET {price_col}=?, {date_col}=?, {correct_col}=? WHERE id=?",
                (actual_price, actual_date, direction_correct, row_id),
            )
        conn.commit()
    except Exception as e:
        logger.error("batch_update_pattern_detection_actuals failed (%d rows): %s", len(payloads), e)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def get_pattern_detection_accuracy(pattern_family: Optional[str] = None) -> dict:
    conn = None
    try:
        conn = get_connection()
        family_filter = "AND pattern_family = ?" if pattern_family else ""
        params = (pattern_family,) if pattern_family else ()
        patterns = [
            dict(r) for r in conn.execute(
                f"""SELECT
                    pattern_family,
                    pattern_type,
                    COUNT(*) AS total,
                    SUM(CASE WHEN direction_correct_14d IS NOT NULL THEN 1 ELSE 0 END) AS resolved_14d,
                    ROUND(AVG(CASE WHEN direction_correct_14d IS NOT NULL
                              THEN direction_correct_14d END) * 100, 1) AS accuracy_14d,
                    SUM(CASE WHEN direction_correct_30d IS NOT NULL THEN 1 ELSE 0 END) AS resolved_30d,
                    ROUND(AVG(CASE WHEN direction_correct_30d IS NOT NULL
                              THEN direction_correct_30d END) * 100, 1) AS accuracy_30d
                   FROM pattern_detection_history
                   WHERE phase = 'CONFIRMED' {family_filter}
                   GROUP BY pattern_family, pattern_type
                   ORDER BY pattern_family, pattern_type""",
                params,
            ).fetchall()
        ]
        overall = dict(conn.execute(
            f"""SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN direction_correct_14d IS NOT NULL THEN 1 ELSE 0 END) AS resolved_14d,
                ROUND(AVG(CASE WHEN direction_correct_14d IS NOT NULL
                          THEN direction_correct_14d END) * 100, 1) AS accuracy_14d,
                SUM(CASE WHEN direction_correct_30d IS NOT NULL THEN 1 ELSE 0 END) AS resolved_30d,
                ROUND(AVG(CASE WHEN direction_correct_30d IS NOT NULL
                          THEN direction_correct_30d END) * 100, 1) AS accuracy_30d
               FROM pattern_detection_history
               WHERE phase = 'CONFIRMED' {family_filter}""",
            params,
        ).fetchone())
        return {"patterns": patterns, "overall": overall}
    except Exception as e:
        logger.error("get_pattern_detection_accuracy failed: %s", e)
        return {"patterns": [], "overall": {}}
    finally:
        if conn:
            conn.close()


def get_unresolved_predicted_movers(cutoff: str) -> list:
    """Every predicted_movers_history row whose ~10-trading-day target has passed but hasn't
    been resolved yet — scanned in full each run (catch-up discipline), not just the newest."""
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT id, ticker, close_price, price_q10, price_q90, target_date
               FROM predicted_movers_history
               WHERE direction_correct IS NULL AND target_date <= ?
               ORDER BY target_date""",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_unresolved_predicted_movers failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def batch_update_predicted_movers_actuals(
    payloads: list[tuple[int, float, str, int, int]],
) -> None:
    """Single-transaction update; each payload is
    (row_id, actual_price, actual_date, direction_correct, within_band_correct)."""
    if not payloads:
        return
    conn = None
    try:
        conn = get_connection()
        for row_id, actual_price, actual_date, direction_correct, within_band_correct in payloads:
            conn.execute(
                """UPDATE predicted_movers_history
                   SET actual_price=?, actual_date=?, direction_correct=?, within_band_correct=?
                   WHERE id=?""",
                (actual_price, actual_date, direction_correct, within_band_correct, row_id),
            )
        conn.commit()
    except Exception as e:
        logger.error("batch_update_predicted_movers_actuals failed (%d rows): %s", len(payloads), e)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def get_predicted_movers_accuracy() -> dict:
    """Per-ticker + overall direction-match / within-band-match hit rates. `resolved` counts
    rows whose ~10-trading-day target_date has passed and been graded; `pending` are still
    within that window. Accuracy percentages are computed over resolved rows only."""
    conn = None
    try:
        conn = get_connection()
        by_ticker = [
            dict(r) for r in conn.execute(
                """SELECT
                    ticker,
                    COUNT(*) AS total,
                    SUM(CASE WHEN direction_correct IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
                    SUM(CASE WHEN direction_correct IS NULL THEN 1 ELSE 0 END) AS pending,
                    ROUND(AVG(CASE WHEN direction_correct IS NOT NULL
                              THEN direction_correct END) * 100, 1) AS direction_accuracy,
                    ROUND(AVG(CASE WHEN within_band_correct IS NOT NULL
                              THEN within_band_correct END) * 100, 1) AS within_band_accuracy
                   FROM predicted_movers_history
                   GROUP BY ticker
                   ORDER BY ticker"""
            ).fetchall()
        ]
        overall = dict(conn.execute(
            """SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN direction_correct IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
                SUM(CASE WHEN direction_correct IS NULL THEN 1 ELSE 0 END) AS pending,
                ROUND(AVG(CASE WHEN direction_correct IS NOT NULL
                          THEN direction_correct END) * 100, 1) AS direction_accuracy,
                ROUND(AVG(CASE WHEN within_band_correct IS NOT NULL
                          THEN within_band_correct END) * 100, 1) AS within_band_accuracy
               FROM predicted_movers_history"""
        ).fetchone())
        return {"by_ticker": by_ticker, "overall": overall}
    except Exception as e:
        logger.error("get_predicted_movers_accuracy failed: %s", e)
        return {"by_ticker": [], "overall": {}}
    finally:
        if conn:
            conn.close()


def get_latest_quantile_bands(tickers: list) -> list:
    """Latest quant_signals row per ticker with non-null price_q10/price_q90 — the same
    inline correlated-subquery idiom used elsewhere in the codebase for 'latest row per
    ticker' (ai_prediction_engine.py, page_routes.py, market_pulse.py). Shared by
    predicted_movers_engine.py and the /earnings-volatility page, both of which need the
    general-purpose ML Quantile Price Band for a ticker."""
    if not tickers:
        return []
    conn = None
    try:
        conn = get_connection()
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"""SELECT qs.ticker, qs.date, qs.close_price, qs.price_q10, qs.price_q90
                FROM quant_signals qs
                WHERE qs.ticker IN ({placeholders})
                  AND qs.price_q10 IS NOT NULL AND qs.price_q90 IS NOT NULL
                  AND qs.date = (
                      SELECT MAX(qs2.date) FROM quant_signals qs2
                      WHERE qs2.ticker = qs.ticker
                        AND qs2.price_q10 IS NOT NULL AND qs2.price_q90 IS NOT NULL
                  )""",
            tickers,
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_latest_quantile_bands failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def log_earnings_drift_prediction(
    ticker: str, earnings_date: str, predicted_ts: str, pre_earnings_close: float,
    sample_size: Optional[int],
    predicted_pct_1d: Optional[float], target_date_1d: str,
    predicted_pct_5d: Optional[float], target_date_5d: str,
    predicted_pct_20d: Optional[float], target_date_20d: str,
) -> None:
    """Logs (or, while unresolved, refreshes) a post-earnings drift prediction. Uses ON CONFLICT
    DO UPDATE rather than INSERT OR REPLACE (mirrors quant_signals.py's own reasoning for the
    same choice) so a daily re-run in the days before earnings can re-anchor pre_earnings_close
    to a fresher close without a bare REPLACE resetting id/actual_* columns — and the WHERE guard
    ensures a row that has already started resolving is never clobbered by a stale re-run."""
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO earnings_drift_predictions
               (ticker, earnings_date, predicted_ts, pre_earnings_close, sample_size,
                predicted_pct_1d, target_date_1d,
                predicted_pct_5d, target_date_5d,
                predicted_pct_20d, target_date_20d)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(ticker, earnings_date) DO UPDATE SET
                   predicted_ts = excluded.predicted_ts,
                   pre_earnings_close = excluded.pre_earnings_close,
                   sample_size = excluded.sample_size,
                   predicted_pct_1d = excluded.predicted_pct_1d,
                   target_date_1d = excluded.target_date_1d,
                   predicted_pct_5d = excluded.predicted_pct_5d,
                   target_date_5d = excluded.target_date_5d,
                   predicted_pct_20d = excluded.predicted_pct_20d,
                   target_date_20d = excluded.target_date_20d
               WHERE earnings_drift_predictions.direction_correct_1d IS NULL""",
            (ticker, earnings_date, predicted_ts, pre_earnings_close, sample_size,
             predicted_pct_1d, target_date_1d,
             predicted_pct_5d, target_date_5d,
             predicted_pct_20d, target_date_20d),
        )
        conn.commit()
    except Exception as e:
        logger.error("log_earnings_drift_prediction failed for %s on %s: %s", ticker, earnings_date, e)
    finally:
        if conn:
            conn.close()


def get_unresolved_earnings_drift(cutoff: str) -> list:
    """Every earnings_drift_predictions row with at least one horizon whose target_date has
    passed but hasn't been resolved yet — 3-horizon generalization of
    get_unresolved_trap_phases's 2-cutoff pattern. Scanned in full each run (catch-up
    discipline), not just the newest."""
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT id, ticker, pre_earnings_close,
                      predicted_pct_1d, target_date_1d, direction_correct_1d,
                      predicted_pct_5d, target_date_5d, direction_correct_5d,
                      predicted_pct_20d, target_date_20d, direction_correct_20d
               FROM earnings_drift_predictions
               WHERE (direction_correct_1d IS NULL AND target_date_1d <= ?)
                  OR (direction_correct_5d IS NULL AND target_date_5d <= ?)
                  OR (direction_correct_20d IS NULL AND target_date_20d <= ?)
               ORDER BY earnings_date""",
            (cutoff, cutoff, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_unresolved_earnings_drift failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def batch_update_earnings_drift_actuals(
    payloads: list,
) -> None:
    """Single-transaction update; each payload is (row_id, horizon, actual_price, actual_date,
    direction_correct). horizon in {1, 5, 20} selects the
    actual_price_{h}d/actual_date_{h}d/direction_correct_{h}d column triple — byte-for-byte the
    pattern in batch_update_trap_phase_actuals."""
    if not payloads:
        return
    conn = None
    try:
        conn = get_connection()
        for row_id, horizon, actual_price, actual_date, direction_correct in payloads:
            price_col = f"actual_price_{horizon}d"
            date_col = f"actual_date_{horizon}d"
            correct_col = f"direction_correct_{horizon}d"
            conn.execute(
                f"UPDATE earnings_drift_predictions SET {price_col}=?, {date_col}=?, {correct_col}=? WHERE id=?",
                (actual_price, actual_date, direction_correct, row_id),
            )
        conn.commit()
    except Exception as e:
        logger.error("batch_update_earnings_drift_actuals failed (%d rows): %s", len(payloads), e)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def get_earnings_drift_accuracy() -> dict:
    """Per-ticker + overall direction-match hit rates at 1/5/20 trading days — exact SQL shape
    of get_trap_phase_accuracy(), grouped by ticker instead of phase."""
    conn = None
    try:
        conn = get_connection()
        by_ticker = [
            dict(r) for r in conn.execute(
                """SELECT
                    ticker,
                    COUNT(*) AS total,
                    SUM(CASE WHEN direction_correct_1d IS NOT NULL THEN 1 ELSE 0 END) AS resolved_1d,
                    ROUND(AVG(CASE WHEN direction_correct_1d IS NOT NULL
                              THEN direction_correct_1d END) * 100, 1) AS accuracy_1d,
                    SUM(CASE WHEN direction_correct_5d IS NOT NULL THEN 1 ELSE 0 END) AS resolved_5d,
                    ROUND(AVG(CASE WHEN direction_correct_5d IS NOT NULL
                              THEN direction_correct_5d END) * 100, 1) AS accuracy_5d,
                    SUM(CASE WHEN direction_correct_20d IS NOT NULL THEN 1 ELSE 0 END) AS resolved_20d,
                    ROUND(AVG(CASE WHEN direction_correct_20d IS NOT NULL
                              THEN direction_correct_20d END) * 100, 1) AS accuracy_20d
                   FROM earnings_drift_predictions
                   GROUP BY ticker
                   ORDER BY ticker"""
            ).fetchall()
        ]
        overall = dict(conn.execute(
            """SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN direction_correct_1d IS NOT NULL THEN 1 ELSE 0 END) AS resolved_1d,
                ROUND(AVG(CASE WHEN direction_correct_1d IS NOT NULL
                          THEN direction_correct_1d END) * 100, 1) AS accuracy_1d,
                SUM(CASE WHEN direction_correct_5d IS NOT NULL THEN 1 ELSE 0 END) AS resolved_5d,
                ROUND(AVG(CASE WHEN direction_correct_5d IS NOT NULL
                          THEN direction_correct_5d END) * 100, 1) AS accuracy_5d,
                SUM(CASE WHEN direction_correct_20d IS NOT NULL THEN 1 ELSE 0 END) AS resolved_20d,
                ROUND(AVG(CASE WHEN direction_correct_20d IS NOT NULL
                          THEN direction_correct_20d END) * 100, 1) AS accuracy_20d
               FROM earnings_drift_predictions"""
        ).fetchone())
        return {"by_ticker": by_ticker, "overall": overall}
    except Exception as e:
        logger.error("get_earnings_drift_accuracy failed: %s", e)
        return {"by_ticker": [], "overall": {}}
    finally:
        if conn:
            conn.close()


_REGISTRY_COLUMNS = (
    "ticker", "display_name", "region", "asset_type", "exchange", "currency",
    "future_ticker", "future_display_name", "invert_color", "is_pulse_tile",
    "pulse_sort_order", "is_pulse_mobile", "sort_order", "enabled",
    "context_blurb", "baseline_parquet",
)


def get_ticker_registry(enabled_only: bool = True) -> List[dict]:
    """Single source of truth for every index/commodity/FX ticker used by the Markets page and
    Market Pulse — see AGENTS.md central-engine rule on market_ticker_registry."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        query = "SELECT * FROM market_ticker_registry"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY region, sort_order"
        cursor.execute(query)
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Failed to fetch ticker registry: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def get_registry_spot_future_tickers(enabled_only: bool = True) -> List[str]:
    """Pure registry ticker/future_ticker list (no live market-state read), shared by data_engine and markets_engine so neither depends on the other."""
    tickers: List[str] = []
    for row in get_ticker_registry(enabled_only=enabled_only):
        for ticker in (row.get("ticker"), row.get("future_ticker")):
            if ticker:
                tickers.append(ticker)
    return tickers


def get_ticker_registry_row(ticker: str) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM market_ticker_registry WHERE ticker = ?", (ticker,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to fetch ticker registry row for %s: %s", ticker, e)
        return None
    finally:
        if conn:
            conn.close()


def get_ticker_registry_row_by_future(future_ticker: str) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM market_ticker_registry WHERE future_ticker = ?", (future_ticker,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to fetch ticker registry row for future %s: %s", future_ticker, e)
        return None
    finally:
        if conn:
            conn.close()


def get_ticker_registry_row_by_exchange(exchange: str, asset_type: str = "Index") -> Optional[dict]:
    """Canonical index for an exchange, e.g. LSE -> FTSE 100 not FTSE 250, via sort_order."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM market_ticker_registry WHERE exchange = ? AND asset_type = ? "
            "AND enabled = 1 ORDER BY sort_order LIMIT 1",
            (exchange, asset_type),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to fetch ticker registry row for exchange %s: %s", exchange, e)
        return None
    finally:
        if conn:
            conn.close()


def upsert_ticker_registry_row(**fields) -> bool:
    """Insert or fully update one market_ticker_registry row. `ticker` is required; any other
    _REGISTRY_COLUMNS field omitted falls back to its table default on insert, or is left
    untouched on update (omitted from the ON CONFLICT SET clause entirely)."""
    ticker = fields.get("ticker")
    if not ticker:
        logger.error("upsert_ticker_registry_row requires a ticker")
        return False
    cols = [c for c in _REGISTRY_COLUMNS if c in fields]
    if "ticker" not in cols:
        cols.insert(0, "ticker")
    placeholders = ", ".join("?" for _ in cols)
    update_clause = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "ticker")
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"""INSERT INTO market_ticker_registry ({", ".join(cols)}, updated_at)
                VALUES ({placeholders}, datetime('now'))
                ON CONFLICT(ticker) DO UPDATE SET {update_clause}, updated_at = datetime('now')""",
            [fields.get(c) for c in cols],
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error("Failed to upsert ticker registry row for %s: %s", ticker, e)
        return False
    finally:
        if conn:
            conn.close()


def soft_delete_ticker_registry_row(ticker: str) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE market_ticker_registry SET enabled = 0, updated_at = datetime('now') WHERE ticker = ?",
            (ticker,),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("Failed to soft-delete ticker registry row for %s: %s", ticker, e)
        return False
    finally:
        if conn:
            conn.close()


def add_ticker_note(ticker: str, note_text: str) -> Optional[int]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO ticker_notes (ticker, note_text, created_at) VALUES (?, ?, ?)",
            (ticker, note_text, now),
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        logger.error("add_ticker_note failed for %s: %s", ticker, e)
        return None
    finally:
        if conn:
            conn.close()


def get_ticker_notes(ticker: str) -> List[dict]:
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, ticker, note_text, created_at, updated_at FROM ticker_notes "
            "WHERE ticker = ? ORDER BY created_at DESC, id DESC",
            (ticker,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_ticker_notes failed for %s: %s", ticker, e)
        return []
    finally:
        if conn:
            conn.close()


def update_ticker_note(note_id: int, ticker: str, note_text: str) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "UPDATE ticker_notes SET note_text = ?, updated_at = ? WHERE id = ? AND ticker = ?",
            (note_text, now, note_id, ticker),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("update_ticker_note failed for id %s: %s", note_id, e)
        return False
    finally:
        if conn:
            conn.close()


def delete_ticker_note(note_id: int, ticker: str) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM ticker_notes WHERE id = ? AND ticker = ?", (note_id, ticker))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("delete_ticker_note failed for id %s: %s", note_id, e)
        return False
    finally:
        if conn:
            conn.close()


def get_all_ticker_notes_grouped() -> List[dict]:
    """One entry per ticker with its full note history nested, ordered by each ticker's most
    recent note — powers the Ticker Notes report (single query, no per-ticker N+1 fetch)."""
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, ticker, note_text, created_at, updated_at FROM ticker_notes ORDER BY ticker, created_at DESC, id DESC"
        ).fetchall()
        grouped: dict = {}
        order: List[str] = []
        for r in rows:
            ticker = r["ticker"]
            if ticker not in grouped:
                grouped[ticker] = []
                order.append(ticker)
            grouped[ticker].append(dict(r))
        result = [{"ticker": t, "notes": grouped[t]} for t in order]
        result.sort(key=lambda e: e["notes"][0]["created_at"], reverse=True)
        return result
    except Exception as e:
        logger.error("get_all_ticker_notes_grouped failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def get_auction_summary() -> List[dict]:
    """Last 6 Treasury auctions (any maturity) within the last 30 days — the "any weak?" window
    the Treasury Auction Demand banner/sensor is derived from."""
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT maturity_label, auction_date, bid_to_cover, tail_bp, alert_fired
               FROM treasury_auction_results
               WHERE auction_date >= date('now', '-30 days')
               ORDER BY auction_date DESC, maturity_label ASC
               LIMIT 6"""
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_auction_summary failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()
