"""
tests/test_treasury_auction_engine.py — Sovereign Debt Auction Monitor Tests

Covers:
  • _safe_float / _pct / _tail_bp — numeric helpers
  • is_weak — alert threshold logic (both signals, each alone, neither, wide tier for bills/FRN/TIPS)
  • _get_baseline — rolling 6-auction mean from real SQLite rows
  • _maturity_label / _record_tail_bp — pooling of reopenings, TIPS/FRN series, bill discount-rate tails
  • fetch_auctions / _window_start — API request shape and catch-up window
  • check_auction_results — DB write + no-op when API returns nothing
  • check_auction_results — unresulted auctions skipped, placeholder rows filled in
  • check_auction_results — alert fires on weak bid-to-cover, silent for older auctions
  • check_auction_results — alert dedup (alert_fired flag prevents re-alert)
  • check_auction_results — flags re-derived for stored rows, even when the API returns nothing
  • migrate_db — empty placeholder rows trigger a rebuild of the table
"""

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
import db_schema
from treasury_auction_engine import (
    _safe_float,
    _pct,
    _tail_bp,
    _maturity_label,
    _record_tail_bp,
    _window_start,
    fetch_auctions,
    is_weak,
    _is_wide_series,
    format_weakness_message,
    _get_baseline,
    check_auction_results,
)


def _api_row(**overrides):
    """One auctions_query record shaped like the live API: every value a string, 'null' for missing."""
    row = {
        "cusip": "912810TU0",
        "security_type": "Note",
        "security_term": "10-Year",
        "original_security_term": "10-Year",
        "floating_rate": "No",
        "inflation_index_security": "No",
        "auction_date": "2026-06-21",
        "high_yield": "4.52",
        "avg_med_yield": "4.50",
        "high_discnt_rate": "null",
        "avg_med_discnt_rate": "null",
        "bid_to_cover_ratio": "2.65",
        "direct_bidder_accepted": "10000000",
        "indirect_bidder_accepted": "25000000",
        "primary_dealer_accepted": "4000000",
        "comp_accepted": "39000000",
        "offering_amt": "39000000",
    }
    row.update(overrides)
    return row


def _clear_table():
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM treasury_auction_results")
        conn.commit()
    finally:
        conn.close()


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
        weak, weak_btc, weak_tail = is_weak(btc=2.7, mean_btc=2.5, tail=1.0, mean_tail=1.0)
        assert not weak
        assert not weak_btc
        assert not weak_tail

    def test_low_btc_triggers_alert(self):
        weak, weak_btc, weak_tail = is_weak(btc=2.1, mean_btc=2.5, tail=1.0, mean_tail=1.0)
        assert weak
        assert weak_btc
        assert not weak_tail

    def test_high_tail_triggers_alert(self):
        weak, weak_btc, weak_tail = is_weak(btc=2.5, mean_btc=2.5, tail=5.0, mean_tail=1.0)
        assert weak
        assert not weak_btc
        assert weak_tail

    def test_both_signals_trigger(self):
        weak, weak_btc, weak_tail = is_weak(btc=2.0, mean_btc=2.5, tail=6.0, mean_tail=1.0)
        assert weak
        assert weak_btc
        assert weak_tail

    def test_none_baseline_no_alert(self):
        weak, weak_btc, weak_tail = is_weak(btc=2.0, mean_btc=None, tail=6.0, mean_tail=None)
        assert not weak

    def test_none_metric_no_alert(self):
        weak, weak_btc, weak_tail = is_weak(btc=None, mean_btc=2.5, tail=None, mean_tail=1.0)
        assert not weak

    def test_btc_exactly_at_threshold_no_alert(self):
        weak, weak_btc, weak_tail = is_weak(btc=2.3, mean_btc=2.5, tail=1.0, mean_tail=1.0)
        assert not weak

    def test_nominal_note_flags_a_0_3_btc_drop(self):
        weak, weak_btc, _ = is_weak(2.2, 2.5, 1.0, 1.0, "10Y")
        assert weak and weak_btc

    def test_bill_tolerates_a_0_3_btc_drop(self):
        weak, _, _ = is_weak(2.2, 2.5, 1.0, 1.0, "4W")
        assert not weak

    def test_bill_flags_a_0_5_btc_drop(self):
        weak, weak_btc, _ = is_weak(2.0, 2.5, 1.0, 1.0, "4W")
        assert weak and weak_btc

    def test_nominal_note_flags_a_3_bp_tail_widening(self):
        weak, _, weak_tail = is_weak(2.5, 2.5, 4.0, 1.0, "10Y")
        assert weak and weak_tail

    def test_bill_tolerates_a_3_bp_tail_widening(self):
        weak, _, _ = is_weak(2.5, 2.5, 4.0, 1.0, "8W")
        assert not weak

    def test_bill_flags_a_5_bp_tail_widening(self):
        weak, _, weak_tail = is_weak(2.5, 2.5, 6.0, 1.0, "8W")
        assert weak and weak_tail

    def test_frn_uses_the_wide_tier(self):
        assert not is_weak(2.2, 2.5, None, None, "2Y FRN")[0]
        assert is_weak(2.0, 2.5, None, None, "2Y FRN")[0]


