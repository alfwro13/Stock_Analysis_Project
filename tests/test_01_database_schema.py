"""
tests/test_01_database_schema.py  ── DATABASE SCHEMA & INTEGRITY

Verifies that:
  • Every expected table was created by init_db()
  • Each critical table has the columns the rest of the code depends on
  • Basic read/write round-trips work (INSERT → SELECT)
  • The database is accessible and not corrupted

These tests run against the session-level temp DB created in conftest.py.
No network access required.
"""

import json
import sys
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# conftest.py redirects database.DB_PATH to the temp test file before this runs.
# We use database.get_connection() so tests share the same redirected path.
import database as _db
import db_schema
import time_engine


# ── helpers ───────────────────────────────────────────────────────────────────

def _conn():
    """Return a connection to the test DB (path is set by conftest.py)."""
    conn = sqlite3.connect(_db.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _tables() -> set:
    conn = _conn()
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {r["name"] for r in rows}
    finally:
        conn.close()


def _columns(table: str) -> set:
    conn = _conn()
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r["name"] for r in rows}
    finally:
        conn.close()


# ── Table existence ───────────────────────────────────────────────────────────

EXPECTED_TABLES = [
    "stock_signals",
    "system_notifications",
    "alert_state",
    "market_pulse_cache",
    "quant_signals",
    "quant_scan_states",
    "earnings_volatility",
    "earnings_volatility_history",
    "market_universe",
    "asset_profiles",
    "ticker_metadata",
    "company_name_overrides",
    "market_regimes",
    "intraday_monitors",
    "intraday_monitor_results",
    "macro_regimes",
    "scheduler_run_log",
    "news_articles",
    "model_training_log",
    "trap_monitor_results",
    "price_hmm_states",
    "etf_predictor_configs",
    "etf_predictor_predictions",
    "watchlist_items",
    "backup_history",
    "market_ticker_registry",
    "market_pulse_sparkline",
    "learn_cards",
    "learn_term_state",
    "ticker_notes",
    "pattern_detection_results",
    "pattern_detection_history",
]


@pytest.mark.db
@pytest.mark.parametrize("table", EXPECTED_TABLES)
def test_table_exists(table):
    """Every table created by init_db() must exist in the database."""
    assert table in _tables(), f"Table '{table}' is missing from the database"


# ── Column presence on critical tables ───────────────────────────────────────

@pytest.mark.db
def test_stock_signals_has_required_columns():
    """stock_signals must have ticker, composite_score, overall_signal, ml_confidence."""
    cols = _columns("stock_signals")
    required = {"ticker", "composite_score", "overall_signal", "ml_confidence", "rsi_14", "current_price"}
    missing = required - cols
    assert not missing, f"stock_signals missing columns: {missing}"


@pytest.mark.db
def test_quant_signals_has_required_columns():
    """quant_signals must have the full technical + ML + risk column set."""
    cols = _columns("quant_signals")
    required = {
        "ticker", "date", "rsi_14", "macd", "sma_50", "sma_200",
        "ml_confidence_score", "var_95", "cvar_95", "composite_score",
    }
    missing = required - cols
    assert not missing, f"quant_signals missing columns: {missing}"


@pytest.mark.db
def test_system_notifications_has_required_columns():
    """Notification table must have id, message_type, message_text, is_read, status."""
    cols = _columns("system_notifications")
    required = {"id", "message_type", "message_text", "is_read", "status", "timestamp"}
    missing = required - cols
    assert not missing, f"system_notifications missing columns: {missing}"


@pytest.mark.db
def test_ticker_notes_has_required_columns():
    """ticker_notes must have id, ticker, note_text, created_at, updated_at."""
    cols = _columns("ticker_notes")
    required = {"id", "ticker", "note_text", "created_at", "updated_at"}
    missing = required - cols
    assert not missing, f"ticker_notes missing columns: {missing}"


@pytest.mark.db
def test_scheduler_run_log_has_duration_columns():
    """scheduler_run_log must carry the Workflow Monitor duration/status columns."""
    cols = _columns("scheduler_run_log")
    required = {"job_id", "last_run", "last_started", "last_duration_sec", "avg_duration_sec", "last_status"}
    missing = required - cols
    assert not missing, f"scheduler_run_log missing columns: {missing}"


