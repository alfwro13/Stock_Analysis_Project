# tests/test_quant_screener.py
"""
Unit tests for quant_screener.py helpers and new screening logic.
All tests are pure-Python — no DB, no network, no scheduler dependencies.
"""
import math
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from quant_screener import (
    _is_valid_numeric,
    _extract_numeric,
    _get_earnings_days,
    compute_quality_grade,
    get_oversold_reversals,
    get_overbought_warnings,
    get_momentum_surges,
    get_longterm_entry_setups,
)


# ------------------------------------------------------------------ #
#  _is_valid_numeric                                                   #
# ------------------------------------------------------------------ #

class TestIsValidNumeric:
    def test_normal_float(self):
        assert _is_valid_numeric(1.5) is True

    def test_zero(self):
        assert _is_valid_numeric(0) is True

    def test_negative(self):
        assert _is_valid_numeric(-42.7) is True

    def test_none(self):
        assert _is_valid_numeric(None) is False

    def test_nan_float(self):
        assert _is_valid_numeric(float('nan')) is False

    def test_nan_string(self):
        assert _is_valid_numeric('nan') is False

    def test_inf_float(self):
        assert _is_valid_numeric(float('inf')) is False

    def test_neg_inf_float(self):
        assert _is_valid_numeric(float('-inf')) is False

    def test_inf_string(self):
        assert _is_valid_numeric('inf') is False

    def test_non_numeric_string(self):
        assert _is_valid_numeric('abc') is False

    def test_numeric_string(self):
        assert _is_valid_numeric('3.14') is True


# ------------------------------------------------------------------ #
#  _extract_numeric                                                    #
# ------------------------------------------------------------------ #

class TestExtractNumeric:
    def test_integer(self):
        assert _extract_numeric('42') == 42.0

    def test_decimal(self):
        assert _extract_numeric('3.14%') == 3.14

    def test_leading_decimal(self):
        # BUG-5 regression: regex previously required at least one digit before the dot
        assert _extract_numeric('.5') == 0.5

    def test_negative(self):
        assert _extract_numeric('-2.3B') == -2.3

    def test_empty(self):
        assert _extract_numeric('') is None

    def test_none(self):
        assert _extract_numeric(None) is None

    def test_no_digits(self):
        assert _extract_numeric('N/A') is None


# ------------------------------------------------------------------ #
#  _get_earnings_days                                                  #
# ------------------------------------------------------------------ #

class TestGetEarningsDays:
    def _row(self, date_str):
        return {'next_earnings_date': date_str}

    def test_upcoming(self):
        row = self._row('2026-06-10')
        days = _get_earnings_days(row, '2026-06-03')
        assert days == 7

    def test_today(self):
        row = self._row('2026-06-03')
        days = _get_earnings_days(row, '2026-06-03')
        assert days == 0

    def test_past_returns_none(self):
        row = self._row('2026-05-01')
        assert _get_earnings_days(row, '2026-06-03') is None

    def test_missing_returns_none(self):
        assert _get_earnings_days({}, '2026-06-03') is None

    def test_unknown_string(self):
        assert _get_earnings_days({'next_earnings_date': 'Unknown'}, '2026-06-03') is None


# ------------------------------------------------------------------ #
#  compute_quality_grade                                               #
# ------------------------------------------------------------------ #

class TestComputeQualityGrade:
    def _row(self, roe=None, debt=None, pe=None, peg=None):
        return {'roe': roe, 'debt_to_equity': debt, 'trailing_pe': pe, 'peg_ratio': peg}

    def test_grade_a_full(self):
        assert compute_quality_grade(self._row(roe=20, debt=0.3, pe=20)) == 'A'

    def test_grade_a_via_peg(self):
        assert compute_quality_grade(self._row(roe=20, debt=0.3, peg=1.2)) == 'A'

    def test_grade_b(self):
        assert compute_quality_grade(self._row(roe=12, debt=0.8, pe=30)) == 'B'

    def test_grade_c_no_data(self):
        assert compute_quality_grade(self._row()) == 'C'

    def test_grade_c_low_roe(self):
        assert compute_quality_grade(self._row(roe=5, debt=0.5, pe=40)) == 'C'

    def test_grade_d_negative_roe(self):
        assert compute_quality_grade(self._row(roe=-5, debt=0.3, pe=15)) == 'D'

    def test_grade_d_high_debt(self):
        assert compute_quality_grade(self._row(roe=10, debt=2.5, pe=20)) == 'D'

    def test_d_overrides_other_good_metrics(self):
        # Loss-making company with otherwise good PE — still D
        assert compute_quality_grade(self._row(roe=-1, debt=0.2, pe=10)) == 'D'


# ------------------------------------------------------------------ #
#  get_oversold_reversals — NaN guard (BUG-1 regression)              #
# ------------------------------------------------------------------ #

