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


def _combined(*tickers):
    return {t: {"ticker": t} for t in tickers}


# ── get_all_tickers ───────────────────────────────────────────────────────────

def test_get_all_tickers_deduplicates_portfolio_and_watchlist():
    """Ticker appearing in both portfolio and watchlist must appear only once."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.watchlist = {"watchlist": ["AAPL", "MSFT"]}
    engine.account_tickers = []

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}), \
         patch("accounts_engine.get_combined_holdings", return_value=_combined("AAPL")):
        tickers = engine.get_all_tickers()

    assert tickers.count("AAPL") == 1
    assert "MSFT" in tickers


def test_get_all_tickers_normalises_case():
    """Tickers are uppercased via normalize_ticker — mixed-case input must be normalised."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.watchlist = {"watchlist": []}
    engine.account_tickers = []

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}), \
         patch("accounts_engine.get_combined_holdings", return_value=_combined("aapl")):
        tickers = engine.get_all_tickers()

    assert "AAPL" in tickers


def test_get_all_tickers_excludes_ignored():
    """Tickers listed in IGNORED_TICKERS must not appear in the result."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.watchlist = {"watchlist": []}
    engine.account_tickers = []

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": ["TSLA"]}), \
         patch("accounts_engine.get_combined_holdings", return_value=_combined("TSLA", "AAPL")):
        tickers = engine.get_all_tickers()

    assert "TSLA" not in tickers
    assert "AAPL" in tickers


def test_get_all_tickers_excludes_tbill_synthetic_tickers():
    """TBILL-{txn_id} synthetic tickers aren't real Yahoo Finance symbols -- including one in the
    fetch universe produces a guaranteed-failing request every run (repeating 'possibly delisted'
    errors in production logs) for a ticker that can never have real market data."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.watchlist = {"watchlist": []}
    engine.account_tickers = []

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}), \
         patch("accounts_engine.get_combined_holdings", return_value=_combined("TBILL-606", "AAPL")):
        tickers = engine.get_all_tickers()

    assert "TBILL-606" not in tickers
    assert "AAPL" in tickers


def test_get_all_tickers_empty_inputs_returns_empty_list():
    """With empty portfolio, watchlist, and market ticker registry, result must be an empty list."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.watchlist = {}
    engine.account_tickers = []

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}), \
         patch("accounts_engine.get_combined_holdings", return_value={}), \
         patch("data_engine.get_registry_spot_future_tickers", return_value=[]):
        tickers = engine.get_all_tickers()

    assert tickers == []


def test_get_all_tickers_includes_market_registry_tickers():
    """Markets page tickers (market_ticker_registry) must be included so the index detail
    page's macro chart and technicals populate from the nightly download without requiring
    a manual per-ticker Refresh click."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.watchlist = {"watchlist": []}
    engine.account_tickers = []

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}), \
         patch("accounts_engine.get_combined_holdings", return_value={}), \
         patch("data_engine.get_registry_spot_future_tickers", return_value=["^GSPC", "ES=F"]):
        tickers = engine.get_all_tickers()

    assert "^GSPC" in tickers
    assert "ES=F" in tickers


def test_get_all_tickers_excludes_ignored_registry_tickers():
    """A registry ticker on the Settings-page Ignored Tickers list must not be fetched."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.watchlist = {"watchlist": []}
    engine.account_tickers = []

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": ["^GSPC"]}), \
         patch("accounts_engine.get_combined_holdings", return_value={}), \
         patch("data_engine.get_registry_spot_future_tickers", return_value=["^GSPC"]):
        tickers = engine.get_all_tickers()

    assert "^GSPC" not in tickers


def test_get_all_tickers_result_is_sorted():
    """Output must be alphabetically sorted."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.watchlist = {"watchlist": ["MSFT"]}
    engine.account_tickers = []

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}), \
         patch("accounts_engine.get_combined_holdings", return_value=_combined("ZM", "AAPL")):
        tickers = engine.get_all_tickers()

    assert tickers == sorted(tickers)


