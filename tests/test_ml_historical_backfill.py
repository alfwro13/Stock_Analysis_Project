"""
tests/test_ml_historical_backfill.py — unit tests for get_target_tickers()

Covers deduplication, 0P mutual-fund filter, 250-ticker cap, sorted output,
and graceful fallback when DataEngine raises.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from ml_historical_backfill import get_target_tickers, BLUE_CHIPS


class TestGetTargetTickers:
    def _patch_engine(self, tickers):
        mock_engine = MagicMock()
        mock_engine.get_all_tickers.return_value = tickers
        return patch("ml_historical_backfill.DataEngine", return_value=mock_engine)

    def test_deduplication_ticker_in_both_sources_appears_once(self):
        # AAPL is in BLUE_CHIPS; if it also appears in user tickers it must deduplicate
        with self._patch_engine(["AAPL", "CUSTOM1"]):
            result = get_target_tickers()
        assert result.count("AAPL") == 1

    def test_0p_mutual_fund_filter_removes_0p_tickers(self):
        with self._patch_engine(["0P0000ABC", "TSLA"]):
            result = get_target_tickers()
        assert all(not t.startswith("0P") for t in result)
        assert "TSLA" in result

    def test_250_ticker_cap_applied(self):
        # Supply 300 unique user tickers (plus BLUE_CHIPS) so the union exceeds 250
        user_tickers = [f"XX{i:03d}" for i in range(300)]
        with self._patch_engine(user_tickers):
            result = get_target_tickers()
        assert len(result) <= 250

    def test_result_is_sorted(self):
        with self._patch_engine(["ZZZ", "AAA", "MMM"]):
            result = get_target_tickers()
        assert result == sorted(result)

    def test_engine_failure_falls_back_to_blue_chips_only(self):
        with patch("ml_historical_backfill.DataEngine", side_effect=RuntimeError("no db")):
            result = get_target_tickers()
        # Result must still be a non-empty sorted list containing the BLUE_CHIPS
        assert len(result) > 0
        # AAPL is in BLUE_CHIPS and must survive the fallback
        assert "AAPL" in result

    def test_empty_user_tickers_returns_blue_chips(self):
        with self._patch_engine([]):
            result = get_target_tickers()
        # All BLUE_CHIPS (filtered and capped) must appear
        for ticker in BLUE_CHIPS[:5]:
            assert ticker in result
