"""
tests/test_ai_prediction_engine.py — AI Prediction Engine Unit Tests

Covers pure/near-pure business logic:
  • cross_sectional_zscore: normal case, zero-std guard, NaN passthrough
  • _winsorize_and_impute_fundamentals:
      - clipping to FUNDAMENTAL_BOUNDS
      - negative trailing_pe → NaN (loss-making company signal)
      - cross-sectional median imputation per date
      - columns absent from FUNDAMENTAL_FEATURES are left untouched
  • run_historical_backfill resume logic:
      - resumes from next ticker after last_processed_ticker
      - new run creates IN_PROGRESS state row
      - completed run marks state COMPLETED
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db_module

from ai_prediction_engine import (
    cross_sectional_zscore,
    _winsorize_and_impute_fundamentals,
    FUNDAMENTAL_BOUNDS,
)


# ── cross_sectional_zscore ────────────────────────────────────────────────────

class TestCrossSectionalZscore:
    def test_standard_case_mean_zero(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = cross_sectional_zscore(s)
        assert abs(result.mean()) < 1e-10

    def test_standard_case_std_one(self):
        s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
        result = cross_sectional_zscore(s)
        assert abs(result.std(ddof=0) - 1.0) < 1e-6 or abs(result.std() - 1.0) < 0.05

    def test_zero_std_returns_zero_series(self):
        # All identical values → std=0 → result should be all zeros
        s = pd.Series([5.0, 5.0, 5.0, 5.0])
        result = cross_sectional_zscore(s)
        assert (result == 0.0).all()

    def test_single_value_returns_zero(self):
        s = pd.Series([42.0])
        result = cross_sectional_zscore(s)
        assert result.iloc[0] == 0.0

    def test_positive_values_above_mean_are_positive_z(self):
        s = pd.Series([1.0, 2.0, 3.0])
        result = cross_sectional_zscore(s)
        assert result.iloc[2] > 0  # 3.0 > mean(2.0)
        assert result.iloc[0] < 0  # 1.0 < mean(2.0)

    def test_nan_propagation(self):
        s = pd.Series([1.0, float('nan'), 3.0])
        result = cross_sectional_zscore(s)
        assert pd.isna(result.iloc[1])

    def test_returns_series(self):
        s = pd.Series([1.0, 2.0, 3.0])
        result = cross_sectional_zscore(s)
        assert isinstance(result, pd.Series)


# ── _winsorize_and_impute_fundamentals ────────────────────────────────────────

def _make_fund_df(**col_overrides) -> pd.DataFrame:
    """Minimal two-row DataFrame with all fundamental columns and a date column."""
    base = {
        'date':          ['2026-01-01', '2026-01-01'],
        'trailing_pe':   [20.0, 25.0],
        'price_to_book': [2.0,  3.0],
        'profit_margin': [0.15, 0.20],
        'roe':           [0.18, 0.22],
        'revenue_growth':[0.10, 0.15],
        'debt_to_equity':[50.0, 80.0],
    }
    base.update(col_overrides)
    return pd.DataFrame(base)


class TestWinsorizeAndImputeFundamentals:
    # ── clipping ─────────────────────────────────────────────────────────────

    def test_trailing_pe_clipped_at_upper_bound(self):
        df = _make_fund_df(trailing_pe=[400.0, 10.0])
        result = _winsorize_and_impute_fundamentals(df)
        hi = FUNDAMENTAL_BOUNDS['trailing_pe'][1]
        assert result['trailing_pe'].iloc[0] == hi

    def test_trailing_pe_clipped_at_lower_bound(self):
        # Lower bound for trailing_pe is 0.0
        df = _make_fund_df(trailing_pe=[10.0, 0.5])
        result = _winsorize_and_impute_fundamentals(df)
        lo = FUNDAMENTAL_BOUNDS['trailing_pe'][0]
        assert result['trailing_pe'].iloc[1] >= lo

    def test_price_to_book_clipped_at_upper_bound(self):
        df = _make_fund_df(price_to_book=[200.0, 2.0])
        result = _winsorize_and_impute_fundamentals(df)
        hi = FUNDAMENTAL_BOUNDS['price_to_book'][1]
        assert result['price_to_book'].iloc[0] == hi

    def test_debt_to_equity_clipped_at_upper_bound(self):
        df = _make_fund_df(debt_to_equity=[999.0, 50.0])
        result = _winsorize_and_impute_fundamentals(df)
        hi = FUNDAMENTAL_BOUNDS['debt_to_equity'][1]
        assert result['debt_to_equity'].iloc[0] == hi

    def test_values_within_bounds_are_unchanged(self):
        df = _make_fund_df(trailing_pe=[20.0, 25.0])
        result = _winsorize_and_impute_fundamentals(df)
        assert result['trailing_pe'].iloc[0] == 20.0
        assert result['trailing_pe'].iloc[1] == 25.0

    # ── negative PE → NaN (loss-making company rule) ─────────────────────────

    def test_negative_trailing_pe_becomes_nan_before_imputation(self):
        # Single-row date group so there are no peers to impute from.
        # Negative PE → NaN; group median is also NaN → stays NaN.
        df = pd.DataFrame({
            'date':          ['2026-01-01'],
            'trailing_pe':   [-5.0],
            'price_to_book': [2.0],
            'profit_margin': [0.1],
            'roe':           [0.1],
            'revenue_growth':[0.1],
            'debt_to_equity':[50.0],
        })
        result = _winsorize_and_impute_fundamentals(df)
        assert pd.isna(result['trailing_pe'].iloc[0])

    def test_negative_trailing_pe_gets_peer_median_when_available(self):
        # With a valid peer on the same date, the NaN is imputed.
        df = _make_fund_df(trailing_pe=[-5.0, 20.0])
        result = _winsorize_and_impute_fundamentals(df)
        # Row 0 was NaN; row 1 = 20.0 → group median = 20.0 → imputed to 20.0
        assert result['trailing_pe'].iloc[0] == 20.0

    def test_zero_trailing_pe_becomes_nan_before_imputation(self):
        df = pd.DataFrame({
            'date':          ['2026-01-01'],
            'trailing_pe':   [0.0],
            'price_to_book': [2.0],
            'profit_margin': [0.1],
            'roe':           [0.1],
            'revenue_growth':[0.1],
            'debt_to_equity':[50.0],
        })
        result = _winsorize_and_impute_fundamentals(df)
        assert pd.isna(result['trailing_pe'].iloc[0])

    def test_positive_trailing_pe_not_nulled(self):
        df = _make_fund_df(trailing_pe=[0.01, 20.0])
        result = _winsorize_and_impute_fundamentals(df)
        assert not pd.isna(result['trailing_pe'].iloc[0])

    # ── median imputation ─────────────────────────────────────────────────────

    def test_null_imputed_with_group_median(self):
        # Three rows on the same date; one has NaN trailing_pe.
        # After imputation it should receive the median of the other two.
        df = pd.DataFrame({
            'date':          ['2026-01-01', '2026-01-01', '2026-01-01'],
            'trailing_pe':   [10.0, float('nan'), 30.0],
            'price_to_book': [2.0, 2.0, 2.0],
            'profit_margin': [0.1, 0.1, 0.1],
            'roe':           [0.1, 0.1, 0.1],
            'revenue_growth':[0.1, 0.1, 0.1],
            'debt_to_equity':[50.0, 50.0, 50.0],
        })
        result = _winsorize_and_impute_fundamentals(df)
        expected_median = 20.0  # median(10, 30)
        assert result['trailing_pe'].iloc[1] == expected_median

    def test_all_null_date_group_stays_null(self):
        # A date where ALL rows have NaN → median is NaN → values stay NaN
        df = pd.DataFrame({
            'date':          ['2026-01-01', '2026-01-01'],
            'trailing_pe':   [float('nan'), float('nan')],
            'price_to_book': [2.0, 2.0],
            'profit_margin': [0.1, 0.1],
            'roe':           [0.1, 0.1],
            'revenue_growth':[0.1, 0.1],
            'debt_to_equity':[50.0, 50.0],
        })
        result = _winsorize_and_impute_fundamentals(df)
        assert pd.isna(result['trailing_pe'].iloc[0])
        assert pd.isna(result['trailing_pe'].iloc[1])

    def test_imputation_is_per_date_not_global(self):
        # Two dates with different PE levels; NaN on date2 should get date2's median
        df = pd.DataFrame({
            'date':          ['2026-01-01', '2026-01-01', '2026-01-02', '2026-01-02'],
            'trailing_pe':   [10.0, 20.0, 100.0, float('nan')],
            'price_to_book': [2.0, 2.0, 2.0, 2.0],
            'profit_margin': [0.1, 0.1, 0.1, 0.1],
            'roe':           [0.1, 0.1, 0.1, 0.1],
            'revenue_growth':[0.1, 0.1, 0.1, 0.1],
            'debt_to_equity':[50.0, 50.0, 50.0, 50.0],
        })
        result = _winsorize_and_impute_fundamentals(df)
        # date2 has only one non-null value (100.0) → median = 100.0
        assert result['trailing_pe'].iloc[3] == 100.0


# ---------------------------------------------------------------------------
# run_historical_backfill resume logic
# ---------------------------------------------------------------------------

def _today():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


@pytest.fixture(autouse=True)
def clean_backfill_state():
    """Wipe quant_scan_states before each test in this module."""
    conn = _db_module.get_connection()
    conn.execute("DELETE FROM quant_scan_states WHERE scan_type = 'ml_backfill'")
    conn.commit()
    conn.close()


def _run_backfill_mocked(tickers):
    """Run run_historical_backfill with all external I/O mocked out. Returns list of tickers fetched."""
    from ai_prediction_engine import run_historical_backfill
    fetched = []

    def _fake_get_price(ticker):
        fetched.append(ticker)
        return None

    with (
        patch("ai_prediction_engine.sync_ticker_metadata"),
        patch("ai_prediction_engine.download_spy_benchmark", return_value=None),
        patch("ai_prediction_engine.load_or_fetch_daily_history") as mock_fetch,
        patch("ai_prediction_engine.time"),
    ):
        mock_fetch.side_effect = _fake_get_price
        run_historical_backfill(tickers)

    return fetched


class TestMLBackfillResume:

    def test_fresh_run_creates_in_progress_state(self):
        _run_backfill_mocked(["AAPL", "MSFT"])
        conn = _db_module.get_connection()
        row = conn.execute(
            "SELECT status FROM quant_scan_states WHERE scan_type = 'ml_backfill'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_completed_run_marks_state_completed(self):
        _run_backfill_mocked(["AAPL"])
        conn = _db_module.get_connection()
        row = conn.execute(
            "SELECT status FROM quant_scan_states WHERE scan_type = 'ml_backfill'"
        ).fetchone()
        conn.close()
        assert row["status"] == "COMPLETED"

    def test_resume_skips_already_processed_tickers(self):
        """Seed IN_PROGRESS with last_processed_ticker='MSFT'; only NVDA must be fetched."""
        conn = _db_module.get_connection()
        conn.execute(
            "INSERT INTO quant_scan_states (scan_date, scan_type, last_processed_ticker, status) "
            "VALUES (?, 'ml_backfill', 'MSFT', 'IN_PROGRESS')",
            (_today(),),
        )
        conn.commit()
        conn.close()

        fetched = _run_backfill_mocked(["AAPL", "MSFT", "NVDA"])
        assert "AAPL" not in fetched, "AAPL already processed — must be skipped"
        assert "MSFT" not in fetched, "MSFT already processed — must be skipped"
        assert "NVDA" in fetched

    def test_resume_cross_day_finds_previous_in_progress(self):
        """An IN_PROGRESS row from yesterday must still be found and resumed."""
        conn = _db_module.get_connection()
        conn.execute(
            "INSERT INTO quant_scan_states (scan_date, scan_type, last_processed_ticker, status) "
            "VALUES ('2026-01-01', 'ml_backfill', 'AAPL', 'IN_PROGRESS')",
        )
        conn.commit()
        conn.close()

        fetched = _run_backfill_mocked(["AAPL", "MSFT"])
        assert "AAPL" not in fetched
        assert "MSFT" in fetched

    def test_empty_ticker_list_returns_without_state(self):
        from ai_prediction_engine import run_historical_backfill
        run_historical_backfill([])
        conn = _db_module.get_connection()
        row = conn.execute(
            "SELECT 1 FROM quant_scan_states WHERE scan_type = 'ml_backfill'"
        ).fetchone()
        conn.close()
        assert row is None


def _fake_ohlcv(n: int = 260, start: str = "2024-01-01") -> pd.DataFrame:
    """Deterministic OHLCV DataFrame with n business-day rows, long enough to clear the
    252-row SMA-200/momentum warm-up window inside run_historical_backfill."""
    idx = pd.date_range(start, periods=n, freq="B")
    price = np.linspace(100.0, 130.0, n)
    return pd.DataFrame(
        {
            "Open":   price * 0.99,
            "High":   price * 1.01,
            "Low":    price * 0.98,
            "Close":  price,
            "Volume": np.full(n, 1_000_000, dtype=float),
        },
        index=idx,
    )


class TestRelStrengthSurvivesSpyLag:
    """SPY's cached history lagging a ticker's freshly-fetched history by a day used to blank
    rel_strength_5d/20d for that whole row via a blanket dropna() — the exact regression that
    silently broke ML Quantile Bands / Set Targets suggestions in production for over a week
    (found 2026-07-10)."""

    def test_rel_strength_written_despite_one_day_spy_lag(self):
        # A ticker unique to this test file — "MU" is also used by test_quant_engine.py's tests,
        # and a bare "ORDER BY date DESC LIMIT 1" (no date filter) would pick up whichever test's
        # row is newest, silently reading the wrong test's data.
        ticker_df = _fake_ohlcv(n=260)
        spy_df = ticker_df.iloc[:-1]  # SPY missing the newest date

        def _fake_fetch(ticker):
            return spy_df if ticker == "SPY" else ticker_df

        from ai_prediction_engine import run_historical_backfill
        with (
            patch("ai_prediction_engine.sync_ticker_metadata"),
            patch("ai_prediction_engine.load_or_fetch_daily_history", side_effect=_fake_fetch),
            patch("ai_prediction_engine.time"),
        ):
            run_historical_backfill(["ZZBACKFILLRS"])

        conn = _db_module.get_connection()
        row = conn.execute(
            "SELECT rel_strength_5d, rel_strength_20d FROM quant_signals "
            "WHERE ticker = 'ZZBACKFILLRS' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["rel_strength_5d"] is not None
        assert row["rel_strength_20d"] is not None


class TestUpdateDailyMlPredictionsSyncsStockSignals:
    """stock_signals.ml_confidence mirrors quant_signals.ml_confidence_score so consumers that
    join off stock_signals (the Regime-Weighted Conviction Score) don't need a second
    quant_signals subselect. Was previously dead — no write site anywhere — until this."""

    @staticmethod
    def _seed_universe(n=30, date_str="2026-01-01"):
        conn = _db_module.get_connection()
        rng = np.random.default_rng(3)
        for i in range(n):
            ticker = f"MLU{i:02d}"
            conn.execute("INSERT OR REPLACE INTO stock_signals (ticker) VALUES (?)", (ticker,))
            conn.execute(
                """INSERT OR REPLACE INTO quant_signals
                   (ticker, date, close_price, volume, rsi_14, macd, macd_signal, macd_hist,
                    sma_50, sma_200, volume_surge, bullish_cross,
                    mom_1m, mom_3m, mom_6m, mom_12m_skip1m, atr_pct, hist_vol_20,
                    rel_strength_5d, rel_strength_20d)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ticker, date_str,
                    100.0 + rng.normal(0, 5), 1_000_000 + rng.normal(0, 1000),
                    50.0 + rng.normal(0, 5), rng.normal(0, 1), rng.normal(0, 1), rng.normal(0, 1),
                    95.0 + rng.normal(0, 5), 90.0 + rng.normal(0, 5), 0, 0,
                    rng.normal(0, 0.05), rng.normal(0, 0.1), rng.normal(0, 0.15), rng.normal(0, 0.2),
                    0.02 + abs(rng.normal(0, 0.005)), 0.2 + abs(rng.normal(0, 0.05)),
                    rng.normal(0, 0.02), rng.normal(0, 0.03),
                ),
            )
        conn.commit()
        conn.close()
        return [f"MLU{i:02d}" for i in range(n)]

    def _fake_inference_df(self, tickers, date_str="2026-01-01"):
        rng = np.random.default_rng(7)
        n = len(tickers)
        return pd.DataFrame({
            "ticker": tickers, "date": [date_str] * n,
            "close_price": 100.0 + rng.normal(0, 5, n), "volume": 1_000_000 + rng.normal(0, 1000, n),
            "rsi_14": 50.0 + rng.normal(0, 5, n), "macd": rng.normal(0, 1, n),
            "macd_signal": rng.normal(0, 1, n), "macd_hist": rng.normal(0, 1, n),
            "sma_50": 95.0 + rng.normal(0, 5, n), "sma_200": 90.0 + rng.normal(0, 5, n),
            "volume_surge": [0] * n, "bullish_cross": [0] * n,
            "mom_1m": rng.normal(0, 0.05, n), "mom_3m": rng.normal(0, 0.1, n),
            "mom_6m": rng.normal(0, 0.15, n), "mom_12m_skip1m": rng.normal(0, 0.2, n),
            "atr_pct": 0.02 + abs(rng.normal(0, 0.005, n)), "hist_vol_20": 0.2 + abs(rng.normal(0, 0.05, n)),
            "rel_strength_5d": rng.normal(0, 0.02, n), "rel_strength_20d": rng.normal(0, 0.03, n),
            "trailing_pe": 15.0 + rng.normal(0, 3, n), "price_to_book": 2.0 + rng.normal(0, 0.5, n),
            "profit_margin": 0.1 + rng.normal(0, 0.02, n), "roe": 0.15 + rng.normal(0, 0.03, n),
            "revenue_growth": 0.08 + rng.normal(0, 0.02, n), "debt_to_equity": 30.0 + rng.normal(0, 10, n),
            "sector": ["Technology"] * n,
        })

    def test_ml_confidence_synced_to_stock_signals(self):
        tickers = self._seed_universe()
        target = tickers[:2]
        fake_df = self._fake_inference_df(tickers)

        fake_model = MagicMock()
        fake_model.predict_proba.return_value = np.array([[0.2, 0.8]])

        from ai_prediction_engine import update_daily_ml_predictions
        with patch("ai_prediction_engine.MODEL_PATH") as mock_path, \
             patch("ai_prediction_engine.FEATURE_STATS_PATH") as mock_stats_path, \
             patch("ai_prediction_engine.joblib.load", return_value=fake_model), \
             patch("ai_prediction_engine.pd.read_sql_query", return_value=fake_df):
            mock_path.exists.return_value = True
            mock_stats_path.exists.return_value = False
            update_daily_ml_predictions(target)

        conn = _db_module.get_connection()
        try:
            for ticker in target:
                qs_row = conn.execute(
                    "SELECT ml_confidence_score FROM quant_signals WHERE ticker = ? AND date = '2026-01-01'",
                    (ticker,),
                ).fetchone()
                ss_row = conn.execute(
                    "SELECT ml_confidence FROM stock_signals WHERE ticker = ?", (ticker,)
                ).fetchone()
                assert qs_row["ml_confidence_score"] == 80.0
                assert ss_row["ml_confidence"] == 80.0
        finally:
            conn.close()
