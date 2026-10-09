from unittest.mock import MagicMock, patch
import sqlite3
import json

import pandas as pd
import pytest

from database import get_connection
from price_repair_engine import PriceRepairError, apply_saved_repairs, check_daily_bar, refresh_downstream, repair_daily_bar, remove_daily_bar, _fingerprint, _history_path


@pytest.fixture(autouse=True)
def stub_downstream_refresh(monkeypatch):
    monkeypatch.setattr("price_repair_engine.refresh_downstream",
                        lambda *args, **kwargs: {"history_rows_rebuilt": 0, "portfolio_caches": "not_in_scope"})


def _history(path):
    df = pd.DataFrame(
        {"Open": [20.0, 4331.41], "High": [20.5, 4331.41],
         "Low": [19.9, 4331.41], "Close": [20.4, 4331.41],
         "Volume": [1000, 0]},
        index=pd.to_datetime(["2026-10-02", "2026-10-05"]),
    )
    df.to_parquet(path)
    return df


def test_history_path_rejects_path_traversal_ticker():
    try:
        _history_path("../../outside")
    except PriceRepairError as exc:
        assert "Invalid ticker" in str(exc)
    else:
        assert False


def test_check_and_manual_repair_updates_stored_and_direct_prices(tmp_path):
    path = tmp_path / "LCJP.L.parquet"
    _history(path)
    conn = get_connection()
    try:
        conn.execute("INSERT INTO quant_signals (ticker,date,close_price,volume) VALUES (?,?,?,?)",
                     ("LCJP.L", "2026-10-05", 4331.41, 0))
        conn.execute("INSERT INTO score_history (ticker,date,close_price,score,signal) VALUES (?,?,?,?,?)",
                     ("LCJP.L", "2026-10-05", 4331.41, 65, "STRONG BUY"))
        conn.commit()
    finally:
        conn.close()

    with patch("price_repair_engine.HISTORICAL_DIR", tmp_path), \
         patch("price_repair_engine.REPAIRS_PATH", tmp_path / "price_repairs.json"), \
         patch("price_repair_engine.yahoo_engine.get_price_history", return_value={}):
        check = check_daily_bar("LCJP.L", "2026-10-05")
        assert check["stored"]["Close"] == 4331.41
        assert check["yahoo"] is None
        assert not check["yahoo_usable"]
        assert "no bar" in check["yahoo_note"]
        assert check["previous"]["Close"] == 20.4
        with patch("price_repair_engine.QuantEngine.analyze_ticker"):
            repair_daily_bar("LCJP.L", "2026-10-05", check["fingerprint"],
                             {"Open": 20.7, "High": 20.9, "Low": 20.6, "Close": 20.78, "Volume": 34950})
        assert pd.read_parquet(path).iloc[-1]["Close"] == 20.78
        downloaded = _history(tmp_path / "downloaded.parquet")
        reapplied = apply_saved_repairs("LCJP.L", downloaded)
        assert reapplied.iloc[-1]["Close"] == 20.78
        assert reapplied.iloc[-1]["Volume"] == 34950
        conn = get_connection()
        try:
            assert conn.execute("SELECT close_price FROM quant_signals WHERE ticker='LCJP.L' AND date='2026-10-05'").fetchone()[0] == 20.78
            assert conn.execute("SELECT close_price FROM score_history WHERE ticker='LCJP.L' AND date='2026-10-05'").fetchone()[0] == 20.78
        finally:
            conn.close()


def test_repair_rejects_stale_check_and_invalid_ohlcv(tmp_path):
    path = tmp_path / "LCJP.L.parquet"
    _history(path)
    replacement = {"Open": 20.7, "High": 20.9, "Low": 20.6, "Close": 20.78, "Volume": 34950}
    with patch("price_repair_engine.HISTORICAL_DIR", tmp_path), \
         patch("price_repair_engine.REPAIRS_PATH", tmp_path / "price_repairs.json"):
        try:
            repair_daily_bar("LCJP.L", "2026-10-05", "0" * 64, replacement)
        except PriceRepairError as exc:
            assert "changed since Check" in str(exc)
        else:
            assert False
        replacement["High"] = 20.0
        try:
            repair_daily_bar("LCJP.L", "2026-10-05", "0" * 64, replacement)
        except PriceRepairError as exc:
            assert "High and low" in str(exc)
        else:
            assert False
        assert pd.read_parquet(path).iloc[-1]["Close"] == 4331.41