class TestIsWideSeries:
    @pytest.mark.parametrize("label", ["4W", "6W", "8W", "3M", "4M", "6M", "1Y", "27-Day", "2Y FRN", "10Y TIPS"])
    def test_wide_series(self, label):
        assert _is_wide_series(label)

    @pytest.mark.parametrize("label", ["2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y", ""])
    def test_nominal_notes_and_bonds_are_not_wide(self, label):
        assert not _is_wide_series(label)


# ── Plain-English alert message ──────────────────────────────────────────────

class TestFormatWeaknessMessage:
    def test_flags_weak_btc_only(self):
        msg = format_weakness_message("10Y", "2026-06-25", 2.74, 2.99, 1.0, 1.0, weak_btc=True, weak_tail=False)
        assert "weaker than usual" in msg
        assert "wider than usual" not in msg
        assert "Why it matters" in msg
        assert "/treasury-auctions" in msg

    def test_flags_weak_tail_only(self):
        msg = format_weakness_message("10Y", "2026-06-25", 2.9, 2.99, 5.0, 1.0, weak_btc=False, weak_tail=True)
        assert "wider than usual" in msg
        assert "weaker than usual" not in msg

    def test_always_covers_both_metrics(self):
        msg = format_weakness_message("10Y", "2026-06-25", 2.74, 2.99, 5.0, 1.0, weak_btc=True, weak_tail=True)
        assert "Demand (bid-to-cover)" in msg
        assert "Bid quality (yield tail)" in msg

    def test_missing_baseline_handled_gracefully(self):
        msg = format_weakness_message("10Y", "2026-06-25", 2.74, None, None, None, weak_btc=False, weak_tail=False)
        assert "no recent baseline yet" in msg


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


# ── Labels and tails ─────────────────────────────────────────────────────────

class TestMaturityLabel:
    def test_clean_note_term(self):
        assert _maturity_label(_api_row()) == "10Y"

    def test_reopened_note_pools_into_original_term(self):
        rec = _api_row(security_term="9-Year 10-Month", original_security_term="10-Year")
        assert _maturity_label(rec) == "10Y"

    def test_reopened_bond_pools_into_original_term(self):
        rec = _api_row(security_type="Bond", security_term="29-Year 10-Month", original_security_term="30-Year")
        assert _maturity_label(rec) == "30Y"

    def test_tips_is_its_own_series(self):
        rec = _api_row(security_term="9-Year 10-Month", original_security_term="10-Year", inflation_index_security="Yes")
        assert _maturity_label(rec) == "10Y TIPS"

    def test_frn_is_its_own_series(self):
        rec = _api_row(security_term="1-Year 10-Month", original_security_term="2-Year", floating_rate="Yes")
        assert _maturity_label(rec) == "2Y FRN"

    def test_reopened_bill_keeps_its_own_term_not_the_original_issue(self):
        rec = _api_row(security_type="Bill", security_term="4-Week", original_security_term="17-Week")
        assert _maturity_label(rec) == "4W"

    def test_six_week_bill_uses_short_label(self):
        rec = _api_row(security_type="Bill", security_term="6-Week", original_security_term="26-Week")
        assert _maturity_label(rec) == "6W"

    def test_null_original_term_falls_back_to_security_term(self):
        rec = _api_row(original_security_term="null")
        assert _maturity_label(rec) == "10Y"


