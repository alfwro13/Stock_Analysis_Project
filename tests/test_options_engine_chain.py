import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from options_engine import fetch_front_month_chain, fetch_options_chain


def _chain_df():
    calls = pd.DataFrame({
        "strike": [100.0], "lastPrice": [1.0], "bid": [0.9], "ask": [1.1],
        "volume": [10], "openInterest": [20], "impliedVolatility": [0.5],
    })
    puts = pd.DataFrame({
        "strike": [90.0], "lastPrice": [1.0], "bid": [0.9], "ask": [1.1],
        "volume": [10], "openInterest": [20], "impliedVolatility": [0.4],
    })
    return calls, puts


@patch("options_engine.yahoo_engine")
def test_fetch_front_month_chain_uses_only_first_expiration(mock_engine):
    mock_engine.get_options_expirations.return_value = ["2026-01-01", "2026-02-01", "2026-03-01"]
    mock_engine.get_options_chain.return_value = _chain_df()

    result = fetch_front_month_chain("AAPL")

    mock_engine.get_options_chain.assert_called_once_with("AAPL", "2026-01-01")
    assert result["expiration"] == "2026-01-01"
    assert result["calls"][0]["strike"] == 100.0
    assert result["puts"][0]["strike"] == 90.0
    assert "current_price" not in result


@patch("options_engine.yahoo_engine")
def test_fetch_front_month_chain_no_expirations_returns_error(mock_engine):
    mock_engine.get_options_expirations.return_value = []
    result = fetch_front_month_chain("AAPL")
    assert "error" in result
    mock_engine.get_options_chain.assert_not_called()


@patch("options_engine.yahoo_engine")
def test_fetch_options_chain_still_fetches_up_to_five_expirations(mock_engine):
    mock_engine.get_options_expirations.return_value = [
        "2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01",
    ]
    mock_engine.get_options_chain.return_value = _chain_df()
    mock_engine.get_intraday.return_value = {}

    result = fetch_options_chain("AAPL")

    assert mock_engine.get_options_chain.call_count == 5
    assert len(result["chains"]) == 5
