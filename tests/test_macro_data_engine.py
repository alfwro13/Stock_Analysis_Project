"""
tests/test_macro_data_engine.py  ── MACRO DATA ENGINE

Covers:
 - fetch_fred_api    : response parsing, publication-lag selection, missing data
 - fetch_boe_data    : HTML detection, CSV parsing, column renaming edge-case
 - fetch_ons_taxonomy : unknown series_id guard, lookahead-bias date shifting
 - update_macro_indicators : missing FRED key path, all-empty early exit,
                             INSERT OR IGNORE DB write
 - Yield vs Nominal GDP    : quarterly release-date shift (FRED GDP, ONS YBHA), YoY + window
                             seeding, the two macro_indicators GDP columns, yield_gdp_link()
"""

import io
import json
import os
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
    get_uk_cpi_yoy_series,
    get_nominal_gdp_yoy_series,
    nominal_gdp_yoy_by_release,
    yield_gdp_link,
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

    def test_gdp_publishes_quarter_end_plus_30_days(self):
        """FRED dates a quarter by its first day; the advance estimate lands ~30 days after the
        quarter ENDS, so a flat 30-day lag from the first day would show Q2 in May."""
        payload = _fred_payload("GDP", [("2024-01-01", 28000.0), ("2024-04-01", 28300.0)])
        session = _mock_session(json_body=payload)

        df = fetch_fred_api(session, "GDP", START, END, "dummy-key")

        assert list(df.index) == [
            pd.Timestamp("2024-03-31") + pd.DateOffset(days=30),
            pd.Timestamp("2024-06-30") + pd.DateOffset(days=30),
        ]
        assert df["GDP"].tolist() == [28000.0, 28300.0]

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

