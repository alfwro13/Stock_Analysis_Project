"""
tests/test_portfolio_optimizer_engine.py — Portfolio Optimizer engine tests

Covers:
  • Closed-form Min-Variance/Max-Sharpe weight math against analytically-known answers
  • Singular-matrix pseudo-inverse fallback
  • Negative-weight surfacing (never clipped)
  • The two-tier returns-matrix read (xray_returns_cache + parquet fallback for
    never-held/Watchlist-only tickers)
  • optimize_portfolio()/list_candidates() integration, seeding data the same way
    tests/test_performance_analytics_engine.py does
"""

import json
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import database as db
from database import create_account, add_transaction
from db_accounts import get_watchlist_account, add_watchlist_item, remove_watchlist_ticker
from xray_engine import BENCHMARK_SYMBOL
from portfolio_optimizer_engine import (
    MIN_TICKERS_WARNING,
    NOT_ENOUGH_DATA_WARNING,
    _closed_form_weights,
    _returns_matrix_for_candidates,
    list_candidates,
    optimize_portfolio,
)

T1 = "POE_T1"
T2 = "POE_T2"
T3 = "POE_T3"
RM_T1 = "POE_RM_T1"
RM_T2 = "POE_RM_T2"
RM_T3 = "POE_RM_T3"


def _seed_asset_profile(ticker, company_name, quote_type="EQUITY"):
    conn = db.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO asset_profiles (ticker, company_name, quote_type) VALUES (?, ?, ?)",
        (ticker, company_name, quote_type),
    )
    conn.commit()
    conn.close()


def _seed_returns_cache(series_by_ticker, dates, last_updated="2026-06-03"):
    conn = db.get_connection()
    for ticker, rets in series_by_ticker.items():
        conn.execute(
            """INSERT OR REPLACE INTO xray_returns_cache
               (ticker, benchmark, last_updated, dates_json, returns_json)
               VALUES (?, ?, ?, ?, ?)""",
            (ticker, BENCHMARK_SYMBOL, last_updated, json.dumps(dates), json.dumps(rets)),
        )
    conn.commit()
    conn.close()


def _builtin_config(extra=None):
    cfg = {"GHOSTFOLIO_ACCOUNTS": {"active": []}, "BASE_CURRENCY": "GBP", "RISK_FREE_RATE": 0.045}
    cfg.update(extra or {})
    return cfg


def _bdate_strings(n, start="2025-01-01"):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


# ─────────────────────────────────────────────────────────────────────────────
# 1. _closed_form_weights — analytically-known answers
# ─────────────────────────────────────────────────────────────────────────────

class TestClosedFormWeights:
    def test_min_variance_is_inverse_variance_weighted(self):
        # Uncorrelated assets: MV weight ratio = inverse-variance ratio (sigma1^2=1, sigma2^2=4).
        mu = np.array([0.1, 0.1])
        cov = np.array([[1.0, 0.0], [0.0, 4.0]])
        result = _closed_form_weights(mu, cov, rf=0.0)
        assert result["w_mv"] == pytest.approx([0.8, 0.2], abs=1e-4)

    def test_max_sharpe_known_ratio(self):
        # Uncorrelated assets, excess = mu - rf = [0.1, 0.2], sigma^2 = [1, 4]:
        # raw = [0.1, 0.05] -> normalized [2/3, 1/3].
        mu = np.array([0.1, 0.2])
        cov = np.array([[1.0, 0.0], [0.0, 4.0]])
        result = _closed_form_weights(mu, cov, rf=0.0)
        assert result["w_ms"] == pytest.approx([2 / 3, 1 / 3], abs=1e-4)
        assert result["warnings"] == []

    def test_negative_weight_is_not_clipped(self):
        mu = np.array([-0.05, 0.2])
        cov = np.array([[1.0, 0.0], [0.0, 1.0]])
        result = _closed_form_weights(mu, cov, rf=0.0)
        assert result["w_ms"][0] < 0
        assert sum(result["w_ms"]) == pytest.approx(1.0)

    def test_max_sharpe_near_zero_denominator_returns_none_with_warning(self):
        mu = np.array([0.045, 0.045])
        cov = np.array([[1.0, 0.0], [0.0, 1.0]])
        result = _closed_form_weights(mu, cov, rf=0.045)
        assert result["w_ms"] is None
        assert any("near-zero excess-return spread" in w for w in result["warnings"])

    def test_max_sharpe_negative_denominator_warns_but_still_computes(self):
        mu = np.array([0.01, 0.02])
        cov = np.array([[1.0, 0.0], [0.0, 1.0]])
        result = _closed_form_weights(mu, cov, rf=0.5)
        assert result["w_ms"] is not None
        assert sum(result["w_ms"]) == pytest.approx(1.0)
        assert any("below the risk-free rate" in w for w in result["warnings"])

    def test_singular_matrix_falls_back_to_pseudo_inverse(self):
        mu = np.array([0.1, 0.1])
        cov = np.array([[1.0, 1.0], [1.0, 1.0]])  # rank-1, singular
        with patch("numpy.linalg.inv", side_effect=np.linalg.LinAlgError):
            result = _closed_form_weights(mu, cov, rf=0.0)
        assert result["w_mv"] is not None
        assert sum(result["w_mv"]) == pytest.approx(1.0)
        assert any("pseudo-inverse fallback" in w for w in result["warnings"])


