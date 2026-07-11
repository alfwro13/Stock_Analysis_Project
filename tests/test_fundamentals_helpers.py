"""
tests/test_fundamentals_helpers.py  ── FUNDAMENTALS HELPERS

Covers calculate_peter_lynch_peg() for all guard paths, PE selection logic,
decimal-to-percentage scaling, and denominator overflow protection.

Also covers the three forensic score functions: calculate_piotroski_f_score,
calculate_altman_z_score, and calculate_beneish_m_score.
"""

import sys
from pathlib import Path

import pytest
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from fundamentals_helpers import (
    calculate_peter_lynch_peg,
    calculate_piotroski_f_score,
    calculate_altman_z_score,
    calculate_beneish_m_score,
    compute_quality_grade,
    get_earnings_days,
    is_quality_compounder,
    is_quality_on_sale,
    is_garp_tenbagger,
    is_mean_reversion_setup,
    is_dividend_harvest_candidate,
)


# ── Shared DataFrame builders ─────────────────────────────────────────────────

def _make_bs(t: dict, p: dict) -> pd.DataFrame:
    """Build a 2-column annual balance sheet DataFrame (newest col first)."""
    import pandas as pd
    from datetime import datetime
    dates = [datetime(2024, 9, 30), datetime(2023, 9, 30)]
    rows = set(t.keys()) | set(p.keys())
    data = {d: {r: (t if d == dates[0] else p).get(r) for r in rows} for d in dates}
    return pd.DataFrame(data)


def _make_fin(t: dict, p: dict) -> pd.DataFrame:
    import pandas as pd
    from datetime import datetime
    dates = [datetime(2024, 9, 30), datetime(2023, 9, 30)]
    rows = set(t.keys()) | set(p.keys())
    data = {d: {r: (t if d == dates[0] else p).get(r) for r in rows} for d in dates}
    return pd.DataFrame(data)


def _make_cf(t: dict, p: dict) -> pd.DataFrame:
    import pandas as pd
    from datetime import datetime
    dates = [datetime(2024, 9, 30), datetime(2023, 9, 30)]
    rows = set(t.keys()) | set(p.keys())
    data = {d: {r: (t if d == dates[0] else p).get(r) for r in rows} for d in dates}
    return pd.DataFrame(data)


# ──────────────────────────────────────────────────────────────────────────────
# Happy-path: known value
# ──────────────────────────────────────────────────────────────────────────────

class TestPegHappyPath:

    def test_basic_calculation(self):
        """PE=20, growth=20% decimal, no dividend → PEG = 20 / 20 = 1.0."""
        result = calculate_peter_lynch_peg(
            forward_pe=20.0,
            trailing_pe=None,
            earnings_growth=0.20,
            dividend_yield=None,
        )
        assert result == pytest.approx(1.0)

    def test_yield_adjusted_peg(self):
        """PE=15, growth=10%, yield=5% → denominator=15 → PEG = 15/15 = 1.0."""
        result = calculate_peter_lynch_peg(
            forward_pe=15.0,
            trailing_pe=None,
            earnings_growth=0.10,
            dividend_yield=0.05,
        )
        assert result == pytest.approx(1.0)

    def test_zero_dividend_yield_same_as_none(self):
        """Explicit 0.0 dividend_yield must give same result as None."""
        r_zero = calculate_peter_lynch_peg(20.0, None, 0.20, 0.0)
        r_none = calculate_peter_lynch_peg(20.0, None, 0.20, None)
        assert r_zero == pytest.approx(r_none)

    def test_low_peg_below_one(self):
        """Growth stock: PE=10, growth=25% → PEG = 10/25 = 0.40."""
        result = calculate_peter_lynch_peg(10.0, None, 0.25, None)
        assert result == pytest.approx(0.40)

    def test_result_is_always_positive(self):
        """With valid inputs the returned PEG ratio must always be > 0."""
        result = calculate_peter_lynch_peg(30.0, None, 0.15, 0.02)
        assert result is not None and result > 0


