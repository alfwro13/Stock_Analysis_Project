"""
tests/test_pairs_spread_engine.py — Pairs Spread Monitor Tests

Covers:
  • _normalize_currency()          — GBp/GBP collapse to one bucket
  • compute_spread_zscore()        — known synthetic spread produces the expected z-score/direction
  • build_chart_series()           — normalized-price chart payload shape
  • PairsSpreadEngine._get_universe() — portfolio+watchlist scope vs universe scope
  • PairsSpreadEngine.run_scan()   — correlation threshold + currency bucketing end to end,
                                     full-replace-per-scope persistence into pairs_spread_results
  • run_pairs_spread_monitor_job() — alert gate fires above threshold, suppressed below
  • run_pairs_spread_universe_scan() — never dispatches alerts regardless of z-score
"""

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
from pairs_spread_engine import (
    PairsSpreadEngine,
    SCOPE_PORTFOLIO_WATCHLIST,
    SCOPE_UNIVERSE,
    _normalize_currency,
    compute_spread_zscore,
    build_chart_series,
)

T_A = "PSM_A"
T_B = "PSM_B"
T_C = "PSM_C"
T_D = "PSM_D"
T_E = "PSM_E"


def _make_df(closes: list) -> pd.DataFrame:
    idx = pd.date_range("2025-01-02", periods=len(closes), freq="B")
    return pd.DataFrame({"Close": closes}, index=idx)


def _correlated_pair(n: int = 100, jump: float = 0.5):
    """close_a/close_b track identically for n-1 days, then diverge by `jump` in log-space
    on the final day — high correlation (pairs the threshold) plus an unmistakable z-score."""
    rng = np.random.default_rng(11)
    returns = rng.normal(0.0005, 0.01, n)
    close_a = 100 * np.exp(np.cumsum(returns))
    close_b = close_a.copy()
    close_b[-1] = close_a[-1] / np.exp(jump)
    return close_a.tolist(), close_b.tolist()


def _independent_series(n: int = 100, seed: int = 99):
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0, 0.01, n)
    return (100 * np.exp(np.cumsum(returns))).tolist()


def _gentle_correlated_pair(n: int = 100):
    """close_b tracks close_a with a small independent noise term on every day — realistic
    ~0.99 return correlation (clears the 0.7 threshold) without any single-day outlier return
    that Pearson correlation would be sensitive to, unlike a one-off large jump."""
    rng = np.random.default_rng(11)
    common = rng.normal(0.0005, 0.01, n)
    noise = rng.normal(0.0, 0.0008, n)
    close_a = 100 * np.exp(np.cumsum(common))
    close_b = 100 * np.exp(np.cumsum(common - noise))
    return close_a.tolist(), close_b.tolist()


def _seed_currency(ticker: str, currency: str) -> None:
    conn = db.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency) VALUES (?, 1.0, ?)",
        (ticker, currency),
    )
    conn.commit()
    conn.close()


class TestNormalizeCurrency:
    def test_gbp_and_gbx_collapse(self):
        assert _normalize_currency("GBP") == _normalize_currency("GBp") == "GBP"

    def test_other_currency_passthrough(self):
        assert _normalize_currency("USD") == "USD"

    def test_none_passthrough(self):
        assert _normalize_currency(None) is None
        assert _normalize_currency("") is None


class TestComputeSpreadZscore:
    def test_known_divergence_produces_positive_zscore_and_direction(self):
        close_a, close_b = _correlated_pair()
        with patch("pairs_spread_engine.load_or_fetch_daily_history",
                   side_effect=lambda t: _make_df(close_a) if t == T_A else _make_df(close_b)):
            result = compute_spread_zscore(T_A, T_B)

        assert result is not None
        assert result["zscore"] > 2.0
        assert result["direction"] == f"{T_A} rich vs {T_B}"

    def test_negative_divergence_flips_direction(self):
        close_a, close_b = _correlated_pair(jump=-0.5)
        with patch("pairs_spread_engine.load_or_fetch_daily_history",
                   side_effect=lambda t: _make_df(close_a) if t == T_A else _make_df(close_b)):
            result = compute_spread_zscore(T_A, T_B)

        assert result is not None
        assert result["zscore"] < -2.0
        assert result["direction"] == f"{T_B} rich vs {T_A}"

    def test_none_when_history_missing(self):
        with patch("pairs_spread_engine.load_or_fetch_daily_history", return_value=None):
            assert compute_spread_zscore(T_A, T_B) is None

    def test_none_when_insufficient_overlap(self):
        short_a = _make_df([100.0] * 10)
        short_b = _make_df([100.0] * 10)
        with patch("pairs_spread_engine.load_or_fetch_daily_history",
                   side_effect=lambda t: short_a if t == T_A else short_b):
            assert compute_spread_zscore(T_A, T_B) is None

    def test_accepts_preloaded_closes_without_fetching(self):
        close_a, close_b = _correlated_pair()
        with patch("pairs_spread_engine.load_or_fetch_daily_history") as mock_load:
            result = compute_spread_zscore(T_A, T_B, _make_df(close_a)["Close"], _make_df(close_b)["Close"])
        mock_load.assert_not_called()
        assert result is not None


