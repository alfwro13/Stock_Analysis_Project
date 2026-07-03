"""
tests/test_yahoo_engine.py  ── YAHOO ENGINE

Comprehensive unit tests for yahoo_engine.YahooEngine.

All network I/O is mocked; no real HTTP calls are made.

Coverage:
  TestCacheMechanics       — get/set, TTL expiry, thread safety
  TestSliceBulk            — MultiIndex vs flat columns, timezone stripping
  TestGetPriceHistory      — batch dedup, cache hit/miss, partial cache hits
  TestGetIntraday          — prepost flag separation, interval-specific TTLs
  TestSingleTickerMethods  — get_ticker_info, options, news, insider, earnings,
                             fund_holdings, ticker_actions, get_fx_rate
  TestInvalidate           — per-ticker flush, full flush
  TestStats                — hit_rate_pct arithmetic, cached_keys count
  TestSingleton            — module-level instance is shared
"""

import sys
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from collections import namedtuple

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from yahoo_engine import YahooEngine, yahoo_engine


# ─── helpers ─────────────────────────────────────────────────────────────────

def _price_df(n: int = 10, tz: str = None) -> pd.DataFrame:
    """Synthetic daily OHLCV DataFrame. tz=None → tz-naive index."""
    idx = pd.date_range("2025-01-02", periods=n, freq="B", tz=tz)
    prices = np.linspace(100.0, 110.0, n)
    return pd.DataFrame(
        {"Open": prices, "High": prices * 1.01, "Low": prices * 0.99,
         "Close": prices, "Volume": [1_000_000] * n},
        index=idx,
    )


def _multi_df(tickers: list[str], n: int = 10) -> pd.DataFrame:
    """Multi-ticker MultiIndex DataFrame as yfinance bulk download returns."""
    idx = pd.date_range("2025-01-02", periods=n, freq="B")
    cols = pd.MultiIndex.from_product([tickers, ["Open", "High", "Low", "Close", "Volume"]])
    data = np.tile(np.linspace(100.0, 110.0, n), (len(tickers) * 5, 1)).T
    return pd.DataFrame(data, index=idx, columns=cols)


# yfinance returns (ticker, OHLCV) for the top-level MultiIndex
def _yf_multi_df(tickers: list[str], n: int = 10) -> pd.DataFrame:
    """Matches actual yfinance group_by='ticker' layout: (ticker, field)."""
    idx = pd.date_range("2025-01-02", periods=n, freq="B")
    fields = ["Close", "High", "Low", "Open", "Volume"]
    cols = pd.MultiIndex.from_product([tickers, fields])
    prices = np.linspace(100.0, 110.0, n)
    block = np.column_stack([prices] * len(fields))
    data = np.hstack([block] * len(tickers))
    return pd.DataFrame(data, index=idx, columns=cols)


# ─── TestCacheMechanics ───────────────────────────────────────────────────────

class TestCacheMechanics:

    def setup_method(self):
        self.eng = YahooEngine()

    def test_cache_miss_on_empty(self):
        assert self.eng._get("no:such:key") is None

    def test_cache_set_then_get(self):
        df = _price_df()
        self.eng._set("history:AAPL:2y:1d", df, ttl=3600)
        cached = self.eng._get("history:AAPL:2y:1d")
        assert cached is not None
        assert len(cached) == len(df)

    def test_expired_entry_is_a_miss(self):
        df = _price_df()
        self.eng._set("history:AAPL:2y:1d", df, ttl=0)
        time.sleep(0.01)
        assert self.eng._get("history:AAPL:2y:1d") is None

    def test_hit_counter_increments(self):
        df = _price_df()
        self.eng._set("k", df, ttl=3600)
        self.eng._get("k")
        self.eng._get("k")
        assert self.eng._hits == 2

    def test_miss_counter_increments(self):
        self.eng._get("missing1")
        self.eng._get("missing2")
        assert self.eng._misses == 2

    def test_set_overwrites_existing_entry(self):
        df1 = _price_df(5)
        df2 = _price_df(20)
        self.eng._set("key", df1, ttl=3600)
        self.eng._set("key", df2, ttl=3600)
        assert len(self.eng._get("key")) == 20

    def test_concurrent_set_get_does_not_raise(self):
        errors = []

        def worker():
            try:
                for _ in range(50):
                    self.eng._set("concurrent:key", _price_df(), ttl=3600)
                    self.eng._get("concurrent:key")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ─── TestSliceBulk ────────────────────────────────────────────────────────────