@pytest.mark.db
def test_alert_state_has_required_columns():
    """alert_state dedup ledger must have all columns the gate logic reads/writes."""
    cols = _columns("alert_state")
    required = {"engine", "ticker", "fingerprint", "last_price", "last_fired_utc",
                "armed", "fire_count", "state_date"}
    missing = required - cols
    assert not missing, f"alert_state missing columns: {missing}"


@pytest.mark.db
def test_alert_state_primary_key_is_engine_ticker():
    """INSERT OR REPLACE on (engine, ticker) must behave as an upsert, not a duplicate."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO alert_state "
            "(engine, ticker, fingerprint, last_price, last_fired_utc, armed, fire_count, state_date) "
            "VALUES ('Crash', 'PK_TEST', 'abc', 100.0, '2024-01-01 10:00:00', 0, 1, '2024-01-01')"
        )
        conn.commit()
        conn.execute(
            "INSERT OR REPLACE INTO alert_state "
            "(engine, ticker, fingerprint, last_price, last_fired_utc, armed, fire_count, state_date) "
            "VALUES ('Crash', 'PK_TEST', 'abc', 95.0, '2024-01-01 12:00:00', 0, 2, '2024-01-01')"
        )
        conn.commit()
        rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM alert_state WHERE engine='Crash' AND ticker='PK_TEST'"
        ).fetchone()
        assert rows["cnt"] == 1, "Upsert created a duplicate row instead of replacing"
        row = conn.execute(
            "SELECT last_price, fire_count FROM alert_state WHERE engine='Crash' AND ticker='PK_TEST'"
        ).fetchone()
        assert row["last_price"] == 95.0
        assert row["fire_count"] == 2
    finally:
        conn.execute("DELETE FROM alert_state WHERE ticker='PK_TEST'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_market_universe_has_required_columns():
    """market_universe must have ticker, sector, exchange, is_freetrade, is_index."""
    cols = _columns("market_universe")
    required = {"ticker", "company_name", "sector", "exchange", "is_freetrade", "is_index"}
    missing = required - cols
    assert not missing, f"market_universe missing columns: {missing}"


@pytest.mark.db
def test_asset_profiles_has_required_columns():
    """asset_profiles must have ticker, sector, currency, quote_type."""
    cols = _columns("asset_profiles")
    required = {"ticker", "sector", "currency", "quote_type", "country", "exchange"}
    missing = required - cols
    assert not missing, f"asset_profiles missing columns: {missing}"


@pytest.mark.db
def test_market_regimes_has_required_columns():
    """market_regimes must record VIX, volatility and regime label for US and UK."""
    cols = _columns("market_regimes")
    required = {"date", "vix_close", "spy_volatility", "us_regime_label", "uk_regime_label"}
    missing = required - cols
    assert not missing, f"market_regimes missing columns: {missing}"


@pytest.mark.db
def test_macro_regimes_has_required_columns():
    """macro_regimes must record yield curve, DXY, threat levels, and regime classifier outputs."""
    cols = _columns("macro_regimes")
    required = {
        "date", "tyx_close", "tnx_close", "dxy_close",
        "us_threat_level", "uk_threat_level",
        "yield_curve_inverted", "days_inverted", "regime_label",
    }
    missing = required - cols
    assert not missing, f"macro_regimes missing columns: {missing}"


@pytest.mark.db
def test_macro_indicators_has_new_rate_columns():
    """macro_indicators must include Fed funds rate, TIPS real yield, and UK base rate."""
    cols = _columns("macro_indicators")
    required = {"us_fed_funds_rate", "us_real_yield_10y", "uk_base_rate"}
    missing = required - cols
    assert not missing, f"macro_indicators missing columns: {missing}"


@pytest.mark.db
def test_scheduler_run_log_has_required_columns():
    """scheduler_run_log must have job_id (PK) and last_run columns."""
    cols = _columns("scheduler_run_log")
    required = {"job_id", "last_run"}
    missing = required - cols
    assert not missing, f"scheduler_run_log missing columns: {missing}"


@pytest.mark.db
def test_news_articles_has_required_columns():
    """news_articles must have all core columns including sentiment fields."""
    cols = _columns("news_articles")
    required = {
        "id", "article_id", "ticker", "company_name", "source_list",
        "headline", "summary", "full_text", "body_fetched",
        "url", "publisher", "published_at", "is_premium", "fetched_at",
        "sentiment_score", "sentiment_label",
    }
    missing = required - cols
    assert not missing, f"news_articles missing columns: {missing}"


@pytest.mark.db
def test_market_ticker_registry_has_required_columns():
    """market_ticker_registry must carry region/asset_type/exchange/future-pairing/pulse-membership fields."""
    cols = _columns("market_ticker_registry")
    required = {
        "ticker", "display_name", "region", "asset_type", "exchange", "currency",
        "future_ticker", "future_display_name", "invert_color",
        "is_pulse_tile", "pulse_sort_order", "is_pulse_mobile", "sort_order", "enabled",
    }
    missing = required - cols
    assert not missing, f"market_ticker_registry missing columns: {missing}"


@pytest.mark.db
def test_market_pulse_sparkline_has_required_columns():
    """market_pulse_sparkline must have a composite (ticker, ts) PK plus price."""
    cols = _columns("market_pulse_sparkline")
    required = {"ticker", "ts", "price"}
    missing = required - cols
    assert not missing, f"market_pulse_sparkline missing columns: {missing}"


@pytest.mark.db
def test_market_ticker_registry_seed_data_present():
    """A fresh init_db() must seed the full ticker registry, preserving today's 10 Market Pulse tickers."""
    conn = _conn()
    try:
        rows = conn.execute("SELECT ticker, region, is_pulse_tile FROM market_ticker_registry").fetchall()
        tickers = {r["ticker"] for r in rows}
        # Today's existing INDEX_TICKERS must all be present and still flagged as pulse tiles,
        # so a fresh install reproduces current Market Pulse behavior with no regression.
        legacy_tickers = {"^FTSE", "^FTMC", "GBPUSD=X", "BZ=F", "UK10YG", "^GSPC", "^NDX", "^TYX", "^TNX", "DX-Y.NYB"}
        missing_legacy = legacy_tickers - tickers
        assert not missing_legacy, f"Seed is missing legacy Market Pulse tickers: {missing_legacy}"
        pulse_tickers = {r["ticker"] for r in rows if r["is_pulse_tile"]}
        assert legacy_tickers == pulse_tickers, (
            f"is_pulse_tile membership drifted from legacy set: {pulse_tickers ^ legacy_tickers}"
        )
        # New Markets-page tickers from the user's seed spec must also be present.
        new_tickers = {"GC=F", "SI=F", "HG=F", "CL=F", "^N225", "^HSI", "000001.SS", "^AXJO",
                        "^STOXX50E", "^GDAXI", "^FCHI", "EURUSD=X", "^DJI", "^RUT", "^VIX"}
        missing_new = new_tickers - tickers
        assert not missing_new, f"Seed is missing new Markets-page tickers: {missing_new}"
    finally:
        conn.close()


