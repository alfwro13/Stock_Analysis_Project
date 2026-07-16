"""
tests/test_predicted_movers_engine.py — Predicted Movers Tests

Covers:
  • _target_date()                    — ~10-trading-day-forward business-day offset
  • get_leaderboard()                 — sort modes, missing-price drop, empty scope
  • log_predictions()                 — insert + same-day idempotency
  • backfill_actual_outcomes()        — resolves direction/within-band correctness from
                                         quant_signals, leaves unresolved rows before target_date
  • get_accuracy_summary()            — empty + aggregate cases
  • db_helpers.get_portfolio_watchlist_tickers() — union + ignored-ticker filtering
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
import db_helpers
from predicted_movers_engine import (
    SORT_GAINERS,
    SORT_LOSERS,
    SORT_MOVERS,
    _target_date,
    backfill_actual_outcomes,
    get_accuracy_summary,
    get_leaderboard,
    log_predictions,
)

T_A = "PM_A"
T_B = "PM_B"
T_C = "PM_C"


def _seed_quant_signal(ticker, date, close_price, price_q10, price_q90):
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO quant_signals (ticker, date, close_price, price_q10, price_q90)
               VALUES (?, ?, ?, ?, ?)""",
            (ticker, date, close_price, price_q10, price_q90),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_predicted_movers_row(ticker, predicted_date, close_price, price_q10, price_q90, target_date):
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO predicted_movers_history
               (ticker, predicted_date, predicted_ts, close_price, price_q10, price_q90, target_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticker, predicted_date, "2020-01-01 10:00:00", close_price, price_q10, price_q90, target_date),
        )
        conn.commit()
        return conn.execute(
            "SELECT id FROM predicted_movers_history WHERE ticker=? AND predicted_date=?",
            (ticker, predicted_date),
        ).fetchone()["id"]
    finally:
        conn.close()


class TestTargetDate:
    def test_weekday_offset_is_ten_business_days_forward(self):
        # 2024-01-02 is a Tuesday; 10 business days forward is 2024-01-16 (a Tuesday).
        assert _target_date("2024-01-02") == "2024-01-16"

    def test_weekend_anchor_rolls_forward_first(self):
        # 2024-01-06 is a Saturday; np.busday_offset rolls forward to the next business day
        # (Monday 2024-01-08) before counting 10 business days.
        assert _target_date("2024-01-06") == "2024-01-22"


class TestGetLeaderboard:
    def test_empty_scope_returns_empty_list(self):
        with patch("predicted_movers_engine.get_portfolio_watchlist_tickers", return_value=[]):
            assert get_leaderboard(scope="portfolio_watchlist") == []

    def test_sort_modes_order_correctly(self):
        _seed_quant_signal(T_A, "2024-01-02", 100.0, 90.0, 92.0)   # predicted mid 91 → -9%
        _seed_quant_signal(T_B, "2024-01-02", 100.0, 108.0, 112.0)  # predicted mid 110 → +10%
        with patch("predicted_movers_engine.get_portfolio_watchlist_tickers", return_value=[T_A, T_B]), \
             patch("predicted_movers_engine.current_price_map",
                   return_value={T_A: (100.0, "USD"), T_B: (100.0, "USD")}), \
             patch("predicted_movers_engine.get_company_names", return_value={}):
            gainers = get_leaderboard(scope="portfolio_watchlist", sort_mode=SORT_GAINERS)
            losers = get_leaderboard(scope="portfolio_watchlist", sort_mode=SORT_LOSERS)
            movers = get_leaderboard(scope="portfolio_watchlist", sort_mode=SORT_MOVERS)

        assert gainers[0]["ticker"] == T_B
        assert losers[0]["ticker"] == T_A
        assert movers[0]["ticker"] == T_B  # 10% > 9% in absolute terms

    def test_missing_current_price_drops_row(self):
        _seed_quant_signal(T_C, "2024-01-02", 100.0, 95.0, 105.0)
        with patch("predicted_movers_engine.get_portfolio_watchlist_tickers", return_value=[T_C]), \
             patch("predicted_movers_engine.current_price_map", return_value={}), \
             patch("predicted_movers_engine.get_company_names", return_value={}):
            results = get_leaderboard(scope="portfolio_watchlist")
        assert results == []


class TestLogPredictions:
    def test_inserts_one_row_per_ticker(self):
        _seed_quant_signal("PM_LOG1", "2024-02-01", 50.0, 45.0, 55.0)
        with patch("predicted_movers_engine.get_portfolio_watchlist_tickers", return_value=["PM_LOG1"]):
            inserted = log_predictions()
        assert inserted == 1
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM predicted_movers_history WHERE ticker='PM_LOG1' AND predicted_date='2024-02-01'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["price_q10"] == 45.0
        assert row["target_date"] == _target_date("2024-02-01")

    def test_same_day_rerun_is_idempotent(self):
        _seed_quant_signal("PM_LOG2", "2024-02-05", 50.0, 45.0, 55.0)
        with patch("predicted_movers_engine.get_portfolio_watchlist_tickers", return_value=["PM_LOG2"]):
            first = log_predictions()
            second = log_predictions()
        assert first == 1
        assert second == 0
        conn = db.get_connection()
        try:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM predicted_movers_history WHERE ticker='PM_LOG2'"
            ).fetchone()["c"]
        finally:
            conn.close()
        assert count == 1

    def test_skips_tickers_without_quantile_bands(self):
        conn = db.get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO quant_signals (ticker, date, close_price) VALUES (?, ?, ?)",
                ("PM_LOG3", "2024-02-06", 20.0),
            )
            conn.commit()
        finally:
            conn.close()
        with patch("predicted_movers_engine.get_portfolio_watchlist_tickers", return_value=["PM_LOG3"]):
            inserted = log_predictions()
        assert inserted == 0


