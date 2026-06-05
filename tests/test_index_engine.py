"""
tests/test_index_engine.py  ── INDEX ENGINE

Covers fetch_index_constituents(), upsert_index_assets(), and sync_all_indices().

Contract tests guard against Wikipedia HTML structure changes that would silently
produce zero constituents or wrong column mappings.
"""

import sys
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import numpy as np
import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import index_engine
from index_engine import fetch_index_constituents, upsert_index_assets, sync_all_indices, INDEX_REGISTRY


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _html_table(headers: list, rows: list) -> str:
    """Build a minimal HTML page containing one table."""
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = ""
    for row in rows:
        tds = "".join(f"<td>{v}</td>" for v in row)
        body += f"<tr>{tds}</tr>"
    return f"<html><body><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></body></html>"


def _mock_resp(status: int, text: str) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.text = text
    m.raise_for_status = MagicMock()
    if status >= 400:
        m.raise_for_status.side_effect = requests.exceptions.HTTPError(f"HTTP {status}")
    return m


SP500_HTML = _html_table(
    ["Symbol", "Security", "GICS Sector", "Other"],
    [
        ["AAPL", "Apple Inc.", "Information Technology", "x"],
        ["BRK.B", "Berkshire Hathaway B", "Financials", "x"],
        ["GOOGL", "Alphabet Inc.", "Communication Services", "x"],
    ]
)

FTSE100_HTML = _html_table(
    ["Ticker", "Company", "FTSE industry classification benchmark sector", "Other"],
    [
        ["SHEL", "Shell PLC", "Energy", "x"],
        ["AZN", "AstraZeneca", "Health Care", "x"],
    ]
)


# ──────────────────────────────────────────────────────────────────────────────
# 1. fetch_index_constituents()
# ──────────────────────────────────────────────────────────────────────────────

class TestFetchIndexConstituents:

    def test_unknown_index_key_returns_empty(self):
        result = fetch_index_constituents("DAX40")
        assert result == []

    def test_sp500_happy_path(self):
        """Valid SP500 HTML → correct ticker/company/sector + dot→hyphen formatting."""
        with patch("requests.get", return_value=_mock_resp(200, SP500_HTML)):
            records = fetch_index_constituents("SP500")
        assert len(records) == 3
        tickers = {r["ticker"] for r in records}
        assert "AAPL" in tickers
        assert "BRK-B" in tickers, "BRK.B must be formatted to BRK-B for Yahoo Finance"
        assert all(r["index_membership"] == "SP500" for r in records)

    def test_ftse100_happy_path(self):
        """Valid FTSE100 HTML → tickers get .L suffix appended."""
        with patch("requests.get", return_value=_mock_resp(200, FTSE100_HTML)):
            records = fetch_index_constituents("FTSE100")
        tickers = {r["ticker"] for r in records}
        assert "SHEL.L" in tickers
        assert "AZN.L" in tickers

    def test_contract_sp500_endpoint(self):
        """CONTRACT: SP500 must scrape from en.wikipedia.org/wiki/List_of_S%26P_500_companies."""
        called_urls = []
        with patch("requests.get", side_effect=lambda url, **kw: called_urls.append(url) or _mock_resp(200, SP500_HTML)):
            fetch_index_constituents("SP500")
        assert any("S%26P_500" in u or "S&P_500" in u for u in called_urls), (
            "CONTRACT VIOLATION: SP500 scrape URL changed."
        )

    def test_contract_ftse100_endpoint(self):
        """CONTRACT: FTSE100 must scrape from en.wikipedia.org/wiki/FTSE_100_Index."""
        called_urls = []
        with patch("requests.get", side_effect=lambda url, **kw: called_urls.append(url) or _mock_resp(200, FTSE100_HTML)):
            fetch_index_constituents("FTSE100")
        assert any("FTSE_100" in u for u in called_urls), (
            "CONTRACT VIOLATION: FTSE100 scrape URL changed."
        )

    def test_missing_required_column_returns_empty(self):
        """If Wikipedia renames a column, scraper must return [] not crash."""
        bad_html = _html_table(
            ["WrongTicker", "Security", "GICS Sector"],
            [["AAPL", "Apple", "Tech"]],
        )
        with patch("requests.get", return_value=_mock_resp(200, bad_html)):
            result = fetch_index_constituents("SP500")
        assert result == [], (
            "CONTRACT VIOLATION: Wikipedia renamed a column — "
            "fetch_index_constituents must return [] not crash."
        )

    def test_nan_ticker_cell_skipped(self):
        """
        REGRESSION: A NaN cell in the ticker column must be filtered out.
        Before fix, formatter(NaN) returned 'nan' (truthy) and got inserted.
        """
        nan_html = _html_table(
            ["Symbol", "Security", "GICS Sector"],
            [
                [float("nan"), "Bad Row", "Tech"],
                ["MSFT", "Microsoft", "Technology"],
            ]
        )
        with patch("requests.get", return_value=_mock_resp(200, nan_html)):
            records = fetch_index_constituents("SP500")
        tickers = [r["ticker"] for r in records]
        assert "nan" not in tickers, "NaN ticker cell must be filtered before formatting"
        assert "MSFT" in tickers

    def test_network_error_returns_empty(self):
        """RequestException → returns [] without raising."""
        with patch("requests.get", side_effect=requests.exceptions.ConnectionError("refused")):
            result = fetch_index_constituents("SP500")
        assert result == []

    def test_http_error_returns_empty(self):
        """HTTP 503 → returns [] without raising."""
        with patch("requests.get", return_value=_mock_resp(503, "")):
            result = fetch_index_constituents("SP500")
        assert result == []

    def test_no_matching_table_returns_empty(self):
        """HTML with no table matching the config match_text → returns []."""
        with patch("requests.get", return_value=_mock_resp(200, "<html><body>no table here</body></html>")):
            result = fetch_index_constituents("SP500")
        assert result == []


