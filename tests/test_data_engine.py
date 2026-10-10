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


# ── nightly FX pair histories ─────────────────────────────────────────────────

def test_in_scope_fx_pairs_cover_account_and_native_currencies_but_not_the_base_or_universe_members():
    from data_engine import DataEngine

    with patch("accounts_engine.load_config", return_value={"ACCOUNT_CURRENCIES": ["GBP", "USD"]}), \
         patch("accounts_engine.native_currencies", return_value={"SAP.DE": "EUR", "VOD.L": "GBp"}):
        pairs = DataEngine.in_scope_fx_pairs(["SAP.DE", "VOD.L"])
    assert pairs == ["EURGBP=X", "USDGBP=X"]


def test_in_scope_fx_pairs_skips_a_pair_already_in_the_universe():
    from data_engine import DataEngine

    with patch("accounts_engine.load_config", return_value={"ACCOUNT_CURRENCIES": ["GBP", "USD"]}), \
         patch("accounts_engine.native_currencies", return_value={}):
        assert DataEngine.in_scope_fx_pairs(["USDGBP=X", "AAPL"]) == []


def test_update_all_data_downloads_fx_pair_histories_but_keeps_them_out_of_the_other_steps():
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    with patch.object(engine, "fetch_market_baseline"), \
         patch.object(engine, "get_all_tickers", return_value=["AAPL"]), \
         patch.object(DataEngine, "in_scope_fx_pairs", return_value=["USDGBP=X"]), \
         patch.object(engine, "bulk_download_historical") as daily, \
         patch.object(engine, "bulk_download_intraday") as intraday, \
         patch.object(engine, "drip_feed_fundamentals") as fundamentals:
        engine.update_all_data()
    daily.assert_called_once_with(["AAPL", "USDGBP=X"])
    intraday.assert_called_once_with(["AAPL"])
    fundamentals.assert_called_once_with(["AAPL"])


# ── market baseline files ─────────────────────────────────────────────────────

def _baseline_frame():
    import pandas as pd

    return pd.DataFrame(
        {"Open": [0.0, 101.0, 102.0], "High": [0.0, 102.0, 103.0], "Low": [0.0, 100.0, 101.0],
         "Close": [100.0, float("nan"), 104.0], "Volume": [0, 0, 0]},
        index=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
    )


def _run_baseline_fetch(tmp_path, frames):
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), \
         patch("data_engine.yahoo_engine.get_price_history", return_value=frames), \
         patch("data_engine.GiltDataService"), \
         patch("time_engine.is_market_open", return_value=False):
        engine.fetch_market_baseline()


def test_market_baseline_is_cleaned_and_written_atomically(tmp_path):
    import pandas as pd

    import data_engine

    with patch("data_engine.write_parquet_atomic", wraps=data_engine.write_parquet_atomic) as writer:
        _run_baseline_fetch(tmp_path, {"^FTSE": _baseline_frame()})
    assert writer.call_args.args[1] == tmp_path / "FTSE_BASELINE.parquet"
    saved = pd.read_parquet(tmp_path / "FTSE_BASELINE.parquet")
    assert saved["Close"].tolist() == [100.0, 104.0]
    assert saved["Open"].iloc[0] == 100.0
    assert not list(tmp_path.glob("*.tmp"))
    assert not (tmp_path / "^FTSE.parquet").exists()


def test_market_baseline_applies_repairs_saved_under_the_real_ticker(tmp_path):
    import pandas as pd

    seen = []

    def repair(ticker, df):
        seen.append(ticker)
        return df.assign(Close=df["Close"] * 2) if ticker == "^GSPC" else df

    with patch("daily_history_writer.apply_saved_repairs", side_effect=repair):
        _run_baseline_fetch(tmp_path, {"^GSPC": _baseline_frame(), "^FTSE": _baseline_frame()})
    assert sorted(seen) == ["^FTSE", "^GSPC"]
    assert pd.read_parquet(tmp_path / "SP500_BASELINE.parquet")["Close"].tolist() == [200.0, 208.0]
    assert pd.read_parquet(tmp_path / "FTSE_BASELINE.parquet")["Close"].tolist() == [100.0, 104.0]


