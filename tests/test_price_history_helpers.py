"""
tests/test_price_history_helpers.py

Unit tests for price_history_helpers.py — the calendar-cutoff period-return anchor logic
powering the Portfolio page's Change Period buttons. All parquet reads are mocked.
"""

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from price_history_helpers import (
    _anchor_closes_for_ticker,
    _calendar_offset,
    get_period_anchor_closes,
    pct_from_anchor,
)


def _fake_ohlcv(start: str, end: str) -> pd.DataFrame:
    idx = pd.date_range(start, end, freq="B")
    price = np.linspace(100.0, 100.0 + len(idx) - 1, len(idx))
    return pd.DataFrame({"Open": price, "High": price, "Low": price, "Close": price, "Volume": 1_000_000.0}, index=idx)


def test_calendar_offset_clamps_month_end_non_leap():
    assert _calendar_offset(date(2024, 3, 31), 1) == date(2024, 2, 29)
    assert _calendar_offset(date(2023, 3, 31), 1) == date(2023, 2, 28)


def test_calendar_offset_plain_month_back():
    assert _calendar_offset(date(2024, 7, 6), 1) == date(2024, 6, 6)
    assert _calendar_offset(date(2024, 7, 6), 12) == date(2023, 7, 6)


def test_5d_anchor_is_five_trading_sessions_back_not_calendar():
    df = _fake_ohlcv("2024-05-01", "2024-07-05")
    today = date(2024, 7, 6)
    with patch("price_history_helpers.load_or_fetch_daily_history", return_value=df):
        anchors = _anchor_closes_for_ticker("TEST", today)
    assert anchors["5d"] == pytest.approx(df["Close"].iloc[-6])


def test_ytd_anchor_snaps_to_last_close_before_weekend_boundary():
    # 2023-12-31 (the YTD cutoff for "today" in 2024) is a Sunday — must snap to Friday 2023-12-29.
    df = _fake_ohlcv("2023-11-01", "2024-07-05")
    today = date(2024, 7, 6)
    with patch("price_history_helpers.load_or_fetch_daily_history", return_value=df):
        anchors = _anchor_closes_for_ticker("TEST", today)
    expected_row = df.loc[df.index.date <= date(2023, 12, 31)].iloc[-1]
    assert expected_row.name.date() == date(2023, 12, 29)
    assert anchors["ytd"] == pytest.approx(expected_row["Close"])


def test_1m_6m_1y_snap_to_nearest_prior_close_on_gap():
    df = _fake_ohlcv("2022-06-01", "2024-07-05")
    today = date(2024, 7, 6)
    with patch("price_history_helpers.load_or_fetch_daily_history", return_value=df):
        anchors = _anchor_closes_for_ticker("TEST", today)
    for key, months_back in (("1m", 1), ("6m", 6), ("1y", 12)):
        cutoff = _calendar_offset(today, months_back)
        expected = df.loc[df.index.date <= cutoff].iloc[-1]["Close"]
        assert anchors[key] == pytest.approx(expected)


def test_insufficient_history_returns_none_per_period_independently():
    df = _fake_ohlcv("2024-06-25", "2024-07-05")  # a handful of days only
    today = date(2024, 7, 6)
    with patch("price_history_helpers.load_or_fetch_daily_history", return_value=df):
        anchors = _anchor_closes_for_ticker("TEST", today)
    assert anchors["6m"] is None
    assert anchors["ytd"] is None
    assert anchors["1y"] is None


def test_missing_parquet_returns_all_none_without_raising():
    today = date(2024, 7, 6)
    with patch("price_history_helpers.load_or_fetch_daily_history", return_value=None):
        anchors = _anchor_closes_for_ticker("MISSING", today)
    assert all(v is None for v in anchors.values())


def test_get_period_anchor_closes_batches_multiple_tickers():
    df_a = _fake_ohlcv("2022-06-01", "2024-07-05")
    df_b = _fake_ohlcv("2022-06-01", "2024-07-05")
    fixed_now = datetime(2024, 7, 6, tzinfo=timezone.utc)

    def fake_loader(ticker):
        return {"AAA": df_a, "BBB": df_b}.get(ticker)

    with patch("price_history_helpers.load_or_fetch_daily_history", side_effect=fake_loader), \
         patch("price_history_helpers.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_now
        result = get_period_anchor_closes(["AAA", "BBB"])

    assert set(result.keys()) == {"AAA", "BBB"}
    assert result["AAA"]["1y"] is not None
    assert result["BBB"]["1y"] is not None


def test_pct_from_anchor_ratio_and_none_passthrough():
    assert pct_from_anchor(110.0, 100.0) == pytest.approx(10.0)
    assert pct_from_anchor(90.0, 100.0) == pytest.approx(-10.0)
    assert pct_from_anchor(100.0, None) is None
    assert pct_from_anchor(None, 100.0) is None
    assert pct_from_anchor(100.0, 0.0) is None