def test_get_all_tickers_includes_account_transaction_tickers():
    """Tickers that exist only in account_transactions (e.g. bought in an ISA, never
    watchlisted) must be included — regression test for the missing-Parquet bug."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.watchlist = {"watchlist": []}
    engine.account_tickers = ["XUKX.L", "IGLG.L"]

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}), \
         patch("accounts_engine.get_combined_holdings", return_value={}):
        tickers = engine.get_all_tickers()

    assert "XUKX.L" in tickers
    assert "IGLG.L" in tickers


def test_get_all_tickers_deduplicates_account_tickers_with_portfolio():
    """An account-only ticker already present in combined holdings must appear once."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.watchlist = {"watchlist": []}
    engine.account_tickers = ["AAPL", "MSFT"]

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": []}), \
         patch("accounts_engine.get_combined_holdings", return_value=_combined("AAPL")):
        tickers = engine.get_all_tickers()

    assert tickers.count("AAPL") == 1
    assert "MSFT" in tickers


def test_get_all_tickers_excludes_ignored_account_tickers():
    """IGNORED_TICKERS filtering must still apply to account-sourced tickers."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    engine.watchlist = {"watchlist": []}
    engine.account_tickers = ["XUKX.L", "BADTICKER"]

    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": ["BADTICKER"]}), \
         patch("accounts_engine.get_combined_holdings", return_value={}):
        tickers = engine.get_all_tickers()

    assert "BADTICKER" not in tickers
    assert "XUKX.L" in tickers


# ── __init__ sources the watchlist/account tickers from the DB, not JSON files ──

def test_init_populates_watchlist_from_db():
    """DataEngine() must source self.watchlist from get_watchlist_tickers(), not a JSON file."""
    from data_engine import DataEngine

    with patch("data_engine.get_watchlist_tickers", return_value=["NVDA", "AMD"]), \
         patch("data_engine.get_all_account_tickers", return_value=[]), \
         patch("data_engine.DataEngine._ensure_directories"):
        engine = DataEngine()

    assert engine.watchlist == {"watchlist": ["NVDA", "AMD"]}


def test_init_populates_account_tickers_from_db():
    """DataEngine() must source self.account_tickers from get_all_account_tickers()."""
    from data_engine import DataEngine

    with patch("data_engine.get_watchlist_tickers", return_value=[]), \
         patch("data_engine.get_all_account_tickers", return_value=["XUKX.L", "IGLG.L"]), \
         patch("data_engine.DataEngine._ensure_directories"):
        engine = DataEngine()

    assert engine.account_tickers == ["XUKX.L", "IGLG.L"]


# ── bulk_download_intraday: mutual funds have no intraday data ───────────────

def test_bulk_download_intraday_excludes_mutual_funds():
    """Mutual funds print one NAV/day and have no 5m bars — fetching them always returns
    empty and logs a misleading 'possibly delisted' error, so they must be filtered out
    before the Yahoo Finance call is even made."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)

    with patch("data_engine.get_mutual_fund_tickers", return_value={"0P00018XAR.L"}), \
         patch("data_engine.yahoo_engine.get_intraday", return_value={}) as mock_intraday:
        engine.bulk_download_intraday(["0P00018XAR.L", "AAPL"])

    mock_intraday.assert_called_once_with(["AAPL"], period="1d", interval="5m")


def test_bulk_download_intraday_skips_yahoo_call_when_all_mutual_funds():
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)

    with patch("data_engine.get_mutual_fund_tickers", return_value={"0P00018XAR.L"}), \
         patch("data_engine.yahoo_engine.get_intraday") as mock_intraday:
        engine.bulk_download_intraday(["0P00018XAR.L"])

    mock_intraday.assert_not_called()


# ── drip_feed_fundamentals: nightly universe fetch's fundamentals JSON writer ──

