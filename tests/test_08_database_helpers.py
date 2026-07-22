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
import db_helpers


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


# ── log_pretrade_check ──────────────────────────────────────────────────────

@pytest.mark.db
def test_log_pretrade_check_inserts_row():
    conn = _conn()
    try:
        db_helpers.log_pretrade_check(
            ticker="TST_PT", scope="all", proposed_value=1000.0, verdict="reject",
            breached_constraint="VaR", phi_score=80.0, var_pct_of_equity=5.0,
            max_correlation=0.6, suggested_reduced_value=300.0,
        )
        row = conn.execute(
            "SELECT * FROM pretrade_check_log WHERE ticker='TST_PT'"
        ).fetchone()
        assert row is not None
        assert row["scope"] == "all"
        assert row["verdict"] == "reject"
        assert row["breached_constraint"] == "VaR"
        assert abs(row["proposed_value"] - 1000.0) < 0.001
        assert abs(row["suggested_reduced_value"] - 300.0) < 0.001
    finally:
        conn.execute("DELETE FROM pretrade_check_log WHERE ticker='TST_PT'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_log_pretrade_check_appends_rather_than_upserts():
    """Unlike log_score_event, every call must insert a new row — a verdict can legitimately
    fire many times a day for the same ticker as the user adjusts size on the panel."""
    conn = _conn()
    try:
        db_helpers.log_pretrade_check(
            ticker="TST_PT2", scope="all", proposed_value=500.0, verdict="approve",
            breached_constraint=None, phi_score=10.0, var_pct_of_equity=0.5,
            max_correlation=0.1, suggested_reduced_value=None,
        )
        db_helpers.log_pretrade_check(
            ticker="TST_PT2", scope="all", proposed_value=2000.0, verdict="warn",
            breached_constraint="Correlation", phi_score=55.0, var_pct_of_equity=2.5,
            max_correlation=0.6, suggested_reduced_value=1200.0,
        )
        rows = conn.execute(
            "SELECT * FROM pretrade_check_log WHERE ticker='TST_PT2' ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["verdict"] == "approve"
        assert rows[1]["verdict"] == "warn"
    finally:
        conn.execute("DELETE FROM pretrade_check_log WHERE ticker='TST_PT2'")
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


# ── get_next_earnings_dates ───────────────────────────────────────────────────

@pytest.mark.db
def test_get_next_earnings_dates_returns_company_name_and_date():
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO stock_signals (ticker, company_name, next_earnings_date) "
            "VALUES ('GU_ERN_1', 'Example Corp', '2026-08-01')"
        )
        conn.commit()
        result = db_helpers.get_next_earnings_dates(["GU_ERN_1"])
        assert result == {"GU_ERN_1": {"company_name": "Example Corp", "next_earnings_date": "2026-08-01"}}
    finally:
        conn.execute("DELETE FROM stock_signals WHERE ticker = 'GU_ERN_1'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_get_next_earnings_dates_empty_input_returns_empty_dict():
    assert db_helpers.get_next_earnings_dates([]) == {}


@pytest.mark.db
def test_get_next_earnings_dates_ticker_with_no_row_omitted():
    """A ticker never scanned yet is simply absent from the result — not a placeholder entry."""
    result = db_helpers.get_next_earnings_dates(["GU_ERN_UNKNOWN_TICKER"])
    assert "GU_ERN_UNKNOWN_TICKER" not in result


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
def test_get_ticker_registry_row_by_exchange_returns_lowest_sort_order_index():
    # LSE has both FTSE 100 (^FTSE) and FTSE 250 (^FTMC) — the headline index (lowest
    # sort_order) must win, matching the Markets page's own display convention.
    row = _db.get_ticker_registry_row_by_exchange("LSE")
    assert row is not None
    assert row["ticker"] == "^FTSE"


@pytest.mark.db
def test_get_ticker_registry_row_by_exchange_unknown_returns_none():
    assert _db.get_ticker_registry_row_by_exchange("NOPE_EXCHANGE") is None


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


@pytest.mark.db
def test_get_registry_spot_future_tickers_includes_dual_instrument_pair():
    tickers = _db.get_registry_spot_future_tickers()
    assert "^GSPC" in tickers
    assert "ES=F" in tickers


@pytest.mark.db
def test_get_registry_spot_future_tickers_skips_missing_future_ticker():
    with patch.object(db_helpers, "get_ticker_registry", return_value=[
        {"ticker": "^FTSE", "future_ticker": None},
    ]):
        tickers = db_helpers.get_registry_spot_future_tickers()
    assert tickers == ["^FTSE"]


# ── resolve_live_price ──────────────────────────────────────────────────────

def test_resolve_live_price_prefers_live_when_within_gap():
    price, used_fallback = db_helpers.resolve_live_price(297.11, 1000.0, 219.05, "1970-01-01 00:16:40")  # +1000s
    assert price == 297.11
    assert used_fallback is False


def test_resolve_live_price_falls_back_when_live_stuck_beyond_gap():
    # fallback ~7 days ahead of the live cache row — mirrors the IBM prod incident
    # (market_pulse_cache stuck on a week-old price after stock_signals refreshed).
    live_epoch = 1000.0
    fallback_epoch_str = "1970-01-08 00:16:40"  # +7 days
    price, used_fallback = db_helpers.resolve_live_price(297.11, live_epoch, 219.05, fallback_epoch_str)
    assert price == 219.05
    assert used_fallback is True


def test_resolve_live_price_missing_live_returns_fallback():
    assert db_helpers.resolve_live_price(None, None, 219.05, "1970-01-01 00:16:40") == (219.05, True)
    assert db_helpers.resolve_live_price(297.11, 0, 219.05, "1970-01-01 00:16:40") == (219.05, True)


def test_resolve_live_price_missing_fallback_timestamp_keeps_live():
    assert db_helpers.resolve_live_price(297.11, 1000.0, None, None) == (297.11, False)


def test_resolve_live_price_identical_values_still_flags_fallback_used():
    """Regression guard: the fallback flag must come from the function's own decision, not be
    inferred by comparing the returned price back against the live price — a coincidental exact
    match between the two sources must not be misread as "the live price was kept"."""
    price, used_fallback = db_helpers.resolve_live_price(219.05, 1000.0, 219.05, "1970-01-08 00:16:40")
    assert price == 219.05
    assert used_fallback is True


def test_parse_utc_epoch_roundtrip():
    assert db_helpers.parse_utc_epoch("1970-01-01 00:16:40") == 1000.0
    assert db_helpers.parse_utc_epoch(None) == 0.0
    assert db_helpers.parse_utc_epoch("not-a-date") == 0.0


# ── get_connection() lock retry ─────────────────────────────────────────────

@pytest.mark.db
def test_get_connection_retries_past_transient_lock():
    """A writer that holds the lock past get_connection()'s own busy_timeout must still let a
    second connection's write succeed once the first releases, instead of raising immediately."""
    import sqlite3
    import threading
    import time

    blocker = sqlite3.connect(str(_db.DB_PATH), timeout=0.1, check_same_thread=False)
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("INSERT INTO score_history (ticker, date, score, signal) VALUES ('TST_LOCK', '2099-01-02', 1, 'BUY')")

    def _release():
        time.sleep(0.5)
        blocker.commit()
        blocker.close()

    th = threading.Thread(target=_release)
    th.start()
    try:
        conn = sqlite3.connect(str(_db.DB_PATH), timeout=0.1, factory=_db._RetryingConnection)
        conn.execute("INSERT INTO score_history (ticker, date, score, signal) VALUES ('TST_LOCK2', '2099-01-02', 2, 'BUY')")
        conn.commit()
        row = conn.execute("SELECT score FROM score_history WHERE ticker='TST_LOCK2'").fetchone()
        assert row[0] == 2
        conn.execute("DELETE FROM score_history WHERE ticker IN ('TST_LOCK', 'TST_LOCK2')")
        conn.commit()
        conn.close()
    finally:
        th.join()
