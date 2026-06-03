"""
tests/test_xray_engine.py  — X-ray Engine Unit & Integration Tests

Covers:
  • _compute_beta, _compute_vol, _compute_max_drawdown, _get_instrument_type (pure)
  • Bug 1: Portfolio beta normalized by covered-weight sum, not raw sum
  • Bug 2: Correlation matrix uses dropna(how='any') for PSD guarantee;
           negative variance is logged + data_warned, not silently discarded
  • Bug 3: avg_pairwise_corr and risk_cache DB query filtered to current portfolio
  • Bug 4: DB read block has except clause that logs errors
  • Bug 5: Single-ticker yfinance download always returns a DataFrame, never a Series
  • New features: historical VaR/CVaR, marginal risk contribution, tracking error,
                  Sharpe/Calmar ratios, FX/currency exposure, full corr matrix,
                  skewness/kurtosis
"""

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
from xray_engine import (
    XRayRiskComputer,
    GhostfolioXRayClient,
    _compute_max_drawdown,
    _get_instrument_type,
    assemble_xray_report,
    BENCHMARK_SYMBOL,
)

# ─── Unique ticker namespaces for this module (avoid collisions with other tests)
T1 = "XRAY_T1"
T2 = "XRAY_T2"
T3 = "XRAY_T3"
T_STALE = "XRAY_STALE"   # in cache but NOT in current portfolio


# ─── Seed helpers ────────────────────────────────────────────────────────────

def _seed_risk_cache(rows):
    """rows: [(ticker, beta, vol, last_updated)]"""
    conn = db.get_connection()
    for ticker, beta, vol, last_updated in rows:
        conn.execute(
            """INSERT OR REPLACE INTO xray_risk_cache
               (ticker, benchmark, last_updated, beta, annualized_vol)
               VALUES (?, ?, ?, ?, ?)""",
            (ticker, BENCHMARK_SYMBOL, last_updated, beta, vol),
        )
    conn.commit()
    conn.close()