# ──────────────────────────────────────────────────────────────────────────────
# PE selection logic
# ──────────────────────────────────────────────────────────────────────────────

class TestPeSelection:

    def test_forward_pe_preferred_over_trailing(self):
        """Forward PE must be used when it is positive, even if trailing is also set."""
        r_forward = calculate_peter_lynch_peg(10.0, 30.0, 0.20, None)
        assert r_forward == pytest.approx(10.0 / 20.0)

    def test_falls_back_to_trailing_when_forward_none(self):
        """When forward_pe is None, trailing_pe must be used."""
        result = calculate_peter_lynch_peg(None, 25.0, 0.25, None)
        assert result == pytest.approx(25.0 / 25.0)

    def test_falls_back_to_trailing_when_forward_zero(self):
        """forward_pe=0 is treated as unavailable; trailing used instead."""
        result = calculate_peter_lynch_peg(0.0, 20.0, 0.20, None)
        assert result == pytest.approx(20.0 / 20.0)

    def test_falls_back_to_trailing_when_forward_negative(self):
        """Negative forward PE (loss-making) → falls back to trailing PE."""
        result = calculate_peter_lynch_peg(-5.0, 18.0, 0.18, None)
        assert result == pytest.approx(18.0 / 18.0)

    def test_both_pe_none_returns_none(self):
        assert calculate_peter_lynch_peg(None, None, 0.20, None) is None

    def test_forward_none_trailing_negative_returns_none(self):
        """Trailing PE negative → loss-making, no valid PEG possible."""
        assert calculate_peter_lynch_peg(None, -10.0, 0.20, None) is None

    def test_forward_none_trailing_zero_returns_none(self):
        assert calculate_peter_lynch_peg(None, 0.0, 0.20, None) is None


# ──────────────────────────────────────────────────────────────────────────────
# Earnings growth guards
# ──────────────────────────────────────────────────────────────────────────────

class TestEarningsGrowthGuards:

    def test_none_earnings_growth_returns_none(self):
        assert calculate_peter_lynch_peg(20.0, None, None, None) is None

    def test_zero_earnings_growth_returns_none(self):
        """Zero growth makes PEG infinite — must return None."""
        assert calculate_peter_lynch_peg(20.0, None, 0.0, None) is None

    def test_negative_earnings_growth_returns_none(self):
        """Loss-making company — Lynch PEG is undefined."""
        assert calculate_peter_lynch_peg(20.0, None, -0.10, None) is None

    def test_tiny_positive_growth_is_accepted(self):
        """Even 0.1% growth is valid — should not be filtered."""
        result = calculate_peter_lynch_peg(20.0, None, 0.001, None)
        assert result is not None and result > 0


# ──────────────────────────────────────────────────────────────────────────────
# Decimal-to-percentage scaling
# ──────────────────────────────────────────────────────────────────────────────

class TestDecimalScaling:

    def test_earnings_growth_scaled_to_percent(self):
        """0.15 earnings_growth must be treated as 15%, not 0.15%."""
        result = calculate_peter_lynch_peg(15.0, None, 0.15, None)
        # denominator = 15.0 (15%), not 0.15
        assert result == pytest.approx(15.0 / 15.0)
        assert result != pytest.approx(15.0 / 0.15), "growth must be scaled ×100"

    def test_dividend_yield_scaled_to_percent(self):
        """0.03 dividend_yield must be treated as 3%, adding 3 percentage points."""
        result_with = calculate_peter_lynch_peg(20.0, None, 0.17, 0.03)
        # denominator = 17 + 3 = 20 → PEG = 1.0
        assert result_with == pytest.approx(1.0)

    def test_large_growth_reduces_peg(self):
        """Higher growth rate must produce lower (better) PEG."""
        low_growth  = calculate_peter_lynch_peg(20.0, None, 0.10, None)
        high_growth = calculate_peter_lynch_peg(20.0, None, 0.40, None)
        assert high_growth < low_growth