class TestSliceBulk:

    def test_multi_index_extracts_correct_ticker(self):
        df_bulk = _yf_multi_df(["AAPL", "MSFT"])
        result = YahooEngine._slice_bulk(df_bulk, "AAPL", is_single=False)
        assert result is not None
        assert "Close" in result.columns
        assert result.index.tz is None

    def test_multi_index_missing_ticker_returns_none(self):
        df_bulk = _yf_multi_df(["AAPL"])
        result = YahooEngine._slice_bulk(df_bulk, "NVDA", is_single=False)
        assert result is None

    def test_flat_single_ticker_returns_df(self):
        df_bulk = _price_df()
        result = YahooEngine._slice_bulk(df_bulk, "AAPL", is_single=True)
        assert result is not None
        assert "Close" in result.columns

    def test_tz_aware_index_stripped(self):
        df_bulk = _price_df(tz="UTC")
        result = YahooEngine._slice_bulk(df_bulk, "X", is_single=True)
        assert result.index.tz is None

    def test_empty_df_returns_none(self):
        result = YahooEngine._slice_bulk(pd.DataFrame(), "X", is_single=True)
        assert result is None


# ─── TestGetPriceHistory ──────────────────────────────────────────────────────

class TestGetPriceHistory:

    def setup_method(self):
        self.eng = YahooEngine()

    def _mock_download(self, tickers, df_or_dict):
        """
        Returns a yf.download mock.
        df_or_dict: pd.DataFrame for single-ticker, dict {ticker: df} for multi.
        """
        if isinstance(df_or_dict, pd.DataFrame):
            return df_or_dict
        return _yf_multi_df(list(df_or_dict.keys()))

    @patch("yahoo_engine.yf.download")
    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_dict_keyed_by_ticker(self, mock_ctx, mock_dl):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_dl.return_value = _yf_multi_df(["AAPL", "MSFT"])

        result = self.eng.get_price_history(["AAPL", "MSFT"])
        assert set(result.keys()) == {"AAPL", "MSFT"}
        assert isinstance(result["AAPL"], pd.DataFrame)
        assert "Close" in result["AAPL"].columns

    @patch("yahoo_engine.yf.download")
    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_second_call_hits_cache_no_second_download(self, mock_ctx, mock_dl):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_dl.return_value = _yf_multi_df(["AAPL"])

        self.eng.get_price_history(["AAPL"])
        self.eng.get_price_history(["AAPL"])
        assert mock_dl.call_count == 1

    @patch("yahoo_engine.yf.download")
    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_only_missing_tickers_fetched(self, mock_ctx, mock_dl):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        # Pre-populate AAPL
        self.eng._set("history:AAPL:2y:1d", _price_df(), ttl=3600)

        # Second call: AAPL cached, MSFT not
        mock_dl.return_value = _yf_multi_df(["MSFT"])
        result = self.eng.get_price_history(["AAPL", "MSFT"])

        # yf.download should only have been called with MSFT
        called_tickers = mock_dl.call_args[0][0]
        assert "AAPL" not in called_tickers
        assert "MSFT" in called_tickers
        assert set(result.keys()) == {"AAPL", "MSFT"}

    @patch("yahoo_engine.yf.download")
    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_deduplicates_ticker_list(self, mock_ctx, mock_dl):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_dl.return_value = _yf_multi_df(["AAPL"])

        self.eng.get_price_history(["AAPL", "AAPL", "AAPL"])
        # Only one unique ticker should be in the download call
        called_tickers = mock_dl.call_args[0][0]
        assert called_tickers.count("AAPL") == 1

    @patch("yahoo_engine.yf.download")
    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_failed_download_returns_empty_dict(self, mock_ctx, mock_dl):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_dl.side_effect = RuntimeError("network error")

        result = self.eng.get_price_history(["AAPL"])
        assert result == {}

    @patch("yahoo_engine.yf.download")
    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_empty_download_returns_empty_dict(self, mock_ctx, mock_dl):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_dl.return_value = pd.DataFrame()

        result = self.eng.get_price_history(["AAPL"])
        assert result == {}

    @patch("yahoo_engine.yf.download")
    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_period_and_interval_included_in_cache_key(self, mock_ctx, mock_dl):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_dl.return_value = _yf_multi_df(["SPY"])

        self.eng.get_price_history(["SPY"], period="1y", interval="1d")
        self.eng.get_price_history(["SPY"], period="5d", interval="1d")
        # Different period → different key → two downloads
        assert mock_dl.call_count == 2


