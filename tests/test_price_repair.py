from unittest.mock import patch
import sqlite3

import pandas as pd

from database import get_connection
from price_repair_engine import PriceRepairError, apply_saved_repairs, check_daily_bar, repair_daily_bar


def _history(path):
    df = pd.DataFrame(
        {"Open": [20.0, 4331.41], "High": [20.5, 4331.41],
         "Low": [19.9, 4331.41], "Close": [20.4, 4331.41],
         "Volume": [1000, 0]},
        index=pd.to_datetime(["2026-10-02", "2026-10-05"]),
    )
    df.to_parquet(path)
    return df


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