def test_market_baseline_failed_write_keeps_the_previous_file(tmp_path):
    import pandas as pd

    previous = pd.DataFrame({"Close": [1.0, 2.0]}, index=pd.to_datetime(["2026-01-01", "2026-01-02"]))
    previous.to_parquet(tmp_path / "FTSE_BASELINE.parquet")
    with patch.object(pd.DataFrame, "to_parquet", side_effect=OSError("disk full")):
        _run_baseline_fetch(tmp_path, {"^FTSE": _baseline_frame()})
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "FTSE_BASELINE.parquet"), previous)
    assert not list(tmp_path.glob("*.tmp"))


def test_market_baseline_skips_missing_and_empty_downloads(tmp_path):
    import pandas as pd

    _run_baseline_fetch(tmp_path, {"^FTSE": pd.DataFrame(), "^GSPC": None})
    assert list(tmp_path.iterdir()) == []


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
            patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path),
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
            patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path),
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
            patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path),
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
        patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path),
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
        patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path),
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
# intraday_bottom_engine.py then read that row as "previous close" via market_pulse_write.upsert_live_price,
# producing a wildly wrong 24h % change that fights with market_pulse_write.fetch_and_save_pulse's correct
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
        from daily_history_writer import _drop_in_progress_last_bar

        today = datetime.now(timezone.utc).date().isoformat()
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        daily = _ohlcv([yesterday, today], [100.0, 105.0])  # today's bar is still-forming
        live = _ohlcv([f"{today} 09:30", f"{today} 10:00"], [104.0, 105.0])

        result = _drop_in_progress_last_bar(daily, live)

        assert len(result) == 1
        assert result["Close"].iloc[-1] == 100.0

    def test_keeps_last_row_when_daily_predates_live_feed(self):
        from daily_history_writer import _drop_in_progress_last_bar

        daily = _ohlcv(["2026-07-01", "2026-07-02"], [98.0, 100.0])  # genuinely completed close
        live = _ohlcv(["2026-07-06 09:30"], [105.0])

        result = _drop_in_progress_last_bar(daily, live)

        assert len(result) == 2
        assert result["Close"].iloc[-1] == 100.0

    def test_noop_when_no_live_data_available(self):
        import pandas as pd
        from daily_history_writer import _drop_in_progress_last_bar

        daily = _ohlcv(["2026-07-02", "2026-07-06"], [100.0, 105.0])

        assert len(_drop_in_progress_last_bar(daily, None)) == 2
        assert len(_drop_in_progress_last_bar(daily, pd.DataFrame())) == 2

    def test_noop_when_daily_has_fewer_than_two_rows(self):
        from daily_history_writer import _drop_in_progress_last_bar

        daily = _ohlcv(["2026-07-06"], [105.0])
        live = _ohlcv(["2026-07-06 09:30"], [105.0])

        assert len(_drop_in_progress_last_bar(daily, live)) == 1

    def test_keeps_last_row_when_ticker_exchange_confirmed_closed(self):
        """Regression test (found 2026-07-13): passing a ticker resolves its exchange via
        time_engine and consults is_market_open — a closed exchange must keep today's bar even
        though its date matches both the live feed's date and today's real calendar date."""
        from datetime import datetime, timezone
        from unittest.mock import patch as _patch
        from daily_history_writer import _drop_in_progress_last_bar

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
        from daily_history_writer import _drop_in_progress_last_bar

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
        patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path),
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
        patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path),
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
        patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path),
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
        patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path),
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


def test_fetch_and_save_data_drops_rows_without_close(tmp_path):
    import pandas as pd
    from data_engine import DataEngine

    engine = DataEngine.__new__(DataEngine)
    daily_df = pd.DataFrame(
        {"Open": [10.0, 11.0], "High": [10.0, 11.0], "Low": [10.0, 11.0],
         "Close": [10.0, float("nan")], "Volume": [100, 100]},
        index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
    )
    with (
        patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path),
        patch("data_engine.INTRADAY_DIR", tmp_path),
        patch("data_engine.FUNDAMENTALS_DIR", tmp_path),
        patch("data_engine.get_mutual_fund_tickers", return_value=set()),
        patch("data_engine.yahoo_engine.get_intraday", return_value={}),
        patch("data_engine.yahoo_engine.get_price_history", return_value={"ZZNAN": daily_df}),
        patch("data_engine.yahoo_engine.get_ticker_info", return_value={}),
        patch("data_engine.time_engine.is_market_open", return_value=False),
    ):
        assert engine.fetch_and_save_data("ZZNAN") is True

    saved = pd.read_parquet(tmp_path / "ZZNAN.parquet")
    assert saved["Close"].tolist() == [10.0]


