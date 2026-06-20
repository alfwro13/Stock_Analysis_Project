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


# ------------------------------------------------------------------ #
#  get_macd_bullish_crosses                                           #
# ------------------------------------------------------------------ #

from quant_screener import get_macd_bullish_crosses, get_momentum_surges, filter_ai_vetoes


class TestMacdBullishCrosses:
    def test_bullish_cross_passes_normal(self):
        rows = [_make_row(bullish_cross=1, close_price=110.0, sma_200=100.0)]
        assert len(get_macd_bullish_crosses(rows, 'Normal')) == 1

    def test_no_cross_excluded(self):
        rows = [_make_row(bullish_cross=0)]
        assert get_macd_bullish_crosses(rows, 'Normal') == []

    def test_crash_regime_requires_above_sma200(self):
        rows = [_make_row(bullish_cross=1, close_price=90.0, sma_200=100.0)]
        assert get_macd_bullish_crosses(rows, 'Crash') == []

    def test_crash_regime_above_sma200_passes(self):
        rows = [_make_row(bullish_cross=1, close_price=110.0, sma_200=100.0)]
        assert len(get_macd_bullish_crosses(rows, 'Crash')) == 1

    def test_volatile_regime_same_as_crash(self):
        rows = [_make_row(bullish_cross=1, close_price=90.0, sma_200=100.0)]
        assert get_macd_bullish_crosses(rows, 'Volatile') == []


# ------------------------------------------------------------------ #
#  get_momentum_surges                                                #
# ------------------------------------------------------------------ #

class TestMomentumSurges:
    def _surge_row(self, **kwargs):
        base = _make_row(volume_surge=1, rsi_14=60.0, close_price=110.0, sma_200=100.0)
        base.update(kwargs)
        return base

    def test_surge_passes_normal(self):
        assert len(get_momentum_surges([self._surge_row()], 'Normal')) == 1

    def test_no_surge_excluded(self):
        rows = [self._surge_row(volume_surge=0)]
        assert get_momentum_surges(rows, 'Normal') == []

    def test_rsi_too_high_excluded(self):
        rows = [self._surge_row(rsi_14=75.0)]
        assert get_momentum_surges(rows, 'Normal') == []

    def test_rsi_too_low_excluded(self):
        rows = [self._surge_row(rsi_14=45.0)]
        assert get_momentum_surges(rows, 'Normal') == []

    def test_crash_regime_requires_above_sma200(self):
        rows = [self._surge_row(close_price=90.0, sma_200=100.0)]
        assert get_momentum_surges(rows, 'Crash') == []

    def test_crash_regime_above_sma200_passes(self):
        rows = [self._surge_row(close_price=110.0, sma_200=100.0)]
        assert len(get_momentum_surges(rows, 'Crash')) == 1


# ------------------------------------------------------------------ #
#  filter_ai_vetoes                                                   #
# ------------------------------------------------------------------ #

class TestFilterAiVetoes:
    def test_above_threshold_approved(self):
        rows = [_make_row(ml_confidence_score=65.0)]
        approved, vetoed = filter_ai_vetoes(rows)
        assert len(approved) == 1
        assert vetoed == []

    def test_below_threshold_vetoed(self):
        rows = [_make_row(ml_confidence_score=30.0)]
        approved, vetoed = filter_ai_vetoes(rows)
        assert approved == []
        assert len(vetoed) == 1

    def test_missing_confidence_vetoed(self):
        rows = [_make_row(ml_confidence_score=None)]
        approved, vetoed = filter_ai_vetoes(rows)
        assert approved == []
        assert len(vetoed) == 1

    def test_exactly_at_threshold_approved(self):
        from constants import ML_CONFIDENCE_THRESHOLD
        rows = [_make_row(ml_confidence_score=ML_CONFIDENCE_THRESHOLD)]
        approved, vetoed = filter_ai_vetoes(rows)
        assert len(approved) == 1

    def test_mixed_list_split_correctly(self):
        rows = [
            _make_row(ticker='A', ml_confidence_score=70.0),
            _make_row(ticker='B', ml_confidence_score=20.0),
            _make_row(ticker='C', ml_confidence_score=None),
        ]
        approved, vetoed = filter_ai_vetoes(rows)
        assert len(approved) == 1
        assert approved[0]['ticker'] == 'A'
        assert len(vetoed) == 2


