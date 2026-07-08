"""
tests/test_08_database_helpers.py  ── DATABASE HELPER FUNCTION TESTS

Exercises the business logic in database.py helper functions:
  - log_score_event: upsert with COALESCE on close_price
  - upsert_quant_signal: insert + conflict update semantics
  - get_universe_tickers: FREETRADE_ONLY_MODE filtering
  - batch_update_trap_phase_actuals: single-transaction multi-row update
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db


# ── helpers ───────────────────────────────────────────────────────────────────

def _conn():
    return _db.get_connection()


# ── log_score_event ───────────────────────────────────────────────────────────

@pytest.mark.db
def test_log_score_event_inserts_row():
    """log_score_event must insert a new row into score_history."""
    conn = _conn()
    try:
        _db.log_score_event("TST_DB", "2099-01-01", 75, "BUY", 150.0)
        row = conn.execute(
            "SELECT score, signal, close_price FROM score_history "
            "WHERE ticker='TST_DB' AND date='2099-01-01'"
        ).fetchone()
        assert row is not None
        assert row["score"] == 75
        assert row["signal"] == "BUY"
        assert abs(row["close_price"] - 150.0) < 0.001
    finally:
        conn.execute("DELETE FROM score_history WHERE ticker='TST_DB'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_log_score_event_upserts_existing_row():
    """log_score_event must update score/signal on conflict."""
    conn = _conn()
    try:
        _db.log_score_event("TST_DB", "2099-01-02", 60, "HOLD", 100.0)
        _db.log_score_event("TST_DB", "2099-01-02", 80, "BUY", 110.0)
        row = conn.execute(
            "SELECT score, signal, close_price FROM score_history "
            "WHERE ticker='TST_DB' AND date='2099-01-02'"
        ).fetchone()
        assert row["score"] == 80
        assert row["signal"] == "BUY"
        assert abs(row["close_price"] - 110.0) < 0.001
    finally:
        conn.execute("DELETE FROM score_history WHERE ticker='TST_DB'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_log_score_event_coalesce_preserves_existing_close_price():
    """When close_price is None on the second write, the existing value must be kept."""
    conn = _conn()
    try:
        _db.log_score_event("TST_DB", "2099-01-03", 55, "HOLD", 200.0)
        _db.log_score_event("TST_DB", "2099-01-03", 65, "BUY", None)
        row = conn.execute(
            "SELECT close_price FROM score_history "
            "WHERE ticker='TST_DB' AND date='2099-01-03'"
        ).fetchone()
        # COALESCE(excluded.close_price, score_history.close_price) — None must not overwrite
        assert row["close_price"] is not None
        assert abs(row["close_price"] - 200.0) < 0.001
    finally:
        conn.execute("DELETE FROM score_history WHERE ticker='TST_DB'")
        conn.commit()
        conn.close()


# ── upsert_quant_signal ───────────────────────────────────────────────────────

@pytest.mark.db
def test_upsert_quant_signal_inserts_row():
    conn = _conn()
    try:
        ok = _db.upsert_quant_signal("UQ_TEST", "2099-02-01", 123.0, 50000, rsi_14=55.0)
        assert ok is True
        row = conn.execute(
            "SELECT close_price, volume, rsi_14 FROM quant_signals "
            "WHERE ticker='UQ_TEST' AND date='2099-02-01'"
        ).fetchone()
        assert row is not None
        assert abs(row["close_price"] - 123.0) < 0.001
        assert row["volume"] == 50000
        assert abs(row["rsi_14"] - 55.0) < 0.001
    finally:
        conn.execute("DELETE FROM quant_signals WHERE ticker='UQ_TEST'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_upsert_quant_signal_updates_on_conflict():
    conn = _conn()
    try:
        _db.upsert_quant_signal("UQ_TEST", "2099-02-02", 100.0, 1000)
        _db.upsert_quant_signal("UQ_TEST", "2099-02-02", 110.0, 2000, rsi_14=70.0)
        row = conn.execute(
            "SELECT close_price, volume, rsi_14 FROM quant_signals "
            "WHERE ticker='UQ_TEST' AND date='2099-02-02'"
        ).fetchone()
        assert abs(row["close_price"] - 110.0) < 0.001
        assert row["volume"] == 2000
        assert abs(row["rsi_14"] - 70.0) < 0.001
    finally:
        conn.execute("DELETE FROM quant_signals WHERE ticker='UQ_TEST'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_upsert_quant_signal_coalesce_preserves_ml_score():
    """ml_confidence_score uses COALESCE — a None update must not overwrite an existing value."""
    conn = _conn()
    try:
        _db.upsert_quant_signal("UQ_TEST", "2099-02-03", 100.0, 1000, ml_confidence_score=0.88)
        _db.upsert_quant_signal("UQ_TEST", "2099-02-03", 101.0, 1100, ml_confidence_score=None)
        row = conn.execute(
            "SELECT ml_confidence_score FROM quant_signals "
            "WHERE ticker='UQ_TEST' AND date='2099-02-03'"
        ).fetchone()
        assert row["ml_confidence_score"] is not None
        assert abs(row["ml_confidence_score"] - 0.88) < 0.001
    finally:
        conn.execute("DELETE FROM quant_signals WHERE ticker='UQ_TEST'")
        conn.commit()
        conn.close()


# ── get_universe_tickers ──────────────────────────────────────────────────────

@pytest.mark.db
def test_get_universe_tickers_returns_all_by_default():
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO market_universe (ticker, is_freetrade) VALUES ('GU_ALL', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO market_universe (ticker, is_freetrade) VALUES ('GU_FT', 1)"
        )
        conn.commit()
        with patch("db_helpers.load_config", return_value={"UI_PREFERENCES": {"FREETRADE_ONLY_MODE": False}}):
            tickers = _db.get_universe_tickers()
        assert "GU_ALL" in tickers
        assert "GU_FT" in tickers
    finally:
        conn.execute("DELETE FROM market_universe WHERE ticker IN ('GU_ALL', 'GU_FT')")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_get_universe_tickers_freetrade_only_mode_filters():
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO market_universe (ticker, is_freetrade) VALUES ('GU_NON', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO market_universe (ticker, is_freetrade) VALUES ('GU_FT2', 1)"
        )
        conn.commit()
        with patch("db_helpers.load_config", return_value={"UI_PREFERENCES": {"FREETRADE_ONLY_MODE": True}}):
            tickers = _db.get_universe_tickers()
        assert "GU_NON" not in tickers
        assert "GU_FT2" in tickers
    finally:
        conn.execute("DELETE FROM market_universe WHERE ticker IN ('GU_NON', 'GU_FT2')")
        conn.commit()
        conn.close()


# ── get_mutual_fund_tickers ───────────────────────────────────────────────────

@pytest.mark.db
def test_get_mutual_fund_tickers_returns_only_mutualfund_quote_type():
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO market_universe (ticker, quote_type) VALUES ('GU_FUND', 'MUTUALFUND')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO market_universe (ticker, quote_type) VALUES ('GU_STOCK', 'EQUITY')"
        )
        conn.commit()
        result = _db.get_mutual_fund_tickers(["GU_FUND", "GU_STOCK"])
        assert result == {"GU_FUND"}
    finally:
        conn.execute("DELETE FROM market_universe WHERE ticker IN ('GU_FUND', 'GU_STOCK')")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_get_mutual_fund_tickers_empty_input_returns_empty_set():
    assert _db.get_mutual_fund_tickers([]) == set()


@pytest.mark.db
def test_get_mutual_fund_tickers_unknown_ticker_excluded():
    """A ticker not yet in market_universe (e.g. brand-new, unclassified) is not a fund match."""
    assert _db.get_mutual_fund_tickers(["GU_UNKNOWN_TICKER"]) == set()


@pytest.mark.db
def test_get_mutual_fund_tickers_matches_0P_prefix_with_no_db_row():
    """Yahoo's OEIC/mutual-fund symbol scheme always starts with '0P' — a ticker not yet
    classified anywhere in the DB (e.g. a just-closed position never scanned) must still be
    caught, since waiting on the nightly quant scan/profiler is what caused the original bug."""
    assert _db.get_mutual_fund_tickers(["0P0001RI3X.L"]) == {"0P0001RI3X.L"}


@pytest.mark.db
def test_get_mutual_fund_tickers_finds_asset_profiles_classification():
    """A portfolio-only ticker bought via a Built-in Account has no market_universe row —
    only asset_profiles (fundamentals profiler) or stock_signals (quant scan) ever get it."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO asset_profiles (ticker, quote_type) VALUES ('GU_AP_FUND', 'MUTUALFUND')"
        )
        conn.commit()
        assert _db.get_mutual_fund_tickers(["GU_AP_FUND"]) == {"GU_AP_FUND"}
    finally:
        conn.execute("DELETE FROM asset_profiles WHERE ticker = 'GU_AP_FUND'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_get_mutual_fund_tickers_finds_stock_signals_classification():
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO stock_signals (ticker, quote_type) VALUES ('GU_SS_FUND', 'MUTUALFUND')"
        )
        conn.commit()
        assert _db.get_mutual_fund_tickers(["GU_SS_FUND"]) == {"GU_SS_FUND"}
    finally:
        conn.execute("DELETE FROM stock_signals WHERE ticker = 'GU_SS_FUND'")
        conn.commit()
        conn.close()