@pytest.mark.db
def test_market_ticker_registry_dual_ticker_rows_have_future_pairing():
    """S&P 500, Nasdaq 100, Dow, Russell 2000, and Nikkei 225 must carry a paired future ticker."""
    conn = _conn()
    try:
        expected_pairs = {
            "^GSPC": "ES=F", "^NDX": "NQ=F", "^DJI": "YM=F", "^RUT": "RTY=F", "^N225": "NIY=F",
        }
        for spot, future in expected_pairs.items():
            row = conn.execute(
                "SELECT future_ticker FROM market_ticker_registry WHERE ticker = ?", (spot,)
            ).fetchone()
            assert row is not None, f"{spot} missing from market_ticker_registry"
            assert row["future_ticker"] == future, f"{spot} expected future {future}, got {row['future_ticker']}"
    finally:
        conn.close()


# ── Read / Write round-trips ──────────────────────────────────────────────────

@pytest.mark.db
def test_can_insert_and_read_notification():
    """Basic INSERT + SELECT on system_notifications must round-trip correctly."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)",
            ("test", "regression-test notification"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT message_text FROM system_notifications WHERE message_type = 'test' LIMIT 1"
        ).fetchone()
        assert row is not None, "Inserted notification not found"
        assert row["message_text"] == "regression-test notification"
    finally:
        conn.execute("DELETE FROM system_notifications WHERE message_type = 'test'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_can_insert_and_read_market_universe():
    """Basic INSERT + SELECT on market_universe must round-trip correctly."""
    conn = _conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO market_universe
               (ticker, company_name, sector, exchange, last_updated)
               VALUES (?, ?, ?, ?, ?)""",
            ("TEST.L", "Test Corp", "Technology", "LSE", "2024-01-01"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT company_name FROM market_universe WHERE ticker = 'TEST.L'"
        ).fetchone()
        assert row is not None, "Inserted universe row not found"
        assert row["company_name"] == "Test Corp"
    finally:
        conn.execute("DELETE FROM market_universe WHERE ticker = 'TEST.L'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_quant_scan_states_composite_pk():
    """quant_scan_states uses a composite primary key (scan_date, scan_type)."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO quant_scan_states (scan_date, scan_type, status) VALUES (?, ?, ?)",
            ("2024-01-01", "daily", "complete"),
        )
        conn.commit()
        # Duplicate insert must REPLACE, not error
        conn.execute(
            "INSERT OR REPLACE INTO quant_scan_states (scan_date, scan_type, status) VALUES (?, ?, ?)",
            ("2024-01-01", "daily", "updated"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT status FROM quant_scan_states WHERE scan_date='2024-01-01' AND scan_type='daily'"
        ).fetchone()
        assert row["status"] == "updated", "Composite PK upsert did not update the existing row"
    finally:
        conn.execute("DELETE FROM quant_scan_states WHERE scan_date='2024-01-01'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_scheduler_run_log_upsert():
    """INSERT … ON CONFLICT(job_id) must upsert, not duplicate."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO scheduler_run_log (job_id, last_run) VALUES (?, ?)"
            " ON CONFLICT(job_id) DO UPDATE SET last_run = excluded.last_run",
            ("test_job", "2024-01-01 01:00"),
        )
        conn.commit()
        conn.execute(
            "INSERT INTO scheduler_run_log (job_id, last_run) VALUES (?, ?)"
            " ON CONFLICT(job_id) DO UPDATE SET last_run = excluded.last_run",
            ("test_job", "2024-01-02 02:00"),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM scheduler_run_log WHERE job_id = 'test_job'"
        ).fetchone()
        assert rows["cnt"] == 1, "Upsert created a duplicate row"
        row = conn.execute(
            "SELECT last_run FROM scheduler_run_log WHERE job_id = 'test_job'"
        ).fetchone()
        assert row["last_run"] == "2024-01-02 02:00", "last_run was not updated by upsert"
    finally:
        conn.execute("DELETE FROM scheduler_run_log WHERE job_id = 'test_job'")
        conn.commit()
        conn.close()


