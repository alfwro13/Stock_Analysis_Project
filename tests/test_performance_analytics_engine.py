"""
tests/test_performance_analytics_engine.py — Portfolio Tearsheet engine tests

Covers:
  • Per-metric pure helpers against synthetic pd.Series with analytically-known answers
  • assemble_performance_report() integration, seeding xray_returns_cache the same way
    tests/test_xray_engine.py does (via database.create_account/add_transaction)
"""

import json
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import database as db
from database import create_account, add_transaction
from xray_engine import BENCHMARK_SYMBOL
from performance_analytics_engine import (
    assemble_performance_report,
    NOT_ENOUGH_DATA_WARNING,
    _calmar_ratio,
    _distribution_stats,
    _downside_deviation,
    _drawdown_stats,
    _max_consecutive,
    _monthly_heatmap_matrix,
    _monthly_returns,
    _omega_ratio,
    _profit_factor,
    _sortino_ratio,
    _win_loss_stats,
)

T1 = "PAE_T1"


def _seed_stock_signal(ticker, price, currency):
    conn = db.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency) VALUES (?, ?, ?)",
        (ticker, price, currency),
    )
    conn.commit()
    conn.close()


def _seed_asset_profile(ticker, sector, country, quote_type="EQUITY"):
    conn = db.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO asset_profiles (ticker, sector, country, quote_type) VALUES (?, ?, ?, ?)",
        (ticker, sector, country, quote_type),
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