def _settled_close_hours_ago(hours):
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def test_cache_only_missing_history_schedules_without_fetch(tmp_path):
    from data_engine import load_or_fetch_daily_history

    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), patch("data_engine.yahoo_engine.get_price_history") as network, patch("cache_refresh_helpers.request_cache_refresh") as refresh:
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
    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), patch("data_engine.yahoo_engine.get_price_history", return_value={}), patch("cache_refresh_helpers.request_cache_refresh") as refresh, \
         patch("time_engine.last_settled_session_close_utc", return_value=_settled_close_hours_ago(1)):
        result = load_or_fetch_daily_history("ZZSTALE", cache_only=True)
        assert result["Close"].iloc[-1] == 100.0
        assert refresh.call_args.args[1]() is None
        pd.testing.assert_frame_equal(pd.read_parquet(path), df)


@pytest.mark.parametrize("ticker", ["TBILL-999", "PENSION-999", "GBP"])
def test_cache_only_history_never_refreshes_excluded_tickers(tmp_path, ticker):
    from data_engine import load_or_fetch_daily_history

    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), patch("cache_refresh_helpers.request_cache_refresh") as refresh:
        assert load_or_fetch_daily_history(ticker, cache_only=True) is None
    refresh.assert_not_called()


@pytest.mark.parametrize("market_open,expected_rows", [(True, 2), (False, 3)])
def test_history_refresh_preserves_completed_bar_rules(tmp_path, market_open, expected_rows):
    from datetime import datetime, timezone
    import pandas as pd
    from daily_history_writer import fetch_daily_history

    today = datetime.now(timezone.utc).date()
    df = pd.DataFrame({"Close": [10.0, 11.0, 12.0]}, index=pd.date_range(end=today, periods=3))
    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), patch("data_engine.yahoo_engine.get_price_history", return_value={"ZZBAR": df}), patch("time_engine.is_market_open", return_value=market_open):
        fetched = fetch_daily_history("ZZBAR")
    assert len(fetched) == expected_rows
    assert len(pd.read_parquet(tmp_path / "ZZBAR.parquet")) == expected_rows


def test_history_refresh_drops_rows_without_close(tmp_path):
    import pandas as pd
    from daily_history_writer import fetch_daily_history

    df = pd.DataFrame(
        {"Close": [10.0, float("nan")], "Volume": [100, 100]},
        index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
    )
    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), \
         patch("data_engine.yahoo_engine.get_price_history", return_value={"ZZNAN": df}), \
         patch("time_engine.is_market_open", return_value=False):
        fetched = fetch_daily_history("ZZNAN")

    saved = pd.read_parquet(tmp_path / "ZZNAN.parquet")
    assert fetched["Close"].tolist() == [10.0]
    pd.testing.assert_frame_equal(saved, fetched)


def test_cache_only_fresh_history_does_not_schedule_refresh(tmp_path):
    import pandas as pd
    from data_engine import load_or_fetch_daily_history

    pd.DataFrame({"Close": [10.0]}, index=pd.to_datetime(["2026-01-01"])).to_parquet(tmp_path / "ZZFRESH.parquet")
    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), patch("cache_refresh_helpers.request_cache_refresh") as refresh:
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
    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), patch("data_engine.yahoo_engine.get_price_history", side_effect=history) as fetch, patch("time_engine.is_market_open", return_value=False), patch("cache_refresh_helpers.request_cache_refresh") as refresh, \
         patch("time_engine.last_settled_session_close_utc", return_value=_settled_close_hours_ago(1)):
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
    with patch("data_engine.HISTORICAL_DIR", cache_root), patch("daily_history_writer.HISTORICAL_DIR", cache_root), patch("cache_refresh_helpers.request_cache_refresh") as refresh:
        assert load_or_fetch_daily_history("ZZLINK", cache_only=True) is None
    refresh.assert_not_called()


def test_history_refresh_rejects_symlink_outside_cache_root(tmp_path):
    from daily_history_writer import fetch_daily_history

    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    outside = tmp_path / "outside.parquet"
    outside.write_bytes(b"unchanged")
    (cache_root / "ZZLINK.parquet").symlink_to(outside)
    with patch("data_engine.HISTORICAL_DIR", cache_root), patch("daily_history_writer.HISTORICAL_DIR", cache_root), patch("data_engine.yahoo_engine.get_price_history") as fetch:
        assert fetch_daily_history("ZZLINK") is None
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