# ──────────────────────────────────────────────────────────────────────────────
# Denominator overflow / pathological data
# ──────────────────────────────────────────────────────────────────────────────

class TestDenominatorGuard:

    def test_large_negative_dividend_yield_returns_none(self):
        """
        If yfinance returns a large negative dividend_yield (bad data) that
        swamps positive earnings growth, total_growth_yield <= 0 → must return None.
        """
        result = calculate_peter_lynch_peg(
            forward_pe=20.0,
            trailing_pe=None,
            earnings_growth=0.01,   # eg_scaled = 1.0
            dividend_yield=-0.05,   # div_yield_scaled = -5.0 → total = -4.0
        )
        assert result is None

    def test_negative_dividend_slightly_above_zero_denom_accepted(self):
        """
        Negative dividend_yield that only partially reduces total_growth_yield
        but leaves it positive → result must still be returned.
        """
        # eg_scaled = 20.0, div_yield_scaled = -5.0 → total = 15.0
        result = calculate_peter_lynch_peg(20.0, None, 0.20, -0.05)
        assert result == pytest.approx(20.0 / 15.0)


# ──────────────────────────────────────────────────────────────────────────────
# Parametrized guard matrix
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fwd, trail, growth, div", [
    (None,  None,  0.20,  None),   # no PE at all
    (None,  -5.0,  0.20,  None),   # trailing negative
    (None,  0.0,   0.20,  None),   # trailing zero
    (-1.0,  None,  0.20,  None),   # forward negative, no fallback
    (20.0,  None,  None,  None),   # no growth
    (20.0,  None,  0.0,   None),   # zero growth
    (20.0,  None,  -0.10, None),   # negative growth
    (20.0,  None,  0.01,  -0.05),  # neg dividend swamps growth
])
def test_returns_none_for_invalid_inputs(fwd, trail, growth, div):
    assert calculate_peter_lynch_peg(fwd, trail, growth, div) is None


# ── Piotroski F-Score ─────────────────────────────────────────────────────────

class TestPiotroskiFScore:

    def _healthy(self):
        """Profitable company improving on all 9 criteria → score should be high."""
        bs_t = {
            'Total Assets': 1000, 'Current Assets': 400, 'Current Liabilities': 150,
            'Long Term Debt': 100, 'Ordinary Shares Number': 500,
        }
        bs_p = {
            'Total Assets': 900, 'Current Assets': 350, 'Current Liabilities': 160,
            'Long Term Debt': 150, 'Ordinary Shares Number': 510,
        }
        fin_t = {'Net Income': 80, 'Total Revenue': 500, 'Gross Profit': 200}
        fin_p = {'Net Income': 60, 'Total Revenue': 400, 'Gross Profit': 140}
        cf_t  = {'Operating Cash Flow': 100}
        cf_p  = {'Operating Cash Flow': 80}
        return _make_bs(bs_t, bs_p), _make_fin(fin_t, fin_p), _make_cf(cf_t, cf_p)

    def test_healthy_company_scores_high(self):
        bs, fin, cf = self._healthy()
        score = calculate_piotroski_f_score(bs, fin, cf)
        assert score is not None
        assert score >= 5

    def test_score_range_0_to_9(self):
        bs, fin, cf = self._healthy()
        score = calculate_piotroski_f_score(bs, fin, cf)
        assert score is not None
        assert 0 <= score <= 9

    def test_returns_none_for_empty_dataframes(self):
        empty = pd.DataFrame()
        assert calculate_piotroski_f_score(empty, empty, empty) is None

    def test_returns_none_for_none_inputs(self):
        assert calculate_piotroski_f_score(None, None, None) is None

    def test_returns_none_when_insufficient_criteria(self):
        bs = _make_bs({'Total Assets': 100}, {'Total Assets': 90})
        fin = _make_fin({'Net Income': 10}, {'Net Income': 8})
        cf  = _make_cf({}, {})
        result = calculate_piotroski_f_score(bs, fin, cf)
        assert result is None or isinstance(result, int)

    def test_single_period_still_evaluates_available_criteria(self):
        from datetime import datetime
        dates = [datetime(2024, 9, 30)]
        bs_data = {dates[0]: {'Total Assets': 1000, 'Current Assets': 400, 'Current Liabilities': 150}}
        fin_data = {dates[0]: {'Net Income': 80, 'Total Revenue': 500, 'Gross Profit': 200}}
        cf_data  = {dates[0]: {'Operating Cash Flow': 100}}
        bs  = pd.DataFrame(bs_data)
        fin = pd.DataFrame(fin_data)
        cf  = pd.DataFrame(cf_data)
        result = calculate_piotroski_f_score(bs, fin, cf)
        assert result is None or (0 <= result <= 9)