# ------------------------------------------------------------------ #
#  filter_macro_vetoes                                                 #
# ------------------------------------------------------------------ #

from unittest.mock import patch, MagicMock
from quant_screener import filter_macro_vetoes


def _mock_conn(us_spread=0.0, uk_spread=0.0):
    """Return a mock connection whose cursor().fetchone() yields the given spreads."""
    row = {"us_high_yield_spread": us_spread, "uk_corporate_spread": uk_spread}
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


class TestFilterMacroVetoes:
    def _us_row(self, pe=18.0, debt=0.5, corr=0.1, **kw):
        return _make_row(country="US", currency="USD", trailing_pe=pe,
                         debt_to_equity=debt, yield_correlation=corr, **kw)

    def _uk_row(self, pe=18.0, debt=0.5, corr=0.1, **kw):
        return _make_row(country="UK", currency="GBP", trailing_pe=pe,
                         debt_to_equity=debt, yield_correlation=corr, **kw)

    def test_green_regime_approves_all(self):
        with patch("quant_screener.get_connection", return_value=_mock_conn()):
            approved, vetoed = filter_macro_vetoes([self._us_row()], "GREEN")
        assert len(approved) == 1 and vetoed == []

    def test_red_regime_high_pe_neg_corr_vetoed(self):
        with patch("quant_screener.get_connection", return_value=_mock_conn()):
            row = self._us_row(pe=35, corr=-0.5)
            approved, vetoed = filter_macro_vetoes([row], "RED")
        assert vetoed and not approved

    def test_red_regime_low_pe_approved_despite_neg_corr(self):
        with patch("quant_screener.get_connection", return_value=_mock_conn()):
            row = self._us_row(pe=15, corr=-0.5)
            approved, vetoed = filter_macro_vetoes([row], "RED")
        assert approved and not vetoed

    def test_red_regime_positive_corr_approved_despite_high_pe(self):
        with patch("quant_screener.get_connection", return_value=_mock_conn()):
            row = self._us_row(pe=40, corr=0.4)
            approved, vetoed = filter_macro_vetoes([row], "RED")
        assert approved and not vetoed

    def test_yellow_regime_behaves_same_as_red(self):
        with patch("quant_screener.get_connection", return_value=_mock_conn()):
            row = self._us_row(pe=35, corr=-0.5)
            approved, vetoed = filter_macro_vetoes([row], "YELLOW")
        assert vetoed and not approved

    def test_us_credit_circuit_breaker_overrides_green_regime(self):
        # US HY spread >6.5% → vetoed regardless of regime
        with patch("quant_screener.get_connection", return_value=_mock_conn(us_spread=7.0)):
            row = self._us_row(pe=10, corr=0.9)  # would normally pass all filters
            approved, vetoed = filter_macro_vetoes([row], "GREEN")
        assert vetoed and not approved

    def test_uk_credit_circuit_breaker(self):
        with patch("quant_screener.get_connection", return_value=_mock_conn(uk_spread=3.5)):
            row = self._uk_row(pe=10, corr=0.9)
            approved, vetoed = filter_macro_vetoes([row], "GREEN")
        assert vetoed and not approved

    def test_us_circuit_breaker_does_not_affect_uk_asset(self):
        # High US spread should NOT trip for a UK-currency asset
        with patch("quant_screener.get_connection", return_value=_mock_conn(us_spread=7.0, uk_spread=1.0)):
            row = self._uk_row(pe=10, corr=0.9)
            approved, vetoed = filter_macro_vetoes([row], "GREEN")
        assert approved and not vetoed

    def test_missing_yield_correlation_vetoed_in_red(self):
        # None correlation is treated as risk → vetoed in RED+high-multiple
        with patch("quant_screener.get_connection", return_value=_mock_conn()):
            row = self._us_row(pe=35, corr=None)
            approved, vetoed = filter_macro_vetoes([row], "RED")
        assert vetoed and not approved