# ── Watchlist migration (db_schema._import_legacy_watchlist_json) ────────────

@pytest.mark.db
def test_import_legacy_watchlist_json_enriches_from_stock_signals(tmp_path):
    """A ticker already cached in stock_signals gets its metadata carried into watchlist_items."""
    import json
    import database as db_module
    import db_schema

    temp_db_path = tmp_path / "migration_test.db"
    watchlist_path = tmp_path / "watchlist.json"
    watchlist_path.write_text(json.dumps({"watchlist": ["AAPL", "MSFT"]}))

    original_db_path = db_module.DB_PATH
    db_module.DB_PATH = temp_db_path
    try:
        conn = db_module.get_connection()
        conn.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, account_type TEXT, deleted_at TEXT)")
        conn.execute("INSERT INTO accounts (id, account_type, deleted_at) VALUES (1, 'Watchlist', NULL)")
        conn.execute(
            "CREATE TABLE watchlist_items (id INTEGER PRIMARY KEY, account_id INTEGER, ticker TEXT, "
            "company_name TEXT, currency TEXT, quote_type TEXT, exchange TEXT, UNIQUE(account_id, ticker))"
        )
        conn.execute("CREATE TABLE stock_signals (ticker TEXT PRIMARY KEY, company_name TEXT, currency TEXT, quote_type TEXT)")
        conn.execute(
            "INSERT INTO stock_signals (ticker, company_name, currency, quote_type) VALUES (?, ?, ?, ?)",
            ("AAPL", "Apple Inc.", "USD", "EQUITY")
        )
        conn.commit()
        conn.close()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(db_schema, "WATCHLIST_PATH", watchlist_path)
            db_schema._import_legacy_watchlist_json()

        conn = db_module.get_connection()
        rows = {r["ticker"]: dict(r) for r in conn.execute("SELECT * FROM watchlist_items").fetchall()}
        conn.close()
    finally:
        db_module.DB_PATH = original_db_path

    assert set(rows) == {"AAPL", "MSFT"}
    assert rows["AAPL"]["company_name"] == "Apple Inc."
    assert rows["AAPL"]["exchange"] == "NYSE"
    assert rows["MSFT"]["company_name"] is None


