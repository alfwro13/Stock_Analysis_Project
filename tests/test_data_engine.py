"""
tests/test_data_engine.py  ── DATA ENGINE UNIT TESTS

Covers the pure business logic in DataEngine that does not touch the network:
  - get_all_tickers: de-duplication, normalisation, ignored-ticker filtering
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── get_all_tickers ───────────────────────────────────────────────────────────

def test_get_all_tickers_deduplicates_portfolio_and_watchlist():
    """Ticker appearing in both portfolio and watchlist must appear only once."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.portfolio = {"pos1": {"ticker": "AAPL"}}
    engine.watchlist = {"watchlist": ["AAPL", "MSFT"]}
    engine.account_tickers = []

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}):
        tickers = engine.get_all_tickers()

    assert tickers.count("AAPL") == 1
    assert "MSFT" in tickers


def test_get_all_tickers_normalises_case():
    """Tickers are uppercased via normalize_ticker — mixed-case input must be normalised."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.portfolio = {"pos1": {"ticker": "aapl"}}
    engine.watchlist = {"watchlist": []}
    engine.account_tickers = []

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}):
        tickers = engine.get_all_tickers()

    assert "AAPL" in tickers


def test_get_all_tickers_excludes_ignored():
    """Tickers listed in IGNORED_TICKERS must not appear in the result."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.portfolio = {"pos1": {"ticker": "TSLA"}, "pos2": {"ticker": "AAPL"}}
    engine.watchlist = {"watchlist": []}
    engine.account_tickers = []

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": ["TSLA"]}):
        tickers = engine.get_all_tickers()

    assert "TSLA" not in tickers
    assert "AAPL" in tickers


def test_get_all_tickers_skips_malformed_portfolio_entries():
    """Non-dict entries and entries without 'ticker' key must be silently skipped."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.portfolio = {
        "good": {"ticker": "MSFT"},
        "bad_str": "just-a-string",
        "bad_no_ticker": {"name": "No ticker here"},
        "bad_empty_ticker": {"ticker": ""},
    }
    engine.watchlist = {"watchlist": []}
    engine.account_tickers = []

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}):
        tickers = engine.get_all_tickers()

    assert tickers == ["MSFT"]


def test_get_all_tickers_empty_inputs_returns_empty_list():
    """With empty portfolio and watchlist, result must be an empty list."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.portfolio = {}
    engine.watchlist = {}
    engine.account_tickers = []

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}):
        tickers = engine.get_all_tickers()

    assert tickers == []


def test_get_all_tickers_result_is_sorted():
    """Output must be alphabetically sorted."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.portfolio = {"p1": {"ticker": "ZM"}, "p2": {"ticker": "AAPL"}}
    engine.watchlist = {"watchlist": ["MSFT"]}
    engine.account_tickers = []

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}):
        tickers = engine.get_all_tickers()

    assert tickers == sorted(tickers)


def test_get_all_tickers_includes_account_transaction_tickers():
    """Tickers that exist only in account_transactions (e.g. bought in an ISA, never
    watchlisted) must be included — regression test for the missing-Parquet bug."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.portfolio = {}
    engine.watchlist = {"watchlist": []}
    engine.account_tickers = ["XUKX.L", "IGLG.L"]

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}):
        tickers = engine.get_all_tickers()

    assert "XUKX.L" in tickers
    assert "IGLG.L" in tickers


def test_get_all_tickers_deduplicates_account_tickers_with_portfolio():
    """An account-only ticker already present in portfolio.json must appear once."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.portfolio = {"pos1": {"ticker": "AAPL"}}
    engine.watchlist = {"watchlist": []}
    engine.account_tickers = ["AAPL", "MSFT"]

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}):
        tickers = engine.get_all_tickers()

    assert tickers.count("AAPL") == 1
    assert "MSFT" in tickers


def test_get_all_tickers_excludes_ignored_account_tickers():
    """IGNORED_TICKERS filtering must still apply to account-sourced tickers."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.portfolio = {}
    engine.watchlist = {"watchlist": []}
    engine.account_tickers = ["XUKX.L", "BADTICKER"]

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": ["BADTICKER"]}):
        tickers = engine.get_all_tickers()

    assert "BADTICKER" not in tickers
    assert "XUKX.L" in tickers


# ── __init__ sources the watchlist/account tickers from the DB, not JSON files ──

def test_init_populates_watchlist_from_db():
    """DataEngine() must source self.watchlist from get_watchlist_tickers(), not a JSON file."""
    from data_engine import DataEngine

    with patch("data_engine.get_watchlist_tickers", return_value=["NVDA", "AMD"]), \
         patch("data_engine.get_all_account_tickers", return_value=[]), \
         patch("data_engine.DataEngine._load_json", return_value={}), \
         patch("data_engine.DataEngine._ensure_directories"):
        engine = DataEngine()

    assert engine.watchlist == {"watchlist": ["NVDA", "AMD"]}


def test_init_populates_account_tickers_from_db():
    """DataEngine() must source self.account_tickers from get_all_account_tickers()."""
    from data_engine import DataEngine

    with patch("data_engine.get_watchlist_tickers", return_value=[]), \
         patch("data_engine.get_all_account_tickers", return_value=["XUKX.L", "IGLG.L"]), \
         patch("data_engine.DataEngine._load_json", return_value={}), \
         patch("data_engine.DataEngine._ensure_directories"):
        engine = DataEngine()

    assert engine.account_tickers == ["XUKX.L", "IGLG.L"]
