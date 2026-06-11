"""
tests/test_macro_data_engine.py  ── MACRO DATA ENGINE

Covers:
 - fetch_fred_api    : response parsing, publication-lag selection, missing data
 - fetch_boe_data    : HTML detection, CSV parsing, column renaming edge-case
 - fetch_ons_taxonomy : unknown series_id guard, lookahead-bias date shifting
 - update_macro_indicators : missing FRED key path, all-empty early exit,
                             INSERT OR IGNORE DB write
"""

import io
import json
import sys
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db_module
from macro_data_engine import (
    fetch_fred_api,
    fetch_boe_data,
    fetch_ons_taxonomy_data,
    update_macro_indicators,
    ONS_TAXONOMY,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _mock_session(status: int = 200, json_body=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = json_body or {}
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(f"HTTP {status}")
    session = MagicMock()
    session.get.return_value = resp
    return session


def _fred_payload(series_id: str, observations: list) -> dict:
    return {"observations": [{"date": d, "value": str(v)} for d, v in observations]}


START = datetime(2024, 1, 1)
END   = datetime(2024, 3, 31)


# ──────────────────────────────────────────────────────────────────────────────
# 1. fetch_fred_api
# ──────────────────────────────────────────────────────────────────────────────

class TestFetchFredApi:

    def test_daily_market_series_uses_zero_day_lag(self):
        """Credit-spread / yield-curve series must have publication_date == observation date."""
        payload = _fred_payload("BAMLH0A0HYM2", [("2024-02-01", 3.5), ("2024-02-02", 3.6)])
        session = _mock_session(json_body=payload)

        df = fetch_fred_api(session, "BAMLH0A0HYM2", START, END, "dummy-key")

        assert not df.empty
        assert "BAMLH0A0HYM2" in df.columns
        expected_idx = pd.to_datetime(["2024-02-01", "2024-02-02"])
        assert list(df.index) == list(expected_idx)

    def test_dfii10_uses_zero_day_lag(self):
        """DFII10 (10-year TIPS real yield) is a daily market series — no publication lag."""
        payload = _fred_payload("DFII10", [("2024-03-01", 1.85)])
        session = _mock_session(json_body=payload)

        df = fetch_fred_api(session, "DFII10", START, END, "dummy-key")

        assert not df.empty
        assert df.index[0] == pd.to_datetime("2024-03-01")

    def test_structural_series_applies_30_day_lag(self):
        """M2 / jobless-claims series must shift the index forward by 30 days."""
        payload = _fred_payload("WM2NS", [("2024-02-01", 21000.0)])
        session = _mock_session(json_body=payload)

        df = fetch_fred_api(session, "WM2NS", START, END, "dummy-key")

        assert not df.empty
        expected_date = pd.to_datetime("2024-02-01") + pd.DateOffset(days=30)
        assert df.index[0] == expected_date

    def test_missing_observations_key_returns_empty(self):
        session = _mock_session(json_body={"error": "not found"})
        df = fetch_fred_api(session, "WM2NS", START, END, "dummy-key")
        assert df.empty

    def test_empty_observations_list_returns_empty(self):
        session = _mock_session(json_body={"observations": []})
        df = fetch_fred_api(session, "ICSA", START, END, "dummy-key")
        assert df.empty

    def test_dot_value_replaced_with_na(self):
        """FRED uses '.' for missing values; these must become NaN, not crash."""
        payload = _fred_payload("T10Y2Y", [("2024-02-01", "."), ("2024-02-02", "0.5")])
        session = _mock_session(json_body=payload)

        df = fetch_fred_api(session, "T10Y2Y", START, END, "dummy-key")

        assert not df.empty
        assert pd.isna(df["T10Y2Y"].iloc[0])
        assert df["T10Y2Y"].iloc[1] == pytest.approx(0.5)

    def test_network_error_returns_empty(self):
        session = MagicMock()
        session.get.side_effect = requests.exceptions.ConnectionError("refused")
        df = fetch_fred_api(session, "WM2NS", START, END, "dummy-key")
        assert df.empty

    def test_http_error_returns_empty(self):
        session = _mock_session(status=500)
        df = fetch_fred_api(session, "WM2NS", START, END, "dummy-key")
        assert df.empty


# ──────────────────────────────────────────────────────────────────────────────
# 2. fetch_boe_data
# ──────────────────────────────────────────────────────────────────────────────

_BOE_CSV_VALID = "DATE,LPMVWNM\n2024-01-31,2938000\n2024-02-29,2940000\n"
_BOE_CSV_VARIANT_COL = "DATE,LPMVWNM (Billions)\n2024-01-31,2938000\n"


class TestFetchBoeData:

    def test_valid_csv_parses_and_applies_30_day_lag(self):
        session = _mock_session(text=_BOE_CSV_VALID)

        df = fetch_boe_data(session, "LPMVWNM", START, END)

        assert not df.empty
        assert "LPMVWNM" in df.columns
        expected = pd.to_datetime("2024-01-31") + pd.DateOffset(days=30)
        assert df.index[0] == expected

    def test_html_response_returns_empty(self):
        """If BoE serves an HTML page instead of CSV, return empty DataFrame."""
        session = _mock_session(text="<html><body>Error</body></html>")
        df = fetch_boe_data(session, "LPMVWNM", START, END)
        assert df.empty

    def test_missing_date_column_returns_empty(self):
        bad_csv = "PERIOD,LPMVWNM\n2024-01-31,2938000\n"
        session = _mock_session(text=bad_csv)
        df = fetch_boe_data(session, "LPMVWNM", START, END)
        assert df.empty

    def test_variant_column_name_still_renames_correctly(self):
        """Column names that contain the series code (e.g. 'LPMVWNM (Billions)') are renamed."""
        session = _mock_session(text=_BOE_CSV_VARIANT_COL)
        df = fetch_boe_data(session, "LPMVWNM", START, END)
        assert "LPMVWNM" in df.columns

    def test_network_error_returns_empty(self):
        session = MagicMock()
        session.get.side_effect = requests.exceptions.Timeout("timeout")
        df = fetch_boe_data(session, "LPMVWNM", START, END)
        assert df.empty

    def test_lag_days_zero_produces_no_shift(self):
        """lag_days=0 (used for Bank Rate IUDBEDR) must not shift dates."""
        csv = "DATE,IUDBEDR\n2024-02-01,5.25\n"
        session = _mock_session(text=csv)
        df = fetch_boe_data(session, "IUDBEDR", START, END, lag_days=0)
        assert not df.empty
        assert df.index[0] == pd.to_datetime("2024-02-01")

    def test_custom_lag_days_applied_correctly(self):
        """Arbitrary lag_days value must shift the index by exactly that many days."""
        csv = "DATE,LPMVWNM\n2024-02-01,2938000\n"
        session = _mock_session(text=csv)
        df = fetch_boe_data(session, "LPMVWNM", START, END, lag_days=7)
        expected = pd.to_datetime("2024-02-01") + pd.DateOffset(days=7)
        assert df.index[0] == expected


# ──────────────────────────────────────────────────────────────────────────────
# 3. fetch_ons_taxonomy_data
# ──────────────────────────────────────────────────────────────────────────────

_ONS_PAYLOAD = {
    "months": [
        {"date": "2024 Jan", "value": "2.5"},
        {"date": "2024 Feb", "value": "2.6"},
    ]
}


class TestFetchOnsTaxonomyData:

    def test_unknown_series_id_returns_empty_without_network_call(self):
        session = MagicMock()
        df = fetch_ons_taxonomy_data(session, "UNKNOWN_SERIES", START)
        assert df.empty
        session.get.assert_not_called()

    def test_valid_series_parses_values(self):
        session = _mock_session(json_body=_ONS_PAYLOAD)
        df = fetch_ons_taxonomy_data(session, "D7G7", START)
        assert not df.empty
        assert "D7G7" in df.columns
        assert df["D7G7"].iloc[0] == pytest.approx(2.5)

    def test_lookahead_bias_shift_end_of_month_plus_30(self):
        """
        '2024 Jan' must be shifted to end-of-January + 30 days = 2024-03-01.
        Verifies the lookahead-bias remediation date arithmetic.
        """
        payload = {"months": [{"date": "2024 Jan", "value": "1.0"}]}
        session = _mock_session(json_body=payload)
        df = fetch_ons_taxonomy_data(session, "D7G7", START)

        expected = pd.Timestamp("2024-01-31") + pd.DateOffset(days=30)
        assert df.index[0] == expected

    def test_start_date_filter_excludes_old_records(self):
        """Records with shifted date before start_date must be excluded."""
        old_start = datetime(2030, 1, 1)
        session = _mock_session(json_body=_ONS_PAYLOAD)
        df = fetch_ons_taxonomy_data(session, "D7G7", old_start)
        assert df.empty

    def test_empty_months_list_returns_empty(self):
        session = _mock_session(json_body={"months": []})
        df = fetch_ons_taxonomy_data(session, "D7G7", START)
        assert df.empty

    def test_missing_months_key_returns_empty(self):
        session = _mock_session(json_body={"quarters": []})
        df = fetch_ons_taxonomy_data(session, "D7G7", START)
        assert df.empty

    def test_network_error_returns_empty(self):
        session = MagicMock()
        session.get.side_effect = requests.exceptions.ConnectionError()
        df = fetch_ons_taxonomy_data(session, "D7G7", START)
        assert df.empty


# ──────────────────────────────────────────────────────────────────────────────
# 4. update_macro_indicators — pipeline-level tests
# ──────────────────────────────────────────────────────────────────────────────

class TestUpdateMacroIndicators:

    def test_missing_fred_key_logs_error_but_does_not_raise(self):
        """When FRED_API_KEY is absent the pipeline must not raise."""
        with patch("macro_data_engine.load_config", return_value={}), \
             patch("macro_data_engine.get_retry_session") as mock_sess, \
             patch("macro_data_engine.fetch_boe_data", return_value=pd.DataFrame()), \
             patch("macro_data_engine.fetch_ons_taxonomy_data", return_value=pd.DataFrame()):
            update_macro_indicators()

    def test_all_sources_empty_returns_early_without_db_write(self):
        """When every source returns an empty DataFrame, the DB must not be touched."""
        with patch("macro_data_engine.load_config", return_value={"FRED_API_KEY": "key"}), \
             patch("macro_data_engine.get_retry_session"), \
             patch("macro_data_engine.fetch_fred_api", return_value=pd.DataFrame()), \
             patch("macro_data_engine.fetch_boe_data", return_value=pd.DataFrame()), \
             patch("macro_data_engine.fetch_ons_taxonomy_data", return_value=pd.DataFrame()), \
             patch("macro_data_engine.get_connection") as mock_conn:
            update_macro_indicators()
            mock_conn.assert_not_called()

    def test_cpi_yoy_conversion_stores_percentage_not_raw_index(self):
        """13 months of raw CPI index values (~310-322) must be stored as YoY % (~3.9), not the index."""
        dates = pd.date_range("2022-01-31", periods=13, freq="ME")
        cpi_df = pd.DataFrame({"CPIAUCSL": [310.0 + i for i in range(13)]}, index=dates)

        def fred_side_effect(session, series_id, *args, **kwargs):
            return cpi_df if series_id == "CPIAUCSL" else pd.DataFrame()

        try:
            with patch("macro_data_engine.load_config", return_value={"FRED_API_KEY": "key"}), \
                 patch("macro_data_engine.get_retry_session"), \
                 patch("macro_data_engine.fetch_fred_api", side_effect=fred_side_effect), \
                 patch("macro_data_engine.fetch_boe_data", return_value=pd.DataFrame()), \
                 patch("macro_data_engine.fetch_ons_taxonomy_data", return_value=pd.DataFrame()):
                update_macro_indicators()

            conn = _db_module.get_connection()
            row = conn.execute(
                "SELECT us_cpi_inflation FROM macro_indicators WHERE date='2023-01-31'"
            ).fetchone()
            conn.close()
            assert row is not None and row["us_cpi_inflation"] is not None
            val = row["us_cpi_inflation"]
            # 322/310 - 1 = ~3.87%: must be small %, not the raw index ~322
            assert abs(val) < 50, f"Expected YoY %%, got raw index value {val}"
            assert abs(val) > 0.5
        finally:
            cleanup = _db_module.get_connection()
            cleanup.execute("DELETE FROM macro_indicators WHERE date BETWEEN '2022-01-01' AND '2023-02-01'")
            cleanup.commit()
            cleanup.close()

    def test_insert_or_ignore_does_not_overwrite_existing_rows(self):
        """
        The pipeline uses INSERT OR IGNORE to preserve point-in-time data.
        An existing row must not be updated even if the fetched value differs.
        """
        seed_conn = _db_module.get_connection()
        seed_conn.execute(
            "INSERT OR IGNORE INTO macro_indicators (date, us_m2) VALUES ('2024-01-31', 99999.0)"
        )
        seed_conn.commit()
        seed_conn.close()

        wm2ns_df = pd.DataFrame(
            {"WM2NS": [88888.0]},
            index=[pd.Timestamp("2024-01-31")]
        )

        def fred_side_effect(session, series_id, *args, **kwargs):
            return wm2ns_df if series_id == "WM2NS" else pd.DataFrame()

        try:
            with patch("macro_data_engine.load_config", return_value={"FRED_API_KEY": "key"}), \
                 patch("macro_data_engine.get_retry_session"), \
                 patch("macro_data_engine.fetch_fred_api", side_effect=fred_side_effect), \
                 patch("macro_data_engine.fetch_boe_data", return_value=pd.DataFrame()), \
                 patch("macro_data_engine.fetch_ons_taxonomy_data", return_value=pd.DataFrame()):
                update_macro_indicators()

            verify_conn = _db_module.get_connection()
            row = verify_conn.execute(
                "SELECT us_m2 FROM macro_indicators WHERE date='2024-01-31'"
            ).fetchone()
            verify_conn.close()
            assert row["us_m2"] == pytest.approx(99999.0), (
                "INSERT OR IGNORE must preserve the original PIT value, not overwrite it"
            )
        finally:
            cleanup = _db_module.get_connection()
            cleanup.execute("DELETE FROM macro_indicators WHERE date='2024-01-31'")
            cleanup.commit()
            cleanup.close()