@pytest.mark.db
def test_import_legacy_watchlist_json_is_idempotent(tmp_path):
    """Running the import twice must not duplicate rows or error on a non-empty table."""
    import json
    import database as db_module
    import db_schema

    temp_db_path = tmp_path / "migration_test_2.db"
    watchlist_path = tmp_path / "watchlist.json"
    watchlist_path.write_text(json.dumps({"watchlist": ["TSLA"]}))

    original_db_path = db_module.DB_PATH
    db_module.DB_PATH = temp_db_path
    try:
        conn = db_module.get_connection()
        conn.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, account_type TEXT, deleted_at TEXT)")
        conn.execute("INSERT INTO accounts (id, account_type, deleted_at) VALUES (1, 'Watchlist', NULL)")
        conn.execute(
            "CREATE TABLE watchlist_items (id INTEGER PRIMARY KEY, account_id INTEGER, ticker TEXT, "
            "company_name TEXT, currency TEXT, quote_type TEXT, exchange TEXT, UNIQUE(account_id, ticker))"
        )
        conn.execute("CREATE TABLE stock_signals (ticker TEXT PRIMARY KEY, company_name TEXT, currency TEXT, quote_type TEXT)")
        conn.commit()
        conn.close()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(db_schema, "WATCHLIST_PATH", watchlist_path)
            db_schema._import_legacy_watchlist_json()
            db_schema._import_legacy_watchlist_json()

        conn = db_module.get_connection()
        count = conn.execute("SELECT COUNT(*) AS cnt FROM watchlist_items").fetchone()["cnt"]
        conn.close()
    finally:
        db_module.DB_PATH = original_db_path

    assert count == 1


@pytest.mark.db
def test_database_file_is_accessible():
    """The database file must be readable and have a positive size."""
    db_path = _db.DB_PATH
    assert db_path.exists(), f"Database file does not exist at {db_path}"
    assert db_path.stat().st_size > 0, "Database file is empty"


# ── Intraday Dip Radar tables ─────────────────────────────────────────────────

@pytest.mark.db
def test_intraday_monitors_has_required_columns():
    """intraday_monitors must have ticker (PK), date_added, is_active, activated_by."""
    cols = _columns("intraday_monitors")
    required = {"ticker", "date_added", "is_active", "activated_by"}
    missing = required - cols
    assert not missing, f"intraday_monitors missing columns: {missing}"


@pytest.mark.db
def test_intraday_monitor_results_has_required_columns():
    """intraday_monitor_results must store the full scoring payload."""
    cols = _columns("intraday_monitor_results")
    required = {"ticker", "scan_ts", "current_price", "reversal_score",
                "is_bottoming", "reasons_json", "rsi", "vwap", "vwap_deviation"}
    missing = required - cols
    assert not missing, f"intraday_monitor_results missing columns: {missing}"