def test_manual_repair_rejects_wild_neighbor_outlier(tmp_path):
    path = tmp_path / "LCJP.L.parquet"
    _history(path)
    with patch("price_repair_engine.HISTORICAL_DIR", tmp_path), \
         patch("price_repair_engine.REPAIRS_PATH", tmp_path / "price_repairs.json"):
        fingerprint = _fingerprint(str(path))
        try:
            repair_daily_bar("LCJP.L", "2026-10-05", fingerprint,
                             {"Open": 4331.41, "High": 4331.41, "Low": 4331.41, "Close": 4331.41, "Volume": 0})
        except PriceRepairError as exc:
            assert "10 times" in str(exc)
        else:
            assert False
        assert pd.read_parquet(path).iloc[-1]["Close"] == 4331.41


def test_remove_interior_bar_clears_direct_records_and_saved_override(tmp_path):
    path = tmp_path / "LCJP.L.parquet"
    pd.DataFrame(
        {"Open": [20, 21, 22], "High": [20.5, 21.5, 22.5], "Low": [19.5, 20.5, 21.5],
         "Close": [20, 21, 22], "Volume": [100, 0, 100]},
        index=pd.to_datetime(["2026-10-02", "2026-10-05", "2026-10-06"]),
    ).to_parquet(path)
    conn = get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO quant_signals (ticker,date,close_price,volume) VALUES (?,?,?,?)", ("LCJP.L", "2026-10-05", 21, 0))
        conn.execute("INSERT OR REPLACE INTO score_history (ticker,date,close_price,score,signal) VALUES (?,?,?,?,?)", ("LCJP.L", "2026-10-05", 21, 50, "HOLD"))
        conn.commit()
    finally:
        conn.close()
    repairs = tmp_path / "price_repairs.json"
    repairs.write_text('{"LCJP.L":{"2026-10-05":{"Open":21,"High":21,"Low":21,"Close":21,"Volume":0}}}')
    with patch("price_repair_engine.HISTORICAL_DIR", tmp_path), patch("price_repair_engine.REPAIRS_PATH", repairs), \
         patch("price_repair_engine.QuantEngine.analyze_ticker"):
        remove_daily_bar("LCJP.L", "2026-10-05", _fingerprint(str(path)))
        assert list(pd.read_parquet(path).index.strftime("%Y-%m-%d")) == ["2026-10-02", "2026-10-06"]
        assert json.loads(repairs.read_text())["LCJP.L"]["_removed_dates"] == ["2026-10-05"]
        downloaded = pd.DataFrame(
            {"Open": [20, 4331.41, 22], "High": [20.5, 4331.41, 22.5],
             "Low": [19.5, 4331.41, 21.5], "Close": [20, 4331.41, 22], "Volume": [100, 0, 100]},
            index=pd.to_datetime(["2026-10-02", "2026-10-05", "2026-10-06"]),
        )
        reapplied = apply_saved_repairs("LCJP.L", downloaded)
        assert list(reapplied.index.strftime("%Y-%m-%d")) == ["2026-10-02", "2026-10-06"]
    conn = get_connection()
    try:
        assert conn.execute("SELECT 1 FROM quant_signals WHERE ticker='LCJP.L' AND date='2026-10-05'").fetchone() is None
        assert conn.execute("SELECT 1 FROM score_history WHERE ticker='LCJP.L' AND date='2026-10-05'").fetchone() is None
    finally:
        conn.close()


def test_remove_latest_bar_is_supported_and_updates_current_price(tmp_path):
    ticker = "REMOVE_LATEST_TEST"
    path = tmp_path / f"{ticker}.parquet"
    pd.DataFrame(
        {"Open": [20, 4331.41], "High": [20.5, 4331.41], "Low": [19.5, 4331.41],
         "Close": [20, 4331.41], "Volume": [100, 0]},
        index=pd.to_datetime(["2026-10-02", "2026-10-05"]),
    ).to_parquet(path)
    conn = get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO stock_signals (ticker,current_price) VALUES (?,?)", (ticker, 4331.41))
        conn.commit()
    finally:
        conn.close()
    with patch("price_repair_engine.HISTORICAL_DIR", tmp_path), \
         patch("price_repair_engine.REPAIRS_PATH", tmp_path / "price_repairs.json"), \
         patch("price_repair_engine.QuantEngine.analyze_ticker"):
        remove_daily_bar(ticker, "2026-10-05", _fingerprint(str(path)))
        remaining = pd.read_parquet(path)
        assert list(remaining.index.strftime("%Y-%m-%d")) == ["2026-10-02"]
        assert remaining.iloc[-1]["Close"] == 20
        reapplied = apply_saved_repairs(ticker, pd.DataFrame(
            {"Open": [20, 4331.41], "High": [20.5, 4331.41], "Low": [19.5, 4331.41],
             "Close": [20, 4331.41], "Volume": [100, 0]},
            index=pd.to_datetime(["2026-10-02", "2026-10-05"]),
        ))
        assert list(reapplied.index.strftime("%Y-%m-%d")) == ["2026-10-02"]
    conn = get_connection()
    try:
        assert conn.execute("SELECT current_price FROM stock_signals WHERE ticker=?", (ticker,)).fetchone()[0] == 20
        conn.execute("DELETE FROM stock_signals WHERE ticker=?", (ticker,))
        conn.commit()
    finally:
        conn.close()


