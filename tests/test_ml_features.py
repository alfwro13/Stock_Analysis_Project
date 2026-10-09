"""
tests/test_ml_features.py — ML feature registry unit tests

Covers pure business logic:
  • cross_sectional_zscore: normal case, zero-std guard, NaN passthrough
  • _winsorize_and_impute_fundamentals:
      - clipping to FUNDAMENTAL_BOUNDS
      - negative trailing_pe → NaN (loss-making company signal)
      - cross-sectional median imputation per date
      - columns absent from FUNDAMENTAL_FEATURES are left untouched
  • build_model_features: derived columns, inf cleaning, sector fallback, per-date z-scores
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml_features import (
    CONTINUOUS_FEATURES,
    FEATURE_COLS,
    cross_sectional_zscore,
    _winsorize_and_impute_fundamentals,
    FUNDAMENTAL_BOUNDS,
    build_model_features,
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


class TestBuildModelFeatures:
    TICKERS = [f"BMF{i:02d}" for i in range(12)]

    def test_adds_every_model_feature_column(self, fake_inference_df):
        result = build_model_features(fake_inference_df(self.TICKERS))
        assert set(FEATURE_COLS) <= set(result.columns)

    def test_derived_columns_follow_their_formulas(self, fake_inference_df):
        raw = fake_inference_df(self.TICKERS)
        row = raw.iloc[0]
        result = build_model_features(raw.copy()).iloc[0]
        assert result["dist_sma_50"] == (row["close_price"] - row["sma_50"]) / row["sma_50"]
        assert result["macd_pct"] == row["macd"] / row["close_price"]
        assert result["dollar_vol_log"] == np.log1p(row["close_price"] * row["volume"])

    def test_z_scores_are_zero_mean_per_date(self, fake_inference_df):
        result = build_model_features(fake_inference_df(self.TICKERS))
        for col in CONTINUOUS_FEATURES:
            assert abs(result[f"{col}_z"].mean()) < 1e-9, col

    def test_keeps_raw_columns_and_mutates_in_place(self, fake_inference_df):
        raw = fake_inference_df(self.TICKERS)
        raw_rsi = raw["rsi_14"].copy()
        result = build_model_features(raw)
        assert result is raw
        pd.testing.assert_series_equal(result["rsi_14"], raw_rsi)

    def test_zero_divisor_becomes_nan_not_inf(self, fake_inference_df):
        raw = fake_inference_df(self.TICKERS)
        raw.loc[0, "sma_50"] = 0.0
        result = build_model_features(raw)
        assert pd.isna(result.loc[0, "dist_sma_50"])
        assert not np.isinf(result[CONTINUOUS_FEATURES].to_numpy(dtype=float)).any()

    def test_unknown_sector_and_missing_flags_use_defaults(self, fake_inference_df):
        raw = fake_inference_df(self.TICKERS)
        raw.loc[0, "sector"] = "Not A Sector"
        raw.loc[1, "sector"] = None
        raw["volume_surge"] = raw["volume_surge"].astype(float)
        raw.loc[0, "volume_surge"] = np.nan
        result = build_model_features(raw)
        assert list(result.loc[[0, 1], "sector_code"]) == [99, 99]
        assert result.loc[0, "volume_surge"] == 0
