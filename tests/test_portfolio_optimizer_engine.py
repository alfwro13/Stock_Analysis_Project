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
from types import SimpleNamespace
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
    MODE_LONG_ONLY,
    NOT_ENOUGH_DATA_WARNING,
    _cap_infeasible_warning,
    _drop_short_history,
    _closed_form_weights,
    _long_only_frontier,
    _long_only_max_sharpe,
    _long_only_min_variance,
    _max_return_weights,
    _returns_matrix_for_candidates,
    _validated_solution,
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
        df, warnings, _ = _returns_matrix_for_candidates([RM_T1, RM_T2])
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
            df, warnings, _ = _returns_matrix_for_candidates([RM_T1, RM_T3])

        assert df is not None
        assert set(df.columns) == {RM_T1, RM_T3}

    def test_short_history_ticker_removed_and_named(self):
        dates = _bdate_strings(60)
        rng = np.random.default_rng(4)
        _seed_returns_cache(
            {RM_T1: rng.normal(0, 0.01, 60).tolist(), RM_T2: rng.normal(0, 0.01, 60).tolist()}, dates
        )
        short = pd.Series(rng.normal(0, 0.01, 10), index=pd.to_datetime(dates[-10:]))
        fallback_df = pd.DataFrame({"POE_RM_SHORT": short}, index=pd.to_datetime(dates))
        with patch(
            "portfolio_optimizer_engine.fetch_close_returns_from_parquet",
            return_value=fallback_df,
        ):
            df, _, removed = _returns_matrix_for_candidates([RM_T1, RM_T2, "POE_RM_SHORT"])

        assert df is not None
        assert set(df.columns) == {RM_T1, RM_T2}
        assert removed == [("POE_RM_SHORT", 10)]

    def test_no_data_anywhere_returns_none(self):
        with patch(
            "portfolio_optimizer_engine.fetch_close_returns_from_parquet",
            return_value=pd.DataFrame(),
        ):
            df, warnings, _ = _returns_matrix_for_candidates(["NOPE1", "NOPE2"])
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
        assert report["efficient_frontier"]["current"] is not None
        assert "return" in report["efficient_frontier"]["current"]
        assert "volatility" in report["efficient_frontier"]["current"]

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


# ─────────────────────────────────────────────────────────────────────────────
# 4. Long-Only solver helpers — hand-computable answers and invariants
# ─────────────────────────────────────────────────────────────────────────────

def _random_problem(n, seed=3):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0005, 0.01, (252, n)) * np.linspace(0.5, 2.0, n)
    return rets.mean(axis=0) * 252, np.cov(rets.T) * 252


def _assert_feasible(w, cap, budget):
    assert w is not None
    assert np.all(np.isfinite(w))
    assert w.sum() == pytest.approx(budget, abs=1e-6)
    assert w.min() >= 0.0
    assert w.max() <= cap + 1e-9