def test_drip_feed_fundamentals_writes_json_for_each_ticker(tmp_path):
    """Regression test for the same NameError('json' not defined) covered in
    test_fetch_and_save_data_writes_fundamentals_json, but for the nightly
    update_all_data() path — it swallows the error per-ticker (logs a warning),
    so a broken import here would silently drop every fundamentals refresh."""
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)

    with (
        patch("data_engine.FUNDAMENTALS_DIR", tmp_path),
        patch("data_engine.yahoo_engine.get_ticker_info", return_value={"sector": "Technology"}),
        patch("data_engine.time.sleep"),
    ):
        engine.drip_feed_fundamentals(["AAPL", "MSFT"])

    import json as json_module
    assert json_module.loads((tmp_path / "AAPL.json").read_text()) == {"sector": "Technology"}
    assert json_module.loads((tmp_path / "MSFT.json").read_text()) == {"sector": "Technology"}


# ── load_or_fetch_daily_history ────────────────────────────────────────────────

class TestLoadOrFetchDailyHistory:

    def test_reads_existing_parquet_without_fetching(self, tmp_path):
        import pandas as pd
        from data_engine import load_or_fetch_daily_history

        df = pd.DataFrame(
            {"Open": [1.0], "High": [1.5], "Low": [0.9], "Close": [1.2], "Volume": [100]},
            index=pd.DatetimeIndex(["2026-01-01"]),
        )
        df.to_parquet(tmp_path / "AAPL.parquet", engine="pyarrow")

        with (
            patch("data_engine.HISTORICAL_DIR", tmp_path),
            patch("data_engine.yahoo_engine.get_price_history") as mock_fetch,
        ):
            result = load_or_fetch_daily_history("AAPL")

        mock_fetch.assert_not_called()
        assert result is not None
        assert result["Close"].iloc[0] == 1.2

    def test_fetches_and_caches_when_parquet_missing(self, tmp_path):
        import pandas as pd
        from data_engine import load_or_fetch_daily_history

        fetched_df = pd.DataFrame(
            {"Open": [1.0], "High": [1.5], "Low": [0.9], "Close": [1.2], "Volume": [100]},
            index=pd.DatetimeIndex(["2026-01-01"]),
        )

        with (
            patch("data_engine.HISTORICAL_DIR", tmp_path),
            patch("data_engine.yahoo_engine.get_price_history", return_value={"NEWTICK": fetched_df}) as mock_fetch,
        ):
            result = load_or_fetch_daily_history("NEWTICK")

        mock_fetch.assert_called_once_with(["NEWTICK"], period="2y", interval="1d")
        assert result is not None
        assert result["Close"].iloc[0] == 1.2
        assert (tmp_path / "NEWTICK.parquet").exists()

    def test_returns_none_when_fetch_returns_empty(self, tmp_path):
        from data_engine import load_or_fetch_daily_history

        with (
            patch("data_engine.HISTORICAL_DIR", tmp_path),
            patch("data_engine.yahoo_engine.get_price_history", return_value={}),
        ):
            result = load_or_fetch_daily_history("MISSING")

        assert result is None
        assert not (tmp_path / "MISSING.parquet").exists()


# ── fetch_and_save_data: manual single-ticker refresh (details-page button) ────

def test_fetch_and_save_data_writes_fundamentals_json(tmp_path):
    """Regression test for a NameError('json' not defined) that broke the manual
    details-page refresh button: json.dump was called with no `import json`."""
    import pandas as pd
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    price_df = pd.DataFrame(
        {"Open": [1.0], "High": [1.5], "Low": [0.9], "Close": [1.2], "Volume": [100]},
        index=pd.DatetimeIndex(["2026-01-01"]),
    )

    with (
        patch("data_engine.HISTORICAL_DIR", tmp_path),
        patch("data_engine.INTRADAY_DIR", tmp_path),
        patch("data_engine.FUNDAMENTALS_DIR", tmp_path),
        patch("data_engine.yahoo_engine.get_price_history", return_value={"KO": price_df}),
        patch("data_engine.yahoo_engine.get_intraday", return_value={}),
        patch("data_engine.yahoo_engine.get_ticker_info", return_value={"sector": "Consumer Defensive"}),
    ):
        result = engine.fetch_and_save_data("KO")

    assert result is True
    assert (tmp_path / "KO.json").exists()


