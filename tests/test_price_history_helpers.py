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


def test_tbill_synthetic_ticker_skips_fetch_and_returns_all_none():
    """TBILL-{txn_id} has no Yahoo Finance listing (see AGENTS.md's Treasury Bill section) —
    must never reach load_or_fetch_daily_history, which would hit Yahoo and log a 404."""
    today = date(2024, 7, 6)
    with patch("price_history_helpers.load_or_fetch_daily_history") as mock_loader:
        anchors = _anchor_closes_for_ticker("TBILL-606", today)
    mock_loader.assert_not_called()
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


@pytest.fixture
def anchor_files(tmp_path, monkeypatch):
    import data_engine
    import price_history_helpers

    monkeypatch.setattr(data_engine, "HISTORICAL_DIR", tmp_path)
    monkeypatch.setattr(data_engine, "is_excluded_from_yahoo_fetch", lambda ticker: False)
    with price_history_helpers._anchor_cache_lock:
        price_history_helpers._anchor_cache.clear()
    yield tmp_path
    with price_history_helpers._anchor_cache_lock:
        price_history_helpers._anchor_cache.clear()


def test_anchor_cache_reuses_reads_and_returns_independent_results(anchor_files):
    df = _fake_ohlcv("2022-06-01", "2024-07-05")
    df.to_parquet(anchor_files / "TEST.parquet")
    today = date(2024, 7, 6)
    with patch("data_engine.pd.read_parquet", wraps=pd.read_parquet) as read:
        first = _anchor_closes_for_ticker("TEST", today, cache_only=True)
        expected = dict(first)
        first["5d"] = -1
        second = _anchor_closes_for_ticker("TEST", today, cache_only=True)
    assert second == expected
    assert read.call_count == 1


def test_anchor_cache_invalidates_on_replace_edit_delete_and_recreate(anchor_files):
    df = _fake_ohlcv("2022-06-01", "2024-07-05")
    path = anchor_files / "TEST.parquet"
    df.to_parquet(path)
    today = date(2024, 7, 6)
    initial = _anchor_closes_for_ticker("TEST", today, cache_only=True)
    replacement = anchor_files / "replacement.parquet"
    (df * 2).to_parquet(replacement)
    replacement.replace(path)
    assert _anchor_closes_for_ticker("TEST", today, cache_only=True)["5d"] == initial["5d"] * 2
    (df * 3).to_parquet(path)
    assert _anchor_closes_for_ticker("TEST", today, cache_only=True)["5d"] == initial["5d"] * 3
    path.unlink()
    with patch("cache_refresh_helpers.request_cache_refresh"):
        assert all(value is None for value in _anchor_closes_for_ticker("TEST", today, cache_only=True).values())
    df.to_parquet(path)
    assert _anchor_closes_for_ticker("TEST", today, cache_only=True) == initial


def test_anchor_cache_date_rollover_prunes_old_entries(anchor_files):
    import price_history_helpers

    df = _fake_ohlcv("2022-06-01", "2024-07-05")
    for ticker in ("AAA", "BBB"):
        df.to_parquet(anchor_files / f"{ticker}.parquet")
        _anchor_closes_for_ticker(ticker, date(2024, 7, 6), cache_only=True)
    expected = float(df.loc[df.index.date <= date(2024, 6, 7), "Close"].iloc[-1])
    assert _anchor_closes_for_ticker("AAA", date(2024, 7, 7), cache_only=True)["1m"] == expected
    assert list(price_history_helpers._anchor_cache) == ["AAA"]


def test_stale_anchor_cache_hit_still_requests_coordinated_refresh(anchor_files):
    import os
    import data_engine

    df = _fake_ohlcv("2022-06-01", "2024-07-05")
    path = anchor_files / "TEST.parquet"
    df.to_parquet(path)
    os.utime(path, (1, 1))
    today = date(2024, 7, 6)
    with patch("cache_refresh_helpers.request_cache_refresh") as refresh, \
         patch("data_engine.pd.read_parquet", wraps=pd.read_parquet) as read:
        initial = _anchor_closes_for_ticker("TEST", today, cache_only=True)
        refresh.reset_mock()
        assert _anchor_closes_for_ticker("TEST", today, cache_only=True) == initial
        assert read.call_count == 1
        assert refresh.call_count == 1
        assert refresh.call_args.args[0] == "daily:TEST"
        with patch.object(data_engine, "_fetch_daily_history") as fetch:
            refresh.call_args.args[1]()
        fetch.assert_called_once_with("TEST", force_refresh=True)


def test_anchor_cache_is_bounded(anchor_files, monkeypatch):
    import price_history_helpers

    monkeypatch.setattr(price_history_helpers, "_ANCHOR_CACHE_LIMIT", 2)
    df = _fake_ohlcv("2022-06-01", "2024-07-05")
    for ticker in ("AAA", "BBB", "CCC"):
        df.to_parquet(anchor_files / f"{ticker}.parquet")
        _anchor_closes_for_ticker(ticker, date(2024, 7, 6), cache_only=True)
    assert list(price_history_helpers._anchor_cache) == ["BBB", "CCC"]