_ONS_GDP_PAYLOAD = {
    "quarters": [
        {"date": "2024 Q1", "value": "700000"},
        {"date": "2024 Q2", "value": "710000"},
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

    def test_quarterly_series_reads_quarters_and_lags_45_days_after_quarter_end(self):
        """ONS dates GDP as '2024 Q1'; the first estimate (PN2) lands ~6 weeks after the quarter ends."""
        session = _mock_session(json_body=_ONS_GDP_PAYLOAD)

        df = fetch_ons_taxonomy_data(session, "YBHA", START)

        assert "/ybha/pn2/" in session.get.call_args[0][0]
        assert list(df.index) == [
            pd.Timestamp("2024-03-31") + pd.DateOffset(days=45),
            pd.Timestamp("2024-06-30") + pd.DateOffset(days=45),
        ]
        assert df["YBHA"].tolist() == [700000.0, 710000.0]

    def test_quarterly_series_ignores_a_months_only_payload(self):
        session = _mock_session(json_body=_ONS_PAYLOAD)
        assert fetch_ons_taxonomy_data(session, "YBHA", START).empty

    def test_quarterly_series_skips_unparseable_period_labels(self):
        payload = {"quarters": [{"date": "garbage", "value": "1"}, {"date": "2024 Q1", "value": "700000"}]}
        session = _mock_session(json_body=payload)

        df = fetch_ons_taxonomy_data(session, "YBHA", START)

        assert df["YBHA"].tolist() == [700000.0]


# ──────────────────────────────────────────────────────────────────────────────
# 4. update_macro_indicators — pipeline-level tests
# ──────────────────────────────────────────────────────────────────────────────

class TestUpdateMacroIndicators:

    def test_missing_fred_key_logs_error_but_does_not_raise(self):
        """When FRED_API_KEY is absent the pipeline must not raise."""
        with patch.dict(os.environ, {"FRED_API_KEY": ""}), \
             patch("macro_data_engine.get_retry_session") as mock_sess, \
             patch("macro_data_engine.fetch_boe_data", return_value=pd.DataFrame()), \
             patch("macro_data_engine.fetch_ons_taxonomy_data", return_value=pd.DataFrame()):
            update_macro_indicators()

    def test_all_sources_empty_returns_early_without_db_write(self):
        """When every source returns an empty DataFrame, the DB must not be touched."""
        with patch.dict(os.environ, {"FRED_API_KEY": "key"}), \
             patch("macro_data_engine.get_retry_session"), \
             patch("macro_data_engine.fetch_fred_api", return_value=pd.DataFrame()), \
             patch("macro_data_engine.fetch_boe_data", return_value=pd.DataFrame()), \
             patch("macro_data_engine.fetch_ons_taxonomy_data", return_value=pd.DataFrame()), \
             patch("macro_data_engine.get_connection") as mock_conn:
            update_macro_indicators()
            mock_conn.assert_not_called()

    def test_cpi_yoy_conversion_stores_percentage_not_raw_index(self):
        """13 months of raw CPI index values (~310-322) must be stored as YoY % (~3.9), not the index.

        cpi_df's index mimics fetch_fred_api's real output shape: each observation dated on the
        1st of its month, shifted by the same flat 30-day publication lag fetch_fred_api applies.
        now_local is fixed to 2023-12-31 so start_dt ≈ 2022-01-02, making the mock
        data window (2022-01 to 2023-01) fall within the 730-day fetch range.
        """
        raw_dates = pd.date_range("2022-01-01", periods=13, freq="MS")
        lagged_dates = raw_dates + pd.DateOffset(days=30)
        cpi_df = pd.DataFrame({"CPIAUCSL": [310.0 + i for i in range(13)]}, index=lagged_dates)

        def fred_side_effect(session, series_id, *args, **kwargs):
            return cpi_df if series_id == "CPIAUCSL" else pd.DataFrame()

        try:
            with patch.dict(os.environ, {"FRED_API_KEY": "key"}), \
                 patch("macro_data_engine.get_retry_session"), \
                 patch("macro_data_engine.fetch_fred_api", side_effect=fred_side_effect), \
                 patch("macro_data_engine.fetch_boe_data", return_value=pd.DataFrame()), \
                 patch("macro_data_engine.fetch_ons_taxonomy_data", return_value=pd.DataFrame()), \
                 patch("macro_data_engine.time_engine") as mock_te:
                mock_te.now_local.return_value = datetime(2023, 12, 31)
                update_macro_indicators()

            # Last raw month-end bucket (2023-01-31) + the reapplied 30-day publication lag.
            conn = _db_module.get_connection()
            row = conn.execute(
                "SELECT us_cpi_inflation FROM macro_indicators WHERE date='2023-03-02'"
            ).fetchone()
            conn.close()
            assert row is not None and row["us_cpi_inflation"] is not None
            val = row["us_cpi_inflation"]
            # (322 - 310) / 310 * 100 ≈ 3.87%: must be the true 12-month change, not inflated
            assert val == pytest.approx(3.871, abs=0.01), f"Expected true 12-month YoY ~3.87%%, got {val}"
        finally:
            cleanup = _db_module.get_connection()
            cleanup.execute("DELETE FROM macro_indicators WHERE date BETWEEN '2022-01-01' AND '2023-04-01'")
            cleanup.commit()
            cleanup.close()

    def test_cpi_yoy_survives_month_length_bucket_collisions(self):
        """24 months of raw CPI observations (1st-of-month + flat 30-day lag) must resample into
        24 distinct calendar-month buckets, not collapse via bucket collisions.

        Regression test: fetch_fred_api's flat +30-day shift applied before month-end resampling
        causes ~5 of every 12 months to land in the same bucket as their neighbour and get dropped
        by dropna(), so pct_change(periods=12) ends up comparing ~18-20 real months apart instead of
        12 — silently inflating the reported YoY%. A steady 0.3%-per-month raw index growth must
        yield a true 12-month YoY of ~3.66% (1.003**12 - 1), not a larger, collapsed-window figure.
        """
        raw_dates = pd.date_range("2021-06-01", periods=24, freq="MS")
        lagged_dates = raw_dates + pd.DateOffset(days=30)
        values = [300.0 * (1.003 ** i) for i in range(24)]
        cpi_df = pd.DataFrame({"CPIAUCSL": values}, index=lagged_dates)

        def fred_side_effect(session, series_id, *args, **kwargs):
            return cpi_df if series_id == "CPIAUCSL" else pd.DataFrame()

        try:
            with patch.dict(os.environ, {"FRED_API_KEY": "key"}), \
                 patch("macro_data_engine.get_retry_session"), \
                 patch("macro_data_engine.fetch_fred_api", side_effect=fred_side_effect), \
                 patch("macro_data_engine.fetch_boe_data", return_value=pd.DataFrame()), \
                 patch("macro_data_engine.fetch_ons_taxonomy_data", return_value=pd.DataFrame()), \
                 patch("macro_data_engine.time_engine") as mock_te:
                mock_te.now_local.return_value = datetime(2023, 6, 1)
                update_macro_indicators()

            conn = _db_module.get_connection()
            rows = conn.execute(
                "SELECT date, us_cpi_inflation FROM macro_indicators "
                "WHERE us_cpi_inflation IS NOT NULL AND date BETWEEN '2021-06-01' AND '2023-08-01' "
                "ORDER BY date"
            ).fetchall()
            conn.close()
            values_out = [r["us_cpi_inflation"] for r in rows]
            expected_yoy = (1.003 ** 12 - 1) * 100
            assert values_out, "Expected at least one computed YoY row"
            for v in values_out:
                assert v == pytest.approx(expected_yoy, abs=0.05), (
                    f"Expected true 12-month YoY ~{expected_yoy:.2f}%%, got {v} "
                    "(a collapsed/mis-bucketed series would compare a longer window and read higher)"
                )
        finally:
            cleanup = _db_module.get_connection()
            cleanup.execute("DELETE FROM macro_indicators WHERE date BETWEEN '2021-06-01' AND '2023-08-01'")
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
            with patch.dict(os.environ, {"FRED_API_KEY": "key"}), \
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

    def test_uk_m4_stored_in_billions_from_boe_level_series(self):
        """LPMAUYN is BoE's M4 amounts-outstanding series in sterling millions; the pipeline
        must divide by 1000 so uk_m4 matches its existing billions scale (mirrors us_m2's
        WM2NS, which FRED already reports in billions)."""
        boe_df = pd.DataFrame({"LPMAUYN": [3000844.0]}, index=[pd.Timestamp("2024-01-31")])

        def boe_side_effect(session, series_code, *args, **kwargs):
            return boe_df if series_code == "LPMAUYN" else pd.DataFrame()

        try:
            with patch.dict(os.environ, {"FRED_API_KEY": "key"}), \
                 patch("macro_data_engine.get_retry_session"), \
                 patch("macro_data_engine.fetch_fred_api", return_value=pd.DataFrame()), \
                 patch("macro_data_engine.fetch_boe_data", side_effect=boe_side_effect), \
                 patch("macro_data_engine.fetch_ons_taxonomy_data", return_value=pd.DataFrame()):
                update_macro_indicators()

            verify_conn = _db_module.get_connection()
            row = verify_conn.execute(
                "SELECT uk_m4 FROM macro_indicators WHERE date='2024-01-31'"
            ).fetchone()
            verify_conn.close()
            assert row is not None and row["uk_m4"] == pytest.approx(3000.844), (
                f"Expected uk_m4 stored in billions (~3000.844), got {row['uk_m4'] if row else None}"
            )
        finally:
            cleanup = _db_module.get_connection()
            cleanup.execute("DELETE FROM macro_indicators WHERE date='2024-01-31'")
            cleanup.commit()
            cleanup.close()

    def test_uk_m4_legacy_growth_rate_value_is_overwritten_on_refetch(self):
        """A pre-existing row still holding the old LPMVWNM growth-rate figure (e.g. -0.6) must be
        corrected to the real billions-scale level when that date is refetched, even though
        INSERT OR IGNORE alone would otherwise leave the stale row untouched."""
        seed_conn = _db_module.get_connection()
        seed_conn.execute(
            "INSERT OR IGNORE INTO macro_indicators (date, uk_m4) VALUES ('2024-01-31', -0.6)"
        )
        seed_conn.commit()
        seed_conn.close()

        boe_df = pd.DataFrame({"LPMAUYN": [3000844.0]}, index=[pd.Timestamp("2024-01-31")])

        def boe_side_effect(session, series_code, *args, **kwargs):
            return boe_df if series_code == "LPMAUYN" else pd.DataFrame()

        try:
            with patch.dict(os.environ, {"FRED_API_KEY": "key"}), \
                 patch("macro_data_engine.get_retry_session"), \
                 patch("macro_data_engine.fetch_fred_api", return_value=pd.DataFrame()), \
                 patch("macro_data_engine.fetch_boe_data", side_effect=boe_side_effect), \
                 patch("macro_data_engine.fetch_ons_taxonomy_data", return_value=pd.DataFrame()):
                update_macro_indicators()

            verify_conn = _db_module.get_connection()
            row = verify_conn.execute(
                "SELECT uk_m4 FROM macro_indicators WHERE date='2024-01-31'"
            ).fetchone()
            verify_conn.close()
            assert row is not None and row["uk_m4"] == pytest.approx(3000.844), (
                f"Expected the stale -0.6 growth-rate value replaced with ~3000.844, got {row['uk_m4'] if row else None}"
            )
        finally:
            cleanup = _db_module.get_connection()
            cleanup.execute("DELETE FROM macro_indicators WHERE date='2024-01-31'")
            cleanup.commit()
            cleanup.close()

    def test_uk_m4_legacy_growth_rate_value_outside_fetch_window_is_nulled(self):
        """A legacy growth-rate value on a date outside the current fetch window (so it can't be
        repatched this run) must still be nulled out rather than left displaying as a bogus
        near-zero/negative 'money supply' figure."""
        seed_conn = _db_module.get_connection()
        seed_conn.execute(
            "INSERT OR IGNORE INTO macro_indicators (date, uk_m4) VALUES ('2020-01-31', 2.1)"
        )
        seed_conn.commit()
        seed_conn.close()

        # A non-empty source for some other date is required so the pipeline doesn't hit its
        # early "all sources empty" return before ever reaching the nullify step.
        wm2ns_df = pd.DataFrame({"WM2NS": [21000.0]}, index=[pd.Timestamp("2024-01-31")])

        def fred_side_effect(session, series_id, *args, **kwargs):
            return wm2ns_df if series_id == "WM2NS" else pd.DataFrame()

        try:
            with patch.dict(os.environ, {"FRED_API_KEY": "key"}), \
                 patch("macro_data_engine.get_retry_session"), \
                 patch("macro_data_engine.fetch_fred_api", side_effect=fred_side_effect), \
                 patch("macro_data_engine.fetch_boe_data", return_value=pd.DataFrame()), \
                 patch("macro_data_engine.fetch_ons_taxonomy_data", return_value=pd.DataFrame()):
                update_macro_indicators()

            verify_conn = _db_module.get_connection()
            row = verify_conn.execute(
                "SELECT uk_m4 FROM macro_indicators WHERE date='2020-01-31'"
            ).fetchone()
            verify_conn.close()
            assert row is not None and row["uk_m4"] is None, (
                f"Expected legacy value nulled out, got {row['uk_m4'] if row else 'row missing'}"
            )
        finally:
            cleanup = _db_module.get_connection()
            cleanup.execute("DELETE FROM macro_indicators WHERE date IN ('2020-01-31', '2024-01-31')")
            cleanup.commit()
            cleanup.close()

    @staticmethod
    def _gdp_fetchers():
        """Eight quarters (2023 Q1 - 2024 Q4) dated the way the fetchers date them: FRED at
        quarter end + 30 days growing 1%/quarter, ONS at quarter end + 45 days growing 1.5%/quarter."""
        quarter_ends = pd.date_range("2023-03-31", periods=8, freq="QE")
        us_df = pd.DataFrame(
            {"GDP": [28000.0 * 1.01 ** i for i in range(8)]}, index=quarter_ends + pd.DateOffset(days=30)
        )
        uk_df = pd.DataFrame(
            {"YBHA": [700000.0 * 1.015 ** i for i in range(8)]}, index=quarter_ends + pd.DateOffset(days=45)
        )

        def fred_side_effect(session, series_id, *args, **kwargs):
            return us_df if series_id == "GDP" else pd.DataFrame()

        def ons_side_effect(session, series_id, *args, **kwargs):
            return uk_df if series_id == "YBHA" else pd.DataFrame()

        return fred_side_effect, ons_side_effect

    @staticmethod
    def _cleanup_gdp_rows():
        cleanup = _db_module.get_connection()
        cleanup.execute("DELETE FROM macro_indicators WHERE date BETWEEN '2024-04-01' AND '2025-03-01'")
        cleanup.commit()
        cleanup.close()

    def test_nominal_gdp_yoy_written_to_both_columns_and_patches_existing_rows(self):
        """Quarterly GDP levels must be stored as YoY % (1.01**4 and 1.015**4), dated by release,
        and an already-inserted row from an earlier run must be patched rather than ignored."""
        fred_side_effect, ons_side_effect = self._gdp_fetchers()
        seed_conn = _db_module.get_connection()
        seed_conn.execute("INSERT OR IGNORE INTO macro_indicators (date, us_m2) VALUES ('2025-02-14', 1.0)")
        seed_conn.commit()
        seed_conn.close()

        try:
            with patch.dict(os.environ, {"FRED_API_KEY": "key"}), \
                 patch("macro_data_engine.get_retry_session"), \
                 patch("macro_data_engine.fetch_fred_api", side_effect=fred_side_effect), \
                 patch("macro_data_engine.fetch_boe_data", return_value=pd.DataFrame()), \
                 patch("macro_data_engine.fetch_ons_taxonomy_data", side_effect=ons_side_effect), \
                 patch("macro_data_engine.time_engine") as mock_te:
                mock_te.now_local.return_value = datetime(2025, 12, 31)
                update_macro_indicators()

            conn = _db_module.get_connection()
            patched = conn.execute(
                "SELECT us_nominal_gdp_yoy, uk_nominal_gdp_yoy FROM macro_indicators WHERE date='2025-02-14'"
            ).fetchone()
            first_us = conn.execute(
                "SELECT us_nominal_gdp_yoy, uk_nominal_gdp_yoy FROM macro_indicators WHERE date='2024-04-30'"
            ).fetchone()
            first_uk = conn.execute(
                "SELECT us_nominal_gdp_yoy, uk_nominal_gdp_yoy FROM macro_indicators WHERE date='2024-05-15'"
            ).fetchone()
            conn.close()

            assert patched["us_nominal_gdp_yoy"] == pytest.approx((1.01 ** 4 - 1) * 100, abs=0.01)
            assert patched["uk_nominal_gdp_yoy"] == pytest.approx((1.015 ** 4 - 1) * 100, abs=0.01)
            assert first_us["us_nominal_gdp_yoy"] == pytest.approx((1.01 ** 4 - 1) * 100, abs=0.01)
            assert first_us["uk_nominal_gdp_yoy"] is None, "UK GDP must not appear before ONS published it"
            assert first_uk["uk_nominal_gdp_yoy"] == pytest.approx((1.015 ** 4 - 1) * 100, abs=0.01)
        finally:
            self._cleanup_gdp_rows()

    def test_uk_nominal_gdp_still_stored_when_fred_key_is_missing(self):
        _, ons_side_effect = self._gdp_fetchers()

        try:
            with patch.dict(os.environ, {"FRED_API_KEY": ""}), \
                 patch("macro_data_engine.get_retry_session"), \
                 patch("macro_data_engine.fetch_boe_data", return_value=pd.DataFrame()), \
                 patch("macro_data_engine.fetch_ons_taxonomy_data", side_effect=ons_side_effect), \
                 patch("macro_data_engine.time_engine") as mock_te:
                mock_te.now_local.return_value = datetime(2025, 12, 31)
                update_macro_indicators()

            conn = _db_module.get_connection()
            row = conn.execute(
                "SELECT us_nominal_gdp_yoy, uk_nominal_gdp_yoy FROM macro_indicators WHERE date='2025-02-14'"
            ).fetchone()
            conn.close()

            assert row["uk_nominal_gdp_yoy"] == pytest.approx((1.015 ** 4 - 1) * 100, abs=0.01)
            assert row["us_nominal_gdp_yoy"] is None
        finally:
            self._cleanup_gdp_rows()


class TestGetUkCpiYoySeries:
    """The single reusable source for UK CPI YoY%, shared by the Market Sentiment page and the
    Pension account's CPI+target benchmark overlay (accounts_engine.pension_benchmark_overlay)."""

    def test_returns_clean_date_indexed_series(self):
        seed_conn = _db_module.get_connection()
        seed_conn.execute("INSERT OR IGNORE INTO macro_indicators (date, uk_cpi_inflation) VALUES ('2026-01-31', 3.2)")
        seed_conn.execute("INSERT OR IGNORE INTO macro_indicators (date, uk_cpi_inflation) VALUES ('2026-02-28', 2.9)")
        seed_conn.commit()
        seed_conn.close()

        try:
            series = get_uk_cpi_yoy_series()
            assert series[pd.Timestamp("2026-01-31")] == pytest.approx(3.2)
            assert series[pd.Timestamp("2026-02-28")] == pytest.approx(2.9)
            assert series.index.is_monotonic_increasing
        finally:
            cleanup = _db_module.get_connection()
            cleanup.execute("DELETE FROM macro_indicators WHERE date IN ('2026-01-31', '2026-02-28')")
            cleanup.commit()
            cleanup.close()

    def test_drops_null_rows(self):
        seed_conn = _db_module.get_connection()
        seed_conn.execute("INSERT OR IGNORE INTO macro_indicators (date, us_m2) VALUES ('2026-03-31', 1.0)")
        seed_conn.commit()
        seed_conn.close()

        try:
            series = get_uk_cpi_yoy_series()
            assert pd.Timestamp("2026-03-31") not in series.index
        finally:
            cleanup = _db_module.get_connection()
            cleanup.execute("DELETE FROM macro_indicators WHERE date='2026-03-31'")
            cleanup.commit()
            cleanup.close()


class TestNominalGdpYoyByRelease:
    """Quarterly levels are already dated by release, so YoY is a 4-observation change, not a calendar resample."""

    @staticmethod
    def _levels(values):
        releases = pd.date_range("2023-04-30", periods=len(values), freq="QE") + pd.DateOffset(days=30)
        return pd.Series(values, index=releases)

    def test_yoy_is_percent_change_over_four_quarters(self):
        levels = self._levels([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])

        yoy = nominal_gdp_yoy_by_release(levels, datetime(2023, 1, 1))

        assert yoy.tolist() == pytest.approx([4.0, (105.0 / 101.0 - 1) * 100])
        assert yoy.index[0] == levels.index[4]

    def test_last_release_before_window_start_is_seeded_at_the_start(self):
        """Without the seed the chart's GDP line would start up to a quarter after the window does."""
        levels = self._levels([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
        window_start = levels.index[4] + pd.DateOffset(days=10)

        yoy = nominal_gdp_yoy_by_release(levels, window_start.to_pydatetime())

        assert yoy.index[0] == pd.Timestamp(window_start.date())
        assert yoy.iloc[0] == pytest.approx(4.0)
        assert yoy.iloc[1] == pytest.approx((105.0 / 101.0 - 1) * 100)
        assert yoy.index[1] == levels.index[5]

    def test_fewer_than_five_quarters_yields_nothing(self):
        assert nominal_gdp_yoy_by_release(self._levels([100.0, 101.0, 102.0, 103.0]), datetime(2023, 1, 1)).empty


def _seed_gdp_rows(rows):
    conn = _db_module.get_connection()
    for date, us_gdp, uk_gdp in rows:
        conn.execute(
            "INSERT OR REPLACE INTO macro_indicators (date, us_nominal_gdp_yoy, uk_nominal_gdp_yoy) VALUES (?, ?, ?)",
            (date, us_gdp, uk_gdp),
        )
    conn.commit()
    conn.close()


def _delete_gdp_rows(dates):
    conn = _db_module.get_connection()
    conn.executemany("DELETE FROM macro_indicators WHERE date=?", [(d,) for d in dates])
    conn.commit()
    conn.close()


class TestGetNominalGdpYoySeries:

    def test_returns_each_region_from_its_own_column_and_drops_nulls(self):
        _seed_gdp_rows([("2999-01-01", 5.0, None), ("2999-02-01", 5.2, 4.1)])
        try:
            us = get_nominal_gdp_yoy_series("US")
            uk = get_nominal_gdp_yoy_series("UK")
            assert us[pd.Timestamp("2999-02-01")] == pytest.approx(5.2)
            assert pd.Timestamp("2999-01-01") in us.index
            assert pd.Timestamp("2999-01-01") not in uk.index
            assert uk[pd.Timestamp("2999-02-01")] == pytest.approx(4.1)
            assert us.index.is_monotonic_increasing
        finally:
            _delete_gdp_rows(["2999-01-01", "2999-02-01"])

    def test_unknown_region_is_rejected(self):
        with pytest.raises(KeyError):
            get_nominal_gdp_yoy_series("EU")


class TestYieldGdpLink:
    """The one calculation behind the warning-box line on the Portfolio and Market Sentiment pages."""

    def test_yield_below_growth_is_below(self):
        _seed_gdp_rows([("2999-01-01", 5.1, 4.0)])
        try:
            link = yield_gdp_link({"tnx_close": 4.30, "uk_gilt_close": 4.60})
            assert link["US"]["status"] == "BELOW"
            assert link["US"]["yield_pct"] == pytest.approx(4.30)
            assert link["US"]["gdp_yoy"] == pytest.approx(5.1)
            assert link["US"]["gap_pp"] == pytest.approx(-0.8)
        finally:
            _delete_gdp_rows(["2999-01-01"])

    def test_yield_above_growth_is_above(self):
        _seed_gdp_rows([("2999-01-01", 5.1, 4.0)])
        try:
            link = yield_gdp_link({"tnx_close": 4.30, "uk_gilt_close": 4.60})
            assert link["UK"]["status"] == "ABOVE"
            assert link["UK"]["gap_pp"] == pytest.approx(0.6)
        finally:
            _delete_gdp_rows(["2999-01-01"])

    def test_latest_gdp_value_is_used(self):
        _seed_gdp_rows([("2999-01-01", 3.0, 3.0), ("2999-02-01", 5.1, 4.0)])
        try:
            link = yield_gdp_link({"tnx_close": 4.30, "uk_gilt_close": 4.60})
            assert link["US"]["gdp_yoy"] == pytest.approx(5.1)
        finally:
            _delete_gdp_rows(["2999-01-01", "2999-02-01"])

    def test_region_without_gdp_is_none_so_the_line_is_omitted(self):
        _seed_gdp_rows([("2999-01-01", 5.1, None)])
        try:
            link = yield_gdp_link({"tnx_close": 4.30, "uk_gilt_close": 4.60})
            assert link["US"] is not None
            assert link["UK"] is None
        finally:
            _delete_gdp_rows(["2999-01-01"])

    def test_region_without_a_yield_is_none(self):
        _seed_gdp_rows([("2999-01-01", 5.1, 4.0)])
        try:
            link = yield_gdp_link({"tnx_close": None, "uk_gilt_close": 4.60})
            assert link["US"] is None
            assert link["UK"] is not None
        finally:
            _delete_gdp_rows(["2999-01-01"])

    def test_no_macro_regime_yields_none_for_both_regions(self):
        assert yield_gdp_link(None) == {"US": None, "UK": None}