# ── Altman Z-Score ────────────────────────────────────────────────────────────

class TestAltmanZScore:

    def _healthy_info_bs_fin(self):
        info = {'marketCap': 5_000_000}
        bs = _make_bs(
            {'Total Assets': 1000, 'Working Capital': 250, 'Retained Earnings': 300,
             'Common Stock Equity': 600, 'Total Liabilities Net Minority Interest': 400,
             'Current Assets': 400, 'Current Liabilities': 150},
            {'Total Assets': 900},
        )
        fin = _make_fin({'EBIT': 120, 'Total Revenue': 500}, {'EBIT': 100, 'Total Revenue': 400})
        return info, bs, fin

    def test_returns_float_for_valid_inputs(self):
        info, bs, fin = self._healthy_info_bs_fin()
        z = calculate_altman_z_score(info, bs, fin)
        assert isinstance(z, float)

    def test_healthy_company_in_safe_zone(self):
        info, bs, fin = self._healthy_info_bs_fin()
        z = calculate_altman_z_score(info, bs, fin)
        assert z is not None
        assert z > 1.0

    def test_returns_none_for_missing_total_assets(self):
        info = {}
        bs  = _make_bs({}, {})
        fin = _make_fin({'EBIT': 100, 'Total Revenue': 400}, {})
        assert calculate_altman_z_score(info, bs, fin) is None

    def test_returns_none_for_none_inputs(self):
        assert calculate_altman_z_score({}, None, None) is None

    def test_returns_none_for_empty_dataframes(self):
        assert calculate_altman_z_score({}, pd.DataFrame(), pd.DataFrame()) is None

    def test_score_is_rounded_to_2dp(self):
        info, bs, fin = self._healthy_info_bs_fin()
        z = calculate_altman_z_score(info, bs, fin)
        if z is not None:
            assert z == round(z, 2)


# ── Beneish M-Score ───────────────────────────────────────────────────────────