def test_anchor_revision_rejects_unsafe_and_symlink_escape(anchor_files):
    from data_engine import daily_history_cache_revision

    outside = anchor_files.parent / (anchor_files.name + "-sibling")
    outside.mkdir()
    _fake_ohlcv("2024-01-01", "2024-07-05").to_parquet(outside / "TEST.parquet")
    (anchor_files / "TEST.parquet").symlink_to(outside / "TEST.parquet")
    with patch("cache_refresh_helpers.request_cache_refresh") as refresh, \
         patch("data_engine.os.stat", wraps=__import__("os").stat) as stat:
        assert daily_history_cache_revision("../TEST", refresh_stale=True) is None
        assert daily_history_cache_revision("TEST", refresh_stale=True) is None
    refresh.assert_not_called()
    stat.assert_not_called()


def test_anchor_cache_does_not_publish_during_source_change(anchor_files):
    import price_history_helpers

    df = _fake_ohlcv("2022-06-01", "2024-07-05")
    path = anchor_files / "TEST.parquet"
    df.to_parquet(path)

    def changed_source(*args, **kwargs):
        (df * 2).to_parquet(path)
        return df

    with patch("price_history_helpers.load_or_fetch_daily_history", side_effect=changed_source):
        initial = _anchor_closes_for_ticker("TEST", date(2024, 7, 6), cache_only=True)
    assert "TEST" not in price_history_helpers._anchor_cache
    assert _anchor_closes_for_ticker("TEST", date(2024, 7, 6), cache_only=True)["5d"] == initial["5d"] * 2


def test_anchor_cache_changes_with_cache_root(anchor_files, monkeypatch):
    import data_engine

    df = _fake_ohlcv("2022-06-01", "2024-07-05")
    df.to_parquet(anchor_files / "TEST.parquet")
    first = _anchor_closes_for_ticker("TEST", date(2024, 7, 6), cache_only=True)
    other_root = anchor_files / "new-root"
    other_root.mkdir()
    (df * 2).to_parquet(other_root / "TEST.parquet")
    monkeypatch.setattr(data_engine, "HISTORICAL_DIR", other_root)
    assert _anchor_closes_for_ticker("TEST", date(2024, 7, 6), cache_only=True)["5d"] == first["5d"] * 2


def test_cached_anchors_are_independent_across_concurrent_readers(anchor_files):
    from concurrent.futures import ThreadPoolExecutor

    df = _fake_ohlcv("2022-06-01", "2024-07-05")
    df.to_parquet(anchor_files / "TEST.parquet")
    today = date(2024, 7, 6)
    expected = _anchor_closes_for_ticker("TEST", today, cache_only=True)
    with patch("price_history_helpers.load_or_fetch_daily_history", side_effect=AssertionError("cached read should not reload")):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: _anchor_closes_for_ticker("TEST", today, cache_only=True), range(20)))
    assert all(result == expected for result in results)
    results[0]["5d"] = -1
    assert all(result == expected for result in results[1:])


class TestSessionWindowReturns:
    @staticmethod
    def _closes(**columns) -> pd.DataFrame:
        n = max(len(v) for v in columns.values())
        idx = pd.date_range("2026-01-05", periods=n, freq="B")
        return pd.DataFrame({k: pd.Series(v, index=idx[:len(v)]) if len(v) < n else pd.Series(v, index=idx) for k, v in columns.items()})

    def test_cumulative_return_over_window_endpoints(self):
        from price_history_helpers import session_window_returns
        closes = self._closes(A=[100, 105, 110, 121], B=[50, 50, 55, 50])
        start, as_of, returns = session_window_returns(closes, 3)
        assert start == closes.index[0] and as_of == closes.index[-1]
        assert returns["A"] == pytest.approx(0.21)
        assert returns["B"] == pytest.approx(0.0)

    def test_window_uses_last_n_sessions_only(self):
        from price_history_helpers import session_window_returns
        closes = self._closes(A=[1, 100, 110, 121])
        start, _, returns = session_window_returns(closes, 2)
        assert start == closes.index[1]
        assert returns["A"] == pytest.approx(0.21)

    def test_too_short_history_returns_none(self):
        from price_history_helpers import session_window_returns
        assert session_window_returns(self._closes(A=[100, 101, 102]), 3) is None

    def test_date_traded_by_minority_is_not_on_the_calendar(self):
        from price_history_helpers import session_window_returns
        closes = self._closes(A=[100, 110, 121, 133.1], B=[100, 110, 121, np.nan], C=[100, 110, 121, np.nan], D=[100, 110, 121, np.nan])
        _, as_of, returns = session_window_returns(closes, 2)
        assert as_of == closes.index[2]
        assert returns["A"] == pytest.approx(0.21)

    def test_column_missing_an_endpoint_is_dropped(self):
        from price_history_helpers import session_window_returns
        closes = self._closes(A=[100, 110, 121], B=[100, 110, np.nan], C=[100, 105, 110])
        _, _, returns = session_window_returns(closes, 2)
        assert set(returns.index) == {"A", "C"}

    def test_sparse_column_below_coverage_is_dropped(self):
        from price_history_helpers import session_window_returns
        values = [100.0] + [np.nan] * 8 + [110.0]
        closes = self._closes(A=list(np.linspace(100, 110, 10)), B=values)
        _, _, returns = session_window_returns(closes, 9)
        assert list(returns.index) == ["A"]

    def test_non_positive_close_is_treated_as_missing(self):
        from price_history_helpers import session_window_returns
        closes = self._closes(A=[100, 110, 121], B=[100, 0, 121])
        _, _, returns = session_window_returns(closes, 2, min_coverage=1.0)
        assert list(returns.index) == ["A"]
