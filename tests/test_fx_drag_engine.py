"""
tests/test_fx_drag_engine.py

Unit tests for fx_drag_engine.py. Uses synthetic Parquet data written to a
temp directory — no live network calls, no real SQLite reads.
"""

import sys
from pathlib import Path
from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import fx_drag_engine


def _make_price_series(start_price: float, end_price: float, n_days: int = 30) -> pd.Series:
    today = pd.Timestamp.today().normalize()
    idx = pd.date_range(end=today, periods=n_days, freq="B")
    prices = [start_price] * len(idx)
    prices[-1] = end_price
    return pd.Series(prices, index=idx, name="Close")


def _make_parquet(tmp_path: Path, ticker: str, start_price: float, end_price: float, n: int = 30):
    series = _make_price_series(start_price, end_price, n)
    df = pd.DataFrame({"Open": series, "High": series, "Low": series, "Close": series, "Volume": 0.0})
    path = tmp_path / f"{ticker}.parquet"
    df.to_parquet(path)
    return path


class TestComputeFxBreakdown:
    def test_equity_only_no_fx_move(self, tmp_path):
        _make_parquet(tmp_path, "AAPL", start_price=100.0, end_price=110.0)
        gbpusd = _make_price_series(1.27, 1.27)

        with (
            patch.object(fx_drag_engine, "HISTORICAL_DIR", tmp_path),
            patch("fx_drag_engine._load_gbpusd_series", return_value=gbpusd),
        ):
            result = fx_drag_engine.compute_fx_breakdown("AAPL", period_days=20)

        assert result is not None
        assert abs(result["equity_pct"] - 10.0) < 0.1
        assert abs(result["fx_pct"]) < 0.1
        assert abs(result["total_gbp_pct"] - 10.0) < 0.2

    def test_fx_tailwind_no_equity_move(self, tmp_path):
        _make_parquet(tmp_path, "MSFT", start_price=100.0, end_price=100.0)
        # USD strengthened: GBPUSD fell from 1.27 → 1.20
        gbpusd = _make_price_series(1.27, 1.20)

        with (
            patch.object(fx_drag_engine, "HISTORICAL_DIR", tmp_path),
            patch("fx_drag_engine._load_gbpusd_series", return_value=gbpusd),
        ):
            result = fx_drag_engine.compute_fx_breakdown("MSFT", period_days=20)

        assert result is not None
        assert abs(result["equity_pct"]) < 0.1
        assert result["fx_pct"] > 0
        assert result["total_gbp_pct"] > 0

    def test_fx_headwind_erodes_equity_gain(self, tmp_path):
        # Stock +10% in USD, but GBP strengthened so USD weakened
        _make_parquet(tmp_path, "GOOG", start_price=100.0, end_price=110.0)
        gbpusd = _make_price_series(1.20, 1.32)  # GBP strengthened ~10%

        with (
            patch.object(fx_drag_engine, "HISTORICAL_DIR", tmp_path),
            patch("fx_drag_engine._load_gbpusd_series", return_value=gbpusd),
        ):
            result = fx_drag_engine.compute_fx_breakdown("GOOG", period_days=20)

        assert result is not None
        assert result["equity_pct"] > 0
        assert result["fx_pct"] < 0
        # GBP total return should be less than USD equity return
        assert result["total_gbp_pct"] < result["equity_pct"]

    def test_returns_none_when_parquet_missing(self, tmp_path):
        gbpusd = _make_price_series(1.27, 1.27)
        with (
            patch.object(fx_drag_engine, "HISTORICAL_DIR", tmp_path),
            patch("fx_drag_engine._load_gbpusd_series", return_value=gbpusd),
        ):
            result = fx_drag_engine.compute_fx_breakdown("NONEXISTENT", period_days=20)

        assert result is None

    def test_returns_none_when_no_data_in_range(self, tmp_path):
        # Data is all 5 years old — nothing falls within any normal lookback
        old_idx = pd.date_range(end=pd.Timestamp("2019-12-31"), periods=10, freq="B")
        df = pd.DataFrame({"Open": 100.0, "High": 100.0, "Low": 100.0, "Close": 100.0, "Volume": 0.0}, index=old_idx)
        (tmp_path / "NVDA.parquet").parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(tmp_path / "NVDA.parquet")

        gbpusd = _make_price_series(1.27, 1.27, 10)
        with (
            patch.object(fx_drag_engine, "HISTORICAL_DIR", tmp_path),
            patch("fx_drag_engine._load_gbpusd_series", return_value=gbpusd),
        ):
            result = fx_drag_engine.compute_fx_breakdown("NVDA", period_days=30)

        assert result is None

    def test_returns_none_when_gbpusd_empty(self, tmp_path):
        _make_parquet(tmp_path, "TSLA", start_price=100.0, end_price=110.0)
        with (
            patch.object(fx_drag_engine, "HISTORICAL_DIR", tmp_path),
            patch("fx_drag_engine._load_gbpusd_series", return_value=pd.Series(dtype=float)),
        ):
            result = fx_drag_engine.compute_fx_breakdown("TSLA", period_days=20)

        assert result is None

    def test_result_keys_present(self, tmp_path):
        _make_parquet(tmp_path, "AMD", start_price=100.0, end_price=115.0)
        gbpusd = _make_price_series(1.27, 1.25)

        with (
            patch.object(fx_drag_engine, "HISTORICAL_DIR", tmp_path),
            patch("fx_drag_engine._load_gbpusd_series", return_value=gbpusd),
        ):
            result = fx_drag_engine.compute_fx_breakdown("AMD", period_days=20)

        assert result is not None
        for key in ("equity_pct", "fx_pct", "total_gbp_pct", "ref_date", "gbpusd_ref", "gbpusd_now"):
            assert key in result, f"Missing key: {key}"

    def test_total_gbp_is_multiplicative(self, tmp_path):
        _make_parquet(tmp_path, "META", start_price=100.0, end_price=120.0)
        gbpusd = _make_price_series(1.30, 1.25)

        with (
            patch.object(fx_drag_engine, "HISTORICAL_DIR", tmp_path),
            patch("fx_drag_engine._load_gbpusd_series", return_value=gbpusd),
        ):
            result = fx_drag_engine.compute_fx_breakdown("META", period_days=20)

        assert result is not None
        expected = ((1 + result["equity_pct"] / 100) * (1 + result["fx_pct"] / 100) - 1) * 100
        assert abs(result["total_gbp_pct"] - expected) < 0.01