def test_check_flags_yahoo_bar_that_matches_extreme_zero_volume_bar(tmp_path):
    path = tmp_path / "LCJP.L.parquet"
    history = _history(path)
    with patch("price_repair_engine.HISTORICAL_DIR", tmp_path), \
         patch("price_repair_engine.REPAIRS_PATH", tmp_path / "price_repairs.json"), \
         patch("price_repair_engine.yahoo_engine.get_price_history", return_value={
             "LCJP.L": history,
         }):
        check = check_daily_bar("LCJP.L", "2026-10-05")
    assert check["yahoo"] == check["stored"]
    assert not check["yahoo_usable"]
    assert "over 10 times" in check["yahoo_note"]


def test_price_repair_api_check_and_rejects_stale_write(tmp_path):
    from api_routes import DailyBarRepairRequest, api_price_repair_apply, api_price_repair_check
    from fastapi import HTTPException

    path = tmp_path / "LCJP.L.parquet"
    _history(path)
    with patch("price_repair_engine.HISTORICAL_DIR", tmp_path), \
         patch("price_repair_engine.REPAIRS_PATH", tmp_path / "price_repairs.json"), \
         patch("price_repair_engine.yahoo_engine.get_price_history", return_value={}):
        check = api_price_repair_check("LCJP.L")["check"]
        assert check["date"] == "2026-10-05"
        assert check["yahoo"] is None
        request = DailyBarRepairRequest(**{
            "ticker": "LCJP.L", "date": check["date"], "fingerprint": "0" * 64,
            "source": "manual", "manual_bar": {
                "Open": 20.7, "High": 20.9, "Low": 20.6, "Close": 20.78, "Volume": 34950,
            },
        })
        try:
            api_price_repair_apply(request)
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            assert False
        assert pd.read_parquet(path).iloc[-1]["Close"] == 4331.41


def test_repair_rolls_back_file_and_saved_override_on_database_failure(tmp_path):
    path = tmp_path / "LCJP.L.parquet"
    _history(path)
    conn = get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO quant_signals (ticker,date,close_price,volume) VALUES (?,?,?,?)",
                     ("LCJP.L", "2026-10-05", 4331.41, 0))
        conn.execute("CREATE TRIGGER fail_price_repair BEFORE UPDATE ON quant_signals "
                     "WHEN NEW.ticker='LCJP.L' BEGIN SELECT RAISE(ABORT, 'injected'); END")
        conn.commit()
    finally:
        conn.close()

    try:
        with patch("price_repair_engine.HISTORICAL_DIR", tmp_path), \
             patch("price_repair_engine.REPAIRS_PATH", tmp_path / "price_repairs.json"), \
             patch("price_repair_engine.yahoo_engine.get_price_history", return_value={}):
            check = check_daily_bar("LCJP.L", "2026-10-05")
            try:
                repair_daily_bar("LCJP.L", "2026-10-05", check["fingerprint"],
                                 {"Open": 20.7, "High": 20.9, "Low": 20.6, "Close": 20.78, "Volume": 34950})
            except sqlite3.DatabaseError:
                pass
            else:
                assert False
            assert pd.read_parquet(path).iloc[-1]["Close"] == 4331.41
            assert not (tmp_path / "price_repairs.json").exists()
    finally:
        conn = get_connection()
        try:
            conn.execute("DROP TRIGGER fail_price_repair")
            conn.commit()
        finally:
            conn.close()


def test_repair_passes_repaired_frame_and_date_to_downstream_refresh(tmp_path, monkeypatch):
    path = tmp_path / "LCJP.L.parquet"
    _history(path)
    calls = []

    def record(ticker, from_date, history):
        calls.append((ticker, from_date, float(history.iloc[-1]["Close"])))
        return {"history_rows_rebuilt": 3, "portfolio_caches": "queued"}

    monkeypatch.setattr("price_repair_engine.refresh_downstream", record)
    with patch("price_repair_engine.HISTORICAL_DIR", tmp_path), \
         patch("price_repair_engine.REPAIRS_PATH", tmp_path / "price_repairs.json"), \
         patch("price_repair_engine.QuantEngine.analyze_ticker"):
        result = repair_daily_bar("LCJP.L", "2026-10-05", _fingerprint(str(path)),
                                  {"Open": 20.7, "High": 20.9, "Low": 20.6, "Close": 20.78, "Volume": 34950})
    assert calls == [("LCJP.L", "2026-10-05", 20.78)]
    assert result["downstream"] == {"history_rows_rebuilt": 3, "portfolio_caches": "queued"}