def test_bulk_intraday_download_replaces_files_atomically(tmp_path):
    import pandas as pd
    from data_engine import DataEngine

    frame = pd.DataFrame({"Close": [10.0, float("nan"), 11.0]},
                         index=pd.to_datetime(["2026-10-02 14:00", "2026-10-02 14:05", "2026-10-02 14:10"]))
    engine = DataEngine.__new__(DataEngine)
    with patch("data_engine.INTRADAY_DIR", tmp_path), \
         patch("data_engine.get_mutual_fund_tickers", return_value=set()), \
         patch("data_engine.yahoo_engine.get_intraday", return_value={"AAA": frame}):
        engine.bulk_download_intraday(["AAA"])
        assert pd.read_parquet(tmp_path / "AAA_intraday.parquet")["Close"].tolist() == [10.0, 11.0]
        with patch.object(pd.DataFrame, "to_parquet", side_effect=OSError("disk full")):
            engine.bulk_download_intraday(["AAA"])

    assert pd.read_parquet(tmp_path / "AAA_intraday.parquet")["Close"].tolist() == [10.0, 11.0]
    assert [p.name for p in tmp_path.iterdir()] == ["AAA_intraday.parquet"]


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


@pytest.mark.parametrize("outside_directory", ["outside", "cache-sibling"])
def test_intraday_history_rejects_symlink_escape(tmp_path, outside_directory):
    from data_engine import load_or_fetch_intraday_history

    root = tmp_path / "cache"
    root.mkdir()
    outside_root = tmp_path / outside_directory
    outside_root.mkdir()
    outside = outside_root / "outside.parquet"
    outside.write_text("untouched")
    (root / "STEP4ESCAPE_intraday.parquet").symlink_to(outside)
    with patch("data_engine.INTRADAY_DIR", root), \
         patch("data_engine.yahoo_engine.get_intraday") as fetch:
        assert load_or_fetch_intraday_history("STEP4ESCAPE") is None
    fetch.assert_not_called()
    assert outside.read_text() == "untouched"


@pytest.mark.parametrize("written_after_close,expected_refreshes", [(True, 0), (False, 1)])
def test_cache_only_history_refreshes_only_when_latest_session_missing(tmp_path, written_after_close, expected_refreshes):
    import os
    import pandas as pd
    from data_engine import load_or_fetch_daily_history

    path = tmp_path / "ZZSESSION.parquet"
    pd.DataFrame({"Close": [10.0]}, index=pd.to_datetime(["2026-01-01"])).to_parquet(path)
    settled = _settled_close_hours_ago(30)
    mtime = settled.timestamp() + (60 if written_after_close else -60)
    os.utime(path, (mtime, mtime))
    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), \
         patch("time_engine.last_settled_session_close_utc", return_value=settled), \
         patch("cache_refresh_helpers.request_cache_refresh") as refresh:
        assert load_or_fetch_daily_history("ZZSESSION", cache_only=True)["Close"].iloc[-1] == 10.0
    assert refresh.call_count == expected_refreshes


def test_navigation_history_refresh_applies_saved_repairs_and_bar_cleaning(tmp_path):
    import pandas as pd
    from daily_history_writer import fetch_daily_history

    index = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
    downloaded = pd.DataFrame({
        "Open": [10.0, 0.0, 120.0], "High": [11.0, 0.0, 130.0], "Low": [9.0, 0.0, 110.0],
        "Close": [10.5, 11.5, 125.0], "Volume": [100, 100, 100],
    }, index=index)
    repairs = {"ZZREPAIR": {
        "2026-01-07": {"Open": 12.0, "High": 13.0, "Low": 11.0, "Close": 12.5, "Volume": 90},
        "_removed_dates": ["2026-01-05"],
    }}
    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), \
         patch("data_engine.yahoo_engine.get_price_history", return_value={"ZZREPAIR": downloaded}), \
         patch("time_engine.is_market_open", return_value=False), \
         patch("price_repair_engine._saved_repairs", return_value=repairs):
        fetched = fetch_daily_history("ZZREPAIR", force_refresh=True)

    saved = pd.read_parquet(tmp_path / "ZZREPAIR.parquet")
    pd.testing.assert_frame_equal(saved, fetched)
    assert list(saved.index) == list(index[1:])
    assert saved.loc["2026-01-06", ["Open", "High", "Low"]].tolist() == [11.5, 11.5, 11.5]
    assert saved.loc["2026-01-07", "Close"] == 12.5