def test_fetch_and_save_data_skips_intraday_for_mutual_fund(tmp_path):
    """Same 'possibly delisted' log-noise bug as bulk_download_intraday, but for the
    manual single-ticker refresh path — a mutual fund has no 5m bars to fetch."""
    import pandas as pd
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    price_df = pd.DataFrame(
        {"Open": [1.0], "High": [1.5], "Low": [0.9], "Close": [1.2], "Volume": [100]},
        index=pd.DatetimeIndex(["2026-01-01"]),
    )

    with (
        patch("data_engine.HISTORICAL_DIR", tmp_path),
        patch("data_engine.INTRADAY_DIR", tmp_path),
        patch("data_engine.FUNDAMENTALS_DIR", tmp_path),
        patch("data_engine.yahoo_engine.get_price_history", return_value={"0P00018XAR.L": price_df}),
        patch("data_engine.get_mutual_fund_tickers", return_value={"0P00018XAR.L"}),
        patch("data_engine.yahoo_engine.get_intraday") as mock_intraday,
        patch("data_engine.yahoo_engine.get_ticker_info", return_value={}),
    ):
        result = engine.fetch_and_save_data("0P00018XAR.L")

    assert result is True
    mock_intraday.assert_not_called()


# ── _drop_in_progress_last_bar / poisoned-history regression ───────────────────
# A manual or scheduled historical refresh triggered while a ticker's market is still open can
# have Yahoo return today's still-forming daily bar as the last row; intraday_orchestrator.py and
# intraday_bottom_engine.py then read that row as "previous close" via market_pulse.upsert_live_price,
# producing a wildly wrong 24h % change that fights with market_pulse.fetch_and_save_pulse's correct
# value. These tests cover the fix at its root: the daily history file must never be written with
# an in-progress bar as its last row.

def _ohlcv(dates, closes):
    import pandas as pd
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": [1_000_000] * len(closes)},
        index=pd.DatetimeIndex(dates),
    )


class TestDropInProgressLastBar:
    def test_trims_last_row_when_it_matches_live_feed_date(self):
        """Daily's last date must match BOTH the live feed's date AND today's real calendar date
        to be trimmed -- a hardcoded past date here would test a scenario that's no longer "still
        forming" as real time moves on (see is_daily_bar_still_forming's 2026-07-08 fix)."""
        from datetime import datetime, timedelta, timezone
        from data_engine import _drop_in_progress_last_bar

        today = datetime.now(timezone.utc).date().isoformat()
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        daily = _ohlcv([yesterday, today], [100.0, 105.0])  # today's bar is still-forming
        live = _ohlcv([f"{today} 09:30", f"{today} 10:00"], [104.0, 105.0])

        result = _drop_in_progress_last_bar(daily, live)

        assert len(result) == 1
        assert result["Close"].iloc[-1] == 100.0

    def test_keeps_last_row_when_daily_predates_live_feed(self):
        from data_engine import _drop_in_progress_last_bar

        daily = _ohlcv(["2026-07-01", "2026-07-02"], [98.0, 100.0])  # genuinely completed close
        live = _ohlcv(["2026-07-06 09:30"], [105.0])

        result = _drop_in_progress_last_bar(daily, live)

        assert len(result) == 2
        assert result["Close"].iloc[-1] == 100.0

    def test_noop_when_no_live_data_available(self):
        import pandas as pd
        from data_engine import _drop_in_progress_last_bar

        daily = _ohlcv(["2026-07-02", "2026-07-06"], [100.0, 105.0])

        assert len(_drop_in_progress_last_bar(daily, None)) == 2
        assert len(_drop_in_progress_last_bar(daily, pd.DataFrame())) == 2

    def test_noop_when_daily_has_fewer_than_two_rows(self):
        from data_engine import _drop_in_progress_last_bar

        daily = _ohlcv(["2026-07-06"], [105.0])
        live = _ohlcv(["2026-07-06 09:30"], [105.0])

        assert len(_drop_in_progress_last_bar(daily, live)) == 1

    def test_keeps_last_row_when_ticker_exchange_confirmed_closed(self):
        """Regression test (found 2026-07-13): passing a ticker resolves its exchange via
        time_engine and consults is_market_open — a closed exchange must keep today's bar even
        though its date matches both the live feed's date and today's real calendar date."""
        from datetime import datetime, timezone
        from unittest.mock import patch as _patch
        from data_engine import _drop_in_progress_last_bar

        today = datetime.now(timezone.utc).date().isoformat()
        daily = _ohlcv(["2026-07-01", today], [100.0, 105.0])
        live = _ohlcv([f"{today} 09:30", f"{today} 10:00"], [104.0, 105.0])

        with _patch("data_engine.time_engine.is_market_open", return_value=False) as mocked:
            result = _drop_in_progress_last_bar(daily, live, "AMD")

        mocked.assert_called_once_with("NYSE")
        assert len(result) == 2
        assert result["Close"].iloc[-1] == 105.0

    def test_trims_last_row_when_ticker_exchange_confirmed_open(self):
        """Same date collision as above, but with the exchange confirmed still open — the bar
        genuinely is still forming and must be trimmed."""
        from datetime import datetime, timezone
        from unittest.mock import patch as _patch
        from data_engine import _drop_in_progress_last_bar

        today = datetime.now(timezone.utc).date().isoformat()
        daily = _ohlcv(["2026-07-01", today], [100.0, 105.0])
        live = _ohlcv([f"{today} 09:30", f"{today} 10:00"], [104.0, 105.0])

        with _patch("data_engine.time_engine.is_market_open", return_value=True):
            result = _drop_in_progress_last_bar(daily, live, "AMD")

        assert len(result) == 1
        assert result["Close"].iloc[-1] == 100.0


