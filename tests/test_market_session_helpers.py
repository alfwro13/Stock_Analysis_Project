from datetime import datetime, timedelta, timezone

import pytest

from constants import INTRADAY_VOLUME_CURVES
from market_session_helpers import projected_session_volume, session_volume_share, volume_curve_key


class TestVolumeCurveKey:
    def test_lse_etf_has_its_own_curve_but_an_lse_stock_does_not(self):
        assert volume_curve_key("LSE", "ETF") == "LSE_ETF"
        assert volume_curve_key("LSE", "EQUITY") == "LSE"
        assert volume_curve_key("LSE", None) == "LSE"

    def test_nyse_uses_one_curve_for_every_quote_type(self):
        assert volume_curve_key("NYSE", "ETF") == "NYSE"
        assert volume_curve_key("NYSE", "EQUITY") == "NYSE"

    def test_exchange_without_a_curve_is_none(self):
        assert volume_curve_key("TSE", "EQUITY") is None
        assert volume_curve_key("XETRA", "ETF") is None


class TestSessionVolumeShare:
    def test_grid_point_returns_the_stored_share(self):
        assert session_volume_share("NYSE", 30 / 390) == pytest.approx(0.180)
        assert session_volume_share("LSE", 60 / 510) == pytest.approx(0.090)

    def test_between_points_interpolates_linearly(self):
        assert session_volume_share("NYSE", 45 / 390) == pytest.approx((0.180 + 0.286) / 2)

    def test_before_the_first_point_interpolates_from_zero(self):
        assert session_volume_share("NYSE", 0.0) == 0.0
        assert session_volume_share("NYSE", 2.5 / 390) == pytest.approx(0.0435)

    def test_clamped_below_zero_and_above_one(self):
        assert session_volume_share("NYSE", -0.5) == 0.0
        assert session_volume_share("NYSE", 1.7) == 1.0
        assert session_volume_share("LSE_ETF", 1.0) == 1.0

    def test_early_close_compresses_the_curve_via_the_elapsed_fraction(self):
        full_day_at_half = session_volume_share("NYSE", 0.5)
        assert full_day_at_half == pytest.approx(session_volume_share("NYSE", 195 / 390))
        assert session_volume_share("NYSE", 90 / 210) > session_volume_share("NYSE", 90 / 390)

    def test_every_curve_is_monotonic_and_ends_at_one(self):
        for key, curve in INTRADAY_VOLUME_CURVES.items():
            shares = [s for _, s in curve]
            assert shares == sorted(shares), key
            assert shares[-1] == 1.0, key


class TestProjectedSessionVolume:
    OPEN = datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc)

    def _project(self, key, minutes, coverage, session_minutes, volume=1000.0):
        close = self.OPEN + timedelta(minutes=session_minutes)
        return projected_session_volume(volume, key, self.OPEN, close, self.OPEN + timedelta(minutes=minutes), coverage)

    def test_us_at_thirty_minutes_divides_by_share_and_coverage(self):
        assert self._project("NYSE", 30, 0.9, 390) == pytest.approx(1000.0 / 0.180 / 0.9)

    def test_lse_before_the_minimum_share_gives_no_verdict(self):
        assert self._project("LSE", 15, 0.9, 510) is None

    def test_missing_or_non_positive_coverage_gives_no_verdict(self):
        assert self._project("NYSE", 30, None, 390) is None
        assert self._project("NYSE", 30, 0.0, 390) is None

    def test_unknown_curve_key_or_missing_volume_gives_no_verdict(self):
        assert self._project(None, 30, 0.9, 390) is None
        assert self._project("NYSE", 30, 0.9, 390, volume=None) is None

    def test_degenerate_session_window_gives_no_verdict(self):
        assert projected_session_volume(1000.0, "NYSE", self.OPEN, self.OPEN, self.OPEN, 0.9) is None
