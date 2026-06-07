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

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}):
        tickers = engine.get_all_tickers()

    assert "AAPL" in tickers


def test_get_all_tickers_excludes_ignored():
    """Tickers listed in IGNORED_TICKERS must not appear in the result."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.portfolio = {"pos1": {"ticker": "TSLA"}, "pos2": {"ticker": "AAPL"}}
    engine.watchlist = {"watchlist": []}

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

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}):
        tickers = engine.get_all_tickers()

    assert tickers == ["MSFT"]


def test_get_all_tickers_empty_inputs_returns_empty_list():
    """With empty portfolio and watchlist, result must be an empty list."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.portfolio = {}
    engine.watchlist = {}

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}):
        tickers = engine.get_all_tickers()

    assert tickers == []


def test_get_all_tickers_result_is_sorted():
    """Output must be alphabetically sorted."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.portfolio = {"p1": {"ticker": "ZM"}, "p2": {"ticker": "AAPL"}}
    engine.watchlist = {"watchlist": ["MSFT"]}

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}):
        tickers = engine.get_all_tickers()

    assert tickers == sorted(tickers)