class TestRecordTailBp:
    def test_note_uses_yields(self):
        assert _record_tail_bp(_api_row()) == pytest.approx(2.0)

    def test_bill_uses_discount_rates(self):
        rec = _api_row(security_type="Bill", high_yield="null", avg_med_yield="null",
                       high_discnt_rate="3.98", avg_med_discnt_rate="3.91")
        assert _record_tail_bp(rec) == pytest.approx(7.0)

    def test_frn_without_yield_has_no_tail(self):
        rec = _api_row(floating_rate="Yes", high_yield="null", avg_med_yield="null")
        assert _record_tail_bp(rec) is None


# ── API request and catch-up window ──────────────────────────────────────────

class TestFetchAuctions:
    def test_sends_singular_filter_with_date_range_and_ascending_sort(self):
        response = MagicMock()
        response.json.return_value = {"data": [_api_row()]}
        with patch("treasury_auction_engine.requests.get", return_value=response) as mock_get:
            rows = fetch_auctions("2026-06-18", "2026-06-21")

        params = mock_get.call_args.kwargs["params"]
        assert params["filter"] == "auction_date:gte:2026-06-18,auction_date:lte:2026-06-21"
        assert "filters" not in params
        assert params["sort"] == "auction_date"
        assert rows == [_api_row()]

    def test_request_failure_returns_empty_list(self):
        import requests as _requests
        with patch("treasury_auction_engine.requests.get", side_effect=_requests.ConnectionError("down")):
            assert fetch_auctions("2026-06-18", "2026-06-21") == []


class TestWindowStart:
    def setup_method(self):
        _clear_table()

    def teardown_method(self):
        _clear_table()

    def _seed(self, auction_date):
        conn = db.get_connection()
        try:
            conn.execute(
                """INSERT INTO treasury_auction_results
                   (cusip, maturity_label, auction_date, bid_to_cover, alert_fired)
                   VALUES ('TAEWIN', '10Y', ?, 2.6, 0)""",
                (auction_date,),
            )
            conn.commit()
        finally:
            conn.close()

    def test_empty_table_looks_back_the_maximum(self):
        assert _window_start(date(2026, 10, 12)) == (date(2026, 10, 12) - timedelta(days=180)).isoformat()

    def test_resumes_a_few_days_before_the_newest_stored_auction(self):
        self._seed("2026-07-02")
        assert _window_start(date(2026, 10, 12)) == "2026-06-29"

    def test_never_looks_back_further_than_the_maximum(self):
        self._seed("2020-01-01")
        assert _window_start(date(2026, 10, 12)) == (date(2026, 10, 12) - timedelta(days=180)).isoformat()


# ── Full pipeline ─────────────────────────────────────────────────────────────