def test_awaited_refresh_is_not_starved_by_full_background_queue():
    from threading import Event
    import cache_refresh_helpers as refresh_helpers

    release = Event()
    def stalled():
        release.wait(10)
        return True
    background = []
    try:
        for i in range(100):
            future = refresh_helpers._submit(f"zz-saturate:{i}", stalled, force=False, awaited=False)
            if future is None:
                break
            background.append(future)
        assert future is None
        awaited = refresh_helpers.submit_cache_refresh("zz-saturate-awaited", lambda: 1.25)
        assert awaited.result(timeout=5) == 1.25
        assert not release.is_set()
    finally:
        release.set()
        for future in background:
            future.result(timeout=10)


def test_awaited_refresh_promotes_queued_background_job():
    from threading import Event
    import cache_refresh_helpers as refresh_helpers

    release = Event()
    ran = []
    def stalled():
        release.wait(10)
        return True
    blockers = [refresh_helpers._submit(f"zz-block:{i}", stalled, force=False, awaited=False) for i in range(2)]
    queued = refresh_helpers._submit("zz-promote", lambda: ran.append("background") or True, force=False, awaited=False)
    try:
        promoted = refresh_helpers.submit_cache_refresh("zz-promote", lambda: ran.append("awaited") or 2.0)
        assert promoted.result(timeout=5) == 2.0
        assert queued.cancelled()
        assert ran == ["awaited"]
    finally:
        release.set()
        for future in blockers:
            future.result(timeout=10)


# ── universe history refresh ──────────────────────────────────────────────────

def _universe_engine():
    from data_engine import DataEngine

    return DataEngine.__new__(DataEngine)


def test_universe_history_pool_is_scored_tickers_outside_nightly_ignored_and_synthetic():
    engine = _universe_engine()
    with patch("data_engine.load_config", return_value={"IGNORED_TICKERS": ["DEAD"]}), \
         patch.object(engine, "get_all_tickers", return_value=["AAPL", "MSFT"]), \
         patch("data_engine.get_stock_signal_tickers",
               return_value=["aapl", "NVDA", "dead", "TBILL-7", "PENSION-2", "NVDA", "VOD.L"]):
        assert engine.get_universe_history_pool() == ["NVDA", "VOD.L"]


def test_universe_history_is_downloaded_in_batches_without_live_bars(tmp_path):
    import pandas as pd

    engine = _universe_engine()
    frame = _ohlcv(["2026-07-01", "2026-07-02"], [10.0, 11.0])

    def fake_history(batch, **kwargs):
        assert kwargs["force_refresh"] is True
        return {t: frame.copy() for t in batch if t != "GONE"}

    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), patch("data_engine.UNIVERSE_HISTORY_BATCH", 2), \
         patch("data_engine.time_engine.is_market_open", return_value=False), \
         patch("data_engine.yahoo_engine.get_price_history", side_effect=fake_history) as history, \
         patch("data_engine.yahoo_engine.get_intraday") as intraday:
        engine.bulk_download_historical([], universe=["AAA", "BBB", "GONE", "CCC", "DDD"])

    assert [c.args[0] for c in history.call_args_list] == [["AAA", "BBB"], ["GONE", "CCC"], ["DDD"]]
    intraday.assert_not_called()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["AAA.parquet", "BBB.parquet", "CCC.parquet", "DDD.parquet"]
    assert pd.read_parquet(tmp_path / "AAA.parquet")["Close"].tolist() == [10.0, 11.0]


def test_universe_history_runs_the_shared_cleaning_path(tmp_path):
    import pandas as pd
    from datetime import datetime, timedelta, timezone

    engine = _universe_engine()
    today = datetime.now(timezone.utc).date()
    frame = _ohlcv([(today - timedelta(days=1)).isoformat(), today.isoformat()], [10.0, 11.0])

    for market_open, expected in ((True, [10.0]), (False, [10.0, 11.0])):
        with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), \
             patch("data_engine.time_engine.is_market_open", return_value=market_open), \
             patch("data_engine.yahoo_engine.get_price_history", return_value={"AAA": frame.copy()}):
            engine.bulk_download_historical([], universe=["AAA"])
        assert pd.read_parquet(tmp_path / "AAA.parquet")["Close"].tolist() == expected