def test_bulk_download_historical_trims_in_progress_last_bar(tmp_path):
    """The bulk 2Y historical refresh must not persist today's still-forming bar as if it were
    a completed close — regression for the root cause of the AMD 24h-change flip-flop bug.
    NYSE is mocked open to isolate this from the exchange-open regression test below."""
    import pandas as pd
    from datetime import datetime, timedelta, timezone
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    today = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    daily_df = _ohlcv([yesterday, today], [517.82, 560.86])
    live_df = _ohlcv([f"{today} 09:30", f"{today} 15:45"], [560.86, 566.0])

    with (
        patch("data_engine.HISTORICAL_DIR", tmp_path),
        patch("data_engine.yahoo_engine.get_price_history", return_value={"AMD": daily_df}),
        patch("data_engine.get_mutual_fund_tickers", return_value=set()),
        patch("data_engine.yahoo_engine.get_intraday", return_value={"AMD": live_df}),
        patch("data_engine.time_engine.is_market_open", return_value=True),
    ):
        engine.bulk_download_historical(["AMD"])

    saved = pd.read_parquet(tmp_path / "AMD.parquet")
    assert len(saved) == 1
    assert saved["Close"].iloc[-1] == 517.82


def test_bulk_download_historical_keeps_todays_close_when_exchange_already_closed(tmp_path):
    """Regression test (found 2026-07-13): a same-day fetch that happens AFTER the exchange has
    closed (e.g. the 22:30 nightly Update Pipeline) produces the same date signature as a
    mid-session fetch — both daily and live feed dates equal today. Without an exchange-open
    signal, this wrongly trimmed the genuinely final close every night, permanently rolling
    stock_signals.current_price one trading day stale. With NYSE mocked closed, today's real
    close must be kept."""
    import pandas as pd
    from datetime import datetime, timedelta, timezone
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    today = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    daily_df = _ohlcv([yesterday, today], [517.82, 560.86])
    live_df = _ohlcv([f"{today} 09:30", f"{today} 15:45"], [560.86, 566.0])

    with (
        patch("data_engine.HISTORICAL_DIR", tmp_path),
        patch("data_engine.yahoo_engine.get_price_history", return_value={"AMD": daily_df}),
        patch("data_engine.get_mutual_fund_tickers", return_value=set()),
        patch("data_engine.yahoo_engine.get_intraday", return_value={"AMD": live_df}),
        patch("data_engine.time_engine.is_market_open", return_value=False),
    ):
        engine.bulk_download_historical(["AMD"])

    saved = pd.read_parquet(tmp_path / "AMD.parquet")
    assert len(saved) == 2
    assert saved["Close"].iloc[-1] == 560.86