class TestLongOnlyHelpers:
    def test_min_variance_uncapped_matches_inverse_variance(self):
        w = _long_only_min_variance(np.diag([1.0, 4.0]), cap=1.0, budget=1.0)
        assert w == pytest.approx([0.8, 0.2], abs=1e-6)

    def test_min_variance_binding_cap(self):
        w = _long_only_min_variance(np.diag([1.0, 4.0]), cap=0.6, budget=1.0)
        assert w == pytest.approx([0.6, 0.4], abs=1e-6)

    def test_min_variance_with_cash_reserve_scales_budget(self):
        w = _long_only_min_variance(np.diag([1.0, 4.0]), cap=1.0, budget=0.8)
        assert w == pytest.approx([0.64, 0.16], abs=1e-6)

    def test_max_sharpe_two_assets_matches_closed_form_when_long(self):
        w, warning = _long_only_max_sharpe(
            np.array([0.1, 0.2]), np.diag([1.0, 4.0]), rf=0.0, cap=1.0, budget=1.0
        )
        assert warning is None
        assert w == pytest.approx([2 / 3, 1 / 3], abs=1e-5)

    def test_max_sharpe_never_shorts_where_unconstrained_would(self):
        mu, cov = np.array([-0.05, 0.2]), np.eye(2)
        assert _closed_form_weights(mu, cov, rf=0.0)["w_ms"][0] < 0
        w, warning = _long_only_max_sharpe(mu, cov, rf=0.0, cap=1.0, budget=1.0)
        assert warning is None
        assert w == pytest.approx([0.0, 1.0], abs=1e-6)

    def test_max_sharpe_unavailable_when_no_mix_beats_risk_free(self):
        w, warning = _long_only_max_sharpe(
            np.array([0.01, 0.02]), np.eye(2), rf=0.05, cap=1.0, budget=1.0
        )
        assert w is None
        assert "above the risk-free rate" in warning

    def test_max_sharpe_unavailable_when_cap_forces_negative_excess(self):
        # Only 20% may go to the single positive-excess ticker; the rest must sit in losers.
        mu = np.array([0.30, -0.10, -0.10, -0.10, -0.10])
        w, warning = _long_only_max_sharpe(mu, np.eye(5), rf=0.0, cap=0.2, budget=1.0)
        assert w is None
        assert "above the risk-free rate" in warning

    def test_max_sharpe_unavailable_for_zero_volatility_solution(self):
        w, warning = _long_only_max_sharpe(
            np.array([0.10, 0.05]), np.diag([0.0, 1.0]), rf=0.0, cap=1.0, budget=1.0
        )
        assert w is None
        assert "zero historical volatility" in warning

    def test_min_variance_handles_singular_covariance(self):
        w = _long_only_min_variance(np.array([[1.0, 1.0], [1.0, 1.0]]), cap=1.0, budget=1.0)
        _assert_feasible(w, 1.0, 1.0)

    @pytest.mark.parametrize("cap,budget", [(0.2, 1.0), (0.15, 0.9), (1.0, 1.0)])
    def test_many_assets_respect_bounds_and_budget(self, cap, budget):
        mu, cov = _random_problem(12)
        _assert_feasible(_long_only_min_variance(cov, cap, budget), cap, budget)
        w_ms, warning = _long_only_max_sharpe(mu, cov, rf=0.0, cap=cap, budget=budget)
        assert warning is None
        _assert_feasible(w_ms, cap, budget)

    def test_max_sharpe_is_at_least_as_good_as_equal_weight(self):
        mu, cov = _random_problem(10)
        w_ms, _ = _long_only_max_sharpe(mu, cov, rf=0.0, cap=0.25, budget=1.0)
        w_ew = np.full(10, 0.1)

        def sharpe(w):
            return (w @ mu) / np.sqrt(w @ cov @ w)

        assert sharpe(w_ms) >= sharpe(w_ew) - 1e-9

    def test_max_return_weights_fills_best_tickers_to_cap(self):
        w = _max_return_weights(np.array([0.1, 0.3, 0.2]), cap=0.4, budget=1.0)
        assert w == pytest.approx([0.2, 0.4, 0.4])

    def test_validated_solution_rejects_instead_of_repairing(self):
        assert _validated_solution(SimpleNamespace(success=False, x=np.array([0.5, 0.5])), 1.0, 1.0) is None
        assert _validated_solution(SimpleNamespace(success=True, x=np.array([0.7, 0.7])), 1.0, 1.0) is None
        assert _validated_solution(SimpleNamespace(success=True, x=np.array([-0.1, 1.1])), 1.0, 1.0) is None
        assert _validated_solution(SimpleNamespace(success=True, x=np.array([0.6, 0.4])), 0.5, 1.0) is None
        assert _validated_solution(SimpleNamespace(success=True, x=np.array([np.nan, 1.0])), 1.0, 1.0) is None
        ok = _validated_solution(SimpleNamespace(success=True, x=np.array([-1e-9, 1.0])), 1.0, 1.0)
        assert ok == pytest.approx([0.0, 1.0])

    def test_frontier_respects_bounds_and_spans_min_variance_to_max_return(self):
        mu, cov = _random_problem(8)
        cap, budget = 0.3, 1.0
        w_mv = _long_only_min_variance(cov, cap, budget)
        points = _long_only_frontier(mu, cov, w_mv, cap, budget, cash_return=0.0)
        mv_ret = float(w_mv @ mu)
        mv_vol = float(np.sqrt(w_mv @ cov @ w_mv))
        max_ret = float(_max_return_weights(mu, cap, budget) @ mu)

        assert len(points) >= 20
        for p in points:
            assert mv_ret - 1e-3 <= p["return"] <= max_ret + 1e-3
            assert p["volatility"] >= mv_vol - 1e-3
        vols = [p["volatility"] for p in points]
        assert vols == sorted(vols)

        for target in np.linspace(mv_ret, max_ret, 5):
            w = _long_only_min_variance(cov, cap, budget, mu=mu, target_return=float(target), x0=w_mv)
            _assert_feasible(w, cap, budget)
            assert float(w @ mu) == pytest.approx(target, abs=1e-6)

    def test_frontier_collapses_to_one_point_when_cap_forces_equal_weight(self):
        mu, cov = _random_problem(4)
        w_mv = _long_only_min_variance(cov, 0.25, 1.0)
        assert w_mv == pytest.approx([0.25] * 4, abs=1e-6)
        assert len(_long_only_frontier(mu, cov, w_mv, 0.25, 1.0, cash_return=0.0)) == 1

    def test_frontier_returns_include_cash_at_risk_free(self):
        mu, cov = _random_problem(5)
        w_mv = _long_only_min_variance(cov, 1.0, 0.9)
        points = _long_only_frontier(mu, cov, w_mv, 1.0, 0.9, cash_return=0.1 * 0.05)
        assert points[0]["return"] == pytest.approx(float(w_mv @ mu) + 0.005, abs=1e-4)

    def test_cap_infeasible_warning_names_minimum_cap(self):
        msg = _cap_infeasible_warning(3, 0.2, 0.1)
        assert "3 × 20% = 60%" in msg
        assert "90% to be invested after the 10% Cash Reserve" in msg
        assert "at least 30%" in msg
        assert "at least 33.4%" in _cap_infeasible_warning(3, 0.2, 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# 5. optimize_portfolio() — Long-Only mode and Equal Weight integration
# ─────────────────────────────────────────────────────────────────────────────

LO_TICKERS = [f"POE_LO{i}" for i in range(6)]


def _seed_long_only_account(name, tickers, seed=21, n_days=252):
    for t in tickers:
        _seed_asset_profile(t, f"{t} plc")
    aid = create_account(name, "GBP")
    for i, t in enumerate(tickers):
        add_transaction(aid, "Buy", "2026-01-05", ticker=t, currency="GBP",
                         quantity=10 + i, unit_price=50 + 5 * i, exchange_rate=1.0)
    rng = np.random.default_rng(seed)
    dates = _bdate_strings(n_days)
    _seed_returns_cache(
        {t: rng.normal(0.0004 + 0.0001 * i, 0.008 + 0.002 * i, n_days).tolist()
         for i, t in enumerate(tickers)},
        dates,
    )
    return aid


def _run(aid, **kwargs):
    with patch("xray_engine.load_config", return_value=_builtin_config()), \
         patch("portfolio_optimizer_engine.load_config", return_value=_builtin_config()):
        return optimize_portfolio(f"acct:{aid}", **kwargs)


class TestOptimizePortfolioLongOnly:
    def test_long_only_weights_respect_cap_and_budget(self):
        aid = _seed_long_only_account("PoeLoCapAcc", LO_TICKERS)
        report = _run(aid, mode=MODE_LONG_ONLY, max_weight=0.2)

        assert report["status"] == "success"
        assert report["mode"] == MODE_LONG_ONLY
        assert report["max_weight"] == 0.2
        assert report["cash_reserve"] == 0.0
        for key in ("suggested_weight_mv", "suggested_weight_ms", "suggested_weight_ew"):
            ws = [w[key] for w in report["weights"]]
            assert sum(ws) == pytest.approx(1.0, abs=1e-3)
            assert min(ws) >= 0.0
            assert max(ws) <= 0.2 + 1e-4
        assert all(w["is_short"] is False for w in report["weights"])
        assert all(w["suggested_weight_ew"] == pytest.approx(1 / 6, abs=1e-4) for w in report["weights"])

        frontier = report["efficient_frontier"]
        assert frontier["points"]
        assert frontier["min_variance"] is not None
        assert frontier["equal_weight"] is not None
        assert frontier["current"] is not None
        assert report["estimation_window"]["trading_days"] == 252
        assert report["estimation_window"]["start"] < report["estimation_window"]["end"]

    def test_cash_reserve_lowers_invested_sum(self):
        aid = _seed_long_only_account("PoeLoCashAcc", LO_TICKERS, seed=22)
        report = _run(aid, mode=MODE_LONG_ONLY, max_weight=0.3, cash_reserve=0.1)

        assert report["cash_reserve"] == 0.1
        for key in ("suggested_weight_mv", "suggested_weight_ew"):
            assert sum(w[key] for w in report["weights"]) == pytest.approx(0.9, abs=1e-3)
        assert all(w["suggested_weight_ew"] == pytest.approx(0.15, abs=1e-4) for w in report["weights"])

    def test_infeasible_cap_returns_no_weights_with_warning(self):
        aid = _seed_long_only_account("PoeLoInfeasibleAcc", LO_TICKERS[:3], seed=23)
        report = _run(aid, mode=MODE_LONG_ONLY, max_weight=0.2)

        assert report["status"] == "success"
        assert report["weights"] is None
        assert report["efficient_frontier"] is None
        assert any("can't be met with 3 tickers" in w for w in report["data_warnings"])
        assert report["estimation_window"]["trading_days"] == 252

    def test_feasibility_uses_tickers_left_after_exclusions(self):
        aid = _seed_long_only_account("PoeLoExclusionAcc", LO_TICKERS[:2], seed=24)
        with patch("portfolio_optimizer_engine.fetch_close_returns_from_parquet", return_value=pd.DataFrame()):
            report = _run(aid, include_tickers=LO_TICKERS[:2] + ["POE_LO_NOHIST"],
                          mode=MODE_LONG_ONLY, max_weight=0.4)

        assert report["weights"] is None
        assert any("excluded" in w and "POE_LO_NOHIST" in w for w in report["data_warnings"])
        assert any("can't be met with 2 tickers" in w for w in report["data_warnings"])

    def test_two_assets_uncapped_long_only(self):
        aid = _seed_long_only_account("PoeLoTwoAcc", LO_TICKERS[:2], seed=25)
        report = _run(aid, mode=MODE_LONG_ONLY, max_weight=1.0)
        assert len(report["weights"]) == 2
        assert sum(w["suggested_weight_mv"] for w in report["weights"]) == pytest.approx(1.0, abs=1e-3)

    def test_duplicate_series_warns_ill_conditioned_but_still_solves(self):
        tickers = ["POE_LO_DUP1", "POE_LO_DUP2", "POE_LO_DUP3"]
        aid = _seed_long_only_account("PoeLoDupAcc", tickers, seed=26)
        rets = np.random.default_rng(27).normal(0.0005, 0.01, 252).tolist()
        _seed_returns_cache({t: rets for t in tickers}, _bdate_strings(252))
        report = _run(aid, mode=MODE_LONG_ONLY, max_weight=0.5)

        assert any("near-singular" in w for w in report["data_warnings"])
        ws = [w["suggested_weight_mv"] for w in report["weights"]]
        assert sum(ws) == pytest.approx(1.0, abs=1e-3)
        assert max(ws) <= 0.5 + 1e-4

    def test_all_returns_below_risk_free_leaves_max_sharpe_unavailable(self):
        tickers = ["POE_LO_NEG1", "POE_LO_NEG2"]
        aid = _seed_long_only_account("PoeLoNegAcc", tickers, seed=28)
        rng = np.random.default_rng(29)
        _seed_returns_cache(
            {t: rng.normal(-0.002, 0.01, 252).tolist() for t in tickers}, _bdate_strings(252)
        )
        report = _run(aid, mode=MODE_LONG_ONLY, max_weight=1.0)

        assert all(w["suggested_weight_ms"] is None for w in report["weights"])
        assert all(w["suggested_weight_mv"] is not None for w in report["weights"])
        assert report["efficient_frontier"]["max_sharpe"] is None
        assert any("above the risk-free rate" in w for w in report["data_warnings"])

    def test_unconstrained_default_keeps_closed_form_weights(self):
        aid = _seed_long_only_account("PoeUncAcc", LO_TICKERS[:3], seed=30)
        report = _run(aid)

        assert report["mode"] == "unconstrained"
        assert report["max_weight"] is None
        assert report["cash_reserve"] is None
        assert len(report["efficient_frontier"]["points"]) == 25

        df, _, _ = _returns_matrix_for_candidates(LO_TICKERS[:3])
        expected = _closed_form_weights(
            df.mean(axis=0).to_numpy() * 252, df.cov().to_numpy() * 252, 0.045
        )
        by_symbol = {w["symbol"]: w for w in report["weights"]}
        for i, t in enumerate(df.columns):
            assert by_symbol[t]["suggested_weight_mv"] == pytest.approx(round(float(expected["w_mv"][i]), 4))
            assert by_symbol[t]["suggested_weight_ms"] == pytest.approx(round(float(expected["w_ms"][i]), 4))
            assert by_symbol[t]["suggested_weight_ew"] == pytest.approx(round(1 / 3, 4))

    def test_unconstrained_ignores_cap_and_cash_arguments(self):
        aid = _seed_long_only_account("PoeUncIgnoreAcc", LO_TICKERS[:3], seed=31)
        plain = _run(aid)
        with_args = _run(aid, max_weight=0.2, cash_reserve=0.5)
        assert with_args["weights"] == plain["weights"]
        assert with_args["max_weight"] is None and with_args["cash_reserve"] is None


class TestDropShortHistory:
    def _frame(self, lengths, n_days=60):
        idx = pd.bdate_range("2025-01-01", periods=n_days)
        rng = np.random.default_rng(5)
        return pd.DataFrame({
            t: pd.Series(rng.normal(0, 0.01, n), index=idx[-n:]) for t, n in lengths.items()
        }, index=idx)

    def test_keeps_everything_when_overlap_is_enough(self):
        df, removed = _drop_short_history(self._frame({"A": 60, "B": 40}))
        assert list(df.columns) == ["A", "B"]
        assert removed == []

    def test_removes_only_as_many_as_needed_shortest_first(self):
        df, removed = _drop_short_history(self._frame({"A": 60, "B": 60, "C": 12, "D": 20, "E": 45}))
        assert removed == [("C", 12), ("D", 20)]
        assert set(df.columns) == {"A", "B", "E"}
        assert len(df.dropna(how="any")) >= 30

    def test_ties_break_on_ticker(self):
        _, removed = _drop_short_history(self._frame({"A": 60, "Z": 10, "M": 10}))
        assert removed == [("M", 10), ("Z", 10)]

    def test_stops_at_one_ticker(self):
        df, removed = _drop_short_history(self._frame({"A": 20, "B": 10}))
        assert removed == [("B", 10)]
        assert list(df.columns) == ["A"]


class TestShortHistoryWarningInReport:
    def test_report_names_removed_ticker_and_still_optimizes(self):
        aid = _seed_long_only_account("PoeShortHistAcc", LO_TICKERS[:3], seed=40)
        dates = _bdate_strings(252)
        short = pd.Series(np.random.default_rng(41).normal(0, 0.01, 15), index=pd.to_datetime(dates[-15:]))
        with patch(
            "portfolio_optimizer_engine.fetch_close_returns_from_parquet",
            return_value=pd.DataFrame({"POE_NEWLIST": short}),
        ):
            report = _run(aid, include_tickers=LO_TICKERS[:3] + ["POE_NEWLIST"])

        assert {w["symbol"] for w in report["weights"]} == set(LO_TICKERS[:3])
        assert any(w.startswith("Removed POE_NEWLIST (15 days)") for w in report["data_warnings"])
        assert not any("no aligned return history" in w for w in report["data_warnings"])