def test_universe_history_failed_batch_does_not_stop_later_batches(tmp_path):
    engine = _universe_engine()
    frame = _ohlcv(["2026-07-01", "2026-07-02"], [10.0, 11.0])
    calls = []

    def fake_history(batch, **kwargs):
        calls.append(batch)
        if len(calls) == 1:
            raise RuntimeError("429")
        return {t: frame.copy() for t in batch}

    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), patch("data_engine.UNIVERSE_HISTORY_BATCH", 1), \
         patch("data_engine.time_engine.is_market_open", return_value=False), \
         patch("data_engine.yahoo_engine.get_price_history", side_effect=fake_history):
        engine.bulk_download_historical([], universe=["AAA", "BBB"])

    assert [p.name for p in tmp_path.iterdir()] == ["BBB.parquet"]


def test_read_only_history_returns_stale_file_without_queueing_a_refresh(tmp_path):
    import os
    import time
    import pandas as pd
    from data_engine import load_or_fetch_daily_history

    path = tmp_path / "ZZSTALE.parquet"
    pd.DataFrame({"Close": [100.0]}, index=pd.to_datetime(["2026-01-01"])).to_parquet(path)
    os.utime(path, (time.time() - 86400, time.time() - 86400))
    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), \
         patch("cache_refresh_helpers.request_cache_refresh") as background, \
         patch("cache_refresh_helpers.submit_cache_refresh") as awaited, \
         patch("time_engine.last_settled_session_close_utc", return_value=_settled_close_hours_ago(1)):
        assert load_or_fetch_daily_history("ZZSTALE", cache_only=True, read_only=True)["Close"].iloc[-1] == 100.0
        assert load_or_fetch_daily_history("ZZMISSING", read_only=True) is None
    background.assert_not_called()
    awaited.assert_not_called()


# ── history exchange resolution ───────────────────────────────────────────────

def _history_ending_today(rows=3):
    import pandas as pd
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date()
    return pd.DataFrame({"Close": [10.0 + i for i in range(rows)]}, index=pd.date_range(end=today, periods=rows))


@pytest.mark.parametrize("ticker,session_open,expected_rows", [
    ("^FTSE", "LSE", 2),
    ("^FTSE", "NYSE", 3),
    ("^N225", "TSE", 2),
    ("^N225", "NYSE", 3),
    ("AAPL", "NYSE", 2),
    ("VOD.L", "LSE", 2),
])
def test_in_progress_trim_follows_the_tickers_own_exchange(ticker, session_open, expected_rows):
    from daily_history_writer import prepare_daily_history

    with patch("time_engine.is_market_open", side_effect=lambda exchange: exchange == session_open), \
         patch("daily_history_writer.apply_saved_repairs", side_effect=lambda t, df: df):
        assert len(prepare_daily_history(ticker, _history_ending_today(), None)) == expected_rows


def test_in_progress_trim_with_live_feed_follows_the_tickers_own_exchange():
    import pandas as pd
    from daily_history_writer import prepare_daily_history

    daily = _history_ending_today()
    live = pd.DataFrame({"Close": [1.0]}, index=pd.DatetimeIndex([daily.index[-1]]))
    with patch("time_engine.is_market_open", side_effect=lambda exchange: exchange == "TSE"), \
         patch("daily_history_writer.apply_saved_repairs", side_effect=lambda t, df: df):
        assert len(prepare_daily_history("^N225", daily, live)) == 2
        assert len(prepare_daily_history("^FTSE", daily, live)) == 3


def test_plain_ticker_is_not_judged_against_the_home_exchange():
    from daily_history_writer import prepare_daily_history

    with patch("time_engine._load_config", return_value={"HOME_EXCHANGE": "LSE"}), \
         patch("time_engine.is_market_open", side_effect=lambda exchange: exchange == "LSE"), \
         patch("daily_history_writer.apply_saved_repairs", side_effect=lambda t, df: df):
        assert len(prepare_daily_history("AAPL", _history_ending_today(), None)) == 3


def test_history_staleness_check_uses_the_registry_exchange(tmp_path):
    from data_engine import daily_history_cache_revision

    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), \
         patch("time_engine.last_settled_session_close_utc", return_value=_settled_close_hours_ago(1)) as settled, \
         patch("cache_refresh_helpers.request_cache_refresh"):
        daily_history_cache_revision("^N225", refresh_stale=True)
        daily_history_cache_revision("AAPL", refresh_stale=True)
    assert [call.args[0] for call in settled.call_args_list] == ["TSE", "NYSE"]


