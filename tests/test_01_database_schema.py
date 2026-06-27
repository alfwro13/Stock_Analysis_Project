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

import sys
import sqlite3
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# conftest.py redirects database.DB_PATH to the temp test file before this runs.
# We use database.get_connection() so tests share the same redirected path.
import database as _db


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