# ── batch_update_trap_phase_actuals ───────────────────────────────────────────

@pytest.mark.db
def test_batch_update_trap_phase_actuals_updates_all_rows():
    conn = _conn()
    try:
        _db.log_trap_phase("BTPA_1", "BULL_TRAP_RISK", "2019-05-01", 100.0, "2019-05-01 10:00:00")
        _db.log_trap_phase("BTPA_2", "CAPITULATION_FORMING", "2019-05-02", 80.0, "2019-05-02 10:00:00")
        rows = conn.execute(
            "SELECT id FROM trap_phase_history WHERE ticker IN ('BTPA_1','BTPA_2') ORDER BY ticker"
        ).fetchall()
        ids = [r["id"] for r in rows]
        assert len(ids) == 2
        payloads = [(ids[0], 14, 105.0, "2019-05-15", 1), (ids[1], 14, 75.0, "2019-05-16", 0)]
        _db.batch_update_trap_phase_actuals(payloads)
        updated = conn.execute(
            "SELECT ticker, actual_price_14d, direction_correct_14d "
            "FROM trap_phase_history WHERE ticker IN ('BTPA_1','BTPA_2') ORDER BY ticker"
        ).fetchall()
        assert len(updated) == 2
        assert abs(updated[0]["actual_price_14d"] - 105.0) < 0.001
        assert updated[0]["direction_correct_14d"] == 1
        assert abs(updated[1]["actual_price_14d"] - 75.0) < 0.001
        assert updated[1]["direction_correct_14d"] == 0
    finally:
        conn.execute("DELETE FROM trap_phase_history WHERE ticker IN ('BTPA_1','BTPA_2')")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_batch_update_trap_phase_actuals_empty_payload_is_noop():
    _db.batch_update_trap_phase_actuals([])


