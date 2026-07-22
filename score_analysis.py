import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from database import get_connection, get_watchlist_tickers

logger = logging.getLogger(__name__)

_HORIZONS = {"3m": 90, "6m": 180, "12m": 365}

_CONFLUENCE_WINDOW = 5
_ML_CONFIDENCE_BULLISH = 50


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


def _pillar_vote(signals: list[str]) -> Optional[str]:
    """A pillar votes 'up'/'down' only when every signal it saw in the window agrees;
    an empty or directionally-mixed window abstains rather than forcing a side."""
    unique = set(signals)
    return unique.pop() if len(unique) == 1 else None


def _recent_window_dates(rows: list[dict], window: int = _CONFLUENCE_WINDOW) -> dict[str, set]:
    """rows: [{ticker, scan_date}, ...] (need not be deduped) -> {ticker: last N distinct
    scan_date strings}. scan_date rows only exist for days a scan actually ran, so this is
    a rolling window of trading days, not calendar days."""
    dates_by_ticker: dict[str, set] = {}
    for row in rows:
        dates_by_ticker.setdefault(row["ticker"], set()).add(row["scan_date"])
    return {t: set(sorted(dates, reverse=True)[:window]) for t, dates in dates_by_ticker.items()}


def _technical_signals_batch(tickers: list[str], conn) -> dict[str, list[str]]:
    """Pattern Detection (any registered family — new families need zero changes here since
    direction is resolved via each family's own DETECTORS[family].PATTERN_TYPES dict) plus
    Trap Monitor phase, both windowed to each ticker's last 5 trading days.

    The window itself is derived from quant_signals, not from pattern_detection_history's own
    scan_date column — pattern_detection_engine.PatternDetectionEngine.run_scan() deliberately
    skips logging a history row when a pattern instance is unchanged from the previous scan, so
    that table's own distinct dates are sparse and can silently span far more than 5 trading days
    for a quiet ticker. quant_signals is written every trading day the nightly quant scan runs
    (the same daily cadence Pattern Detection and Trap Monitor themselves scan on), so it's the
    reliable trading-day calendar to filter both sources against."""
    from pattern_detection_engine import DETECTORS
    from bull_bear_trap_engine import _PHASE_EXPECTED_DIRECTION

    signals: dict[str, list[str]] = {t: [] for t in tickers}
    if not tickers:
        return signals
    placeholders = ",".join("?" * len(tickers))

    # 30 calendar days comfortably covers 5 trading days through any holiday stretch, without
    # pulling a ticker's full multi-year quant_signals history just to keep the last 5 dates.
    recent_cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    trading_dates = conn.execute(
        f"SELECT DISTINCT ticker, date AS scan_date FROM quant_signals WHERE ticker IN ({placeholders}) AND date >= ?",
        tickers + [recent_cutoff],
    ).fetchall()
    trading_windows = _recent_window_dates([dict(r) for r in trading_dates])

    confirmed_rows = conn.execute(
        f"""SELECT ticker, pattern_family, pattern_type, scan_date FROM pattern_detection_history
            WHERE ticker IN ({placeholders}) AND phase = 'CONFIRMED'""",
        tickers,
    ).fetchall()
    for row in confirmed_rows:
        row = dict(row)
        if row["scan_date"] not in trading_windows.get(row["ticker"], set()):
            continue
        module = DETECTORS.get(row["pattern_family"])
        if module is None:
            continue
        direction = module.PATTERN_TYPES.get(row["pattern_type"])
        if direction:
            signals[row["ticker"]].append(direction)

    trap_rows = conn.execute(
        f"SELECT ticker, phase, scan_date FROM trap_phase_history WHERE ticker IN ({placeholders})",
        tickers,
    ).fetchall()
    for row in trap_rows:
        row = dict(row)
        if row["scan_date"] not in trading_windows.get(row["ticker"], set()):
            continue
        direction = _PHASE_EXPECTED_DIRECTION.get(row["phase"])
        if direction:
            signals[row["ticker"]].append(direction)

    return signals


