"""Tests for universe_engine parsing/filtering and DB-write logic."""
import os
import tempfile
import textwrap
from unittest.mock import patch

import pytest
import database as _db_module
from universe_engine import update_market_universe


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_tables():
    conn = _db_module.get_connection()
    conn.execute("DELETE FROM market_universe WHERE country = 'US'")
    conn.execute("DELETE FROM system_notifications")
    conn.commit()
    conn.close()


def _make_nasdaqlisted(rows):
    """
    Build a nasdaqlisted.txt content string.
    Columns (pipe-separated): Symbol|SecurityName|MarketCategory|TestIssue|FinancialStatus|RoundLotSize|ETF|NextShares
    Column index 6 is the test-issue flag.
    """
    header = "Symbol|SecurityName|MarketCategory|TestIssue|FinancialStatus|RoundLotSize|ETF|NextShares\n"
    return header + "".join(
        f"{sym}|{name}|||||{test_flag}|\n"
        for sym, name, test_flag in rows
    )


def _make_otherlisted(rows):
    """
    Build an otherlisted.txt content string.
    Columns: ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
    Column index 4 is the test-issue flag.
    """
    header = "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
    return header + "".join(
        f"{sym}|{name}|||{test_flag}|||\n"
        for sym, name, test_flag in rows
    )


def _patch_ftp_with_content(nasdaq_content, other_content):
    """
    Context manager: patches _download_ftp_files to write synthetic file content
    instead of making a real FTP connection.
    """
    def _fake_download(filenames):
        for fname, fpath in filenames.items():
            if fname == "nasdaqlisted":
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(nasdaq_content)
            else:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(other_content)
        return True

    return patch("universe_engine._download_ftp_files", side_effect=_fake_download)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCommonStockFilter:
    def test_common_stock_rows_inserted(self):
        nasdaq = _make_nasdaqlisted([("AAPL", "Apple Inc - Common Stock", "N")])
        other = _make_otherlisted([])
        with _patch_ftp_with_content(nasdaq, other):
            update_market_universe()
        conn = _db_module.get_connection()
        row = conn.execute("SELECT * FROM market_universe WHERE ticker = 'AAPL'").fetchone()
        conn.close()
        assert row is not None

    def test_non_common_stock_excluded(self):
        nasdaq = _make_nasdaqlisted([
            ("SPY", "SPDR S&P 500 ETF Trust", "N"),
            ("PFD", "Some Preferred Shares - Preferred Stock", "N"),
        ])
        other = _make_otherlisted([])
        with _patch_ftp_with_content(nasdaq, other):
            update_market_universe()
        conn = _db_module.get_connection()
        tickers = {r["ticker"] for r in conn.execute("SELECT ticker FROM market_universe WHERE country='US'").fetchall()}
        conn.close()
        assert "SPY" not in tickers
        assert "PFD" not in tickers


class TestTestIssueFilter:
    def test_nasdaq_test_issue_excluded(self):
        # Column 6 == " Y" marks a test issue in nasdaqlisted
        nasdaq = _make_nasdaqlisted([("ZTEST", "Test Corp - Common Stock", "Y")])
        other = _make_otherlisted([])
        with _patch_ftp_with_content(nasdaq, other):
            update_market_universe()
        conn = _db_module.get_connection()
        row = conn.execute("SELECT * FROM market_universe WHERE ticker = 'ZTEST'").fetchone()
        conn.close()
        assert row is None

    def test_other_test_issue_excluded(self):
        nasdaq = _make_nasdaqlisted([])
        other = _make_otherlisted([("OTEST", "Other Test Corp - Common Stock", "Y")])
        with _patch_ftp_with_content(nasdaq, other):
            update_market_universe()
        conn = _db_module.get_connection()
        row = conn.execute("SELECT * FROM market_universe WHERE ticker = 'OTEST'").fetchone()
        conn.close()
        assert row is None


class TestSymbolNormalisation:
    def test_dot_replaced_with_hyphen(self):
        nasdaq = _make_nasdaqlisted([("BRK.B", "Berkshire Hathaway Inc - Common Stock", "N")])
        other = _make_otherlisted([])
        with _patch_ftp_with_content(nasdaq, other):
            update_market_universe()
        conn = _db_module.get_connection()
        row = conn.execute("SELECT * FROM market_universe WHERE ticker = 'BRK-B'").fetchone()
        conn.close()
        assert row is not None

    def test_dollar_sign_symbol_excluded(self):
        nasdaq = _make_nasdaqlisted([("$SPX", "S&P 500 Index - Common Stock", "N")])
        other = _make_otherlisted([])
        with _patch_ftp_with_content(nasdaq, other):
            update_market_universe()
        conn = _db_module.get_connection()
        row = conn.execute("SELECT * FROM market_universe WHERE ticker = '$SPX'").fetchone()
        conn.close()
        assert row is None

    def test_company_name_suffix_stripped(self):
        nasdaq = _make_nasdaqlisted([("MSFT", "Microsoft Corporation - Common Stock", "N")])
        other = _make_otherlisted([])
        with _patch_ftp_with_content(nasdaq, other):
            update_market_universe()
        conn = _db_module.get_connection()
        row = conn.execute("SELECT company_name FROM market_universe WHERE ticker = 'MSFT'").fetchone()
        conn.close()
        assert row["company_name"] == "Microsoft Corporation"


class TestExchangeLabel:
    def test_nasdaq_tickers_labelled_nasdaq(self):
        nasdaq = _make_nasdaqlisted([("NVDA", "NVIDIA Corporation - Common Stock", "N")])
        other = _make_otherlisted([])
        with _patch_ftp_with_content(nasdaq, other):
            update_market_universe()
        conn = _db_module.get_connection()
        row = conn.execute("SELECT exchange FROM market_universe WHERE ticker = 'NVDA'").fetchone()
        conn.close()
        assert row["exchange"] == "NASDAQ"

    def test_other_tickers_labelled_nyse_amex(self):
        nasdaq = _make_nasdaqlisted([])
        other = _make_otherlisted([("GE", "General Electric - Common Stock", "N")])
        with _patch_ftp_with_content(nasdaq, other):
            update_market_universe()
        conn = _db_module.get_connection()
        row = conn.execute("SELECT exchange FROM market_universe WHERE ticker = 'GE'").fetchone()
        conn.close()
        assert row["exchange"] == "NYSE/AMEX"


class TestFtpFailure:
    def test_ftp_failure_posts_error_notification(self):
        with patch("universe_engine._download_ftp_files", return_value=False):
            update_market_universe()
        conn = _db_module.get_connection()
        rows = conn.execute(
            "SELECT message_text FROM system_notifications WHERE message_type = 'Error'"
        ).fetchall()
        conn.close()
        assert any("FTP" in r["message_text"] for r in rows)

    def test_ftp_failure_inserts_nothing(self):
        with patch("universe_engine._download_ftp_files", return_value=False):
            update_market_universe()
        conn = _db_module.get_connection()
        count = conn.execute("SELECT COUNT(*) FROM market_universe WHERE country='US'").fetchone()[0]
        conn.close()
        assert count == 0