class TestBackfillActualOutcomes:
    def test_returns_zero_when_no_pending_rows(self):
        with patch("db_helpers.get_unresolved_predicted_movers", return_value=[]):
            assert backfill_actual_outcomes() == 0

    def test_resolves_direction_correct_and_within_band(self):
        row_id = _seed_predicted_movers_row("PM_BF1", "2024-03-01", 100.0, 105.0, 115.0, "2024-03-15")
        _seed_quant_signal("PM_BF1", "2024-03-15", 112.0, None, None)
        resolved = backfill_actual_outcomes()
        assert resolved == 1
        conn = db.get_connection()
        try:
            row = dict(conn.execute(
                "SELECT * FROM predicted_movers_history WHERE id=?", (row_id,)
            ).fetchone())
        finally:
            conn.close()
        assert row["actual_price"] == 112.0
        assert row["actual_date"] == "2024-03-15"
        assert row["direction_correct"] == 1   # predicted mid 110 > 100, actual 112 > 100
        assert row["within_band_correct"] == 1  # 105 <= 112 <= 115

    def test_outside_band_and_wrong_direction(self):
        row_id = _seed_predicted_movers_row("PM_BF2", "2024-03-01", 100.0, 105.0, 115.0, "2024-03-15")
        _seed_quant_signal("PM_BF2", "2024-03-15", 90.0, None, None)  # fell instead of rose
        backfill_actual_outcomes()
        conn = db.get_connection()
        try:
            row = dict(conn.execute(
                "SELECT * FROM predicted_movers_history WHERE id=?", (row_id,)
            ).fetchone())
        finally:
            conn.close()
        assert row["direction_correct"] == 0
        assert row["within_band_correct"] == 0

    def test_leaves_rows_before_target_date_unresolved(self):
        _seed_predicted_movers_row("PM_BF3", "2024-03-01", 100.0, 95.0, 105.0, "2099-12-31")
        backfill_actual_outcomes()
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT direction_correct FROM predicted_movers_history WHERE ticker='PM_BF3'"
            ).fetchone()
        finally:
            conn.close()
        assert row["direction_correct"] is None

    def test_boundary_actual_equal_to_q10_counts_as_within_band(self):
        row_id = _seed_predicted_movers_row("PM_BF4", "2024-03-01", 100.0, 95.0, 115.0, "2024-03-15")
        _seed_quant_signal("PM_BF4", "2024-03-15", 95.0, None, None)
        backfill_actual_outcomes()
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT within_band_correct FROM predicted_movers_history WHERE id=?", (row_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row["within_band_correct"] == 1


class TestGetAccuracySummary:
    def test_empty_table_returns_shape(self):
        with patch("predicted_movers_engine.get_predicted_movers_accuracy",
                   return_value={"by_ticker": [], "overall": {}}):
            data = get_accuracy_summary()
        assert data == {"by_ticker": [], "overall": {}}

    def test_enriches_with_company_names(self):
        fake_accuracy = {
            "by_ticker": [{"ticker": "PM_ACC1", "total": 1, "resolved": 1, "pending": 0,
                            "direction_accuracy": 100.0, "within_band_accuracy": 100.0}],
            "overall": {"total": 1, "resolved": 1, "pending": 0,
                        "direction_accuracy": 100.0, "within_band_accuracy": 100.0},
        }
        with patch("predicted_movers_engine.get_predicted_movers_accuracy", return_value=fake_accuracy), \
             patch("predicted_movers_engine.get_company_names", return_value={"PM_ACC1": "Test Co"}):
            data = get_accuracy_summary()
        assert data["by_ticker"][0]["company_name"] == "Test Co"


class TestGetPortfolioWatchlistTickers:
    def test_unions_holdings_and_watchlist(self):
        with patch("accounts_engine.get_combined_holdings", return_value={T_A: {}}), \
             patch("database.get_watchlist_tickers", return_value=[T_B]):
            tickers = db_helpers.get_portfolio_watchlist_tickers()
        assert tickers == sorted([T_A, T_B])

    def test_ignored_ticker_excluded(self):
        config = {"IGNORED_TICKERS": [T_A]}
        with patch("accounts_engine.get_combined_holdings", return_value={T_A: {}}), \
             patch("database.get_watchlist_tickers", return_value=[T_B]), \
             patch("db_helpers.load_config", return_value=config):
            tickers = db_helpers.get_portfolio_watchlist_tickers()
        assert tickers == [T_B]