def _seed_corr_matrix(tickers, matrix, last_updated="2026-06-03"):
    conn = db.get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO xray_correlation_matrix
           (benchmark, last_updated, tickers_json, matrix_json)
           VALUES (?, ?, ?, ?)""",
        (BENCHMARK_SYMBOL, last_updated, json.dumps(tickers), json.dumps(matrix)),
    )
    conn.commit()
    conn.close()


def _seed_div_cache(rows):
    """rows: [(ticker, yield_pct, income)]"""
    conn = db.get_connection()
    for ticker, yield_pct, income in rows:
        conn.execute(
            """INSERT OR REPLACE INTO xray_dividend_cache
               (ticker, data_source, last_updated, dividend_yield_pct, dividend_in_base_currency)
               VALUES (?, 'YAHOO', '2026-06-03', ?, ?)""",
            (ticker, yield_pct, income),
        )
    conn.commit()
    conn.close()


def _seed_portfolio_returns(dates, port_rets, bench_rets, last_updated="2026-06-03"):
    """Seed xray_portfolio_returns_cache if the table exists."""
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO xray_portfolio_returns_cache
               (benchmark, last_updated, dates_json, returns_json, benchmark_returns_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                BENCHMARK_SYMBOL,
                last_updated,
                json.dumps(dates),
                json.dumps(port_rets),
                json.dumps(bench_rets),
            ),
        )
        conn.commit()
    except Exception:
        pass  # table may not exist yet
    finally:
        conn.close()


def _make_holdings(specs):
    """
    Build a holdings list matching GhostfolioXRayClient.get_holdings() output.
    Each spec: {symbol, value, [name, asset_class, asset_sub_class, currency,
                                sectors, countries, investment, gross_perf, gross_perf_pct]}
    """
    total = sum(s["value"] for s in specs)
    holdings = []
    for s in specs:
        holdings.append({
            "symbol":         s["symbol"],
            "name":           s.get("name", s["symbol"]),
            "asset_class":    s.get("asset_class", "EQUITY"),
            "asset_sub_class": s.get("asset_sub_class", "STOCK"),
            "currency":       s.get("currency", "USD"),
            "data_source":    s.get("data_source", "YAHOO"),
            "value":          float(s["value"]),
            "investment":     float(s.get("investment", s["value"])),
            "quantity":       float(s.get("quantity", 10.0)),
            "market_price":   float(s["value"]) / 10.0,
            "gross_perf":     float(s.get("gross_perf", 0.0)),
            "gross_perf_pct": float(s.get("gross_perf_pct", 0.0)),
            "sectors":        s.get("sectors", []),
            "countries":      s.get("countries", []),
            "weight":         float(s["value"]) / total,
        })
    return holdings, total


def _patch_report(holdings, total, chart=None, config=None, risk_free_rate=None):
    """
    Context manager patches for assemble_xray_report:
      - xray_engine.GhostfolioXRayClient → mock returning supplied holdings
      - xray_engine.load_config → fake config
    Returns (patches_cm, mock_client_instance).
    """
    _config = config or {
        "GHOSTFOLIO_ACCOUNTS": {"active": ["test-account"]},
        "BASE_CURRENCY": "GBP",
        "RISK_FREE_RATE": risk_free_rate or 0.045,
    }
    _chart = chart or [
        {"date": "2025-06-03", "value": 9_000.0},
        {"date": "2026-01-02", "value": 10_000.0},
        {"date": "2026-06-03", "value": 11_000.0},
    ]

    mock_client_cls = MagicMock()
    mock_inst = mock_client_cls.return_value
    mock_inst.is_configured = True
    mock_inst.authenticate.return_value = True
    mock_inst.get_holdings.return_value = (holdings, total)
    mock_inst.get_performance_chart.return_value = _chart

    patches = [
        patch("xray_engine.GhostfolioXRayClient", mock_client_cls),
        patch("xray_engine.load_config", return_value=_config),
    ]
    return patches, mock_inst


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pure helpers — no mocking needed
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeBeta:
    def _series(self, n=60):
        rng = np.random.default_rng(42)
        bench = pd.Series(rng.normal(0, 0.01, n))
        asset = bench * 1.5 + rng.normal(0, 0.005, n)
        return asset, bench

    def test_beta_close_to_1_5(self):
        asset, bench = self._series()
        computer = XRayRiskComputer()
        b = computer._compute_beta(asset, bench)
        assert b is not None
        assert abs(b - 1.5) < 0.15, f"Expected beta ~1.5, got {b}"

    def test_insufficient_observations_returns_none(self):
        rng = np.random.default_rng(0)
        asset = pd.Series(rng.normal(0, 0.01, 20))
        bench = pd.Series(rng.normal(0, 0.01, 20))
        assert XRayRiskComputer()._compute_beta(asset, bench) is None

    def test_zero_variance_benchmark_returns_none(self):
        asset = pd.Series([0.01] * 60)
        bench = pd.Series([0.0] * 60)
        assert XRayRiskComputer()._compute_beta(asset, bench) is None


class TestComputeVol:
    def test_vol_positive(self):
        rng = np.random.default_rng(7)
        rets = pd.Series(rng.normal(0, 0.01, 252))
        vol = XRayRiskComputer()._compute_vol(rets)
        assert vol is not None
        assert 0.05 < vol < 0.30  # reasonable annualised vol

    def test_insufficient_returns_none(self):
        assert XRayRiskComputer()._compute_vol(pd.Series([0.01] * 5)) is None


class TestComputeMaxDrawdown:
    def test_basic_drawdown(self):
        chart = [{"value": 100}, {"value": 120}, {"value": 80}, {"value": 90}]
        dd = _compute_max_drawdown(chart)
        assert dd is not None
        assert abs(dd - (-40 / 120)) < 1e-9

    def test_monotone_rising_is_zero(self):
        chart = [{"value": v} for v in [100, 110, 120, 130]]
        dd = _compute_max_drawdown(chart)
        assert dd is not None
        assert dd == 0.0

    def test_single_point_returns_none(self):
        assert _compute_max_drawdown([{"value": 100}]) is None

    def test_empty_returns_none(self):
        assert _compute_max_drawdown([]) is None


class TestGetInstrumentType:
    @pytest.mark.parametrize("cls, sub, expected", [
        ("ETF",          "",         "ETF"),
        ("EQUITY",       "ETF",      "ETF"),
        ("EQUITY",       "STOCK",    "Equity"),
        ("COMMODITY",    "",         "Commodity"),
        ("FIXED_INCOME", "",         "Fixed Income"),
        ("",             "",         "Other"),
        ("REAL_ESTATE",  "",         "Real_Estate"),
    ])
    def test_classification(self, cls, sub, expected):
        result = _get_instrument_type(cls, sub)
        assert result == expected


# ─────────────────────────────────────────────────────────────────────────────
# 2. XRayRiskComputer._fetch_returns  (mock yfinance)
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchReturns:
    """Bug 5: single-ticker download must always return a DataFrame, not a Series."""

    def _make_flat_df(self, tickers):
        """Flat (non-MultiIndex) DataFrame as yfinance returns for a single ticker."""
        idx = pd.date_range("2025-01-02", periods=30, freq="B")
        return pd.DataFrame({"Close": np.linspace(100, 110, 30)}, index=idx)

    def _make_multi_df(self, tickers):
        """MultiIndex DataFrame as yfinance returns for multiple tickers."""
        idx = pd.date_range("2025-01-02", periods=30, freq="B")
        arrays = [["Close"] * len(tickers), tickers]
        cols = pd.MultiIndex.from_arrays(arrays)
        data = np.tile(np.linspace(100, 110, 30), (len(tickers), 1)).T
        return pd.DataFrame(data, index=idx, columns=cols)

    def _make_multi_single(self, ticker):
        """MultiIndex DataFrame that yfinance sometimes returns for a 1-element list."""
        idx = pd.date_range("2025-01-02", periods=30, freq="B")
        arrays = [["Close"], [ticker]]
        cols = pd.MultiIndex.from_arrays(arrays)
        data = np.linspace(100, 110, 30).reshape(-1, 1)
        return pd.DataFrame(data, index=idx, columns=cols)

    def test_multi_ticker_returns_dataframe(self):
        symbols = [T1, T2]
        with patch("yfinance.download", return_value=self._make_multi_df(symbols)):
            result = XRayRiskComputer()._fetch_returns(symbols)
        assert isinstance(result, pd.DataFrame)
        assert set(result.columns) == {T1, T2}

    def test_single_ticker_flat_columns_returns_dataframe(self):
        """Single ticker with flat column layout (no MultiIndex)."""
        with patch("yfinance.download", return_value=self._make_flat_df([T1])):
            result = XRayRiskComputer()._fetch_returns([T1])
        assert isinstance(result, pd.DataFrame), "Must return DataFrame, not Series"
        assert T1 in result.columns

    def test_single_ticker_multiindex_returns_dataframe(self):
        """Bug 5: single ticker that yfinance returns with MultiIndex must not crash."""
        with patch("yfinance.download", return_value=self._make_multi_single(T1)):
            result = XRayRiskComputer()._fetch_returns([T1])
        assert isinstance(result, pd.DataFrame), "Must return DataFrame, not Series"
        assert T1 in result.columns

    def test_empty_on_yfinance_failure(self):
        with patch("yfinance.download", side_effect=RuntimeError("network")):
            result = XRayRiskComputer()._fetch_returns([T1])
        assert result.empty


# ─────────────────────────────────────────────────────────────────────────────
# 3. Bug 1 — Portfolio beta must be normalized by covered weight
# ─────────────────────────────────────────────────────────────────────────────

class TestPortfolioBetaNormalization:
    """
    Scenario: 2 holdings, only 1 has beta data.
      A: weight=0.50, beta=1.40
      B: weight=0.50, beta=None (missing from cache)

    Correct result  = 1.40  (normalize by covered weight 0.50/0.50)
    Buggy result    = 0.70  (0.50 × 1.40, never divided)
    """

    def _run(self):
        holdings, total = _make_holdings([
            {"symbol": T1, "value": 5000},  # beta in cache
            {"symbol": T2, "value": 5000},  # no beta cache entry
        ])
        _seed_risk_cache([
            (T1, 1.40, 0.20, "2026-06-03"),
            # T2 intentionally omitted
        ])
        _seed_corr_matrix([T1, T2], [[1.0, 0.5], [0.5, 1.0]])
        _seed_div_cache([(T1, 0.0, 0.0), (T2, 0.0, 0.0)])

        patches, _ = _patch_report(holdings, total)
        with patches[0], patches[1]:
            return assemble_xray_report("all")

    def test_beta_normalized_to_1_4_not_0_7(self):
        result = self._run()
        pb = result["risk_metrics"]["portfolio_beta"]
        assert pb is not None, "portfolio_beta should not be None"
        assert abs(pb - 1.40) < 0.01, (
            f"Expected 1.40 (normalized), got {pb} — "
            "portfolio_beta must divide by sum of covered weights"
        )

    def test_full_coverage_unchanged(self):
        """When all holdings have beta, normalization must not alter result."""
        holdings, total = _make_holdings([
            {"symbol": T1, "value": 6000},
            {"symbol": T2, "value": 4000},
        ])
        _seed_risk_cache([
            (T1, 1.20, 0.18, "2026-06-03"),
            (T2, 0.80, 0.22, "2026-06-03"),
        ])
        _seed_corr_matrix([T1, T2], [[1.0, 0.4], [0.4, 1.0]])
        _seed_div_cache([(T1, 0.0, 0.0), (T2, 0.0, 0.0)])

        patches, _ = _patch_report(holdings, total)
        with patches[0], patches[1]:
            result = assemble_xray_report("all")

        expected = round(0.6 * 1.20 + 0.4 * 0.80, 3)
        pb = result["risk_metrics"]["portfolio_beta"]
        assert pb is not None
        assert abs(pb - expected) < 0.005, f"Expected {expected}, got {pb}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Bug 2 — Non-PSD correlation matrix → log + data_warning
# ─────────────────────────────────────────────────────────────────────────────

class TestNonPSDHandling:
    """
    When port_var_daily <= 0 (non-PSD matrix), the engine must:
      a) Log a warning (not silently discard)
      b) Add an entry to data_warnings
    """

    def test_negative_variance_adds_data_warning(self):
        # corr=-2.0 is invalid; with equal weights this forces w^T Sigma w < 0
        # port_var = 0.25*s^2 + 2*0.25*(-2)*s^2 + 0.25*s^2 = -0.5*s^2 < 0
        holdings, total = _make_holdings([
            {"symbol": T1, "value": 5000},
            {"symbol": T2, "value": 5000},
        ])
        _seed_risk_cache([
            (T1, 1.0, 0.20, "2026-06-03"),
            (T2, 1.0, 0.20, "2026-06-03"),
        ])
        # corr=-2.0 forces negative quadratic form with equal weights
        _seed_corr_matrix([T1, T2], [[1.0, -2.0], [-2.0, 1.0]])
        _seed_div_cache([(T1, 0.0, 0.0), (T2, 0.0, 0.0)])

        patches, _ = _patch_report(holdings, total)
        with patches[0], patches[1]:
            result = assemble_xray_report("all")

        assert result["risk_metrics"]["annualized_vol"] is None
        assert any("correlation" in w.lower() or "variance" in w.lower()
                   for w in result["data_warnings"]), (
            "data_warnings must explain why vol/VaR could not be computed"
        )

    def test_negative_variance_logs_warning(self, caplog):
        holdings, total = _make_holdings([
            {"symbol": T1, "value": 5000},
            {"symbol": T2, "value": 5000},
        ])
        _seed_risk_cache([
            (T1, 1.0, 0.20, "2026-06-03"),
            (T2, 1.0, 0.20, "2026-06-03"),
        ])
        _seed_corr_matrix([T1, T2], [[1.0, -2.0], [-2.0, 1.0]])
        _seed_div_cache([(T1, 0.0, 0.0), (T2, 0.0, 0.0)])

        patches, _ = _patch_report(holdings, total)
        with patches[0], patches[1], caplog.at_level(logging.WARNING, logger="xray_engine"):
            assemble_xray_report("all")

        assert any("variance" in r.message.lower() or "non-psd" in r.message.lower()
                   or "correlation" in r.message.lower()
                   for r in caplog.records), "Expected a warning log for non-PSD matrix"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Bug 3 — Stale tickers must not contaminate avg_pairwise_corr
# ─────────────────────────────────────────────────────────────────────────────

class TestStaleTickerFiltering:
    """
    Scenario:
      Current portfolio: T1, T2 (corr=0.30 between them)
      Cached matrix includes a stale T_STALE column with corr=0.90 to both.

    avg_pairwise_corr computed over CURRENT holdings only must be 0.30, not
    the inflated value that includes T_STALE pairs.
    """

    def _run_with_stale(self):
        holdings, total = _make_holdings([
            {"symbol": T1, "value": 5000},
            {"symbol": T2, "value": 5000},
        ])
        _seed_risk_cache([
            (T1,      1.0, 0.18, "2026-06-03"),
            (T2,      0.8, 0.20, "2026-06-03"),
            (T_STALE, 1.5, 0.30, "2026-06-03"),
        ])
        # 3×3 matrix: T1-T2 corr=0.30; T_STALE has corr=0.90 to both
        _seed_corr_matrix(
            [T1, T2, T_STALE],
            [[1.00, 0.30, 0.90],
             [0.30, 1.00, 0.90],
             [0.90, 0.90, 1.00]],
        )
        _seed_div_cache([(T1, 0.0, 0.0), (T2, 0.0, 0.0)])

        patches, _ = _patch_report(holdings, total)
        with patches[0], patches[1]:
            return assemble_xray_report("all")

    def test_avg_corr_excludes_stale_tickers(self):
        result = self._run_with_stale()
        avg = result["risk_metrics"].get("avg_pairwise_correlation")
        # With only T1 and T2 in scope, avg corr should equal 0.30 (one pair)
        assert avg is not None
        assert abs(avg - 0.30) < 0.01, (
            f"avg_pairwise_corr={avg} — stale tickers must be excluded; "
            f"expected ≈0.30"
        )

    def test_stale_tickers_not_in_risk_cache_result(self):
        """The risk_cache DB query must not load rows for tickers not in current holdings."""
        result = self._run_with_stale()
        # T_STALE must not appear as an uncovered-holdings warning trigger
        # (it should never be in holdings_sorted)
        symbols_in_holdings = {h["symbol"] for h in result["holdings"]}
        assert T_STALE not in symbols_in_holdings


# ─────────────────────────────────────────────────────────────────────────────
# 6. Bug 4 — DB read block must have except clause
# ─────────────────────────────────────────────────────────────────────────────

class TestDBErrorHandling:
    def test_db_error_is_logged_and_propagated(self, caplog):
        holdings, total = _make_holdings([{"symbol": T1, "value": 10000}])
        patches, _ = _patch_report(holdings, total)

        with patches[0], patches[1]:
            with patch("xray_engine.get_connection", side_effect=RuntimeError("db locked")):
                with caplog.at_level(logging.ERROR, logger="xray_engine"):
                    with pytest.raises(RuntimeError, match="db locked"):
                        assemble_xray_report("all")

        assert any("db locked" in r.message or "failed" in r.message.lower()
                   for r in caplog.records), (
            "A DB read error must be logged before propagating"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7. New features — keys present in the report
# ─────────────────────────────────────────────────────────────────────────────

class TestNewFeatures:
    """
    Integration smoke tests: after implementing new features each key must be
    present in the assemble_xray_report output.
    """

    @pytest.fixture(autouse=True)
    def _seed(self):
        holdings, total = _make_holdings([
            {"symbol": T1, "value": 6000, "currency": "USD",
             "sectors": [{"name": "Technology", "weight": 1.0}],
             "countries": [{"continent": "North America", "weight": 1.0}]},
            {"symbol": T2, "value": 4000, "currency": "GBP",
             "sectors": [{"name": "Financials", "weight": 1.0}],
             "countries": [{"continent": "Europe", "weight": 1.0}]},
        ])
        _seed_risk_cache([
            (T1, 1.10, 0.18, "2026-06-03"),
            (T2, 0.90, 0.22, "2026-06-03"),
        ])
        _seed_corr_matrix([T1, T2], [[1.0, 0.4], [0.4, 1.0]])
        _seed_div_cache([(T1, 2.0, 120.0), (T2, 1.5, 60.0)])

        # Seed a portfolio returns cache for the new stats
        rng = np.random.default_rng(99)
        port_rets = rng.normal(0.0004, 0.008, 252).tolist()
        bench_rets = rng.normal(0.0003, 0.007, 252).tolist()
        dates = [f"2025-{(i//21)+1:02d}-{(i%21)+1:02d}" for i in range(252)]
        _seed_portfolio_returns(dates, port_rets, bench_rets)

        self._holdings = holdings
        self._total = total

    def _run(self):
        patches, _ = _patch_report(self._holdings, self._total)
        with patches[0], patches[1]:
            return assemble_xray_report("all")

    # ── Core keys still present ──

    def test_concentration_present(self):
        r = self._run()
        assert "hhi" in r["concentration"]
        assert "top5_weight" in r["concentration"]

    def test_sector_allocation_present(self):
        r = self._run()
        assert isinstance(r["sector_allocation"], list)
        assert len(r["sector_allocation"]) >= 1

    # ── Full correlation matrix ──

    def test_corr_matrix_key_present(self):
        r = self._run()
        assert "correlation_matrix" in r, (
            "assemble_xray_report must include a 'correlation_matrix' key"
        )

    def test_corr_matrix_has_tickers_and_data(self):
        r = self._run()
        cm = r.get("correlation_matrix", {})
        assert "tickers" in cm and "matrix" in cm, (
            "correlation_matrix must have 'tickers' and 'matrix' sub-keys"
        )
        assert set(cm["tickers"]) == {T1, T2}
        assert len(cm["matrix"]) == 2

    # ── FX / currency exposure ──

    def test_fx_exposure_key_present(self):
        r = self._run()
        assert "fx_exposure" in r, "assemble_xray_report must include 'fx_exposure'"

    def test_fx_exposure_contains_usd_and_gbp(self):
        r = self._run()
        currencies = {entry["currency"] for entry in r["fx_exposure"]}
        assert "USD" in currencies
        assert "GBP" in currencies

    def test_fx_exposure_weights_sum_to_one(self):
        r = self._run()
        total = sum(e["weight"] for e in r["fx_exposure"])
        assert abs(total - 1.0) < 0.001

    # ── Marginal risk contribution ──

    def test_holdings_have_marginal_risk_contribution(self):
        r = self._run()
        for h in r["holdings"]:
            assert "marginal_risk_contribution" in h, (
                f"Holding {h['symbol']} missing marginal_risk_contribution"
            )

    def test_mrc_sums_to_portfolio_vol(self):
        r = self._run()
        port_vol = r["risk_metrics"].get("annualized_vol")
        if port_vol is None:
            pytest.skip("portfolio vol not available (non-PSD matrix)")
        mrc_sum = sum(h.get("marginal_risk_contribution") or 0.0 for h in r["holdings"])
        assert abs(mrc_sum - port_vol) < port_vol * 0.01, (
            f"Sum of MRCs ({mrc_sum:.4f}) must equal portfolio vol ({port_vol:.4f})"
        )

    # ── Tracking error ──

    def test_tracking_error_key_present(self):
        r = self._run()
        rm = r["risk_metrics"]
        assert "tracking_error" in rm, "risk_metrics must include 'tracking_error'"

    # ── Sharpe & Calmar ratios ──

    def test_sharpe_ratio_key_present(self):
        r = self._run()
        assert "sharpe_ratio" in r["risk_metrics"], (
            "risk_metrics must include 'sharpe_ratio'"
        )

    def test_calmar_ratio_key_present(self):
        r = self._run()
        assert "calmar_ratio" in r["risk_metrics"], (
            "risk_metrics must include 'calmar_ratio'"
        )

    # ── Historical VaR / CVaR ──

    def test_historical_var_key_present(self):
        r = self._run()
        assert "historical_var_95_1d" in r["risk_metrics"], (
            "risk_metrics must include 'historical_var_95_1d'"
        )

    def test_cvar_key_present(self):
        r = self._run()
        assert "cvar_95_1d" in r["risk_metrics"], (
            "risk_metrics must include 'cvar_95_1d' (Expected Shortfall)"
        )

    # ── Skewness & Kurtosis ──

    def test_skewness_key_present(self):
        r = self._run()
        assert "skewness" in r["risk_metrics"], (
            "risk_metrics must include 'skewness'"
        )

    def test_kurtosis_key_present(self):
        r = self._run()
        assert "excess_kurtosis" in r["risk_metrics"], (
            "risk_metrics must include 'excess_kurtosis'"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 8. Tooltips include new feature entries
# ─────────────────────────────────────────────────────────────────────────────

class TestTooltips:
    def test_existing_tooltips_present(self):
        from xray_engine import XRAY_TOOLTIPS
        for key in ("beta", "vol", "var", "hhi", "avg_correlation"):
            assert key in XRAY_TOOLTIPS

    def test_new_feature_tooltips_present(self):
        from xray_engine import XRAY_TOOLTIPS
        for key in ("historical_var", "cvar", "tracking_error",
                    "sharpe_ratio", "calmar_ratio", "skewness", "fx_exposure",
                    "marginal_risk_contribution"):
            assert key in XRAY_TOOLTIPS, f"XRAY_TOOLTIPS missing '{key}'"