def test_remove_passes_remaining_frame_to_downstream_refresh(tmp_path, monkeypatch):
    path = tmp_path / "LCJP.L.parquet"
    pd.DataFrame(
        {"Open": [20, 21, 22], "High": [20.5, 21.5, 22.5], "Low": [19.5, 20.5, 21.5],
         "Close": [20, 21, 22], "Volume": [100, 0, 100]},
        index=pd.to_datetime(["2026-10-02", "2026-10-05", "2026-10-06"]),
    ).to_parquet(path)
    calls = []
    monkeypatch.setattr("price_repair_engine.refresh_downstream",
                        lambda ticker, from_date, history: calls.append((ticker, from_date, len(history))) or {})
    with patch("price_repair_engine.HISTORICAL_DIR", tmp_path), \
         patch("price_repair_engine.REPAIRS_PATH", tmp_path / "price_repairs.json"), \
         patch("price_repair_engine.QuantEngine.analyze_ticker"):
        remove_daily_bar("LCJP.L", "2026-10-05", _fingerprint(str(path)))
    assert calls == [("LCJP.L", "2026-10-05", 2)]


class TestRefreshDownstream:

    @staticmethod
    def _history_frame():
        return pd.DataFrame({"Close": [1.0]}, index=pd.to_datetime(["2026-10-05"]))

    def test_in_scope_ticker_rebuilds_history_and_queues_portfolio_caches(self):
        with patch("ai_prediction_engine.rebuild_quant_history", return_value=42) as rebuild, \
             patch("db_helpers.get_portfolio_watchlist_tickers", return_value=["LCJP.L"]), \
             patch("price_repair_engine.request_cache_refresh", return_value=MagicMock()) as queue:
            result = refresh_downstream("LCJP.L", "2026-10-05", self._history_frame())
        assert result == {"history_rows_rebuilt": 42, "portfolio_caches": "queued"}
        assert rebuild.call_args.args[0] == "LCJP.L"
        assert rebuild.call_args.args[2] == "2026-10-05"
        assert queue.call_args.args[0] == "price-repair:LCJP.L"

    def test_ticker_outside_portfolio_and_watchlist_queues_nothing(self):
        with patch("ai_prediction_engine.rebuild_quant_history", return_value=5), \
             patch("db_helpers.get_portfolio_watchlist_tickers", return_value=["AAPL"]), \
             patch("price_repair_engine.request_cache_refresh") as queue:
            result = refresh_downstream("LCJP.L", "2026-10-05", self._history_frame())
        assert result == {"history_rows_rebuilt": 5, "portfolio_caches": "not_in_scope"}
        queue.assert_not_called()

    def test_refused_refresh_request_is_reported_as_unavailable(self):
        with patch("ai_prediction_engine.rebuild_quant_history", return_value=5), \
             patch("db_helpers.get_portfolio_watchlist_tickers", return_value=["LCJP.L"]), \
             patch("price_repair_engine.request_cache_refresh", return_value=None):
            result = refresh_downstream("LCJP.L", "2026-10-05", self._history_frame())
        assert result["portfolio_caches"] == "unavailable"

    def test_history_rebuild_failure_is_reported_and_does_not_block_cache_refresh(self):
        with patch("ai_prediction_engine.rebuild_quant_history", side_effect=RuntimeError("boom")), \
             patch("db_helpers.get_portfolio_watchlist_tickers", return_value=["LCJP.L"]), \
             patch("price_repair_engine.request_cache_refresh", return_value=MagicMock()):
            result = refresh_downstream("LCJP.L", "2026-10-05", self._history_frame())
        assert result == {"history_rows_rebuilt": None, "portfolio_caches": "queued"}

    def test_queued_refresh_runs_xray_then_risk_scan_then_account_performance(self):
        from price_repair_engine import _refresh_portfolio_caches

        order = []
        with patch("xray_engine.run_xray_precompute", side_effect=lambda: order.append("xray") or True), \
             patch("risk_orchestrator_engine.run_scan", side_effect=lambda: order.append("risk")), \
             patch("accounts_engine.refresh_all_trading_performance_caches", side_effect=lambda: order.append("accounts")):
            assert _refresh_portfolio_caches() is True
        assert order == ["xray", "risk", "accounts"]
