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


def _trading_windows_batch(tickers: list[str], conn, as_of: Optional[str] = None) -> dict[str, set]:
    """Each ticker's last 5 trading days, derived from quant_signals (written every trading day
    the nightly quant scan runs) rather than from pattern_detection_history's own scan_date
    column — pattern_detection_engine.PatternDetectionEngine.run_scan() deliberately skips
    logging a history row when a pattern instance is unchanged from the previous scan, so that
    table's own distinct dates are sparse and can silently span far more than 5 trading days for
    a quiet ticker. trap_phase_history logs unconditionally every scan so it doesn't have this
    problem on its own, but sharing one window keeps both sources' cutoffs identical.

    as_of, when given (a 'YYYY-MM-DD' string), reconstructs the window as it stood on that
    historical date instead of today — used by the Cross-Engine Alert Referee's historical
    backfill to score an already-resolved trap_phase_history/pattern_detection_history row with
    the pillar votes that were actually available at the time, not today's."""
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    as_of_dt = datetime.strptime(as_of, "%Y-%m-%d") if as_of else datetime.now(timezone.utc)
    # 30 calendar days comfortably covers 5 trading days through any holiday stretch, without
    # pulling a ticker's full multi-year quant_signals history just to keep the last 5 dates.
    recent_cutoff = (as_of_dt - timedelta(days=30)).strftime("%Y-%m-%d")
    params = tickers + [recent_cutoff]
    upper_bound_sql = ""
    if as_of:
        upper_bound_sql = " AND date <= ?"
        params.append(as_of)
    trading_dates = conn.execute(
        f"SELECT DISTINCT ticker, date AS scan_date FROM quant_signals WHERE ticker IN ({placeholders}) AND date >= ?{upper_bound_sql}",
        params,
    ).fetchall()
    return _recent_window_dates([dict(r) for r in trading_dates])


def _pattern_signals_batch(tickers: list[str], conn, trading_windows: dict[str, set]) -> dict[str, list[str]]:
    """Confirmed Pattern Detection results only, windowed. Any registered family — new families
    need zero changes here since direction is resolved via each family's own
    DETECTORS[family].PATTERN_TYPES dict."""
    from pattern_detection_engine import DETECTORS

    signals: dict[str, list[str]] = {t: [] for t in tickers}
    if not tickers:
        return signals
    placeholders = ",".join("?" * len(tickers))
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
    return signals


def _trap_signals_batch(tickers: list[str], conn, trading_windows: dict[str, set]) -> dict[str, list[str]]:
    """Trap Monitor phase only, windowed."""
    from bull_bear_trap_engine import _PHASE_EXPECTED_DIRECTION

    signals: dict[str, list[str]] = {t: [] for t in tickers}
    if not tickers:
        return signals
    placeholders = ",".join("?" * len(tickers))
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


def _technical_signals_batch(tickers: list[str], conn, as_of: Optional[str] = None) -> dict[str, list[str]]:
    """Idea A's Technical pillar — Pattern Detection and Trap Monitor merged into one vote per
    ticker. compute_regime_weighted_score_batch() needs the same two sources kept separate
    (they're weighted independently there), so it calls _pattern_signals_batch()/
    _trap_signals_batch() directly instead of this merged view."""
    if not tickers:
        return {}
    trading_windows = _trading_windows_batch(tickers, conn, as_of=as_of)
    pattern_signals = _pattern_signals_batch(tickers, conn, trading_windows)
    trap_signals = _trap_signals_batch(tickers, conn, trading_windows)
    return {t: pattern_signals.get(t, []) + trap_signals.get(t, []) for t in tickers}


