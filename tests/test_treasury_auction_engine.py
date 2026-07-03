"""
tests/test_treasury_auction_engine.py — Sovereign Debt Auction Monitor Tests

Covers:
  • _safe_float / _pct / _tail_bp — numeric helpers
  • _is_weak — alert threshold logic (both signals, each alone, neither)
  • _get_baseline — rolling 6-auction mean from real SQLite rows
  • check_auction_results — DB write + no-op when API returns nothing
  • check_auction_results — alert fires on weak bid-to-cover
  • check_auction_results — alert dedup (alert_fired flag prevents re-alert)
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
from treasury_auction_engine import (
    _safe_float,
    _pct,
    _tail_bp,
    _is_weak,
    _get_baseline,
    check_auction_results,
)


# ── Numeric helpers ──────────────────────────────────────────────────────────

class TestSafeFloat:
    def test_numeric_string(self):
        assert _safe_float("4.50") == pytest.approx(4.50)

    def test_integer(self):
        assert _safe_float(3) == pytest.approx(3.0)

    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_empty_string_returns_none(self):
        assert _safe_float("") is None

    def test_non_numeric_string_returns_none(self):
        assert _safe_float("N/A") is None


class TestPct:
    def test_basic_calculation(self):
        assert _pct("25", 100.0) == pytest.approx(25.0)

    def test_zero_total_returns_none(self):
        assert _pct("25", 0.0) is None

    def test_none_part_returns_none(self):
        assert _pct(None, 100.0) is None

    def test_none_total_returns_none(self):
        assert _pct("25", None) is None


class TestTailBp:
    def test_positive_tail(self):
        assert _tail_bp(4.52, 4.50) == pytest.approx(2.0)

    def test_zero_tail(self):
        assert _tail_bp(4.50, 4.50) == pytest.approx(0.0)

    def test_none_high_returns_none(self):
        assert _tail_bp(None, 4.50) is None

    def test_none_median_returns_none(self):
        assert _tail_bp(4.52, None) is None


# ── Weakness detection ───────────────────────────────────────────────────────

class TestIsWeak:
    def test_strong_auction_no_alert(self):
        weak, reasons = _is_weak(btc=2.7, mean_btc=2.5, tail=1.0, mean_tail=1.0)
        assert not weak
        assert reasons == []

    def test_low_btc_triggers_alert(self):
        weak, reasons = _is_weak(btc=2.1, mean_btc=2.5, tail=1.0, mean_tail=1.0)
        assert weak
        assert any("bid-to-cover" in r for r in reasons)

    def test_high_tail_triggers_alert(self):
        weak, reasons = _is_weak(btc=2.5, mean_btc=2.5, tail=5.0, mean_tail=1.0)
        assert weak
        assert any("tail" in r for r in reasons)

    def test_both_signals_trigger(self):
        weak, reasons = _is_weak(btc=2.0, mean_btc=2.5, tail=6.0, mean_tail=1.0)
        assert weak
        assert len(reasons) == 2

    def test_none_baseline_no_alert(self):
        weak, reasons = _is_weak(btc=2.0, mean_btc=None, tail=6.0, mean_tail=None)
        assert not weak

    def test_none_metric_no_alert(self):
        weak, reasons = _is_weak(btc=None, mean_btc=2.5, tail=None, mean_tail=1.0)
        assert not weak

    def test_btc_exactly_at_threshold_no_alert(self):
        weak, reasons = _is_weak(btc=2.3, mean_btc=2.5, tail=1.0, mean_tail=1.0)
        assert not weak


# ── Baseline calculation ─────────────────────────────────────────────────────

class TestGetBaseline:
    def _seed_rows(self, conn, rows):
        conn.executemany(
            """INSERT INTO treasury_auction_results
               (cusip, maturity_label, auction_date, high_yield, bid_to_cover, tail_bp,
                direct_pct, indirect_pct, dealer_pct, offering_amt, alert_fired)
               VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 0)""",
            rows,
        )
        conn.commit()

    def test_no_prior_rows_returns_nones(self):
        conn = db.get_connection()
        try:
            mean_btc, mean_tail = _get_baseline(conn, "10Y", "CUSIP_TODAY", "2026-06-21")
            assert mean_btc is None
            assert mean_tail is None
        finally:
            conn.close()

    def test_rolling_mean_of_prior_auctions(self):
        conn = db.get_connection()
        try:
            rows = [
                ("TST10Y001", "10Y", "2026-06-01", 4.40, 2.6, 1.0),
                ("TST10Y002", "10Y", "2026-05-01", 4.38, 2.8, 1.5),
                ("TST10Y003", "10Y", "2026-04-01", 4.35, 2.5, 2.0),
            ]
            self._seed_rows(conn, rows)
            mean_btc, mean_tail = _get_baseline(conn, "10Y", "CUSIP_FUTURE", "2026-06-22")
            assert mean_btc == pytest.approx((2.6 + 2.8 + 2.5) / 3)
            assert mean_tail == pytest.approx((1.0 + 1.5 + 2.0) / 3)
        finally:
            conn.execute(
                "DELETE FROM treasury_auction_results WHERE cusip LIKE 'TST10Y%'"
            )
            conn.commit()
            conn.close()

    def test_excludes_current_cusip(self):
        conn = db.get_connection()
        try:
            rows = [
                ("EX_CURRENT", "30Y", "2026-06-20", 4.80, 2.2, 3.0),
                ("EX_PRIOR1",  "30Y", "2026-05-20", 4.75, 2.4, 1.5),
            ]
            self._seed_rows(conn, rows)
            mean_btc, _ = _get_baseline(conn, "30Y", "EX_CURRENT", "2026-06-21")
            assert mean_btc == pytest.approx(2.4)
        finally:
            conn.execute(
                "DELETE FROM treasury_auction_results WHERE cusip LIKE 'EX_%'"
            )
            conn.commit()
            conn.close()

    def test_capped_at_six_auctions(self):
        conn = db.get_connection()
        try:
            rows = [
                (f"CAP{i:02d}", "2Y", f"2026-0{i}-01", 4.0, float(i), 1.0)
                for i in range(1, 9)
            ]
            self._seed_rows(conn, rows)
            mean_btc, _ = _get_baseline(conn, "2Y", "CAP_FUTURE", "2026-09-01")
            # Only the 6 most recent rows are used: i=3..8 → mean = (3+4+5+6+7+8)/6 = 5.5
            assert mean_btc == pytest.approx(5.5)
        finally:
            conn.execute(
                "DELETE FROM treasury_auction_results WHERE cusip LIKE 'CAP%'"
            )
            conn.commit()
            conn.close()


# ── Full pipeline ─────────────────────────────────────────────────────────────

class TestCheckAuctionResults:
    _SAMPLE_API_ROW = {
        "cusip": "912810TU0",
        "security_term": "10-Year",
        "security_type": "Note",
        "auction_date": "2026-06-21",
        "high_yield": "4.52",
        "avg_med_yield": "4.50",
        "bid_to_cover_ratio": "2.65",
        "direct_bidder_accepted": "10000000",
        "indirect_bidder_accepted": "25000000",
        "primary_dealer_accepted": "4000000",
        "comp_accepted": "39000000",
        "offering_amt": "39000000",
    }

    def setup_method(self):
        conn = db.get_connection()
        conn.execute("DELETE FROM treasury_auction_results WHERE cusip = '912810TU0'")
        conn.commit()
        conn.close()

    def test_no_auctions_returns_zero(self):
        with patch("treasury_auction_engine.fetch_todays_auctions", return_value=[]):
            result = check_auction_results()
        assert result == 0

    def test_new_auction_stored_in_db(self):
        with (
            patch("treasury_auction_engine.fetch_todays_auctions", return_value=[self._SAMPLE_API_ROW]),
            patch("treasury_auction_engine.notify") as mock_notify,
        ):
            result = check_auction_results()

        assert result == 1
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT bid_to_cover, tail_bp, maturity_label FROM treasury_auction_results WHERE cusip = '912810TU0'"
            ).fetchone()
            assert row is not None
            assert row[0] == pytest.approx(2.65)
            assert row[1] == pytest.approx(2.0)
            assert row[2] == "10Y"
        finally:
            conn.close()

    def test_duplicate_auction_not_double_stored(self):
        with (
            patch("treasury_auction_engine.fetch_todays_auctions", return_value=[self._SAMPLE_API_ROW]),
            patch("treasury_auction_engine.notify"),
        ):
            check_auction_results()
            result = check_auction_results()

        assert result == 0

    def test_weak_btc_fires_alert(self):
        conn = db.get_connection()
        try:
            for i, (c, d, btc) in enumerate([
                ("PRIOR10Y1", "2026-06-01", 2.7),
                ("PRIOR10Y2", "2026-05-01", 2.8),
                ("PRIOR10Y3", "2026-04-01", 2.75),
                ("PRIOR10Y4", "2026-03-01", 2.65),
                ("PRIOR10Y5", "2026-02-01", 2.72),
                ("PRIOR10Y6", "2026-01-01", 2.70),
            ]):
                conn.execute(
                    """INSERT OR IGNORE INTO treasury_auction_results
                       (cusip, maturity_label, auction_date, high_yield, bid_to_cover, tail_bp,
                        direct_pct, indirect_pct, dealer_pct, offering_amt, alert_fired)
                       VALUES (?, '10Y', ?, 4.50, ?, 1.0, NULL, NULL, NULL, NULL, 0)""",
                    (c, d, btc),
                )
            conn.commit()
        finally:
            conn.close()

        weak_row = dict(self._SAMPLE_API_ROW)
        weak_row["bid_to_cover_ratio"] = "2.3"

        with (
            patch("treasury_auction_engine.fetch_todays_auctions", return_value=[weak_row]),
            patch("treasury_auction_engine.notify") as mock_notify,
        ):
            check_auction_results()

        mock_notify.assert_called_once()
        call_kwargs = mock_notify.call_args
        assert call_kwargs[0][0] == "treasury_auction_alert"
        assert "bid-to-cover" in call_kwargs[0][2]

        conn = db.get_connection()
        try:
            conn.execute(
                "DELETE FROM treasury_auction_results WHERE cusip LIKE 'PRIOR10Y%'"
            )
            conn.commit()
        finally:
            conn.close()

    def test_alert_dedup_does_not_refire(self):
        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM treasury_auction_results WHERE cusip = '912810TU0'")
            conn.execute(
                """INSERT INTO treasury_auction_results
                   (cusip, maturity_label, auction_date, high_yield, bid_to_cover, tail_bp,
                    direct_pct, indirect_pct, dealer_pct, offering_amt, alert_fired)
                   VALUES ('912810TU0', '10Y', '2026-06-21', 4.52, 2.65, 2.0,
                           NULL, NULL, NULL, NULL, 1)"""
            )
            conn.commit()
        finally:
            conn.close()

        with (
            patch("treasury_auction_engine.fetch_todays_auctions", return_value=[self._SAMPLE_API_ROW]),
            patch("treasury_auction_engine.notify") as mock_notify,
        ):
            check_auction_results()

        mock_notify.assert_not_called()

    def teardown_method(self):
        conn = db.get_connection()
        conn.execute("DELETE FROM treasury_auction_results WHERE cusip = '912810TU0'")
        conn.commit()
        conn.close()