# ─── TestGetIntraday ─────────────────────────────────────────────────────────

class TestGetIntraday:

    def setup_method(self):
        self.eng = YahooEngine()

    @patch("yahoo_engine.yf.download")
    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_prepost_true_false_are_separate_cache_keys(self, mock_ctx, mock_dl):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_dl.return_value = _yf_multi_df(["NVDA"])

        self.eng.get_intraday(["NVDA"], period="1d", interval="5m", prepost=False)
        self.eng.get_intraday(["NVDA"], period="1d", interval="5m", prepost=True)
        assert mock_dl.call_count == 2

    @patch("yahoo_engine.yf.download")
    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_cached_intraday_not_refetched(self, mock_ctx, mock_dl):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_dl.return_value = _yf_multi_df(["SPY"])

        self.eng.get_intraday(["SPY"], period="1d", interval="5m")
        self.eng.get_intraday(["SPY"], period="1d", interval="5m")
        assert mock_dl.call_count == 1

    @patch("yahoo_engine.yf.download")
    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_per_ticker_flat_dataframe(self, mock_ctx, mock_dl):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_dl.return_value = _yf_multi_df(["QQQ", "SPY"])

        result = self.eng.get_intraday(["QQQ", "SPY"])
        assert "QQQ" in result
        assert "SPY" in result
        assert isinstance(result["QQQ"], pd.DataFrame)


# ─── TestSingleTickerMethods ──────────────────────────────────────────────────