# ── get_auction_summary ───────────────────────────────────────────────────────

@pytest.mark.db
def test_get_auction_summary_empty_table_returns_empty_list():
    conn = _conn()
    try:
        conn.execute("DELETE FROM treasury_auction_results")
        conn.commit()
        assert _db.get_auction_summary() == []
    finally:
        conn.close()


@pytest.mark.db
def test_get_auction_summary_returns_recent_rows_within_30_days():
    conn = _conn()
    try:
        conn.execute("DELETE FROM treasury_auction_results")
        conn.execute(
            """INSERT INTO treasury_auction_results
               (cusip, maturity_label, auction_date, high_yield, bid_to_cover, tail_bp,
                direct_pct, indirect_pct, dealer_pct, offering_amt, alert_fired)
               VALUES ('GAS1', '10Y', date('now'), 4.3, 2.5, 1.0, 18.0, 65.0, 17.0, 39000, 0)"""
        )
        conn.execute(
            """INSERT INTO treasury_auction_results
               (cusip, maturity_label, auction_date, high_yield, bid_to_cover, tail_bp,
                direct_pct, indirect_pct, dealer_pct, offering_amt, alert_fired)
               VALUES ('GAS2', '30Y', date('now', '-60 days'), 4.6, 2.2, 3.0, 15.0, 60.0, 25.0, 20000, 1)"""
        )
        conn.commit()
        rows = _db.get_auction_summary()
        assert len(rows) == 1
        assert rows[0]["maturity_label"] == "10Y"
        assert rows[0]["alert_fired"] == 0
    finally:
        conn.execute("DELETE FROM treasury_auction_results WHERE cusip IN ('GAS1', 'GAS2')")
        conn.commit()
        conn.close()


