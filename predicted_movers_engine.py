from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from accounts_engine import current_price_map
from constants import PREDICTION_HORIZON_DAYS
from database import get_connection
from db_helpers import (
    batch_update_predicted_movers_actuals,
    get_company_names,
    get_portfolio_watchlist_tickers,
    get_predicted_movers_accuracy,
    get_unresolved_predicted_movers,
    get_universe_tickers,
)
from pairs_spread_engine import SCOPE_PORTFOLIO_WATCHLIST, SCOPE_UNIVERSE
from utils import ignored_tickers_set, is_excluded_from_yahoo_fetch

logger = logging.getLogger(__name__)

# GUI name: "Predicted Movers". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

SORT_GAINERS = "gainers"
SORT_LOSERS = "losers"
SORT_MOVERS = "movers"


def _get_scope_tickers(scope: str) -> list[str]:
    if scope == SCOPE_UNIVERSE:
        tickers = set(get_universe_tickers())
        ignored = ignored_tickers_set()
        return sorted(
            t.upper() for t in tickers
            if t and not is_excluded_from_yahoo_fetch(t, ignored)
        )
    return get_portfolio_watchlist_tickers()


def _latest_quantile_rows(tickers: list[str]) -> list[dict]:
    """Latest quant_signals row per ticker with non-null price_q10/price_q90 — the same
    inline correlated-subquery idiom used elsewhere in the codebase for 'latest row per
    ticker' (ai_prediction_engine.py, page_routes.py, market_pulse.py)."""
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
        logger.error("_latest_quantile_rows failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def get_leaderboard(
    scope: str = SCOPE_PORTFOLIO_WATCHLIST,
    sort_mode: str = SORT_MOVERS,
    limit: int = 200,
) -> list[dict]:
    """Live, on-demand ranking by ML-predicted 10-trading-day forward % move — no persisted
    results table, this is recomputed on every call since it's a cheap read over
    already-computed quant_signals columns."""
    tickers = _get_scope_tickers(scope)
    if not tickers:
        return []

    quantile_rows = _latest_quantile_rows(tickers)
    if not quantile_rows:
        return []

    priced = current_price_map([r["ticker"] for r in quantile_rows])
    company_names = get_company_names([r["ticker"] for r in quantile_rows])

    results = []
    for row in quantile_rows:
        ticker = row["ticker"]
        price_info = priced.get(ticker)
        if not price_info or not price_info[0]:
            continue
        current_price, currency = price_info
        predicted_mid = (row["price_q10"] + row["price_q90"]) / 2.0
        predicted_move_pct = (predicted_mid - current_price) / current_price * 100.0
        results.append({
            "ticker": ticker,
            "company_name": company_names.get(ticker),
            "current_price": current_price,
            "currency": currency,
            "quant_signals_date": row["date"],
            "close_price": row["close_price"],
            "price_q10": row["price_q10"],
            "price_q90": row["price_q90"],
            "predicted_mid": predicted_mid,
            "predicted_move_pct": predicted_move_pct,
        })

    if sort_mode == SORT_GAINERS:
        results.sort(key=lambda r: r["predicted_move_pct"], reverse=True)
    elif sort_mode == SORT_LOSERS:
        results.sort(key=lambda r: r["predicted_move_pct"])
    else:
        results.sort(key=lambda r: abs(r["predicted_move_pct"]), reverse=True)

    return results[:limit]


def _target_date(predicted_date: str) -> str:
    """Approximate 'PREDICTION_HORIZON_DAYS trading days forward' of predicted_date using
    numpy's Mon-Fri business-day calendar (no exchange-holiday awareness). Acceptable because
    the only consumer is backfill_actual_outcomes()'s 'first quant_signals close on/after
    target_date' lookup, which self-corrects for the few-day slack a missed holiday
    introduces."""
    d = np.datetime64(predicted_date, "D")
    return str(np.busday_offset(d, PREDICTION_HORIZON_DAYS, roll="forward"))


def log_predictions(tickers: Optional[list[str]] = None) -> int:
    """Logs today's quantile prediction for each Portfolio+Watchlist ticker into
    predicted_movers_history — must run the same day score_quantile_predictions() runs, since
    quant_signals.price_q10/price_q90 are overwritten in place with no history kept. Safe to
    re-run same-day (INSERT OR IGNORE on UNIQUE(ticker, predicted_date))."""
    if tickers is None:
        tickers = get_portfolio_watchlist_tickers()
    if not tickers:
        return 0

    rows = _latest_quantile_rows(tickers)
    if not rows:
        return 0

    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    conn = None
    try:
        conn = get_connection()
        for row in rows:
            target_date = _target_date(row["date"])
            cursor = conn.execute(
                """INSERT OR IGNORE INTO predicted_movers_history
                   (ticker, predicted_date, predicted_ts, close_price, price_q10, price_q90, target_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (row["ticker"], row["date"], now_ts, row["close_price"],
                 row["price_q10"], row["price_q90"], target_date),
            )
            if cursor.rowcount:
                inserted += 1
        conn.commit()
    except Exception as e:
        logger.error("log_predictions failed: %s", e)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
    return inserted


def backfill_actual_outcomes() -> int:
    """Resolves every predicted_movers_history row whose target_date has passed, using the
    first quant_signals close on/after target_date (mirrors bubble_radar_engine's resolution
    idiom) — scans the whole unresolved set each run, not just the newest (mirrors
    bull_bear_trap_engine's catch-up discipline)."""
    cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pending = get_unresolved_predicted_movers(cutoff)
    if not pending:
        return 0

    conn = None
    payloads: list[tuple[int, float, str, int, int]] = []
    try:
        conn = get_connection()
        for row in pending:
            future = conn.execute(
                """SELECT date, close_price FROM quant_signals
                   WHERE ticker=? AND date>=? ORDER BY date ASC LIMIT 1""",
                (row["ticker"], row["target_date"]),
            ).fetchone()
            if not future or future["close_price"] is None:
                continue
            actual_price = future["close_price"]
            actual_date = future["date"]
            close_price = row["close_price"]
            predicted_mid = (row["price_q10"] + row["price_q90"]) / 2.0
            direction_correct = 1 if np.sign(predicted_mid - close_price) == np.sign(actual_price - close_price) and actual_price != close_price else 0
            within_band_correct = 1 if row["price_q10"] <= actual_price <= row["price_q90"] else 0
            payloads.append((row["id"], actual_price, actual_date, direction_correct, within_band_correct))
    except Exception as e:
        logger.error("backfill_actual_outcomes failed while resolving actuals: %s", e)
    finally:
        if conn:
            conn.close()

    batch_update_predicted_movers_actuals(payloads)
    return len(payloads)


def get_accuracy_summary() -> dict:
    data = get_predicted_movers_accuracy()
    tickers = [r["ticker"] for r in data.get("by_ticker", [])]
    names = get_company_names(tickers) if tickers else {}
    for row in data.get("by_ticker", []):
        row["company_name"] = names.get(row["ticker"])
    return data