def test_bulk_download_historical_keeps_completed_close_unchanged(tmp_path):
    """When the refresh runs after close (no same-day live bar), the daily download is saved as-is."""
    import pandas as pd
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    daily_df = _ohlcv(["2026-07-01", "2026-07-02"], [538.16, 517.82])

    with (
        patch("data_engine.HISTORICAL_DIR", tmp_path),
        patch("data_engine.get_mutual_fund_tickers", return_value=set()),
        patch("data_engine.yahoo_engine.get_price_history", return_value={"AMD": daily_df}),
        patch("data_engine.yahoo_engine.get_intraday", return_value={}),
    ):
        engine.bulk_download_historical(["AMD"])

    saved = pd.read_parquet(tmp_path / "AMD.parquet")
    assert len(saved) == 2
    assert saved["Close"].iloc[-1] == 517.82


def test_fetch_and_save_data_trims_in_progress_last_bar(tmp_path):
    """Single-ticker manual refresh (details-page button / POST /api/data/refresh-single) must
    apply the same in-progress-bar guard as the bulk path."""
    import pandas as pd
    from datetime import datetime, timedelta, timezone
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    today = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    daily_df = _ohlcv([yesterday, today], [517.82, 560.86])
    live_df = _ohlcv([f"{today} 09:30", f"{today} 15:45"], [560.86, 566.0])

    with (
        patch("data_engine.HISTORICAL_DIR", tmp_path),
        patch("data_engine.INTRADAY_DIR", tmp_path),
        patch("data_engine.FUNDAMENTALS_DIR", tmp_path),
        patch("data_engine.get_mutual_fund_tickers", return_value=set()),
        patch("data_engine.yahoo_engine.get_intraday", return_value={"AMD": live_df}),
        patch("data_engine.yahoo_engine.get_price_history", return_value={"AMD": daily_df}),
        patch("data_engine.yahoo_engine.get_ticker_info", return_value={}),
        patch("data_engine.time_engine.is_market_open", return_value=True),
    ):
        result = engine.fetch_and_save_data("AMD")

    assert result is True
    saved = pd.read_parquet(tmp_path / "AMD.parquet")
    assert len(saved) == 1
    assert saved["Close"].iloc[-1] == 517.82


def test_cache_only_missing_history_schedules_without_fetch(tmp_path):
    from data_engine import load_or_fetch_daily_history

    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("data_engine.yahoo_engine.get_price_history") as network, patch("cache_refresh_helpers.request_cache_refresh") as refresh:
        assert load_or_fetch_daily_history("ZZCOLD", cache_only=True) is None
    network.assert_not_called()
    refresh.assert_called_once()
    assert refresh.call_args.args[0] == "daily:ZZCOLD"
    assert not (tmp_path / "ZZCOLD.parquet").exists()


def test_cache_only_stale_history_returns_last_good_after_failed_refresh(tmp_path):
    import os
    import time
    import pandas as pd
    from data_engine import load_or_fetch_daily_history

    path = tmp_path / "ZZSTALE.parquet"
    df = pd.DataFrame({"Close": [100.0]}, index=pd.to_datetime(["2026-01-01"]))
    df.to_parquet(path)
    os.utime(path, (time.time() - 86400, time.time() - 86400))
    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("data_engine.yahoo_engine.get_price_history", return_value={}), patch("cache_refresh_helpers.request_cache_refresh") as refresh:
        result = load_or_fetch_daily_history("ZZSTALE", cache_only=True)
        assert result["Close"].iloc[-1] == 100.0
        assert refresh.call_args.args[1]() is None
        pd.testing.assert_frame_equal(pd.read_parquet(path), df)