def _make_row(**kwargs):
    defaults = {
        'ticker': 'TEST', 'close_price': 100, 'rsi_14': 25.0, 'macd_hist': 0.1,
        'macd': 0.0, 'macd_signal': -0.1, 'volume_surge': 0, 'bullish_cross': 0,
        'sma_50': 95.0, 'sma_200': 90.0, 'ml_confidence_score': 60.0,
        'composite_score': 30, 'overall_signal': 'BULLISH / HOLD',
        'beta': 0.7, 'sector': 'Technology', 'atr_pct': 0.015,
        'week52_pct': 0.45, 'roe': 12.0, 'debt_to_equity': 0.5,
        'trailing_pe': 18.0, 'peg_ratio': 1.1, 'next_earnings_date': None,
    }
    defaults.update(kwargs)
    return defaults


class TestOversoldNaNGuard:
    def test_normal_row_qualifies(self):
        rows = [_make_row()]
        result = get_oversold_reversals(rows, 'Normal')
        assert len(result) == 1

    def test_nan_rsi_excluded(self):
        rows = [_make_row(rsi_14=float('nan'))]
        result = get_oversold_reversals(rows, 'Normal')
        assert result == []

    def test_nan_string_rsi_excluded(self):
        rows = [_make_row(rsi_14='nan')]
        result = get_oversold_reversals(rows, 'Normal')
        assert result == []

    def test_none_macd_hist_excluded(self):
        rows = [_make_row(macd_hist=None)]
        result = get_oversold_reversals(rows, 'Normal')
        assert result == []

    def test_rsi_above_threshold_excluded(self):
        rows = [_make_row(rsi_14=35.0)]
        result = get_oversold_reversals(rows, 'Normal')
        assert result == []

    def test_inf_beta_treated_as_one_in_crash_regime(self):
        # beta=inf → treated as 1.0 → is_low_beta=False; only passes if defensive sector
        rows = [_make_row(beta=float('inf'), sector='Technology')]
        result = get_oversold_reversals(rows, 'Crash')
        assert result == []

    def test_defensive_sector_passes_in_crash(self):
        rows = [_make_row(sector='Utilities')]
        result = get_oversold_reversals(rows, 'Crash')
        assert len(result) == 1


class TestOverboughtNaNGuard:
    def test_normal_overbought_qualifies(self):
        rows = [_make_row(rsi_14=75.0, macd_hist=-0.1)]
        result = get_overbought_warnings(rows, 'Normal')
        assert len(result) == 1

    def test_nan_excluded(self):
        rows = [_make_row(rsi_14=float('nan'), macd_hist=-0.1)]
        result = get_overbought_warnings(rows, 'Normal')
        assert result == []


# ------------------------------------------------------------------ #
#  get_longterm_entry_setups                                           #
# ------------------------------------------------------------------ #

class TestLongtermEntrySetups:
    def _good_row(self, **kwargs):
        base = _make_row(
            rsi_14=48.0, close_price=110.0, sma_200=100.0,
            composite_score=30, atr_pct=0.018,
            roe=18.0, debt_to_equity=0.3, trailing_pe=22.0
        )
        base.update(kwargs)
        return base

    def test_good_setup_qualifies(self):
        rows = [self._good_row()]
        result = get_longterm_entry_setups(rows, 'Normal')
        assert len(result) == 1

    def test_below_200d_excluded(self):
        rows = [self._good_row(close_price=95.0, sma_200=100.0)]
        assert get_longterm_entry_setups(rows, 'Normal') == []

    def test_low_score_excluded(self):
        rows = [self._good_row(composite_score=10)]
        assert get_longterm_entry_setups(rows, 'Normal') == []

    def test_rsi_too_high_excluded(self):
        rows = [self._good_row(rsi_14=65.0)]
        assert get_longterm_entry_setups(rows, 'Normal') == []

    def test_rsi_too_low_excluded(self):
        rows = [self._good_row(rsi_14=30.0)]
        assert get_longterm_entry_setups(rows, 'Normal') == []

    def test_high_volatility_excluded(self):
        rows = [self._good_row(atr_pct=0.030)]
        assert get_longterm_entry_setups(rows, 'Normal') == []

    def test_grade_d_excluded(self):
        rows = [self._good_row(roe=-5.0)]
        assert get_longterm_entry_setups(rows, 'Normal') == []

    def test_crash_regime_tighter_rsi_ceiling(self):
        # RSI=58 passes Normal but fails Crash (ceiling=55)
        rows = [self._good_row(rsi_14=58.0)]
        assert len(get_longterm_entry_setups(rows, 'Normal')) == 1
        assert get_longterm_entry_setups(rows, 'Crash') == []

    def test_nan_fields_excluded(self):
        rows = [self._good_row(rsi_14=float('nan'))]
        assert get_longterm_entry_setups(rows, 'Normal') == []
