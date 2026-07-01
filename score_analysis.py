import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from database import get_connection, get_watchlist_tickers

logger = logging.getLogger(__name__)

_HORIZONS = {"3m": 90, "6m": 180, "12m": 365}


def _available_from(earliest: str, days: int) -> str:
    return (date.fromisoformat(earliest) + timedelta(days=days)).isoformat()


def _compute_return(entry_price: float, future_price: Optional[float]) -> Optional[float]:
    if entry_price and future_price:
        return round((future_price - entry_price) / entry_price * 100, 2)
    return None


def _build_ticker_accounts_map(combined_holdings: dict) -> dict[str, list[str]]:
    result = {}
    for entry in combined_holdings.values():
        ticker = entry.get("ticker")
        accounts = [a.get("name", "") for a in entry.get("accounts", []) if a.get("name")]
        if ticker and accounts:
            result[ticker] = accounts
    return result


def get_score_analysis(filter_name: str = "all") -> dict:
    """Forward-returns analysis from score_history; filter_name: 'all' | 'portfolio' | 'watchlist'."""
    from accounts_engine import get_combined_holdings
    filter_tickers: Optional[list] = None
    ticker_accounts: dict[str, list[str]] = {}
    if filter_name == "portfolio":
        combined = get_combined_holdings()
        filter_tickers = list(combined.keys()) or None
        ticker_accounts = _build_ticker_accounts_map(combined)
    elif filter_name == "watchlist":
        filter_tickers = get_watchlist_tickers() or None

    conn = None
    try:
        conn = get_connection()
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
        if conn:
            conn.close()

    earliest_date = min(r["date"] for r in rows)
    today_str = datetime.now(timezone.utc).date().isoformat()

    horizons_meta = {
        k: {"days": v, "available_from": _available_from(earliest_date, v), "ready": _available_from(earliest_date, v) <= today_str}
        for k, v in _HORIZONS.items()
    }

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

    enriched: list[dict] = []
    for row in rows:
        entry = row["close_price"]
        r3m = _compute_return(entry, forward_price(row["ticker"], row["date"], 90))
        r6m = _compute_return(entry, forward_price(row["ticker"], row["date"], 180))
        r12m = _compute_return(entry, forward_price(row["ticker"], row["date"], 365))
        accounts = ticker_accounts.get(row["ticker"], [])
        enriched.append({**row, "return_3m": r3m, "return_6m": r6m, "return_12m": r12m,
                         "accounts": accounts})

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