@pytest.mark.parametrize("ticker", ["TBILL-999", "PENSION-999", "GBP"])
def test_cache_only_history_never_refreshes_excluded_tickers(tmp_path, ticker):
    from data_engine import load_or_fetch_daily_history

    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("cache_refresh_helpers.request_cache_refresh") as refresh:
        assert load_or_fetch_daily_history(ticker, cache_only=True) is None
    refresh.assert_not_called()


@pytest.mark.parametrize("market_open,expected_rows", [(True, 2), (False, 3)])
def test_history_refresh_preserves_completed_bar_rules(tmp_path, market_open, expected_rows):
    from datetime import datetime, timezone
    import pandas as pd
    from data_engine import _fetch_daily_history

    today = datetime.now(timezone.utc).date()
    df = pd.DataFrame({"Close": [10.0, 11.0, 12.0]}, index=pd.date_range(end=today, periods=3))
    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("data_engine.yahoo_engine.get_price_history", return_value={"ZZBAR": df}), patch("time_engine.is_market_open", return_value=market_open):
        fetched = _fetch_daily_history("ZZBAR")
    assert len(fetched) == expected_rows
    assert len(pd.read_parquet(tmp_path / "ZZBAR.parquet")) == expected_rows


def test_cache_only_fresh_history_does_not_schedule_refresh(tmp_path):
    import pandas as pd
    from data_engine import load_or_fetch_daily_history

    pd.DataFrame({"Close": [10.0]}, index=pd.to_datetime(["2026-01-01"])).to_parquet(tmp_path / "ZZFRESH.parquet")
    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("cache_refresh_helpers.request_cache_refresh") as refresh:
        assert load_or_fetch_daily_history("ZZFRESH", cache_only=True)["Close"].iloc[-1] == 10.0
    refresh.assert_not_called()


def test_stale_daily_refresh_bypasses_partial_yahoo_memory_cache(tmp_path):
    import pandas as pd
    import os
    import time
    from datetime import datetime, timezone
    from data_engine import load_or_fetch_daily_history

    ticker = "ZZSETTLED"
    path = tmp_path / (ticker + ".parquet")
    today = datetime.now(timezone.utc).date()
    old = pd.DataFrame({"Close": [10.0, 11.0]}, index=pd.date_range(end=today, periods=2))
    old.to_parquet(path)
    os.utime(path, (time.time() - 86400, time.time() - 86400))
    settled = old.copy()
    settled.iloc[-1, 0] = 12.0
    def history(tickers, **kwargs):
        return {ticker: settled if kwargs.get("force_refresh") else old}
    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("data_engine.yahoo_engine.get_price_history", side_effect=history) as fetch, patch("time_engine.is_market_open", return_value=False), patch("cache_refresh_helpers.request_cache_refresh") as refresh:
        assert load_or_fetch_daily_history(ticker, cache_only=True)["Close"].iloc[-1] == 11.0
        assert refresh.call_args.args[1]()["Close"].iloc[-1] == 12.0
    fetch.assert_called_once_with([ticker], period="2y", interval="1d", force_refresh=True)
    assert pd.read_parquet(path)["Close"].iloc[-1] == 12.0


def test_cache_only_history_rejects_symlink_outside_cache_root(tmp_path):
    import pandas as pd
    from data_engine import load_or_fetch_daily_history

    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    outside = tmp_path / "outside.parquet"
    pd.DataFrame({"Close": [99.0]}).to_parquet(outside)
    (cache_root / "ZZLINK.parquet").symlink_to(outside)
    with patch("data_engine.HISTORICAL_DIR", cache_root), patch("cache_refresh_helpers.request_cache_refresh") as refresh:
        assert load_or_fetch_daily_history("ZZLINK", cache_only=True) is None
    refresh.assert_not_called()


def test_history_refresh_rejects_symlink_outside_cache_root(tmp_path):
    from data_engine import _fetch_daily_history

    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    outside = tmp_path / "outside.parquet"
    outside.write_bytes(b"unchanged")
    (cache_root / "ZZLINK.parquet").symlink_to(outside)
    with patch("data_engine.HISTORICAL_DIR", cache_root), patch("data_engine.yahoo_engine.get_price_history") as fetch:
        assert _fetch_daily_history("ZZLINK") is None
    fetch.assert_not_called()
    assert outside.read_bytes() == b"unchanged"