def _dated_series(values, start="2025-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="B"))


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pure per-metric helpers — analytically-known answers
# ─────────────────────────────────────────────────────────────────────────────

class TestDownsideDeviation:
    def test_all_positive_returns_is_zero(self):
        assert _downside_deviation(pd.Series([0.01, 0.02, 0.03])) == 0.0

    def test_mixed_returns_positive(self):
        rets = pd.Series([0.01, -0.02, 0.03, -0.01])
        dd = _downside_deviation(rets)
        assert dd > 0


class TestSortinoRatio:
    def test_no_downside_returns_none(self):
        rets = pd.Series([0.01] * 40)
        assert _sortino_ratio(rets, ann_return=0.1, rf=0.045) is None

    def test_positive_excess_return_is_positive_sortino(self):
        rets = pd.Series([0.01, -0.005] * 20)
        result = _sortino_ratio(rets, ann_return=0.20, rf=0.045)
        assert result is not None
        assert result > 0


class TestOmegaRatio:
    def test_all_gains_returns_none(self):
        # No losses below the rf threshold → division by zero guarded → None
        rets = pd.Series([0.01] * 30)
        assert _omega_ratio(rets, rf=0.0) is None

    def test_balanced_gains_and_losses_near_one(self):
        rets = pd.Series([0.01, -0.01] * 20)
        result = _omega_ratio(rets, rf=0.0)
        assert result == pytest.approx(1.0, rel=0.05)


class TestProfitFactor:
    def test_no_losses_returns_none(self):
        assert _profit_factor(pd.Series([0.01, 0.02])) is None

    def test_double_the_gains_is_factor_two(self):
        rets = pd.Series([0.02, -0.01])
        assert _profit_factor(rets) == pytest.approx(2.0)


class TestCalmarRatio:
    def test_zero_drawdown_returns_none(self):
        assert _calmar_ratio(ann_return=0.1, max_dd=0.0) is None

    def test_known_ratio(self):
        assert _calmar_ratio(ann_return=0.20, max_dd=-0.10) == pytest.approx(2.0)


class TestDrawdownStats:
    def test_monotone_rising_has_zero_underwater_time(self):
        rets = _dated_series([0.01] * 10)
        cumulative = (1 + rets).cumprod()
        dd_series = cumulative / cumulative.cummax() - 1
        stats = _drawdown_stats(dd_series)
        assert stats["longest_drawdown_days"] == 0
        assert stats["time_underwater_days"] == 0
        assert stats["ulcer_index"] == 0.0

    def test_single_drawdown_has_nonzero_duration(self):
        # Up, then down for several days — a genuine underwater streak.
        rets = _dated_series([0.05, -0.01, -0.01, -0.01, -0.01, 0.02])
        cumulative = (1 + rets).cumprod()
        dd_series = cumulative / cumulative.cummax() - 1
        stats = _drawdown_stats(dd_series)
        assert stats["longest_drawdown_days"] > 0
        assert stats["ulcer_index"] > 0


class TestWinLossStats:
    def test_all_wins(self):
        result = _win_loss_stats(pd.Series([0.01, 0.02, 0.03]))
        assert result["win_rate"] == 1.0
        assert result["avg_loss"] is None
        assert result["payoff_ratio"] is None

    def test_known_win_rate(self):
        rets = pd.Series([0.01, -0.01, 0.01, -0.01, 0.01])
        result = _win_loss_stats(rets)
        assert result["win_rate"] == pytest.approx(0.6)


class TestMaxConsecutive:
    def test_alternating_never_exceeds_one(self):
        rets = pd.Series([0.01, -0.01, 0.01, -0.01])
        result = _max_consecutive(rets)
        assert result["max_consecutive_wins"] == 1
        assert result["max_consecutive_losses"] == 1

    def test_streak_detected(self):
        rets = pd.Series([0.01, 0.01, 0.01, -0.01])
        result = _max_consecutive(rets)
        assert result["max_consecutive_wins"] == 3
        assert result["max_consecutive_losses"] == 1


class TestDistributionStats:
    def test_best_worst_day(self):
        rets = _dated_series([0.05, -0.03, 0.01, -0.001])
        result = _distribution_stats(rets)
        assert result["best_day"] == pytest.approx(0.05)
        assert result["worst_day"] == pytest.approx(-0.03)


class TestMonthlyReturnsAndHeatmap:
    def test_monthly_returns_compounds_within_month(self):
        rets = _dated_series([0.01, 0.01], start="2025-01-01")
        monthly = _monthly_returns(rets)
        assert len(monthly) == 1
        assert monthly.iloc[0] == pytest.approx((1.01 * 1.01) - 1)

    def test_heatmap_matrix_shape(self):
        rets = _dated_series([0.01] * 40, start="2025-01-01")
        hm = _monthly_heatmap_matrix(rets)
        assert hm["months"] == list(range(1, 13))
        assert len(hm["matrix"]) == len(hm["years"])
        assert all(len(row) == 12 for row in hm["matrix"])


# ─────────────────────────────────────────────────────────────────────────────
# 2. assemble_performance_report() — integration
# ─────────────────────────────────────────────────────────────────────────────

class TestAssemblePerformanceReport:
    def test_not_enough_data_warns_and_leaves_metrics_none(self):
        _seed_stock_signal(T1, 100.0, "GBP")
        _seed_asset_profile(T1, "Technology", "United States")
        aid = create_account("PaeNoDataAcc", "GBP")
        add_transaction(aid, "Buy", "2026-01-05", ticker=T1, currency="GBP",
                         quantity=10, unit_price=80, exchange_rate=1.0)

        with patch("xray_engine.load_config", return_value=_builtin_config()), \
             patch("performance_analytics_engine.load_config", return_value=_builtin_config()):
            report = assemble_performance_report(f"acct:{aid}")

        assert report["status"] == "success"
        assert report["metrics"] is None
        assert NOT_ENOUGH_DATA_WARNING in report["data_warnings"]

    def test_empty_scope_returns_error_status(self):
        aid = create_account("PaeEmptyAcc", "GBP")
        with patch("xray_engine.load_config", return_value=_builtin_config()):
            report = assemble_performance_report(f"acct:{aid}")
        assert report["status"] == "error"

    def test_full_report_populates_all_metric_groups(self):
        _seed_stock_signal(T1, 100.0, "GBP")
        _seed_asset_profile(T1, "Technology", "United States")
        aid = create_account("PaeFullAcc", "GBP")
        add_transaction(aid, "Buy", "2026-01-05", ticker=T1, currency="GBP",
                         quantity=10, unit_price=80, exchange_rate=1.0)

        rng = np.random.default_rng(21)
        t1_rets = rng.normal(0.0004, 0.012, 252).tolist()
        bench_rets = rng.normal(0.0003, 0.007, 252).tolist()
        dates = [f"2025-{(i // 21) + 1:02d}-{(i % 21) + 1:02d}" for i in range(252)]
        _seed_returns_cache({T1: t1_rets, BENCHMARK_SYMBOL: bench_rets}, dates)

        with patch("xray_engine.load_config", return_value=_builtin_config()), \
             patch("performance_analytics_engine.load_config", return_value=_builtin_config()):
            report = assemble_performance_report(f"acct:{aid}")

        assert report["status"] == "success"
        assert report["annualized_return"] is not None
        for group in ("risk_adjusted_ratios", "drawdown_analytics",
                      "distribution_tail_stats", "win_loss_stats"):
            assert group in report["metrics"]
        for chart in ("underwater", "cumulative_growth", "monthly_heatmap", "histogram"):
            assert chart in report["charts"]
        assert len(report["charts"]["underwater"]) == 252
        assert len(report["charts"]["histogram"]["returns"]) == 252