class TestCheckAuctionResults:
    _TODAY = date(2026, 6, 21)

    def setup_method(self):
        _clear_table()

    def teardown_method(self):
        _clear_table()

    def _run(self, rows, today=None):
        with (
            patch("treasury_auction_engine.fetch_auctions", return_value=rows) as mock_fetch,
            patch("treasury_auction_engine.time_engine.exchange_local_date", return_value=today or self._TODAY),
            patch("treasury_auction_engine.notify") as mock_notify,
        ):
            result = check_auction_results()
        return result, mock_notify, mock_fetch

    def _seed_priors(self, label="10Y", btc=2.7):
        conn = db.get_connection()
        try:
            for i in range(6):
                conn.execute(
                    """INSERT INTO treasury_auction_results
                       (cusip, maturity_label, auction_date, high_yield, bid_to_cover, tail_bp, alert_fired)
                       VALUES (?, ?, ?, 4.50, ?, 1.0, 0)""",
                    (f"TAEPRIOR{i}", label, f"2026-05-0{i + 1}", btc),
                )
            conn.commit()
        finally:
            conn.close()

    def _fetch_row(self, cusip="912810TU0"):
        conn = db.get_connection()
        try:
            return conn.execute(
                "SELECT * FROM treasury_auction_results WHERE cusip = ?", (cusip,)
            ).fetchone()
        finally:
            conn.close()

    def test_no_auctions_returns_zero(self):
        result, _, _ = self._run([])
        assert result == 0

    def test_new_auction_stored_in_db(self):
        result, _, _ = self._run([_api_row()])

        assert result == 1
        row = self._fetch_row()
        assert row["bid_to_cover"] == pytest.approx(2.65)
        assert row["tail_bp"] == pytest.approx(2.0)
        assert row["maturity_label"] == "10Y"

    def test_fetches_from_empty_table_window_through_today(self):
        _, _, mock_fetch = self._run([])
        start = (self._TODAY - timedelta(days=180)).isoformat()
        mock_fetch.assert_called_once_with(start, "2026-06-21")

    def test_duplicate_auction_not_double_stored(self):
        self._run([_api_row()])
        result, _, _ = self._run([_api_row()])

        assert result == 0

    def test_announced_auction_without_results_is_not_stored(self):
        rec = _api_row(cusip="TAEUPCOMING", auction_date="2026-06-22",
                       bid_to_cover_ratio="null", high_yield="null", avg_med_yield="null")
        result, _, _ = self._run([rec])

        assert result == 0
        assert self._fetch_row("TAEUPCOMING") is None

    def test_existing_row_without_results_is_filled_in(self):
        conn = db.get_connection()
        try:
            conn.execute(
                """INSERT INTO treasury_auction_results
                   (cusip, maturity_label, auction_date, offering_amt, alert_fired)
                   VALUES ('912810TU0', '9-Year 10-Month', '2026-06-21', 39000000, 0)"""
            )
            conn.commit()
        finally:
            conn.close()

        result, _, _ = self._run([_api_row(security_term="9-Year 10-Month")])

        assert result == 0
        row = self._fetch_row()
        assert row["bid_to_cover"] == pytest.approx(2.65)
        assert row["maturity_label"] == "10Y"

    def test_bill_tail_and_label_stored(self):
        rec = _api_row(cusip="TAEBILL", security_type="Bill", security_term="4-Week",
                       original_security_term="17-Week", high_yield="null", avg_med_yield="null",
                       high_discnt_rate="3.98", avg_med_discnt_rate="3.91", bid_to_cover_ratio="2.40")
        self._run([rec])

        row = self._fetch_row("TAEBILL")
        assert row["maturity_label"] == "4W"
        assert row["tail_bp"] == pytest.approx(7.0)

    def test_weak_btc_today_fires_alert(self):
        self._seed_priors()
        _, mock_notify, _ = self._run([_api_row(bid_to_cover_ratio="2.3")])

        mock_notify.assert_called_once()
        args = mock_notify.call_args[0]
        assert args[0] == "treasury_auction_alert"
        assert "bid-to-cover" in args[2]
        assert self._fetch_row()["alert_fired"] == 1

    def test_weak_btc_on_older_auction_is_flagged_silently(self):
        self._seed_priors()
        _, mock_notify, _ = self._run([_api_row(bid_to_cover_ratio="2.3", auction_date="2026-06-18")])

        mock_notify.assert_not_called()
        assert self._fetch_row()["alert_fired"] == 1

    def test_catch_up_processes_oldest_first_so_baselines_include_earlier_rows(self):
        rows = [
            _api_row(cusip="TAEC3", auction_date="2026-06-17", bid_to_cover_ratio="2.2"),
            _api_row(cusip="TAEC1", auction_date="2026-06-15", bid_to_cover_ratio="2.7"),
            _api_row(cusip="TAEC2", auction_date="2026-06-16", bid_to_cover_ratio="2.7"),
        ]
        self._run(rows)

        assert self._fetch_row("TAEC3")["alert_fired"] == 1
        assert self._fetch_row("TAEC1")["alert_fired"] == 0

    def _bill_row(self, btc, **overrides):
        rec = _api_row(cusip="TAEBILL", security_type="Bill", security_term="4-Week",
                       original_security_term="17-Week", high_yield="null", avg_med_yield="null",
                       high_discnt_rate="3.90", avg_med_discnt_rate="3.90", bid_to_cover_ratio=btc)
        rec.update(overrides)
        return rec

    def _seed_row(self, cusip, label, auction_date, btc, alert_fired):
        conn = db.get_connection()
        try:
            conn.execute(
                """INSERT INTO treasury_auction_results
                   (cusip, maturity_label, auction_date, high_yield, bid_to_cover, tail_bp, alert_fired)
                   VALUES (?, ?, ?, 4.50, ?, 1.0, ?)""",
                (cusip, label, auction_date, btc, alert_fired),
            )
            conn.commit()
        finally:
            conn.close()

    def test_bill_btc_drop_within_the_wide_tier_does_not_alert(self):
        self._seed_priors(label="4W")
        _, mock_notify, _ = self._run([self._bill_row("2.4")])

        mock_notify.assert_not_called()
        assert self._fetch_row("TAEBILL")["alert_fired"] == 0

    def test_bill_btc_drop_beyond_the_wide_tier_alerts(self):
        self._seed_priors(label="4W")
        _, mock_notify, _ = self._run([self._bill_row("2.2")])

        mock_notify.assert_called_once()
        assert self._fetch_row("TAEBILL")["alert_fired"] == 1

    def test_nominal_note_alerts_on_a_0_3_btc_drop(self):
        self._seed_priors(label="10Y")
        _, mock_notify, _ = self._run([_api_row(bid_to_cover_ratio="2.4")])

        mock_notify.assert_called_once()

    def test_stored_flag_no_longer_weak_under_the_rule_is_cleared_silently(self):
        self._seed_priors(label="4W")
        self._seed_row("TAEOLD", "4W", "2026-06-10", 2.4, alert_fired=1)

        _, mock_notify, _ = self._run([])

        mock_notify.assert_not_called()
        assert self._fetch_row("TAEOLD")["alert_fired"] == 0

    def test_stored_weak_row_is_flagged_silently_even_when_the_api_returns_nothing(self):
        self._seed_priors(label="4W")
        self._seed_row("TAEOLD", "4W", "2026-06-10", 2.1, alert_fired=0)

        _, mock_notify, _ = self._run([])

        mock_notify.assert_not_called()
        assert self._fetch_row("TAEOLD")["alert_fired"] == 1

    def test_weak_auction_today_notifies_once_across_runs(self):
        self._seed_priors(label="10Y")
        self._run([_api_row(bid_to_cover_ratio="2.3")])
        _, mock_notify, _ = self._run([_api_row(bid_to_cover_ratio="2.3")])

        mock_notify.assert_not_called()

    def test_alert_dedup_does_not_refire(self):
        conn = db.get_connection()
        try:
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
        self._seed_priors()

        _, mock_notify, _ = self._run([_api_row(bid_to_cover_ratio="2.3")])

        mock_notify.assert_not_called()
        assert self._fetch_row()["alert_fired"] == 1


# ── Migration ────────────────────────────────────────────────────────────────

class TestPlaceholderRowMigration:
    def setup_method(self):
        _clear_table()

    def teardown_method(self):
        _clear_table()

    def _seed(self, cusip, bid_to_cover):
        conn = db.get_connection()
        try:
            conn.execute(
                """INSERT INTO treasury_auction_results
                   (cusip, maturity_label, auction_date, bid_to_cover, alert_fired)
                   VALUES (?, '10Y', '2026-06-21', ?, 0)""",
                (cusip, bid_to_cover),
            )
            conn.commit()
        finally:
            conn.close()

    def _migrate_and_count(self):
        conn = db.get_connection()
        try:
            db_schema.migrate_db(conn, conn.cursor())
            conn.commit()
            return conn.execute("SELECT COUNT(*) FROM treasury_auction_results").fetchone()[0]
        finally:
            conn.close()

    def test_placeholder_rows_trigger_a_full_rebuild(self):
        self._seed("TAEMIG1", None)
        self._seed("TAEMIG2", 2.6)

        assert self._migrate_and_count() == 0

    def test_table_without_placeholders_is_left_alone(self):
        self._seed("TAEMIG3", 2.6)

        assert self._migrate_and_count() == 1