class TestBuildChartSeries:
    def test_chart_payload_shape(self):
        close_a, close_b = _correlated_pair()
        with patch("pairs_spread_engine.load_or_fetch_daily_history",
                   side_effect=lambda t: _make_df(close_a) if t == T_A else _make_df(close_b)):
            chart = build_chart_series(T_B, T_A)  # order-independent — sorted internally

        assert chart is not None
        assert chart["ticker_a"] == T_A
        assert chart["ticker_b"] == T_B
        assert len(chart["dates"]) == len(chart["normalized_a"]) == len(chart["normalized_b"])
        assert len(chart["close_a"]) == len(chart["close_b"]) == len(chart["dates"])
        # Both series are indexed to 100 at the start of the window.
        assert chart["normalized_a"][0] == pytest.approx(100.0)
        assert chart["normalized_b"][0] == pytest.approx(100.0)
        assert chart["correlation"] is not None
        assert chart["zscore"] is not None

    def test_none_when_history_missing(self):
        with patch("pairs_spread_engine.load_or_fetch_daily_history", return_value=None):
            assert build_chart_series(T_A, T_B) is None


class TestGetUniverse:
    def test_portfolio_watchlist_scope_delegates_to_shared_helper(self):
        engine = PairsSpreadEngine({})
        with patch("pairs_spread_engine.get_portfolio_watchlist_tickers",
                   return_value=[T_A, T_B]) as mock_helper:
            universe = engine._get_universe(SCOPE_PORTFOLIO_WATCHLIST)
        mock_helper.assert_called_once_with()
        assert universe == [T_A, T_B]

    def test_universe_scope_uses_market_universe_not_holdings(self):
        engine = PairsSpreadEngine({})
        with patch("pairs_spread_engine.get_universe_tickers", return_value=[T_A, T_B]), \
             patch("pairs_spread_engine.get_portfolio_watchlist_tickers",
                   return_value=[T_C, T_D]):
            universe = engine._get_universe(SCOPE_UNIVERSE)
        assert universe == sorted([T_A, T_B])


class TestRunScan:
    def _seed_universe_and_prices(self):
        close_a, close_b = _gentle_correlated_pair()
        close_c = _independent_series(seed=7)
        _seed_currency(T_A, "USD")
        _seed_currency(T_B, "USD")
        _seed_currency(T_C, "USD")
        _seed_currency(T_D, "GBP")  # different currency bucket than A/B/C

        def _loader(t):
            return {
                T_A: _make_df(close_a),
                T_B: _make_df(close_b),
                T_C: _make_df(close_c),
                T_D: _make_df(close_a),  # would correlate with A if currency didn't split it out
            }.get(t)

        return _loader

    def _run(self, engine, loader, scope=SCOPE_PORTFOLIO_WATCHLIST):
        with patch("pairs_spread_engine.get_portfolio_watchlist_tickers",
                   return_value=sorted([T_A, T_B, T_C, T_D])), \
             patch("pairs_spread_engine.load_or_fetch_daily_history", side_effect=loader), \
             patch("xray_engine.load_or_fetch_daily_history", side_effect=loader):
            return engine.run_scan(scope=scope)

    def test_correlated_same_currency_pair_saved(self):
        loader = self._seed_universe_and_prices()
        engine = PairsSpreadEngine({})
        results = self._run(engine, loader)

        pairs = {r["pair_key"] for r in results}
        assert f"{SCOPE_PORTFOLIO_WATCHLIST}:{T_A}:{T_B}" in pairs, "Highly correlated same-currency pair must be flagged"
        assert all(r["scope"] == SCOPE_PORTFOLIO_WATCHLIST for r in results)

    def test_uncorrelated_pair_excluded(self):
        loader = self._seed_universe_and_prices()
        engine = PairsSpreadEngine({})
        results = self._run(engine, loader)

        pairs = {(r["ticker_a"], r["ticker_b"]) for r in results}
        assert not any(T_C in p for p in pairs), "Uncorrelated ticker must never appear in a pair"

    def test_cross_currency_pair_excluded(self):
        loader = self._seed_universe_and_prices()
        engine = PairsSpreadEngine({})
        results = self._run(engine, loader)

        pairs = {(r["ticker_a"], r["ticker_b"]) for r in results}
        assert not any(T_D in p for p in pairs), "GBP ticker must never pair with a USD ticker"

    def test_results_persisted_and_full_replace_scoped(self):
        loader = self._seed_universe_and_prices()
        engine = PairsSpreadEngine({})
        self._run(engine, loader)

        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM pairs_spread_results WHERE pair_key = ?",
                (f"{SCOPE_PORTFOLIO_WATCHLIST}:{T_A}:{T_B}",),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["correlation"] > 0.7
        assert row["scope"] == SCOPE_PORTFOLIO_WATCHLIST

        # A universe scan must not clear the portfolio_watchlist scope's rows.
        with patch("pairs_spread_engine.get_universe_tickers", return_value=[]):
            engine.run_scan(scope=SCOPE_UNIVERSE)

        conn = db.get_connection()
        try:
            still_there = conn.execute(
                "SELECT COUNT(*) AS c FROM pairs_spread_results WHERE scope = ?",
                (SCOPE_PORTFOLIO_WATCHLIST,),
            ).fetchone()["c"]
        finally:
            conn.close()
        assert still_there > 0, "Universe scan must not clear portfolio_watchlist scope's rows"

        # Empty universe on the next portfolio_watchlist scan must clear its own stale rows.
        with patch("pairs_spread_engine.get_portfolio_watchlist_tickers", return_value=[]):
            engine.run_scan(scope=SCOPE_PORTFOLIO_WATCHLIST)

        conn = db.get_connection()
        try:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM pairs_spread_results WHERE scope = ?",
                (SCOPE_PORTFOLIO_WATCHLIST,),
            ).fetchone()["c"]
        finally:
            conn.close()
        assert count == 0