def _statistical_signals_batch(tickers: list[str], conn) -> dict[str, list[str]]:
    """earnings_volatility_history drift sign, gated on a real mispricing edge (edge_score>0),
    windowed to each ticker's last 5 scan runs (earnings_vol_engine only scans a ticker within
    ~14 days of its next earnings date, so these 5 rows may span several weeks of calendar
    time for a ticker that isn't near-term)."""
    signals: dict[str, list[str]] = {t: [] for t in tickers}
    if not tickers:
        return signals
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""SELECT ticker, scan_date, edge_score, drift_avg_pct_5d FROM earnings_volatility_history
            WHERE ticker IN ({placeholders}) ORDER BY ticker, scan_date DESC""",
        tickers,
    ).fetchall()

    counts: dict[str, int] = {}
    for row in rows:
        row = dict(row)
        ticker = row["ticker"]
        counts[ticker] = counts.get(ticker, 0) + 1
        if counts[ticker] > _CONFLUENCE_WINDOW:
            continue
        if row["edge_score"] is None or row["edge_score"] <= 0 or row["drift_avg_pct_5d"] is None:
            continue
        if row["drift_avg_pct_5d"] > 0:
            signals[ticker].append("up")
        elif row["drift_avg_pct_5d"] < 0:
            signals[ticker].append("down")

    return signals


def _ml_signals_batch(tickers: list[str], conn) -> dict[str, list[str]]:
    """quant_signals.ml_confidence_score across each ticker's last 5 trading days."""
    signals: dict[str, list[str]] = {t: [] for t in tickers}
    if not tickers:
        return signals
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""SELECT ticker, date, ml_confidence_score FROM quant_signals
            WHERE ticker IN ({placeholders}) AND ml_confidence_score IS NOT NULL
            ORDER BY ticker, date DESC""",
        tickers,
    ).fetchall()

    counts: dict[str, int] = {}
    for row in rows:
        row = dict(row)
        ticker = row["ticker"]
        counts[ticker] = counts.get(ticker, 0) + 1
        if counts[ticker] > _CONFLUENCE_WINDOW:
            continue
        score = row["ml_confidence_score"]
        if score > _ML_CONFIDENCE_BULLISH:
            signals[ticker].append("up")
        elif score < _ML_CONFIDENCE_BULLISH:
            signals[ticker].append("down")

    return signals


def evaluate_pillar_confluence_batch(tickers: list[str]) -> dict[str, dict]:
    """Per-ticker Signal Pillar Confluence: bullish confluence = >=2 of {technical, statistical,
    ml} pillars vote 'up' and none votes 'down' within a 5-trading-day rolling window (bearish
    confluence is the mirror case). Each pillar's own vote requires directional agreement across
    every signal it saw in the window — see _pillar_vote(). Batched (one query per source table,
    not per ticker) since Portfolio/Watchlist call this for every held/watched ticker at once."""
    tickers = list(dict.fromkeys(tickers))
    if not tickers:
        return {}

    conn = None
    try:
        conn = get_connection()
        technical = _technical_signals_batch(tickers, conn)
        statistical = _statistical_signals_batch(tickers, conn)
        ml = _ml_signals_batch(tickers, conn)
    except Exception as e:
        logger.error("evaluate_pillar_confluence_batch failed: %s", e)
        return {}
    finally:
        if conn:
            conn.close()

    results: dict[str, dict] = {}
    for ticker in tickers:
        votes = {
            "technical": _pillar_vote(technical.get(ticker, [])),
            "statistical": _pillar_vote(statistical.get(ticker, [])),
            "ml": _pillar_vote(ml.get(ticker, [])),
        }
        bullish_pillars = [name for name, vote in votes.items() if vote == "up"]
        bearish_pillars = [name for name, vote in votes.items() if vote == "down"]

        direction: Optional[str] = None
        if len(bullish_pillars) >= 2 and not bearish_pillars:
            direction = "bullish"
        elif len(bearish_pillars) >= 2 and not bullish_pillars:
            direction = "bearish"

        results[ticker] = {
            "bullish_pillars": bullish_pillars,
            "bearish_pillars": bearish_pillars,
            "confluence": direction is not None,
            "direction": direction,
        }
    return results


def evaluate_pillar_confluence(ticker: str) -> dict:
    return evaluate_pillar_confluence_batch([ticker]).get(
        ticker, {"bullish_pillars": [], "bearish_pillars": [], "confluence": False, "direction": None}
    )


def pillar_confluence_label(result: Optional[dict]) -> Optional[str]:
    if not result or not result.get("confluence"):
        return None
    pillars = result["bullish_pillars"] if result["direction"] == "bullish" else result["bearish_pillars"]
    return f"{result['direction'].capitalize()} ({len(pillars)}/3)"