# ─────────────────────────────────────────────────────────────────────────────
# 2. _returns_matrix_for_candidates — cache + parquet fallback merge
# ─────────────────────────────────────────────────────────────────────────────

class TestReturnsMatrixForCandidates:
    def test_all_cached_returns_combined_df(self):
        dates = _bdate_strings(40)
        rng = np.random.default_rng(1)
        _seed_returns_cache(
            {RM_T1: rng.normal(0, 0.01, 40).tolist(), RM_T2: rng.normal(0, 0.01, 40).tolist()}, dates
        )
        df, warnings = _returns_matrix_for_candidates([RM_T1, RM_T2])
        assert df is not None
        assert set(df.columns) == {RM_T1, RM_T2}
        assert len(df) >= 30

    def test_missing_ticker_falls_back_to_parquet(self):
        dates = _bdate_strings(40)
        rng = np.random.default_rng(2)
        _seed_returns_cache({RM_T1: rng.normal(0, 0.01, 40).tolist()}, dates)

        parquet_index = pd.bdate_range("2025-01-01", periods=40)
        fallback_df = pd.DataFrame(
            {RM_T3: rng.normal(0, 0.01, 40)}, index=parquet_index
        )
        with patch(
            "portfolio_optimizer_engine.fetch_close_returns_from_parquet",
            return_value=fallback_df,
        ):
            df, warnings = _returns_matrix_for_candidates([RM_T1, RM_T3])

        assert df is not None
        assert set(df.columns) == {RM_T1, RM_T3}

    def test_no_data_anywhere_returns_none(self):
        with patch(
            "portfolio_optimizer_engine.fetch_close_returns_from_parquet",
            return_value=pd.DataFrame(),
        ):
            df, warnings = _returns_matrix_for_candidates(["NOPE1", "NOPE2"])
        assert df is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. optimize_portfolio() / list_candidates() — integration
# ─────────────────────────────────────────────────────────────────────────────