@pytest.mark.db
def test_intraday_monitors_upsert_on_ticker_pk():
    """INSERT OR REPLACE on intraday_monitors must update the existing row, not duplicate it."""
    conn = _conn()
    today = "2025-01-15"
    try:
        conn.execute(
            "INSERT OR REPLACE INTO intraday_monitors (ticker, date_added, is_active, activated_by)"
            " VALUES ('TEST_RADAR', ?, 1, 'user')", (today,)
        )
        conn.commit()
        conn.execute(
            "INSERT OR REPLACE INTO intraday_monitors (ticker, date_added, is_active, activated_by)"
            " VALUES ('TEST_RADAR', ?, 0, 'user')", (today,)
        )
        conn.commit()
        rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM intraday_monitors WHERE ticker = 'TEST_RADAR'"
        ).fetchone()
        assert rows["cnt"] == 1, "Upsert created a duplicate row"
        row = conn.execute(
            "SELECT is_active FROM intraday_monitors WHERE ticker = 'TEST_RADAR'"
        ).fetchone()
        assert row["is_active"] == 0, "is_active was not updated by upsert"
    finally:
        conn.execute("DELETE FROM intraday_monitors WHERE ticker = 'TEST_RADAR'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_intraday_monitor_results_roundtrip():
    """INSERT + SELECT on intraday_monitor_results must preserve all scoring fields."""
    import json
    conn = _conn()
    reasons = ["Extreme Oversold (RSI: 22.1)", "Volume Capitulation detected"]
    try:
        conn.execute(
            """INSERT OR REPLACE INTO intraday_monitor_results
               (ticker, scan_ts, current_price, reversal_score, is_bottoming,
                reasons_json, rsi, vwap, vwap_deviation)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("TEST_RADAR", "2025-01-15 10:32", 185.42, 80, 1,
             json.dumps(reasons), 22.1, 188.75, -3.33),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM intraday_monitor_results WHERE ticker = 'TEST_RADAR'"
        ).fetchone()
        assert row is not None, "Inserted result row not found"
        assert row["reversal_score"] == 80
        assert row["is_bottoming"] == 1
        assert json.loads(row["reasons_json"]) == reasons
    finally:
        conn.execute("DELETE FROM intraday_monitor_results WHERE ticker = 'TEST_RADAR'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_news_articles_roundtrip():
    """INSERT a news article with sentiment, SELECT it back, verify all fields."""
    conn = _db.get_connection()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO news_articles
               (article_id, ticker, company_name, source_list, headline, summary,
                body_fetched, url, publisher, published_at, fetched_at,
                sentiment_score, sentiment_label)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("test-uuid-sentiment", "TSLA", "Tesla Inc.", "portfolio",
             "Test headline for sentiment", "Short summary text.",
             0, "https://example.com/article", "Test Publisher",
             1700000000, 1700001000, 0.82, "positive"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM news_articles WHERE article_id = 'test-uuid-sentiment'"
        ).fetchone()
        assert row is not None, "Inserted news article not found"
        assert row["ticker"] == "TSLA"
        assert row["source_list"] == "portfolio"
        assert row["sentiment_label"] == "positive"
        assert abs(row["sentiment_score"] - 0.82) < 0.001
    finally:
        conn.execute("DELETE FROM news_articles WHERE article_id = 'test-uuid-sentiment'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_news_articles_deduplication_via_unique_constraint():
    """INSERT OR IGNORE on duplicate article_id must not raise and must not duplicate."""
    conn = _db.get_connection()
    try:
        for _ in range(3):
            conn.execute(
                """INSERT OR IGNORE INTO news_articles
                   (article_id, ticker, source_list, headline, published_at, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("test-dedup-id", "AAPL", "watchlist", "Dedup headline", 1700000000, 1700001000),
            )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM news_articles WHERE article_id = 'test-dedup-id'"
        ).fetchone()[0]
        assert count == 1, f"Expected 1 row after 3 identical inserts, got {count}"
    finally:
        conn.execute("DELETE FROM news_articles WHERE article_id = 'test-dedup-id'")
        conn.commit()
        conn.close()




@pytest.mark.db
def test_trap_monitor_results_has_required_columns():
    """trap_monitor_results must expose all columns the API and engine read."""
    cols = _columns("trap_monitor_results")
    required = {
        "ticker", "phase",
        "bull_trap_level", "bull_trap_vol_ratio", "bull_trap_notes",
        "bear_trap_level", "bear_trap_notes",
        "cap_level", "cap_vol_zscore", "cap_notes",
        "wyckoff_level", "wyckoff_bb_width", "wyckoff_notes",
        "ema_distance", "rsi", "scan_ts",
    }
    missing = required - cols
    assert not missing, f"trap_monitor_results missing columns: {missing}"


@pytest.mark.db
def test_trap_monitor_results_primary_key_is_ticker():
    """ON CONFLICT(ticker) upsert must replace, not duplicate."""
    conn = _db.get_connection()
    try:
        for phase in ("NEUTRAL", "BULL_TRAP_RISK"):
            conn.execute(
                "INSERT INTO trap_monitor_results (ticker, phase, bull_trap_level, bear_trap_level, "
                "cap_level, wyckoff_level, scan_ts) VALUES (?, ?, 'SAFE', 'SAFE', 'NONE', 'NONE', '2026-01-01 00:00:00') "
                "ON CONFLICT(ticker) DO UPDATE SET phase=excluded.phase",
                ("PK_TEST_TRAP", phase),
            )
            conn.commit()
        rows = conn.execute(
            "SELECT COUNT(*) AS cnt, phase FROM trap_monitor_results WHERE ticker = 'PK_TEST_TRAP'"
        ).fetchone()
        assert rows["cnt"] == 1, "Upsert created a duplicate row"
        assert rows["phase"] == "BULL_TRAP_RISK", "Phase was not updated by upsert"
    finally:
        conn.execute("DELETE FROM trap_monitor_results WHERE ticker = 'PK_TEST_TRAP'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_seed_exchange_hours_json_backfills_new_fields_without_overwriting_existing(tmp_path):
    """A pre-existing exchange_hours.json (seeded before quote_delay_minutes was added to
    _DEFAULT_EXCHANGE_HOURS) must gain the new field on the next init_db() run, without
    touching any field/exchange the operator already had on disk."""
    path = tmp_path / "exchange_hours.json"
    stale = {
        "NYSE": {"open": "09:30", "close": "16:00", "tz": "America/New_York", "currency": "USD", "suffixes": []},
        "LSE": {"open": "07:45", "close": "16:30", "tz": "Europe/London", "currency": "GBP", "suffixes": [".L"]},
    }
    path.write_text(json.dumps(stale))

    with patch("db_schema._EXCHANGE_HOURS_PATH", str(path)):
        db_schema._seed_exchange_hours_json()

    result = json.loads(path.read_text())
    assert result["LSE"]["quote_delay_minutes"] == 15
    assert result["LSE"]["open"] == "07:45", "operator-edited field must survive the backfill"
    assert "XETRA" in result, "an exchange added to defaults after the file was first seeded must be backfilled"


@pytest.mark.db
def test_seed_exchange_hours_json_refreshes_time_engine_cache_on_fresh_install(tmp_path):
    """time_engine caches its exchange registry at import time; on a fresh install (no
    exchange_hours.json yet) that import can happen before init_db() ever runs, pinning
    time_engine to its incomplete built-in fallback (NYSE/LSE/XETRA/TSE only) for the rest of
    the process unless _seed_exchange_hours_json() explicitly tells it to reload."""
    path = tmp_path / "exchange_hours.json"
    assert not path.exists()

    with patch("time_engine._EXCHANGE_HOURS_PATH", str(path)):
        time_engine.reload_exchange_registry()
        assert "KRX" not in time_engine.EXCHANGE_HOURS, "precondition: simulating the pre-seed fallback state"

        with patch("db_schema._EXCHANGE_HOURS_PATH", str(path)):
            db_schema._seed_exchange_hours_json()

        assert "KRX" in time_engine.EXCHANGE_HOURS
        assert time_engine.ticker_exchange_from_suffix("005930.KS") == "KRX"

    time_engine.reload_exchange_registry()


@pytest.mark.db
def test_learn_cards_has_required_columns():
    cols = _columns("learn_cards")
    required = {"term_key", "section_id", "level_order", "term_title", "question", "answer", "distractors"}
    assert not (required - cols), f"learn_cards missing columns: {required - cols}"


@pytest.mark.db
def test_learn_term_state_has_required_columns():
    cols = _columns("learn_term_state")
    required = {"term_key", "box", "due_at", "correct_streak", "lapses", "total_reviews", "last_result"}
    assert not (required - cols), f"learn_term_state missing columns: {required - cols}"


@pytest.mark.db
def test_seed_learn_cards_is_populated_and_idempotent():
    import learn_cards_seed
    conn = _conn()
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM learn_cards").fetchone()["n"]
        assert count == len(learn_cards_seed.CARDS)

        conn.execute(
            "INSERT INTO learn_term_state (term_key, box, total_reviews) VALUES (?, ?, ?)",
            (learn_cards_seed.CARDS[0]["term_key"], 3, 5)
        )
        conn.commit()

        cursor = conn.cursor()
        db_schema._seed_learn_cards(cursor)
        conn.commit()

        count_after = conn.execute("SELECT COUNT(*) AS n FROM learn_cards").fetchone()["n"]
        assert count_after == len(learn_cards_seed.CARDS), "re-seeding must not duplicate rows"

        state = conn.execute(
            "SELECT box FROM learn_term_state WHERE term_key = ?",
            (learn_cards_seed.CARDS[0]["term_key"],)
        ).fetchone()
        assert state["box"] == 3, "re-seeding cards must not wipe existing progress state"
    finally:
        conn.execute("DELETE FROM learn_term_state WHERE term_key = ?", (learn_cards_seed.CARDS[0]["term_key"],))
        conn.commit()


@pytest.mark.db
def test_migrate_db_copies_head_shoulders_results_into_pattern_detection_results():
    conn = _conn()
    try:
        conn.execute("DELETE FROM head_shoulders_results WHERE ticker = 'MIGTST1'")
        conn.execute("DELETE FROM pattern_detection_results WHERE ticker = 'MIGTST1'")
        conn.execute(
            """INSERT INTO head_shoulders_results
               (ticker, pattern_type, phase, l_shoulder_date, l_shoulder_price, l_armpit_date, l_armpit_price,
                head_date, head_price, r_armpit_date, r_armpit_price, r_shoulder_date, r_shoulder_price,
                neck_slope, breakout_date, breakout_price, measured_target, volume_confirms, rsi_divergence,
                pattern_r2, prior_trend_pct, scan_ts)
               VALUES ('MIGTST1', 'regular', 'CONFIRMED', '2026-01-01', 110.0, '2026-01-10', 95.0,
                       '2026-01-20', 120.0, '2026-01-30', 96.0, '2026-02-05', 108.0,
                       0.04, '2026-02-10', 90.0, 74.0, 1, 1, 0.85, 12.0, '2026-02-10 22:20:00')""",
        )
        conn.commit()

        cursor = conn.cursor()
        db_schema.migrate_db(conn, cursor)
        conn.commit()

        row = conn.execute(
            "SELECT * FROM pattern_detection_results WHERE ticker = 'MIGTST1' AND pattern_family = 'head_shoulders'"
        ).fetchone()
        assert row is not None
        assert row["phase"] == "CONFIRMED"
        points = json.loads(row["points_json"])
        assert len(points) == 5
        assert points[2]["label"] == "Head"
        assert points[2]["price"] == 120.0

        # Re-running migrate_db must not duplicate the row.
        db_schema.migrate_db(conn, cursor)
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM pattern_detection_results WHERE ticker = 'MIGTST1' AND pattern_family = 'head_shoulders'"
        ).fetchone()["n"]
        assert count == 1
    finally:
        conn.execute("DELETE FROM head_shoulders_results WHERE ticker = 'MIGTST1'")
        conn.execute("DELETE FROM pattern_detection_results WHERE ticker = 'MIGTST1'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_account_transactions_has_account_date_index():
    """get_transactions() filters on account_id and orders by txn_date — must be indexed, not a table scan."""
    conn = _conn()
    try:
        rows = conn.execute("PRAGMA index_list(account_transactions)").fetchall()
        index_names = {r["name"] for r in rows}
        assert "idx_account_transactions_account_date" in index_names

        cursor = conn.cursor()
        db_schema.migrate_db(conn, cursor)
        conn.commit()
        rows = conn.execute("PRAGMA index_list(account_transactions)").fetchall()
        assert sum(1 for r in rows if r["name"] == "idx_account_transactions_account_date") == 1
    finally:
        conn.close()
