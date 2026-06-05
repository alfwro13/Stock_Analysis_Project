# score_analysis.py
import json
import logging
import os
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from config import PORTFOLIO_PATH, WATCHLIST_PATH
from database import get_connection
from insider_engine import get_tickers_from_json

logger = logging.getLogger(__name__)

_HORIZONS = {"3m": 90, "6m": 180, "12m": 365}


def _available_from(earliest: str, days: int) -> str:
    return (date.fromisoformat(earliest) + timedelta(days=days)).isoformat()


def _compute_return(entry_price: float, future_price: Optional[float]) -> Optional[float]:
    if entry_price and future_price:
        return round((future_price - entry_price) / entry_price * 100, 2)
    return None


def _build_ticker_accounts_map() -> dict[str, list[str]]:
    """Returns a mapping of ticker -> list of account names from portfolio.json."""
    if not os.path.exists(PORTFOLIO_PATH):
        return {}
    try:
        with open(PORTFOLIO_PATH, "r") as f:
            data = json.load(f)
        result = {}
        for entry in data.values():
            ticker = entry.get("ticker")
            accounts = [a.get("name", "") for a in entry.get("accounts", []) if a.get("name")]
            if ticker and accounts:
                result[ticker] = accounts
        return result
    except Exception:
        return {}


def get_score_analysis(filter_name: str = "all") -> dict:
    """
    Returns forward-returns analysis from score_history joined with quant_signals prices.
    filter_name: "all" | "portfolio" | "watchlist"
    """
    filter_tickers: Optional[list] = None
    ticker_accounts: dict[str, list[str]] = {}
    if filter_name == "portfolio":
        filter_tickers = get_tickers_from_json(PORTFOLIO_PATH, is_watchlist=False) or None
        ticker_accounts = _build_ticker_accounts_map()
    elif filter_name == "watchlist":
        filter_tickers = get_tickers_from_json(WATCHLIST_PATH, is_watchlist=True) or None

    conn = get_connection()
    try:
        cursor = conn.cursor()

        if filter_tickers:
            placeholders = ",".join("?" * len(filter_tickers))
            cursor.execute(
                f"SELECT ticker, date, score, signal, close_price FROM score_history "
                f"WHERE ticker IN ({placeholders}) ORDER BY date DESC",
                filter_tickers,
            )
        else:
            cursor.execute(
                "SELECT ticker, date, score, signal, close_price FROM score_history ORDER BY date DESC"
            )
        rows = [dict(r) for r in cursor.fetchall()]

        if not rows:
            return {
                "earliest_date": None,
                "total_events": 0,
                "horizons": {k: {"days": v, "available_from": None, "ready": False} for k, v in _HORIZONS.items()},
                "summary": [],
                "events": [],
            }

        tickers_needed = list({r["ticker"] for r in rows})
        placeholders = ",".join("?" * len(tickers_needed))
        cursor.execute(
            f"SELECT ticker, date, close_price FROM quant_signals "
            f"WHERE ticker IN ({placeholders}) AND close_price IS NOT NULL ORDER BY ticker, date",
            tickers_needed,
        )
        price_rows = cursor.fetchall()
    finally:
        conn.close()

    earliest_date = min(r["date"] for r in rows)
    today_str = date.today().isoformat()

    horizons_meta = {
        k: {"days": v, "available_from": _available_from(earliest_date, v), "ready": _available_from(earliest_date, v) <= today_str}
        for k, v in _HORIZONS.items()
    }

    # Build per-ticker price series for fast forward-return lookups
    prices_df = pd.DataFrame([dict(r) for r in price_rows])
    price_lookup: dict[str, pd.Series] = {}
    if not prices_df.empty:
        for ticker, grp in prices_df.groupby("ticker"):
            price_lookup[ticker] = grp.set_index("date")["close_price"].sort_index()

    def forward_price(ticker: str, entry_date: str, days: int) -> Optional[float]:
        if ticker not in price_lookup:
            return None
        s = price_lookup[ticker]
        lo = (date.fromisoformat(entry_date) + timedelta(days=days - 3)).isoformat()
        hi = (date.fromisoformat(entry_date) + timedelta(days=days + 7)).isoformat()
        window = s[(s.index >= lo) & (s.index <= hi)]
        return float(window.iloc[0]) if not window.empty else None

    # Compute returns for all rows (used for summary aggregation)
    enriched: list[dict] = []
    for row in rows:
        entry = row["close_price"]
        r3m = _compute_return(entry, forward_price(row["ticker"], row["date"], 90))
        r6m = _compute_return(entry, forward_price(row["ticker"], row["date"], 180))
        r12m = _compute_return(entry, forward_price(row["ticker"], row["date"], 365))
        accounts = ticker_accounts.get(row["ticker"], [])
        enriched.append({**row, "return_3m": r3m, "return_6m": r6m, "return_12m": r12m,
                         "accounts": accounts})

    # Summary grouped by signal bucket
    signal_order = ["STRONG BUY", "BULLISH / HOLD", "NEUTRAL", "BEARISH / CAUTION", "STRONG SELL", "TOXIC / AVOID"]

    def _avg(vals: list) -> Optional[float]:
        v = [x for x in vals if x is not None]
        return round(sum(v) / len(v), 2) if v else None

    summary = []
    for sig in signal_order:
        bucket = [e for e in enriched if e["signal"] == sig]
        if not bucket:
            continue
        r3 = [e["return_3m"] for e in bucket]
        r6 = [e["return_6m"] for e in bucket]
        r12 = [e["return_12m"] for e in bucket]
        summary.append({
            "signal": sig,
            "count": len(bucket),
            "avg_3m": _avg(r3),
            "avg_6m": _avg(r6),
            "avg_12m": _avg(r12),
            "n_3m": sum(1 for x in r3 if x is not None),
            "n_6m": sum(1 for x in r6 if x is not None),
            "n_12m": sum(1 for x in r12 if x is not None),
        })

    return {
        "earliest_date": earliest_date,
        "total_events": len(rows),
        "horizons": horizons_meta,
        "summary": summary,
        "events": enriched[:500],
    }