# ── ticker registry CRUD ────────────────────────────────────────────────────────

@pytest.mark.db
def test_upsert_ticker_registry_row_inserts_new_row():
    conn = _conn()
    try:
        ok = _db.upsert_ticker_registry_row(
            ticker="TST_REG", display_name="Test Registry Row", region="Europe",
            asset_type="Index", exchange="LSE", currency="GBP",
        )
        assert ok is True
        row = _db.get_ticker_registry_row("TST_REG")
        assert row is not None
        assert row["display_name"] == "Test Registry Row"
        assert row["region"] == "Europe"
        assert row["enabled"] == 1
    finally:
        conn.execute("DELETE FROM market_ticker_registry WHERE ticker='TST_REG'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_upsert_ticker_registry_row_updates_on_conflict():
    conn = _conn()
    try:
        _db.upsert_ticker_registry_row(
            ticker="TST_REG", display_name="Original", region="Europe",
            asset_type="Index", exchange="LSE", currency="GBP",
        )
        _db.upsert_ticker_registry_row(
            ticker="TST_REG", display_name="Updated", region="Europe",
            asset_type="Index", exchange="LSE", currency="GBP", sort_order=5,
        )
        row = _db.get_ticker_registry_row("TST_REG")
        assert row["display_name"] == "Updated"
        assert row["sort_order"] == 5
    finally:
        conn.execute("DELETE FROM market_ticker_registry WHERE ticker='TST_REG'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_get_ticker_registry_row_by_future_resolves_spot_row():
    conn = _conn()
    try:
        row = _db.get_ticker_registry_row_by_future("ES=F")
        assert row is not None
        assert row["ticker"] == "^GSPC"
    finally:
        conn.close()


@pytest.mark.db
def test_get_ticker_registry_row_by_future_unknown_returns_none():
    assert _db.get_ticker_registry_row_by_future("NOPE=F") is None


@pytest.mark.db
def test_soft_delete_ticker_registry_row_disables_without_deleting():
    conn = _conn()
    try:
        _db.upsert_ticker_registry_row(
            ticker="TST_REG", display_name="Original", region="Europe",
            asset_type="Index", exchange="LSE", currency="GBP",
        )
        ok = _db.soft_delete_ticker_registry_row("TST_REG")
        assert ok is True
        row = _db.get_ticker_registry_row("TST_REG")
        assert row is not None
        assert row["enabled"] == 0
        enabled_only = _db.get_ticker_registry(enabled_only=True)
        assert "TST_REG" not in {r["ticker"] for r in enabled_only}
        all_rows = _db.get_ticker_registry(enabled_only=False)
        assert "TST_REG" in {r["ticker"] for r in all_rows}
    finally:
        conn.execute("DELETE FROM market_ticker_registry WHERE ticker='TST_REG'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_get_ticker_registry_enabled_only_excludes_disabled_rows():
    rows = _db.get_ticker_registry(enabled_only=True)
    assert all(r.get("enabled", 1) != 0 for r in rows)
    tickers = {r["ticker"] for r in rows}
    assert "^GSPC" in tickers