# ── Yahoo-reported sessions (FX, futures, rates) ──────────────────────────────

@pytest.fixture
def reported_sessions():
    import daily_history_writer
    import market_session_helpers
    from database import get_connection

    market_session_helpers._instrument_sessions.clear()
    daily_history_writer._session_lookup_failed.clear()
    yield market_session_helpers._instrument_sessions
    market_session_helpers._instrument_sessions.clear()
    daily_history_writer._session_lookup_failed.clear()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM instrument_sessions")
        conn.commit()
    finally:
        conn.close()


def _stored_session(tz, end, updated_at=None):
    import time

    return {"tz": tz, "regular_end": end, "updated_at": time.time() if updated_at is None else updated_at}


def _frame_with_last_bar(last_bar):
    import pandas as pd

    return pd.DataFrame({"Close": [10.0, 11.0, 12.0]}, index=pd.date_range(end=last_bar, periods=3))


def _prepare_at(now, ticker, frame, *, exchange_open=False):
    from daily_history_writer import prepare_daily_history

    with patch("time_engine.datetime", _fake_datetime_for_data_engine(now)), \
         patch("time_engine.is_market_open", return_value=exchange_open), \
         patch("daily_history_writer.apply_saved_repairs", side_effect=lambda t, df: df):
        return prepare_daily_history(ticker, frame, None)