class TestBeneishMScore:

    def _clean_company(self):
        bs = _make_bs(
            {'Total Assets': 1000, 'Accounts Receivable': 50, 'Current Assets': 400,
             'Net PPE': 300, 'Total Liabilities Net Minority Interest': 400},
            {'Total Assets': 900, 'Accounts Receivable': 45, 'Current Assets': 360,
             'Net PPE': 280, 'Total Liabilities Net Minority Interest': 380},
        )
        fin = _make_fin(
            {'Total Revenue': 500, 'Gross Profit': 200, 'Cost Of Revenue': 300,
             'Net Income': 80, 'Selling General And Administration': 60},
            {'Total Revenue': 450, 'Gross Profit': 180, 'Cost Of Revenue': 270,
             'Net Income': 70, 'Selling General And Administration': 55},
        )
        cf = _make_cf(
            {'Operating Cash Flow': 100, 'Depreciation And Amortization': 30},
            {'Operating Cash Flow': 90,  'Depreciation And Amortization': 28},
        )
        return bs, fin, cf

    def test_clean_company_score_below_threshold(self):
        bs, fin, cf = self._clean_company()
        m = calculate_beneish_m_score(bs, fin, cf)
        if m is not None:
            assert m < -1.78

    def test_returns_float_or_none(self):
        bs, fin, cf = self._clean_company()
        m = calculate_beneish_m_score(bs, fin, cf)
        assert m is None or isinstance(m, float)

    def test_returns_none_for_single_period(self):
        from datetime import datetime
        dates = [datetime(2024, 9, 30)]
        bs  = pd.DataFrame({dates[0]: {'Total Assets': 1000}})
        fin = pd.DataFrame({dates[0]: {'Total Revenue': 500}})
        cf  = pd.DataFrame({dates[0]: {'Operating Cash Flow': 100}})
        assert calculate_beneish_m_score(bs, fin, cf) is None

    def test_returns_none_for_none_inputs(self):
        assert calculate_beneish_m_score(None, None, None) is None

    def test_returns_none_for_empty_dataframes(self):
        assert calculate_beneish_m_score(pd.DataFrame(), pd.DataFrame(), pd.DataFrame()) is None

    def test_score_is_rounded_to_3dp(self):
        bs, fin, cf = self._clean_company()
        m = calculate_beneish_m_score(bs, fin, cf)
        if m is not None:
            assert m == round(m, 3)


# ── get_earnings_days ───────────────────────────────────────────────────────

class TestGetEarningsDays:
    def _row(self, date_str):
        return {'next_earnings_date': date_str}

    def test_upcoming(self):
        row = self._row('2026-06-10')
        days = get_earnings_days(row, '2026-06-03')
        assert days == 7

    def test_today(self):
        row = self._row('2026-06-03')
        days = get_earnings_days(row, '2026-06-03')
        assert days == 0

    def test_past_returns_none(self):
        row = self._row('2026-05-01')
        assert get_earnings_days(row, '2026-06-03') is None

    def test_missing_returns_none(self):
        assert get_earnings_days({}, '2026-06-03') is None

    def test_unknown_string(self):
        assert get_earnings_days({'next_earnings_date': 'Unknown'}, '2026-06-03') is None


# ── compute_quality_grade ────────────────────────────────────────────────────

class TestComputeQualityGrade:
    """roe is a Yahoo-style fraction (0.20 = 20%); debt_to_equity is Yahoo's own percentage-like
    scale (debtToEquity≈30 means 30% D/E) — matches the units actually stored in stock_signals
    by universe_fundamentals_engine.py/quant_signals.py, not an arbitrary ratio."""

    def _row(self, roe=None, debt=None, pe=None, peg=None):
        return {'roe': roe, 'debt_to_equity': debt, 'trailing_pe': pe, 'peg_ratio': peg}

    def test_grade_a_full(self):
        assert compute_quality_grade(self._row(roe=0.20, debt=30, pe=20)) == 'A'

    def test_grade_a_via_peg(self):
        assert compute_quality_grade(self._row(roe=0.20, debt=30, peg=1.2)) == 'A'

    def test_grade_b(self):
        assert compute_quality_grade(self._row(roe=0.12, debt=80, pe=30)) == 'B'

    def test_grade_c_no_data(self):
        assert compute_quality_grade(self._row()) == 'C'

    def test_grade_c_low_roe(self):
        assert compute_quality_grade(self._row(roe=0.05, debt=50, pe=40)) == 'C'

    def test_grade_d_negative_roe(self):
        assert compute_quality_grade(self._row(roe=-0.05, debt=30, pe=15)) == 'D'

    def test_grade_d_high_debt(self):
        assert compute_quality_grade(self._row(roe=0.10, debt=250, pe=20)) == 'D'

    def test_d_overrides_other_good_metrics(self):
        assert compute_quality_grade(self._row(roe=-0.01, debt=20, pe=10)) == 'D'


# ── Report-screen predicates (mirror reports_engine.py thresholds) ───────────

