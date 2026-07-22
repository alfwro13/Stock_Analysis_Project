from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from data_engine import load_or_fetch_daily_history
from database import get_connection
from db_helpers import get_portfolio_watchlist_tickers, get_ticker_currency_map, get_universe_tickers
from utils import ignored_tickers_set, is_excluded_from_yahoo_fetch
from xray_engine import fetch_close_returns_from_parquet

logger = logging.getLogger(__name__)

# GUI name: "Pairs Spread Monitor". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

LOOKBACK_DAYS = 252
MIN_OVERLAP_DAYS = 60

SCOPE_PORTFOLIO_WATCHLIST = "portfolio_watchlist"
SCOPE_UNIVERSE = "universe"


def _normalize_currency(currency: Optional[str]) -> Optional[str]:
    """GBp (LSE pence) and GBP both mean "British Pounds" for pairing purposes — Yahoo returns either depending on the ticker."""
    if not currency:
        return None
    return "GBP" if currency in ("GBp", "GBP") else currency


def _load_close(ticker: str) -> Optional[pd.Series]:
    df = load_or_fetch_daily_history(ticker)
    if df is None or "Close" not in df.columns:
        return None
    return df["Close"].tail(LOOKBACK_DAYS)


def _aligned_closes(
    ticker_a: str, ticker_b: str,
    close_a: Optional[pd.Series] = None, close_b: Optional[pd.Series] = None,
) -> Optional[pd.DataFrame]:
    """Aligned trailing daily closes for both tickers — the shared calc behind the scan,
    the on-demand chart, and the spread z-score. Pass pre-loaded closes (e.g. from a scan's
    own price cache) to avoid re-reading the same ticker's parquet for every pair it appears in."""
    if close_a is None:
        close_a = _load_close(ticker_a)
    if close_b is None:
        close_b = _load_close(ticker_b)
    if close_a is None or close_b is None:
        return None

    aligned = pd.concat([close_a, close_b], axis=1, keys=["a", "b"]).dropna()
    aligned = aligned[(aligned["a"] > 0) & (aligned["b"] > 0)]
    if len(aligned) < MIN_OVERLAP_DAYS:
        return None
    return aligned


def compute_spread_zscore(
    ticker_a: str, ticker_b: str,
    close_a: Optional[pd.Series] = None, close_b: Optional[pd.Series] = None,
) -> Optional[dict]:
    """log-spread (log(close_a) - log(close_b)) z-score against its own trailing-year mean. Ratio-based, so pence-vs-pounds quoting never needs converting."""
    aligned = _aligned_closes(ticker_a, ticker_b, close_a, close_b)
    if aligned is None:
        return None

    log_spread = np.log(aligned["a"]) - np.log(aligned["b"])
    mean = float(log_spread.mean())
    std = float(log_spread.std())
    if std == 0:
        return None
    last = float(log_spread.iloc[-1])
    z = (last - mean) / std
    rich_ticker, cheap_ticker = (ticker_a, ticker_b) if z > 0 else (ticker_b, ticker_a)
    direction = f"{rich_ticker} rich vs {cheap_ticker}"
    return {
        "zscore": round(z, 3),
        "spread_mean": round(mean, 6),
        "spread_std": round(std, 6),
        "last_spread": round(last, 6),
        "direction": direction,
        "cheap_ticker": cheap_ticker,
        "rich_ticker": rich_ticker,
    }


def build_chart_series(ticker_a: str, ticker_b: str) -> Optional[dict]:
    """Aligned price history for both tickers, each indexed to 100 at the start of the window
    so the two lines are visually comparable regardless of absolute price or currency scale —
    the intuitive complement to the (fairly abstract) log-spread z-score the alert fires on."""
    a, b = sorted((ticker_a.upper(), ticker_b.upper()))
    aligned = _aligned_closes(a, b)
    if aligned is None:
        return None

    log_spread = np.log(aligned["a"]) - np.log(aligned["b"])
    spread_mean = float(log_spread.mean())
    spread_std = float(log_spread.std())
    zscore = round((float(log_spread.iloc[-1]) - spread_mean) / spread_std, 3) if spread_std else None

    returns_a = aligned["a"].pct_change().dropna()
    returns_b = aligned["b"].pct_change().dropna()
    correlation = float(returns_a.corr(returns_b)) if len(returns_a) >= MIN_OVERLAP_DAYS else None

    normalized_a = aligned["a"] / aligned["a"].iloc[0] * 100
    normalized_b = aligned["b"] / aligned["b"].iloc[0] * 100

    return {
        "ticker_a": a,
        "ticker_b": b,
        "dates": aligned.index.strftime("%Y-%m-%d").tolist(),
        "close_a": [round(float(v), 4) for v in aligned["a"].tolist()],
        "close_b": [round(float(v), 4) for v in aligned["b"].tolist()],
        "normalized_a": [round(float(v), 3) for v in normalized_a.tolist()],
        "normalized_b": [round(float(v), 3) for v in normalized_b.tolist()],
        "correlation": round(correlation, 4) if correlation is not None else None,
        "zscore": zscore,
    }


