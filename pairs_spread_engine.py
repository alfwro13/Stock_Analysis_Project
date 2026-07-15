from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from accounts_engine import get_combined_holdings
from data_engine import load_or_fetch_daily_history
from database import get_connection, get_watchlist_tickers
from db_helpers import get_ticker_currency_map
from utils import ignored_tickers_set, is_excluded_from_yahoo_fetch
from xray_engine import fetch_close_returns_from_parquet

logger = logging.getLogger(__name__)

# GUI name: "Pairs Spread Monitor". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

LOOKBACK_DAYS = 252
MIN_OVERLAP_DAYS = 60


def _normalize_currency(currency: Optional[str]) -> Optional[str]:
    """GBp (LSE pence) and GBP both mean "British Pounds" for pairing purposes — Yahoo returns either depending on the ticker."""
    if not currency:
        return None
    return "GBP" if currency in ("GBp", "GBP") else currency


def compute_spread_series(ticker_a: str, ticker_b: str) -> Optional[pd.DataFrame]:
    """Aligned trailing log-spread (log(close_a) - log(close_b)) — the shared calc behind both the scan and the on-demand chart. Ratio-based, so pence-vs-pounds quoting never needs converting."""
    df_a = load_or_fetch_daily_history(ticker_a)
    df_b = load_or_fetch_daily_history(ticker_b)
    if df_a is None or df_b is None or "Close" not in df_a.columns or "Close" not in df_b.columns:
        return None

    close_a = df_a["Close"].tail(LOOKBACK_DAYS)
    close_b = df_b["Close"].tail(LOOKBACK_DAYS)
    aligned = pd.concat([close_a, close_b], axis=1, keys=["a", "b"]).dropna()
    aligned = aligned[(aligned["a"] > 0) & (aligned["b"] > 0)]
    if len(aligned) < MIN_OVERLAP_DAYS:
        return None

    log_spread = np.log(aligned["a"]) - np.log(aligned["b"])
    return pd.DataFrame({"log_spread": log_spread})


def compute_spread_zscore(ticker_a: str, ticker_b: str) -> Optional[dict]:
    series = compute_spread_series(ticker_a, ticker_b)
    if series is None:
        return None
    log_spread = series["log_spread"]
    mean = float(log_spread.mean())
    std = float(log_spread.std())
    if std == 0:
        return None
    last = float(log_spread.iloc[-1])
    z = (last - mean) / std
    direction = f"{ticker_a} rich vs {ticker_b}" if z > 0 else f"{ticker_b} rich vs {ticker_a}"
    return {
        "zscore": round(z, 3),
        "spread_mean": round(mean, 6),
        "spread_std": round(std, 6),
        "last_spread": round(last, 6),
        "direction": direction,
    }


def build_chart_series(ticker_a: str, ticker_b: str) -> Optional[dict]:
    a, b = sorted((ticker_a.upper(), ticker_b.upper()))
    series = compute_spread_series(a, b)
    if series is None:
        return None
    log_spread = series["log_spread"]
    mean = float(log_spread.mean())
    std = float(log_spread.std())
    return {
        "ticker_a": a,
        "ticker_b": b,
        "dates": log_spread.index.strftime("%Y-%m-%d").tolist(),
        "log_spread": [round(float(v), 6) for v in log_spread.tolist()],
        "mean": round(mean, 6),
        "upper_2sd": round(mean + 2 * std, 6),
        "lower_2sd": round(mean - 2 * std, 6),
    }


class PairsSpreadEngine:
    # Filters portfolio+watchlist pairs to those already correlated (via xray_engine's own
    # parquet-returns helper), then flags the ones whose log-spread has diverged from its
    # trailing-year mean by more than zscore_threshold standard deviations.

    def __init__(self, config: dict) -> None:
        cfg = config.get("SCHEDULING", {}).get("PAIRS_SPREAD_MONITOR", {})
        self.correlation_threshold: float = float(cfg.get("CORRELATION_THRESHOLD", 0.7))
        self.zscore_threshold: float = float(cfg.get("ZSCORE_THRESHOLD", 2.0))
        self.ignored_tickers: set = ignored_tickers_set(config)

    def _get_universe(self) -> list[str]:
        tickers: set[str] = set()
        tickers.update(get_combined_holdings().keys())
        tickers.update(get_watchlist_tickers())
        return sorted(
            t.upper() for t in tickers
            if t and not is_excluded_from_yahoo_fetch(t, self.ignored_tickers)
        )

    @staticmethod
    def _currency_map(tickers: list[str], conn) -> dict:
        raw = get_ticker_currency_map(tickers, conn)
        return {t: _normalize_currency(c) for t, c in raw.items() if c}

    def run_scan(self) -> list[dict]:
        tickers = self._get_universe()
        if len(tickers) < 2:
            self._save_results([])
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
            self._save_results([])
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
            results.extend(self._scan_bucket(returns_df, currency, bucket_tickers, scan_ts))

        self._save_results(results)
        return results

    def _scan_bucket(
        self, returns_df: pd.DataFrame, currency: str, tickers: list[str], scan_ts: str
    ) -> list[dict]:
        corr = returns_df[tickers].corr(min_periods=MIN_OVERLAP_DAYS)

        pairs: list[dict] = []
        for i, x in enumerate(tickers):
            for y in tickers[i + 1:]:
                r = corr.loc[x, y]
                if pd.isna(r) or abs(r) < self.correlation_threshold:
                    continue
                a, b = sorted((x, y))
                spread_row = compute_spread_zscore(a, b)
                if spread_row is None:
                    continue
                pairs.append({
                    "pair_key": f"{a}:{b}",
                    "ticker_a": a,
                    "ticker_b": b,
                    "currency": currency,
                    "correlation": round(float(r), 4),
                    **spread_row,
                    "scan_ts": scan_ts,
                })
        return pairs

    @staticmethod
    def _save_results(results: list[dict]) -> None:
        """Full replace each scan — a pair that drops out of the correlation threshold should stop showing as a stale monitored pair, not linger with an old scan_ts."""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM pairs_spread_results")
            for row in results:
                cursor.execute(
                    """
                    INSERT INTO pairs_spread_results
                        (pair_key, ticker_a, ticker_b, currency, correlation, zscore,
                         spread_mean, spread_std, last_spread, direction, scan_ts)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row["pair_key"], row["ticker_a"], row["ticker_b"], row["currency"],
                        row["correlation"], row["zscore"], row["spread_mean"], row["spread_std"],
                        row["last_spread"], row["direction"], row["scan_ts"],
                    ),
                )
            conn.commit()
        except Exception as e:
            logger.error("PairsSpreadEngine: failed to save results: %s", e)
        finally:
            if conn:
                conn.close()