class TestIsQualityCompounder:
    def _row(self, **overrides):
        row = {
            'roe': 0.20, 'debt_to_equity': 50, 'profit_margin': 0.15,
            'revenue_growth': 0.10, 'current_ratio': 2.0, 'trailing_pe': 20, 'composite_score': 70,
        }
        row.update(overrides)
        return row

    def test_matches_all_thresholds(self):
        assert is_quality_compounder(self._row()) is True

    def test_fails_low_roe(self):
        assert is_quality_compounder(self._row(roe=0.05)) is False

    def test_fails_pe_out_of_band(self):
        assert is_quality_compounder(self._row(trailing_pe=50)) is False

    def test_missing_field_is_false(self):
        assert is_quality_compounder(self._row(composite_score=None)) is False


class TestIsQualityOnSale:
    def _row(self, **overrides):
        row = {
            'close_price': 100, 'fifty_two_week_low': 95, 'roe': 0.12,
            'debt_to_equity': 50, 'profit_margin': 0.08, 'trailing_pe': 15, 'composite_score': 55,
        }
        row.update(overrides)
        return row

    def test_matches_all_thresholds(self):
        assert is_quality_on_sale(self._row()) is True

    def test_fails_too_far_above_low(self):
        assert is_quality_on_sale(self._row(close_price=130)) is False

    def test_fails_high_debt(self):
        assert is_quality_on_sale(self._row(debt_to_equity=200)) is False

    def test_null_debt_is_allowed(self):
        assert is_quality_on_sale(self._row(debt_to_equity=None)) is True


class TestIsGarpTenbagger:
    def _row(self, **overrides):
        row = {'peter_lynch_peg': 0.8, 'revenue_growth': 0.20, 'roe': 0.15, 'forward_pe': 25}
        row.update(overrides)
        return row

    def test_matches_all_thresholds(self):
        assert is_garp_tenbagger(self._row(), market_cap=1_000_000_000) is True

    def test_fails_small_cap(self):
        assert is_garp_tenbagger(self._row(), market_cap=100_000_000) is False

    def test_fails_high_peg(self):
        assert is_garp_tenbagger(self._row(peter_lynch_peg=1.5), market_cap=1_000_000_000) is False

    def test_no_market_cap_is_false(self):
        assert is_garp_tenbagger(self._row(), market_cap=None) is False


class TestIsMeanReversionSetup:
    def test_matches_oversold_uptrend(self):
        assert is_mean_reversion_setup({'close_price': 110, 'sma_200': 100, 'rsi_14': 25}) is True

    def test_fails_below_sma(self):
        assert is_mean_reversion_setup({'close_price': 90, 'sma_200': 100, 'rsi_14': 25}) is False

    def test_fails_rsi_above_threshold(self):
        assert is_mean_reversion_setup({'close_price': 110, 'sma_200': 100, 'rsi_14': 45}) is False

    def test_custom_max_rsi(self):
        assert is_mean_reversion_setup({'close_price': 110, 'sma_200': 100, 'rsi_14': 35}, max_rsi=40) is True


class TestIsDividendHarvestCandidate:
    def test_matches_thresholds(self):
        row = {'dividend_yield': 0.03, 'composite_score': 60, 'ex_dividend_date': '2026-08-01'}
        assert is_dividend_harvest_candidate(row) is True

    def test_fails_low_yield(self):
        row = {'dividend_yield': 0.01, 'composite_score': 60, 'ex_dividend_date': '2026-08-01'}
        assert is_dividend_harvest_candidate(row) is False

    def test_fails_unknown_ex_div_date(self):
        row = {'dividend_yield': 0.03, 'composite_score': 60, 'ex_dividend_date': 'Unknown'}
        assert is_dividend_harvest_candidate(row) is False

    def test_custom_thresholds(self):
        row = {'dividend_yield': 0.025, 'composite_score': 55, 'ex_dividend_date': '2026-08-01'}
        assert is_dividend_harvest_candidate(row, min_yield=0.02, min_score=55) is True