class TestRunPairsSpreadMonitorJob:
    @staticmethod
    def _row(ticker_a, ticker_b, zscore, correlation=0.9, scope=SCOPE_PORTFOLIO_WATCHLIST):
        return {
            "pair_key": f"{scope}:{ticker_a}:{ticker_b}", "scope": scope,
            "ticker_a": ticker_a, "ticker_b": ticker_b,
            "currency": "USD", "correlation": correlation, "zscore": zscore,
            "spread_mean": 0.0, "spread_std": 0.1, "last_spread": zscore * 0.1,
            "direction": f"{ticker_a} rich vs {ticker_b}",
            "scan_ts": "2026-06-10 19:10:00",
        }

    def test_fires_alert_above_threshold(self):
        import scheduler_jobs
        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM alert_state WHERE engine = 'PairsSpreadMonitor'")
            conn.commit()
        finally:
            conn.close()

        with patch("pairs_spread_engine.PairsSpreadEngine.run_scan",
                   return_value=[self._row(T_A, T_B, 3.0)]), \
             patch("scheduler_jobs.notify", return_value=True) as mock_notify:
            scheduler_jobs.run_pairs_spread_monitor_job()

        mock_notify.assert_called_once()

    def test_suppresses_alert_below_threshold(self):
        import scheduler_jobs
        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM alert_state WHERE engine = 'PairsSpreadMonitor'")
            conn.commit()
        finally:
            conn.close()

        with patch("pairs_spread_engine.PairsSpreadEngine.run_scan",
                   return_value=[self._row(T_A, T_B, 0.5)]), \
             patch("scheduler_jobs.notify", return_value=True) as mock_notify:
            scheduler_jobs.run_pairs_spread_monitor_job()

        mock_notify.assert_not_called()

    def test_no_results_does_not_call_notify(self):
        import scheduler_jobs
        with patch("pairs_spread_engine.PairsSpreadEngine.run_scan", return_value=[]), \
             patch("scheduler_jobs.notify", return_value=True) as mock_notify:
            scheduler_jobs.run_pairs_spread_monitor_job()

        mock_notify.assert_not_called()

    def test_run_scan_called_with_portfolio_watchlist_scope(self):
        import scheduler_jobs
        with patch("pairs_spread_engine.PairsSpreadEngine.run_scan", return_value=[]) as mock_scan, \
             patch("scheduler_jobs.notify", return_value=True):
            scheduler_jobs.run_pairs_spread_monitor_job()
        mock_scan.assert_called_once_with(scope=SCOPE_PORTFOLIO_WATCHLIST)


class TestRunPairsSpreadUniverseScan:
    def test_never_calls_notify_even_above_threshold(self):
        import scheduler_jobs
        with patch("pairs_spread_engine.PairsSpreadEngine.run_scan",
                   return_value=[TestRunPairsSpreadMonitorJob._row(T_A, T_B, 5.0, scope=SCOPE_UNIVERSE)]), \
             patch("scheduler_jobs.notify", return_value=True) as mock_notify:
            scheduler_jobs.run_pairs_spread_universe_scan()

        mock_notify.assert_not_called()

    def test_run_scan_called_with_universe_scope(self):
        import scheduler_jobs
        with patch("pairs_spread_engine.PairsSpreadEngine.run_scan", return_value=[]) as mock_scan:
            scheduler_jobs.run_pairs_spread_universe_scan()
        mock_scan.assert_called_once_with(scope=SCOPE_UNIVERSE)