def _fake_datetime_for_data_engine(fixed_utc):
    from datetime import datetime

    class _Fake(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc

        @classmethod
        def combine(cls, *a, **kw):
            return datetime.combine(*a, **kw)

    return _Fake


def test_fx_bar_is_trimmed_until_the_london_day_ends_even_after_nyse_closed(reported_sessions):
    from datetime import datetime, timezone

    reported_sessions["GBPUSD=X"] = _stored_session("Europe/London", "23:59")
    nightly_run = datetime(2026, 10, 9, 21, 33, tzinfo=timezone.utc)
    assert len(_prepare_at(nightly_run, "GBPUSD=X", _frame_with_last_bar("2026-10-09"))) == 2
    after_midnight = datetime(2026, 10, 9, 23, 30, tzinfo=timezone.utc)
    assert len(_prepare_at(after_midnight, "GBPUSD=X", _frame_with_last_bar("2026-10-09"))) == 3


def test_futures_bar_follows_the_new_york_day_not_the_utc_date(reported_sessions):
    from datetime import datetime, timezone

    reported_sessions["GC=F"] = _stored_session("America/New_York", "23:59")
    evening = datetime(2026, 10, 10, 2, 0, tzinfo=timezone.utc)
    assert len(_prepare_at(evening, "GC=F", _frame_with_last_bar("2026-10-09"))) == 2


def test_rate_bar_is_kept_once_the_cboe_window_has_ended(reported_sessions):
    from datetime import datetime, timezone

    reported_sessions["^TNX"] = _stored_session("America/Chicago", "14:00")
    assert len(_prepare_at(datetime(2026, 10, 9, 18, 30, tzinfo=timezone.utc), "^TNX", _frame_with_last_bar("2026-10-09"))) == 2
    assert len(_prepare_at(datetime(2026, 10, 9, 19, 30, tzinfo=timezone.utc), "^TNX", _frame_with_last_bar("2026-10-09"))) == 3


def test_unregistered_fx_pair_uses_its_reported_session(reported_sessions):
    from datetime import datetime, timezone

    reported_sessions["EURGBP=X"] = _stored_session("Europe/London", "23:59")
    assert len(_prepare_at(datetime(2026, 10, 9, 21, 33, tzinfo=timezone.utc), "EURGBP=X", _frame_with_last_bar("2026-10-09"))) == 2


@pytest.mark.parametrize("exchange_open,expected_rows", [(False, 3), (True, 2)])
def test_unknown_session_falls_back_to_the_exchange_judgement(reported_sessions, exchange_open, expected_rows):
    from daily_history_writer import prepare_daily_history

    with patch("time_engine.is_market_open", return_value=exchange_open), \
         patch("daily_history_writer.apply_saved_repairs", side_effect=lambda t, df: df):
        assert len(prepare_daily_history("GC=F", _history_ending_today(), None)) == expected_rows


def test_registry_exchange_wins_over_a_stored_session(reported_sessions):
    from datetime import datetime, timezone

    reported_sessions["^FTSE"] = _stored_session("Asia/Tokyo", "23:59")
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    with patch("time_engine.is_market_open", side_effect=lambda exchange: exchange == "LSE"), \
         patch("time_engine.datetime", _fake_datetime_for_data_engine(now)), \
         patch("daily_history_writer.apply_saved_repairs", side_effect=lambda t, df: df):
        from daily_history_writer import prepare_daily_history

        assert len(prepare_daily_history("^FTSE", _frame_with_last_bar("2026-10-09"), None)) == 3


def test_history_staleness_uses_the_reported_window_end(tmp_path, reported_sessions):
    import os
    import time

    import pandas as pd
    from data_engine import daily_history_cache_revision

    reported_sessions["GBPUSD=X"] = _stored_session("Europe/London", "23:59")
    path = tmp_path / "GBPUSD=X.parquet"
    pd.DataFrame({"Close": [1.3]}, index=pd.to_datetime(["2026-10-08"])).to_parquet(path)
    os.utime(path, (time.time() - 7200, time.time() - 7200))
    with patch("data_engine.HISTORICAL_DIR", tmp_path), patch("daily_history_writer.HISTORICAL_DIR", tmp_path), \
         patch("time_engine.last_reported_session_end_utc", return_value=_settled_close_hours_ago(1)) as window, \
         patch("time_engine.last_settled_session_close_utc") as exchange_close, \
         patch("cache_refresh_helpers.request_cache_refresh") as refresh:
        daily_history_cache_revision("GBPUSD=X", refresh_stale=True)
    window.assert_called_once_with("Europe/London", "23:59")
    exchange_close.assert_not_called()
    refresh.assert_called_once()


def test_session_is_learned_once_from_yahoo_and_persisted(reported_sessions):
    import daily_history_writer
    import market_session_helpers
    from database import get_instrument_sessions

    shape = {"tz": "America/Chicago", "regular_end": 1791572400}
    with patch("daily_history_writer.yahoo_engine.get_session_shape", return_value=shape) as lookup:
        daily_history_writer._learn_reported_session("^TNX")
        daily_history_writer._learn_reported_session("^TNX")
    lookup.assert_called_once_with("^TNX")
    assert market_session_helpers.reported_session("^TNX") == ("America/Chicago", "14:00")
    assert get_instrument_sessions()["^TNX"]["regular_end"] == "14:00"
    reported_sessions.clear()
    assert market_session_helpers.reported_session("^TNX") == ("America/Chicago", "14:00")


def test_failed_session_lookup_is_not_retried_within_the_retry_window(reported_sessions):
    import daily_history_writer
    import market_session_helpers

    with patch("daily_history_writer.yahoo_engine.get_session_shape", return_value=None) as lookup:
        daily_history_writer._learn_reported_session("GC=F")
        daily_history_writer._learn_reported_session("GC=F")
    lookup.assert_called_once_with("GC=F")
    assert market_session_helpers.reported_session("GC=F") is None


def test_stored_session_survives_a_failed_refresh(reported_sessions):
    import daily_history_writer
    import market_session_helpers

    old = _stored_session("America/Chicago", "14:00", updated_at=0.0)
    reported_sessions["^TNX"] = old
    with patch("daily_history_writer.yahoo_engine.get_session_shape", return_value=None) as lookup:
        daily_history_writer._learn_reported_session("^TNX")
    lookup.assert_called_once()
    assert market_session_helpers.reported_session("^TNX") == ("America/Chicago", "14:00")


@pytest.mark.parametrize("ticker", ["AAPL", "VOD.L", "^FTSE", "^GSPC", "UK10YG", "TBILL-606"])
def test_session_lookup_is_skipped_for_exchange_listed_and_unfetchable_tickers(reported_sessions, ticker):
    import daily_history_writer

    with patch("daily_history_writer.yahoo_engine.get_session_shape") as lookup:
        daily_history_writer._learn_reported_session(ticker)
    lookup.assert_not_called()


@pytest.mark.parametrize("ticker,expected", [
    ("GBPUSD=X", True), ("DX-Y.NYB", True), ("^TNX", True), ("ES=F", True), ("EURGBP=X", True), ("^SOX", True),
    ("^FTSE", False), ("^N225", False), ("AAPL", False), ("VOD.L", False),
])
def test_reported_session_applies_only_to_instruments_without_an_exchange_calendar(ticker, expected):
    from market_session_helpers import has_reported_session

    assert has_reported_session(ticker) is expected