class PairsSpreadEngine:
    # Filters portfolio+watchlist (or, on demand, the full market universe) pairs to those
    # already correlated, then flags the ones whose log-spread has diverged from its
    # trailing-year mean by more than zscore_threshold standard deviations.

    def __init__(self, config: dict) -> None:
        cfg = config.get("SCHEDULING", {}).get("PAIRS_SPREAD_MONITOR", {})
        self.correlation_threshold: float = float(cfg.get("CORRELATION_THRESHOLD", 0.7))
        self.zscore_threshold: float = float(cfg.get("ZSCORE_THRESHOLD", 2.0))
        self.ignored_tickers: set = ignored_tickers_set(config)

    def _get_universe(self, scope: str) -> list[str]:
        if scope == SCOPE_UNIVERSE:
            tickers: set[str] = set(get_universe_tickers())
            return sorted(
                t.upper() for t in tickers
                if t and not is_excluded_from_yahoo_fetch(t, self.ignored_tickers)
            )
        return get_portfolio_watchlist_tickers()

    @staticmethod
    def _currency_map(tickers: list[str], conn) -> dict:
        raw = get_ticker_currency_map(tickers, conn)
        return {t: _normalize_currency(c) for t, c in raw.items() if c}

    def run_scan(self, scope: str = SCOPE_PORTFOLIO_WATCHLIST) -> list[dict]:
        tickers = self._get_universe(scope)
        if len(tickers) < 2:
            self._save_results([], scope)
            return []

        conn = None
        try:
            conn = get_connection()
            currency_map = self._currency_map(tickers, conn)
        finally:
            if conn:
                conn.close()

        tickers = [t for t in tickers if t in currency_map]
        if len(tickers) < 2:
            self._save_results([], scope)
            return []

        returns_df = fetch_close_returns_from_parquet(tickers)
        available = [t for t in tickers if t in returns_df.columns]

        buckets: dict[str, list[str]] = {}
        for t in available:
            buckets.setdefault(currency_map[t], []).append(t)

        results: list[dict] = []
        scan_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for currency, bucket_tickers in buckets.items():
            if len(bucket_tickers) < 2:
                continue
            results.extend(self._scan_bucket(returns_df, currency, bucket_tickers, scope, scan_ts))

        self._save_results(results, scope)
        return results

    def _scan_bucket(
        self, returns_df: pd.DataFrame, currency: str, tickers: list[str], scope: str, scan_ts: str
    ) -> list[dict]:
        corr = returns_df[tickers].corr(min_periods=MIN_OVERLAP_DAYS)

        # Each ticker's own close series is loaded at most once per bucket, however many
        # surviving pairs it ends up in — with a universe-sized bucket (thousands of tickers)
        # re-reading parquet per pair instead of per ticker would be an O(n^2) I/O blowup.
        price_cache: dict[str, Optional[pd.Series]] = {}

        def _cached_close(ticker: str) -> Optional[pd.Series]:
            if ticker not in price_cache:
                price_cache[ticker] = _load_close(ticker)
            return price_cache[ticker]

        pairs: list[dict] = []
        for i, x in enumerate(tickers):
            for y in tickers[i + 1:]:
                r = corr.loc[x, y]
                if pd.isna(r) or abs(r) < self.correlation_threshold:
                    continue
                a, b = sorted((x, y))
                spread_row = compute_spread_zscore(a, b, _cached_close(a), _cached_close(b))
                if spread_row is None:
                    continue
                pairs.append({
                    "pair_key": f"{scope}:{a}:{b}",
                    "scope": scope,
                    "ticker_a": a,
                    "ticker_b": b,
                    "currency": currency,
                    "correlation": round(float(r), 4),
                    **spread_row,
                    "scan_ts": scan_ts,
                })
        return pairs

    @staticmethod
    def _save_results(results: list[dict], scope: str) -> None:
        """Full replace per scope, each scan — a pair that drops out of the correlation
        threshold should stop showing as a stale monitored pair, not linger with an old
        scan_ts. Scoped so a Universe scan never clears Portfolio/Watchlist rows or vice versa."""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM pairs_spread_results WHERE scope = ?", (scope,))
            for row in results:
                cursor.execute(
                    """
                    INSERT INTO pairs_spread_results
                        (pair_key, scope, ticker_a, ticker_b, currency, correlation, zscore,
                         spread_mean, spread_std, last_spread, direction, cheap_ticker, rich_ticker, scan_ts)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row["pair_key"], row["scope"], row["ticker_a"], row["ticker_b"], row["currency"],
                        row["correlation"], row["zscore"], row["spread_mean"], row["spread_std"],
                        row["last_spread"], row["direction"], row["cheap_ticker"], row["rich_ticker"], row["scan_ts"],
                    ),
                )
            conn.commit()
        except Exception as e:
            logger.error("PairsSpreadEngine: failed to save results (scope=%s): %s", scope, e)
        finally:
            if conn:
                conn.close()