class TestOptimizePortfolio:
    def test_empty_scope_returns_error(self):
        aid = create_account("PoeEmptyAcc", "GBP")
        with patch("xray_engine.load_config", return_value=_builtin_config()):
            report = optimize_portfolio(f"acct:{aid}")
        assert report["status"] == "error"

    def test_fewer_than_two_tickers_warns(self):
        _seed_asset_profile(T1, "Company One")
        aid = create_account("PoeOneTickerAcc", "GBP")
        add_transaction(aid, "Buy", "2026-01-05", ticker=T1, currency="GBP",
                         quantity=10, unit_price=80, exchange_rate=1.0)
        with patch("xray_engine.load_config", return_value=_builtin_config()), \
             patch("portfolio_optimizer_engine.load_config", return_value=_builtin_config()):
            report = optimize_portfolio(f"acct:{aid}")
        assert report["status"] == "success"
        assert report["weights"] is None
        assert MIN_TICKERS_WARNING in report["data_warnings"]

    def test_not_enough_history_warns(self):
        _seed_asset_profile(T1, "Company One")
        _seed_asset_profile(T2, "Company Two")
        aid = create_account("PoeNoHistoryAcc", "GBP")
        add_transaction(aid, "Buy", "2026-01-05", ticker=T1, currency="GBP",
                         quantity=10, unit_price=80, exchange_rate=1.0)
        add_transaction(aid, "Buy", "2026-01-05", ticker=T2, currency="GBP",
                         quantity=5, unit_price=50, exchange_rate=1.0)
        with patch("xray_engine.load_config", return_value=_builtin_config()), \
             patch("portfolio_optimizer_engine.load_config", return_value=_builtin_config()):
            report = optimize_portfolio(f"acct:{aid}")
        assert report["status"] == "success"
        assert report["weights"] is None
        assert NOT_ENOUGH_DATA_WARNING in report["data_warnings"]

    def test_full_report_includes_weights_and_frontier(self):
        _seed_asset_profile(T1, "Company One")
        _seed_asset_profile(T2, "Company Two")
        aid = create_account("PoeFullAcc", "GBP")
        add_transaction(aid, "Buy", "2026-01-05", ticker=T1, currency="GBP",
                         quantity=10, unit_price=80, exchange_rate=1.0)
        add_transaction(aid, "Buy", "2026-01-05", ticker=T2, currency="GBP",
                         quantity=5, unit_price=50, exchange_rate=1.0)

        rng = np.random.default_rng(7)
        dates = _bdate_strings(252)
        _seed_returns_cache(
            {T1: rng.normal(0.0004, 0.012, 252).tolist(), T2: rng.normal(0.0003, 0.009, 252).tolist()},
            dates,
        )

        with patch("xray_engine.load_config", return_value=_builtin_config()), \
             patch("portfolio_optimizer_engine.load_config", return_value=_builtin_config()):
            report = optimize_portfolio(f"acct:{aid}")

        assert report["status"] == "success"
        assert len(report["weights"]) == 2
        symbols = {w["symbol"] for w in report["weights"]}
        assert symbols == {T1, T2}
        for w in report["weights"]:
            assert w["suggested_weight_mv"] is not None
            assert w["current_weight"] > 0
            assert w["is_new_addition"] is False
        assert report["efficient_frontier"] is not None
        assert len(report["efficient_frontier"]["points"]) == 25

    def test_watchlist_only_ticker_included_with_zero_current_weight(self):
        _seed_asset_profile(T1, "Company One")
        _seed_asset_profile(T3, "Company Three")
        aid = create_account("PoeWatchlistAcc", "GBP")
        add_transaction(aid, "Buy", "2026-01-05", ticker=T1, currency="GBP",
                         quantity=10, unit_price=80, exchange_rate=1.0)

        rng = np.random.default_rng(9)
        dates = _bdate_strings(252)
        _seed_returns_cache(
            {T1: rng.normal(0.0004, 0.012, 252).tolist(), T3: rng.normal(0.0003, 0.009, 252).tolist()},
            dates,
        )

        with patch("xray_engine.load_config", return_value=_builtin_config()), \
             patch("portfolio_optimizer_engine.load_config", return_value=_builtin_config()):
            report = optimize_portfolio(f"acct:{aid}", include_tickers=[T1, T3])

        assert report["status"] == "success"
        by_symbol = {w["symbol"]: w for w in report["weights"]}
        assert by_symbol[T3]["current_weight"] == 0.0
        assert by_symbol[T3]["is_new_addition"] is True
        assert by_symbol[T1]["current_weight"] > 0

    def test_list_candidates_marks_held_and_watchlist(self):
        _seed_asset_profile(T1, "Company One")
        _seed_asset_profile(T2, "Company Two")
        aid = create_account("PoeCandidatesAcc", "GBP")
        add_transaction(aid, "Buy", "2026-01-05", ticker=T1, currency="GBP",
                         quantity=10, unit_price=80, exchange_rate=1.0)

        watchlist_account = get_watchlist_account()
        add_watchlist_item(watchlist_account["id"], T2, company_name="Company Two")
        try:
            with patch("xray_engine.load_config", return_value=_builtin_config()):
                result = list_candidates(f"acct:{aid}")

            assert result["status"] == "success"
            by_symbol = {c["symbol"]: c for c in result["candidates"]}
            assert by_symbol[T1]["held"] is True
            assert by_symbol[T1]["current_weight"] > 0
            assert by_symbol[T2]["held"] is False
            assert by_symbol[T2]["current_weight"] == 0.0
        finally:
            remove_watchlist_ticker(watchlist_account["id"], T2)