def _statistical_signals_batch(tickers: list[str], conn, as_of: Optional[str] = None) -> dict[str, list[str]]:
    """earnings_volatility_history drift sign, gated on a real mispricing edge (edge_score>0),
    windowed to each ticker's last 5 scan runs (earnings_vol_engine only scans a ticker within
    ~14 days of its next earnings date, so these 5 rows may span several weeks of calendar
    time for a ticker that isn't near-term). as_of bounds the window to a historical date for
    the Cross-Engine Alert Referee's historical backfill instead of "most recent 5"."""
    signals: dict[str, list[str]] = {t: [] for t in tickers}
    if not tickers:
        return signals
    placeholders = ",".join("?" * len(tickers))
    params: list = list(tickers)
    as_of_sql = ""
    if as_of:
        as_of_sql = " AND scan_date <= ?"
        params.append(as_of)
    rows = conn.execute(
        f"""SELECT ticker, scan_date, edge_score, drift_avg_pct_5d FROM earnings_volatility_history
            WHERE ticker IN ({placeholders}){as_of_sql} ORDER BY ticker, scan_date DESC""",
        params,
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


def _ml_signals_batch(tickers: list[str], conn, as_of: Optional[str] = None) -> dict[str, list[str]]:
    """quant_signals.ml_confidence_score across each ticker's last 5 trading days. as_of bounds
    the window to a historical date for the Cross-Engine Alert Referee's historical backfill."""
    signals: dict[str, list[str]] = {t: [] for t in tickers}
    if not tickers:
        return signals
    placeholders = ",".join("?" * len(tickers))
    params: list = list(tickers)
    as_of_sql = ""
    if as_of:
        as_of_sql = " AND date <= ?"
        params.append(as_of)
    rows = conn.execute(
        f"""SELECT ticker, date, ml_confidence_score FROM quant_signals
            WHERE ticker IN ({placeholders}) AND ml_confidence_score IS NOT NULL{as_of_sql}
            ORDER BY ticker, date DESC""",
        params,
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


def evaluate_pillar_confluence_batch(tickers: list[str], as_of: Optional[str] = None) -> dict[str, dict]:
    """Per-ticker Signal Pillar Confluence: bullish confluence = >=2 of {technical, statistical,
    ml} pillars vote 'up' and none votes 'down' within a 5-trading-day rolling window (bearish
    confluence is the mirror case). Each pillar's own vote requires directional agreement across
    every signal it saw in the window — see _pillar_vote(). Batched (one query per source table,
    not per ticker) since Portfolio/Watchlist call this for every held/watched ticker at once.
    as_of ('YYYY-MM-DD'), when given, reconstructs confluence as of that historical date rather
    than today — used by the Cross-Engine Alert Referee's historical backfill."""
    tickers = list(dict.fromkeys(tickers))
    if not tickers:
        return {}

    conn = None
    try:
        conn = get_connection()
        technical = _technical_signals_batch(tickers, conn, as_of=as_of)
        statistical = _statistical_signals_batch(tickers, conn, as_of=as_of)
        ml = _ml_signals_batch(tickers, conn, as_of=as_of)
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


def evaluate_pillar_confluence_as_of(ticker: str, as_of_date: str) -> dict:
    """Single-ticker Pillar Confluence reconstructed as of a historical date (YYYY-MM-DD) rather
    than today — used by alert_referee_engine.backfill_historical_confluence_features() to score
    an already-resolved trap_phase_history/pattern_detection_history row with the pillar votes
    that were actually available at the time."""
    return evaluate_pillar_confluence_batch([ticker], as_of=as_of_date).get(
        ticker, {"bullish_pillars": [], "bearish_pillars": [], "confluence": False, "direction": None}
    )


def pillar_confluence_label(result: Optional[dict]) -> Optional[str]:
    if not result or not result.get("confluence"):
        return None
    pillars = result["bullish_pillars"] if result["direction"] == "bullish" else result["bearish_pillars"]
    return f"{result['direction'].capitalize()} ({len(pillars)}/3)"


_REGIME_WEIGHTED_SCORE_REGIMES = ("Bull", "Chop")


def _direction_to_100(signals: list[str]) -> float:
    vote = _pillar_vote(signals)
    return {"up": 100.0, "down": 0.0}.get(vote, 50.0)


def _regime_as_of(as_of: str) -> Optional[dict]:
    """Most recent market_regimes row on or before as_of ('YYYY-MM-DD') — the historical
    equivalent of regime_engine.get_latest_regime() for the Cross-Engine Alert Referee's
    historical backfill. META_SCORING.REGIME_WEIGHTS itself is not versioned historically —
    the backfill uses today's config as an approximation, same as every other config value in
    this app has no point-in-time history."""
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM market_regimes WHERE date <= ? ORDER BY date DESC LIMIT 1", (as_of,)
        ).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("_regime_as_of failed for %s: %s", as_of, e)
        return None
    finally:
        if conn:
            conn.close()


def compute_regime_weighted_score_batch(tickers: list[str], as_of: Optional[str] = None) -> dict[str, Optional[dict]]:
    """Regime-Weighted Conviction Score: a 0-100 blend of stock_signals.composite_score,
    stock_signals.ml_confidence, and Idea A's two Technical-pillar sources (Pattern Detection,
    Trap Monitor — kept separate here since the weight vector moves them independently, unlike
    Idea A's merged single vote), weighted by the current market regime
    (regime_engine.get_latest_regime()'s price_hmm_label).

    Every ticker maps to None (no signal) rather than a reweighted number whenever: the regime
    is Crash, the Isolation Forest market_stress_score is at or above the configured threshold,
    no regime has been computed yet, or the ticker itself is missing composite_score/ml_confidence
    (never scanned/never run through ML inference). None of these are a "bug" — a hard veto is
    more honest than fabricating a number the underlying inputs were never validated to support.

    as_of ('YYYY-MM-DD'), when given, reconstructs the score as of that historical date instead
    of today — regime and composite_score/ml_confidence are sourced from market_regimes/
    quant_signals' own per-date history rather than the latest-only regime_engine/stock_signals
    lookups. Used by the Cross-Engine Alert Referee's historical backfill."""
    from config import load_config
    from regime_engine import get_latest_regime

    tickers = list(dict.fromkeys(tickers))
    results: dict[str, Optional[dict]] = {t: None for t in tickers}
    if not tickers:
        return results

    regime_row = _regime_as_of(as_of) if as_of else get_latest_regime()
    regime = regime_row.get("price_hmm_label") if regime_row else None
    market_stress_score = regime_row.get("market_stress_score") if regime_row else None

    meta_cfg = load_config().get("META_SCORING", {})
    stress_threshold = meta_cfg.get("CRASH_VETO", {}).get("MARKET_STRESS_THRESHOLD", 0.75)
    crash_veto = regime == "Crash" or (market_stress_score is not None and market_stress_score >= stress_threshold)

    weights = None
    if not crash_veto and regime in _REGIME_WEIGHTED_SCORE_REGIMES:
        weights = meta_cfg.get("REGIME_WEIGHTS", {}).get(regime)
    if weights is None:
        return results

    conn = None
    try:
        conn = get_connection()
        placeholders = ",".join("?" * len(tickers))
        if as_of:
            base = {}
            for ticker in tickers:
                row = conn.execute(
                    """SELECT composite_score, ml_confidence_score AS ml_confidence FROM quant_signals
                       WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1""",
                    (ticker, as_of),
                ).fetchone()
                if row:
                    base[ticker] = dict(row)
        else:
            base_rows = conn.execute(
                f"SELECT ticker, composite_score, ml_confidence FROM stock_signals WHERE ticker IN ({placeholders})",
                tickers,
            ).fetchall()
            base = {row["ticker"]: dict(row) for row in base_rows}

        trading_windows = _trading_windows_batch(tickers, conn, as_of=as_of)
        pattern_signals = _pattern_signals_batch(tickers, conn, trading_windows)
        trap_signals = _trap_signals_batch(tickers, conn, trading_windows)
    except Exception as e:
        logger.error("compute_regime_weighted_score_batch failed: %s", e)
        return results
    finally:
        if conn:
            conn.close()

    for ticker in tickers:
        row = base.get(ticker)
        if row is None or row.get("composite_score") is None or row.get("ml_confidence") is None:
            continue

        components = {
            "composite_score": float(row["composite_score"]),
            "ml_confidence": float(row["ml_confidence"]),
            "pattern": _direction_to_100(pattern_signals.get(ticker, [])),
            "trap": _direction_to_100(trap_signals.get(ticker, [])),
        }
        # weights.get(..., 0.0) rather than weights[key] — META_SCORING.REGIME_WEIGHTS is
        # user-editable config, not internal state; a manually-edited config.json missing one
        # weight key must not crash the whole Portfolio/Watchlist page for every ticker.
        score = sum(components[key] * weights.get(key, 0.0) for key in components)
        results[ticker] = {"score": round(score, 1), "regime": regime, "components": components}

    return results


def compute_regime_weighted_score(ticker: str) -> Optional[dict]:
    return compute_regime_weighted_score_batch([ticker]).get(ticker)


def compute_regime_weighted_score_as_of(ticker: str, as_of_date: str) -> Optional[dict]:
    """Single-ticker Regime-Weighted Conviction Score reconstructed as of a historical date
    (YYYY-MM-DD) — used by alert_referee_engine.backfill_historical_confluence_features()."""
    return compute_regime_weighted_score_batch([ticker], as_of=as_of_date).get(ticker)