def _make_txn(
    ticker: str,
    qty: float,
    usd_price: float,
    exchange_rate: float,
    date_str: str = "2023-01-10",
    txn_type: str = "Buy",
    currency: str = "USD",
) -> dict:
    return {
        "txn_type": txn_type,
        "ticker": ticker,
        "currency": currency,
        "quantity": qty,
        "unit_price": usd_price,
        "exchange_rate": exchange_rate,
        "txn_date": date_str,
    }


class TestLifetimeBuyStats:
    def test_single_buy_correct_gbpusd(self):
        # GBP cost = 10*150*(1/1.5) = 1000 -> total_usd/total_gbp = 1500/1000 = 1.5
        txns = [_make_txn("AAPL", qty=10.0, usd_price=150.0, exchange_rate=1 / 1.5)]
        with (
            patch.object(fx_drag_engine, "get_accounts", return_value=[{"id": 1}]),
            patch.object(fx_drag_engine, "get_transactions", return_value=txns),
        ):
            result = fx_drag_engine._lifetime_buy_stats("AAPL")
        assert result is not None
        vwap_usd, gbpusd_buy, count, earliest = result
        assert abs(vwap_usd - 150.0) < 0.01
        assert abs(gbpusd_buy - 1.5) < 0.001
        assert count == 1
        assert earliest == "2023-01-10"

    def test_two_buys_weighted_correctly(self):
        txns = [
            _make_txn("MSFT", qty=5.0, usd_price=200.0, exchange_rate=160.0 / 200.0, date_str="2022-06-01"),
            _make_txn("MSFT", qty=10.0, usd_price=220.0, exchange_rate=180.0 / 220.0, date_str="2023-03-15"),
        ]
        with (
            patch.object(fx_drag_engine, "get_accounts", return_value=[{"id": 1}]),
            patch.object(fx_drag_engine, "get_transactions", return_value=txns),
        ):
            result = fx_drag_engine._lifetime_buy_stats("MSFT")
        assert result is not None
        vwap_usd, gbpusd_buy, count, earliest = result
        # total_usd = 5*200 + 10*220 = 3200, total_gbp = 5*160 + 10*180 = 2600
        assert abs(vwap_usd - 3200 / 15) < 0.01
        assert abs(gbpusd_buy - 3200 / 2600) < 0.001
        assert count == 2
        assert earliest == "2022-06-01"

    def test_non_usd_excluded(self):
        txns = [_make_txn("VOD.L", qty=100.0, usd_price=150.0, exchange_rate=1.0, currency="GBP")]
        with (
            patch.object(fx_drag_engine, "get_accounts", return_value=[{"id": 1}]),
            patch.object(fx_drag_engine, "get_transactions", return_value=txns),
        ):
            result = fx_drag_engine._lifetime_buy_stats("VOD.L")
        assert result is None

    def test_sell_transactions_excluded(self):
        txns = [_make_txn("TSLA", qty=5.0, usd_price=250.0, exchange_rate=0.8, txn_type="Sell")]
        with (
            patch.object(fx_drag_engine, "get_accounts", return_value=[{"id": 1}]),
            patch.object(fx_drag_engine, "get_transactions", return_value=txns),
        ):
            result = fx_drag_engine._lifetime_buy_stats("TSLA")
        assert result is None

    def test_no_transactions_returns_none(self):
        with (
            patch.object(fx_drag_engine, "get_accounts", return_value=[{"id": 1}]),
            patch.object(fx_drag_engine, "get_transactions", return_value=[]),
        ):
            result = fx_drag_engine._lifetime_buy_stats("GOOG")
        assert result is None

    def test_wrong_ticker_excluded(self):
        txns = [_make_txn("AAPL", qty=10.0, usd_price=150.0, exchange_rate=1 / 1.5)]
        with (
            patch.object(fx_drag_engine, "get_accounts", return_value=[{"id": 1}]),
            patch.object(fx_drag_engine, "get_transactions", return_value=txns),
        ):
            result = fx_drag_engine._lifetime_buy_stats("NVDA")
        assert result is None

    def test_spans_multiple_accounts(self):
        txn_a = _make_txn("AMD", qty=5.0, usd_price=100.0, exchange_rate=1 / 1.4, date_str="2022-01-01")
        txn_b = _make_txn("AMD", qty=5.0, usd_price=100.0, exchange_rate=1 / 1.4, date_str="2022-06-01")

        def _get_transactions(account_id):
            return [txn_a] if account_id == 1 else [txn_b]

        with (
            patch.object(fx_drag_engine, "get_accounts", return_value=[{"id": 1}, {"id": 2}]),
            patch.object(fx_drag_engine, "get_transactions", side_effect=_get_transactions),
        ):
            result = fx_drag_engine._lifetime_buy_stats("AMD")
        assert result is not None
        vwap_usd, gbpusd_buy, count, earliest = result
        assert count == 2
        assert earliest == "2022-01-01"

    def test_equity_fx_total_relationship(self):
        # Buy at GBPUSD=1.4, current GBPUSD=1.4 (flat FX), stock +20% USD
        # expected: equity≈20%, fx≈0%, total≈20%
        txns = [_make_txn("AMD", qty=10.0, usd_price=100.0, exchange_rate=1 / 1.4)]
        with (
            patch.object(fx_drag_engine, "get_accounts", return_value=[{"id": 1}]),
            patch.object(fx_drag_engine, "get_transactions", return_value=txns),
        ):
            result = fx_drag_engine._lifetime_buy_stats("AMD")
        assert result is not None
        vwap_usd, gbpusd_buy, count, _ = result
        assert abs(gbpusd_buy - 1.4) < 0.001
        current_usd = 120.0
        gbpusd_now = 1.4
        equity_pct = (current_usd / vwap_usd - 1) * 100
        fx_pct = (gbpusd_buy / gbpusd_now - 1) * 100
        total_pct = ((1 + equity_pct / 100) * (1 + fx_pct / 100) - 1) * 100
        assert abs(equity_pct - 20.0) < 0.01
        assert abs(fx_pct) < 0.01
        assert abs(total_pct - 20.0) < 0.1