class TestGetTickerInfo:

    def setup_method(self):
        self.eng = YahooEngine()

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_info_dict(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        fake_info = {"symbol": "AAPL", "quoteType": "EQUITY", "marketCap": 3e12}

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.info = fake_info
            result = self.eng.get_ticker_info("AAPL")

        assert result == fake_info

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_caches_result(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        fake_info = {"symbol": "AAPL", "quoteType": "EQUITY"}

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.info = fake_info
            self.eng.get_ticker_info("AAPL")
            self.eng.get_ticker_info("AAPL")
            assert mock_tk_cls.call_count == 1

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_none_on_exception(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Ticker", side_effect=RuntimeError("no data")):
            result = self.eng.get_ticker_info("BAD")

        assert result is None


class TestGetMarketState:

    def setup_method(self):
        self.eng = YahooEngine()

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_market_state(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.info = {"marketState": "REGULAR"}
            result = self.eng.get_market_state("^GSPC")

        assert result == "REGULAR"

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_caches_result(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.info = {"marketState": "CLOSED"}
            self.eng.get_market_state("^GSPC")
            self.eng.get_market_state("^GSPC")
            assert mock_tk_cls.call_count == 1

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_cache_key_independent_from_get_ticker_info(self, mock_ctx):
        """A prior get_ticker_info() call for the same ticker must not satisfy get_market_state()
        from its (much longer-lived) cache entry."""
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.info = {"marketState": "REGULAR", "symbol": "^GSPC"}
            self.eng.get_ticker_info("^GSPC")
            result = self.eng.get_market_state("^GSPC")

        assert result == "REGULAR"
        assert mock_tk_cls.call_count == 2

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_none_when_marketstate_missing(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.info = {"symbol": "^GSPC"}
            result = self.eng.get_market_state("^GSPC")

        assert result is None

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_none_on_exception(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Ticker", side_effect=RuntimeError("no data")):
            result = self.eng.get_market_state("BAD")

        assert result is None


class TestGetOptionsExpirations:

    def setup_method(self):
        self.eng = YahooEngine()

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_list_of_expiry_strings(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        exps = ("2025-07-18", "2025-08-15", "2025-09-19")

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.options = exps
            result = self.eng.get_options_expirations("AAPL")

        assert result == list(exps)

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_caches_expirations(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.options = ("2025-07-18",)
            self.eng.get_options_expirations("AAPL")
            self.eng.get_options_expirations("AAPL")
            assert mock_tk_cls.call_count == 1

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_none_on_exception(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Ticker", side_effect=RuntimeError("boom")):
            assert self.eng.get_options_expirations("BAD") is None


class TestGetOptionsChain:

    def setup_method(self):
        self.eng = YahooEngine()

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_calls_puts_tuple(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        calls_df = pd.DataFrame({"strike": [150.0], "lastPrice": [5.0]})
        puts_df  = pd.DataFrame({"strike": [150.0], "lastPrice": [4.5]})
        fake_chain = MagicMock(calls=calls_df, puts=puts_df)

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.option_chain.return_value = fake_chain
            result = self.eng.get_options_chain("AAPL", "2025-07-18")

        assert result is not None
        calls, puts = result
        assert len(calls) == 1
        assert len(puts) == 1

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_caches_chain(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        fake_chain = MagicMock(calls=pd.DataFrame(), puts=pd.DataFrame())

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.option_chain.return_value = fake_chain
            self.eng.get_options_chain("AAPL", "2025-07-18")
            self.eng.get_options_chain("AAPL", "2025-07-18")
            assert mock_tk_cls.call_count == 1


class TestGetNews:

    def setup_method(self):
        self.eng = YahooEngine()

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_list(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        fake_news = [{"title": "Headline 1"}, {"title": "Headline 2"}]

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.news = fake_news
            result = self.eng.get_news("AAPL")

        assert result == fake_news

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_caches_news(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.news = [{"title": "x"}]
            self.eng.get_news("AAPL")
            self.eng.get_news("AAPL")
            assert mock_tk_cls.call_count == 1

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_none_on_exception(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Ticker", side_effect=RuntimeError("net")):
            assert self.eng.get_news("BAD") is None


class TestGetInsiderTransactions:

    def setup_method(self):
        self.eng = YahooEngine()

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_dataframe(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        df = pd.DataFrame({"Insider": ["CEO"], "Value": ["$1M"]})

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.insider_transactions = df
            mock_tk_cls.return_value.get_insider_transactions.return_value = df
            result = self.eng.get_insider_transactions("AAPL")

        assert result is not None
        assert len(result) == 1

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_caches_transactions(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        df = pd.DataFrame({"Insider": ["CFO"]})

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.insider_transactions = df
            self.eng.get_insider_transactions("AAPL")
            self.eng.get_insider_transactions("AAPL")
            assert mock_tk_cls.call_count == 1


class TestGetEarningsDates:

    def setup_method(self):
        self.eng = YahooEngine()

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_dataframe(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        df = pd.DataFrame({"EPS": [2.5]}, index=pd.to_datetime(["2025-01-28"]))

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.get_earnings_dates.return_value = df
            result = self.eng.get_earnings_dates("AAPL", limit=5)

        assert result is not None
        assert len(result) == 1

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_limit_included_in_cache_key(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        df = pd.DataFrame({"EPS": [1.0]}, index=pd.to_datetime(["2025-01-28"]))

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.get_earnings_dates.return_value = df
            self.eng.get_earnings_dates("AAPL", limit=5)
            self.eng.get_earnings_dates("AAPL", limit=10)
            assert mock_tk_cls.call_count == 2


class TestGetFxRate:

    def setup_method(self):
        self.eng = YahooEngine()

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_float(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        df = pd.DataFrame({"Close": [1.2650]}, index=pd.to_datetime(["2025-06-01"]))

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.history.return_value = df
            result = self.eng.get_fx_rate("GBPUSD=X")

        assert result == pytest.approx(1.2650)

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_caches_rate(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        df = pd.DataFrame({"Close": [1.27]}, index=pd.to_datetime(["2025-06-01"]))

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.history.return_value = df
            self.eng.get_fx_rate("GBPUSD=X")
            self.eng.get_fx_rate("GBPUSD=X")
            assert mock_tk_cls.call_count == 1

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_none_on_empty_history(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.history.return_value = pd.DataFrame()
            result = self.eng.get_fx_rate("GBPUSD=X")

        assert result is None

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_none_on_exception(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Ticker", side_effect=RuntimeError("net")):
            assert self.eng.get_fx_rate("GBPUSD=X") is None


class TestGetFundHoldings:

    def setup_method(self):
        self.eng = YahooEngine()

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_dataframe(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        holdings_df = pd.DataFrame({"Symbol": ["NVDA", "MSFT"], "holdingPercent": [15.0, 12.0]})
        fake_funds = MagicMock(top_holdings=holdings_df)

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.get_funds_data.return_value = fake_funds
            result = self.eng.get_fund_holdings("SMGB.L")

        assert result is not None
        assert len(result) == 2


class TestGetTickerActions:

    def setup_method(self):
        self.eng = YahooEngine()

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_dataframe(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        actions_df = pd.DataFrame({"Dividends": [0.25], "Stock Splits": [0.0]},
                                   index=pd.to_datetime(["2025-05-15"]))

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value.actions = actions_df
            result = self.eng.get_ticker_actions("AAPL")

        assert result is not None
        assert "Dividends" in result.columns


# ─── TestInvalidate ───────────────────────────────────────────────────────────

class TestInvalidate:

    def setup_method(self):
        self.eng = YahooEngine()

    def test_invalidate_specific_ticker_removes_its_keys(self):
        self.eng._set("history:AAPL:2y:1d", _price_df(), ttl=3600)
        self.eng._set("info:AAPL", {"symbol": "AAPL"}, ttl=3600)
        self.eng._set("history:MSFT:2y:1d", _price_df(), ttl=3600)

        self.eng.invalidate("AAPL")

        assert self.eng._get("history:AAPL:2y:1d") is None
        assert self.eng._get("info:AAPL") is None
        assert self.eng._get("history:MSFT:2y:1d") is not None

    def test_invalidate_none_clears_all(self):
        self.eng._set("history:AAPL:2y:1d", _price_df(), ttl=3600)
        self.eng._set("history:MSFT:2y:1d", _price_df(), ttl=3600)
        self.eng._set("info:NVDA", {}, ttl=3600)

        self.eng.invalidate()

        with self.eng._lock:
            assert len(self.eng._cache) == 0

    def test_invalidate_nonexistent_ticker_does_not_raise(self):
        self.eng.invalidate("GHOST")

    def test_invalidate_only_removes_matching_ticker(self):
        self.eng._set("history:AAPL:2y:1d", _price_df(), ttl=3600)
        self.eng._set("history:AAPLX:2y:1d", _price_df(), ttl=3600)

        self.eng.invalidate("AAPL")

        # AAPLX should still be present (no false substring match)
        assert self.eng._get("history:AAPLX:2y:1d") is not None


# ─── TestStats ────────────────────────────────────────────────────────────────

class TestStats:

    def setup_method(self):
        self.eng = YahooEngine()

    def test_zero_requests_returns_zero_hit_rate(self):
        stats = self.eng.get_stats()
        assert stats["hit_rate_pct"] == 0.0
        assert stats["hits"] == 0
        assert stats["misses"] == 0

    def test_all_hits_returns_100_pct(self):
        self.eng._set("k", "v", ttl=3600)
        self.eng._get("k")
        self.eng._get("k")
        stats = self.eng.get_stats()
        assert stats["hit_rate_pct"] == 100.0

    def test_half_hits_returns_50_pct(self):
        self.eng._set("k", "v", ttl=3600)
        self.eng._get("k")       # hit
        self.eng._get("missing") # miss
        stats = self.eng.get_stats()
        assert stats["hit_rate_pct"] == 50.0

    def test_cached_keys_count(self):
        self.eng._set("a", "1", ttl=3600)
        self.eng._set("b", "2", ttl=3600)
        assert self.eng.get_stats()["cached_keys"] == 2

    def test_invalidate_reduces_cached_keys(self):
        self.eng._set("history:AAPL:2y:1d", _price_df(), ttl=3600)
        self.eng._set("history:MSFT:2y:1d", _price_df(), ttl=3600)
        self.eng.invalidate("AAPL")
        assert self.eng.get_stats()["cached_keys"] == 1


# ─── TestSearchByIsin ────────────────────────────────────────────────────────

class TestSearchByIsin:

    def setup_method(self):
        self.eng = YahooEngine()

    def _mock_session(self, status_code, json_body):
        """Returns a context-manager mock whose __enter__ yields a session with .get()."""
        fake_resp = MagicMock()
        fake_resp.status_code = status_code
        fake_resp.json.return_value = json_body
        fake_session = MagicMock()
        fake_session.get.return_value = fake_resp
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = lambda s: fake_session
        mock_ctx.__exit__ = MagicMock(return_value=False)
        return mock_ctx

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_symbol_on_success(self, mock_ctx):
        mock_ctx.return_value = self._mock_session(
            200, {"quotes": [{"symbol": "VANEA.L"}, {"symbol": "VANEA.AS"}]}
        )
        result = self.eng.search_by_isin("IE00B3RBWM25")
        assert result == "VANEA.L"

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_caches_result(self, mock_ctx):
        mock_ctx.return_value = self._mock_session(200, {"quotes": [{"symbol": "SPY"}]})
        self.eng.search_by_isin("US78462F1030")
        mock_ctx.reset_mock()
        # Second call must hit cache — yahoo_connection_boundary not entered again
        result = self.eng.search_by_isin("US78462F1030")
        mock_ctx.assert_not_called()
        assert result == "SPY"

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_none_on_empty_quotes(self, mock_ctx):
        mock_ctx.return_value = self._mock_session(200, {"quotes": []})
        assert self.eng.search_by_isin("XX0000000000") is None

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_none_on_non_200(self, mock_ctx):
        mock_ctx.return_value = self._mock_session(404, {})
        assert self.eng.search_by_isin("XX0000000001") is None

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_none_on_quote_missing_symbol_key(self, mock_ctx):
        mock_ctx.return_value = self._mock_session(200, {"quotes": [{"name": "No Symbol"}]})
        assert self.eng.search_by_isin("XX0000000002") is None

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_none_on_exception(self, mock_ctx):
        mock_ctx.side_effect = RuntimeError("connection refused")
        assert self.eng.search_by_isin("XX0000000003") is None


# ─── TestGetAnnualFinancials ──────────────────────────────────────────────────

class TestGetAnnualFinancials:

    def setup_method(self):
        self.eng = YahooEngine()

    def _fake_tk(self, bs=None, fin=None, cf=None):
        mock_tk = MagicMock()
        mock_tk.balance_sheet = bs if bs is not None else pd.DataFrame()
        mock_tk.income_stmt  = fin if fin is not None else pd.DataFrame()
        mock_tk.cash_flow    = cf if cf is not None else pd.DataFrame()
        return mock_tk

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_three_tuple(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        bs  = pd.DataFrame({"TotalAssets": [1e9]})
        fin = pd.DataFrame({"TotalRevenue": [5e8]})
        cf  = pd.DataFrame({"FreeCashFlow": [1e8]})

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value = self._fake_tk(bs, fin, cf)
            result = self.eng.get_annual_financials("AAPL")

        assert len(result) == 3
        r_bs, r_fin, r_cf = result
        assert r_bs is not None and len(r_bs) == 1
        assert r_fin is not None and len(r_fin) == 1
        assert r_cf is not None and len(r_cf) == 1

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_empty_dataframes_return_none_elements(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value = self._fake_tk()
            bs, fin, cf = self.eng.get_annual_financials("EMPTY")

        assert bs is None and fin is None and cf is None

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_caches_result(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        bs = pd.DataFrame({"TotalAssets": [1e9]})

        with patch("yahoo_engine.yf.Ticker") as mock_tk_cls:
            mock_tk_cls.return_value = self._fake_tk(bs=bs)
            self.eng.get_annual_financials("AAPL")
            self.eng.get_annual_financials("AAPL")
            assert mock_tk_cls.call_count == 1

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_three_nones_on_exception(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Ticker", side_effect=RuntimeError("network")):
            result = self.eng.get_annual_financials("BAD")

        assert result == (None, None, None)


# ─── TestSearchTicker ─────────────────────────────────────────────────────────

class TestSearchTicker:

    def setup_method(self):
        self.eng = YahooEngine()

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_maps_quotes_to_result_dicts(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        fake_quotes = [
            {"symbol": "AAPL", "longname": "Apple Inc.", "quoteType": "EQUITY"},
            {"symbol": "AAPL.TO", "shortname": "Apple Inc CDR", "quoteType": "EQUITY"},
        ]

        with patch("yahoo_engine.yf.Search") as mock_search_cls:
            mock_search_cls.return_value.quotes = fake_quotes
            result = self.eng.search_ticker("Apple")

        assert result == [
            {"ticker": "AAPL", "company_name": "Apple Inc.", "quote_type": "EQUITY"},
            {"ticker": "AAPL.TO", "company_name": "Apple Inc CDR", "quote_type": "EQUITY"},
        ]

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_drops_quotes_without_symbol(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Search") as mock_search_cls:
            mock_search_cls.return_value.quotes = [{"longname": "No Symbol Here"}]
            result = self.eng.search_ticker("nonsense")

        assert result == []

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_caches_result(self, mock_ctx):
        mock_ctx.return_value.__enter__ = lambda s: MagicMock()
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yahoo_engine.yf.Search") as mock_search_cls:
            mock_search_cls.return_value.quotes = [{"symbol": "MSFT", "longname": "Microsoft"}]
            self.eng.search_ticker("Microsoft")
            self.eng.search_ticker("Microsoft")
            assert mock_search_cls.call_count == 1

    @patch("yahoo_engine.yahoo_connection_boundary")
    def test_returns_empty_list_on_exception(self, mock_ctx):
        mock_ctx.side_effect = RuntimeError("network down")
        assert self.eng.search_ticker("anything") == []


# ─── TestSingleton ────────────────────────────────────────────────────────────

class TestSingleton:

    def test_module_level_instance_is_yahoo_engine_type(self):
        assert isinstance(yahoo_engine, YahooEngine)

    def test_module_level_instance_is_same_object_on_reimport(self):
        import yahoo_engine as mod
        assert mod.yahoo_engine is yahoo_engine


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