@pytest.mark.parametrize("age", [0, 86400 * 3])
def test_intraday_history_reuses_only_fresh_file(tmp_path, age):
    import os
    import time
    import pandas as pd
    from data_engine import load_or_fetch_intraday_history

    path = tmp_path / "STEP4FRESH_intraday.parquet"
    df = pd.DataFrame({"Close": [10]}, index=pd.to_datetime(["2026-10-02 14:00"]))
    df.to_parquet(path)
    os.utime(path, (time.time() - age, time.time() - age))
    new = df * 2
    with patch("data_engine.INTRADAY_DIR", tmp_path), \
         patch("data_engine.yahoo_engine.get_intraday", return_value={"STEP4FRESH": new}) as fetch:
        result = load_or_fetch_intraday_history("STEP4FRESH")
    assert result.iloc[-1]["Close"] == (10 if age == 0 else 20)
    assert fetch.call_count == (0 if age == 0 else 1)


def test_intraday_history_uses_fetch_timestamp_not_rewrite_time(tmp_path):
    import time
    import pandas as pd
    from data_engine import load_or_fetch_intraday_history

    df = pd.DataFrame({"Close": [10]}, index=pd.to_datetime(["2026-10-02 14:00"]))
    df.attrs["yahoo_fetched_at"] = time.time() - 600
    df.to_parquet(tmp_path / "STEP4STAMP_intraday.parquet")
    with patch("data_engine.INTRADAY_DIR", tmp_path), \
         patch("data_engine.yahoo_engine.get_intraday", return_value={}) as fetch:
        assert load_or_fetch_intraday_history("STEP4STAMP") is None
    fetch.assert_called_once()
    assert pd.read_parquet(tmp_path / "STEP4STAMP_intraday.parquet").iloc[-1]["Close"] == 10


def test_intraday_history_coalesces_concurrent_fetches_and_awaits(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import pandas as pd
    from data_engine import load_or_fetch_intraday_history

    entered, release = Event(), Event()
    df = pd.DataFrame({"Close": [10]}, index=pd.to_datetime(["2026-10-02 14:00"]))

    def fetch(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return {"STEP4SHARED": df}

    with patch("data_engine.INTRADAY_DIR", tmp_path), \
         patch("data_engine.yahoo_engine.get_intraday", side_effect=fetch) as network:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(load_or_fetch_intraday_history, "STEP4SHARED")
            try:
                assert entered.wait(5)
                second = pool.submit(load_or_fetch_intraday_history, "STEP4SHARED")
                assert not first.done()
            finally:
                release.set()
            assert first.result(timeout=5).equals(df)
            assert second.result(timeout=5).equals(df)
    assert network.call_count == 1


@pytest.mark.parametrize("ticker", ["0P00018XAR.L", "../ESCAPE", "TBILL-123", "IGNORED"])
def test_intraday_history_exclusions_do_not_fetch(tmp_path, ticker):
    from data_engine import load_or_fetch_intraday_history

    with patch("data_engine.INTRADAY_DIR", tmp_path), \
         patch("utils.ignored_tickers_set", return_value={"IGNORED"}), \
         patch("data_engine.yahoo_engine.get_intraday") as fetch:
        assert load_or_fetch_intraday_history(ticker) is None
    fetch.assert_not_called()


def test_intraday_history_rejects_symlink_escape(tmp_path):
    from data_engine import load_or_fetch_intraday_history

    root = tmp_path / "cache"
    root.mkdir()
    outside = tmp_path / "outside.parquet"
    outside.write_text("untouched")
    (root / "STEP4ESCAPE_intraday.parquet").symlink_to(outside)
    with patch("data_engine.INTRADAY_DIR", root), \
         patch("data_engine.yahoo_engine.get_intraday") as fetch:
        assert load_or_fetch_intraday_history("STEP4ESCAPE") is None
    fetch.assert_not_called()
    assert outside.read_text() == "untouched"