# ──────────────────────────────────────────────────────────────────────────────
# 2. upsert_index_assets()
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path):
    """Temp-file SQLite with the market_universe schema.
    Using a file (not :memory:) so upsert_index_assets can close its own
    connection and the test can open a fresh one to verify the results."""
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE market_universe (
            ticker        TEXT PRIMARY KEY,
            company_name  TEXT,
            sector        TEXT,
            is_index      INTEGER DEFAULT 0,
            is_freetrade  INTEGER DEFAULT 0,
            index_membership TEXT,
            last_updated  TEXT
        );
    """)
    conn.commit()
    conn.close()
    return path


def _read(db_path, sql, *params):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return row


def _get_conn(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


class TestUpsertIndexAssets:

    def test_empty_records_returns_false(self):
        assert upsert_index_assets([]) is False

    def test_inserts_new_record(self, db_path):
        records = [{"ticker": "AAPL", "company_name": "Apple", "sector": "Tech", "index_membership": "SP500"}]
        with patch("index_engine.get_connection", side_effect=lambda: _get_conn(db_path)):
            result = upsert_index_assets(records)
        assert result is True
        row = _read(db_path, "SELECT * FROM market_universe WHERE ticker='AAPL'")
        assert row is not None
        assert row["is_index"] == 1
        assert row["index_membership"] == "SP500"

    def test_freetrade_flag_not_overwritten(self, db_path):
        """Freetrade Firewall: is_freetrade must survive an index upsert."""
        setup = _get_conn(db_path)
        setup.execute("INSERT INTO market_universe (ticker, company_name, is_freetrade, index_membership) VALUES ('AAPL', 'Apple', 1, NULL)")
        setup.commit()
        setup.close()

        records = [{"ticker": "AAPL", "company_name": "Apple Inc.", "sector": "Tech", "index_membership": "SP500"}]
        with patch("index_engine.get_connection", side_effect=lambda: _get_conn(db_path)):
            upsert_index_assets(records)
        row = _read(db_path, "SELECT is_freetrade FROM market_universe WHERE ticker='AAPL'")
        assert row["is_freetrade"] == 1, "is_freetrade must not be overwritten by index upsert"

    def test_index_membership_concatenated(self, db_path):
        """A ticker already in SP500 that joins FTSE100 gets both memberships concatenated."""
        setup = _get_conn(db_path)
        setup.execute("INSERT INTO market_universe (ticker, company_name, is_index, index_membership) VALUES ('DUALLIST', 'Dual Listed Co', 1, 'SP500')")
        setup.commit()
        setup.close()

        records = [{"ticker": "DUALLIST", "company_name": "Dual Listed Co", "sector": "Finance", "index_membership": "FTSE100"}]
        with patch("index_engine.get_connection", side_effect=lambda: _get_conn(db_path)):
            upsert_index_assets(records)
        row = _read(db_path, "SELECT index_membership FROM market_universe WHERE ticker='DUALLIST'")
        assert "SP500" in row["index_membership"]
        assert "FTSE100" in row["index_membership"]

    def test_duplicate_membership_not_added_twice(self, db_path):
        """Re-syncing the same index must not duplicate the membership string."""
        setup = _get_conn(db_path)
        setup.execute("INSERT INTO market_universe (ticker, company_name, is_index, index_membership) VALUES ('AAPL', 'Apple', 1, 'SP500')")
        setup.commit()
        setup.close()

        records = [{"ticker": "AAPL", "company_name": "Apple", "sector": "Tech", "index_membership": "SP500"}]
        with patch("index_engine.get_connection", side_effect=lambda: _get_conn(db_path)):
            upsert_index_assets(records)
        row = _read(db_path, "SELECT index_membership FROM market_universe WHERE ticker='AAPL'")
        assert row["index_membership"].count("SP500") == 1, "SP500 must not appear twice"

    def test_last_updated_uses_utc(self, db_path):
        """last_updated must be stored in UTC (AGENTS.md: always store UTC)."""
        from datetime import datetime, timezone
        records = [{"ticker": "TEST", "company_name": "Test Co", "sector": "Tech", "index_membership": "SP500"}]
        before = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with patch("index_engine.get_connection", side_effect=lambda: _get_conn(db_path)):
            upsert_index_assets(records)
        after = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        row = _read(db_path, "SELECT last_updated FROM market_universe WHERE ticker='TEST'")
        assert before <= row["last_updated"] <= after, "last_updated must be a UTC timestamp"

    def test_db_error_returns_false(self):
        """DB failure → returns False without raising."""
        bad_conn = MagicMock()
        bad_conn.cursor.side_effect = Exception("disk full")
        with patch("index_engine.get_connection", return_value=bad_conn):
            result = upsert_index_assets([{"ticker": "X", "company_name": "X", "sector": "X", "index_membership": "SP500"}])
        assert result is False


# ──────────────────────────────────────────────────────────────────────────────
# 3. sync_all_indices()
# ──────────────────────────────────────────────────────────────────────────────

class TestSyncAllIndices:

    def test_no_indices_configured_returns_early(self):
        """sync_all_indices() exits without network calls when INDICES list is empty."""
        cfg = {"SCHEDULING": {"SYNC_INDICES": {"INDICES": []}}}
        with patch("index_engine.load_config", return_value=cfg), \
             patch("requests.get") as mock_get:
            sync_all_indices()
        mock_get.assert_not_called()

    def test_successful_sync_logs_notification(self):
        """When constituents are found and upserted, a Success notification is logged."""
        cfg = {"SCHEDULING": {"SYNC_INDICES": {"INDICES": ["SP500"]}}}
        records = [{"ticker": "AAPL", "company_name": "Apple", "sector": "Tech", "index_membership": "SP500"}]
        with patch("index_engine.load_config", return_value=cfg), \
             patch("index_engine.fetch_index_constituents", return_value=records), \
             patch("index_engine.upsert_index_assets", return_value=True), \
             patch("index_engine.log_notification") as mock_notify:
            sync_all_indices()
        calls = [str(c) for c in mock_notify.call_args_list]
        assert any("Success" in c for c in calls)

    def test_empty_fetch_logs_warning_notification(self):
        """When no records are found, a Warning notification is logged."""
        cfg = {"SCHEDULING": {"SYNC_INDICES": {"INDICES": ["SP500"]}}}
        with patch("index_engine.load_config", return_value=cfg), \
             patch("index_engine.fetch_index_constituents", return_value=[]), \
             patch("index_engine.log_notification") as mock_notify:
            sync_all_indices()
        calls = [str(c) for c in mock_notify.call_args_list]
        assert any("Warning" in c for c in calls)
