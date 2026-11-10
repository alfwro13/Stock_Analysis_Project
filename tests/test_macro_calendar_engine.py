"""
tests/test_macro_calendar_engine.py  ── MACRO CALENDAR ENGINE

Tests for deterministic pure functions:

  clean_value()         — financial magnitude string → float conversion
  generate_event_id()   — deterministic SHA-256 dedup hash
  upsert_calendar_events() — DB write / upsert behaviour
  _ET / UTC conversion  — ForexFactory ET timestamps stored as UTC
"""

import hashlib
import logging
import sqlite3
import sys
import tempfile
import os
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timezone

from macro_calendar_engine import (
    clean_value, fetch_and_process_calendar, generate_event_id, upsert_calendar_events, vote_split_text,
)
from time_engine import localize_naive_to_utc


# ──────────────────────────────────────────────────────────────────────────────
# 1. clean_value()
# ──────────────────────────────────────────────────────────────────────────────

class TestCleanValue:

    @pytest.mark.parametrize("val, expected", [
        ("5.0%",      5.0),
        ("-1.2%",    -1.2),
        ("250K",      250_000.0),
        ("1.5M",      1_500_000.0),
        ("2.3B",      2_300_000_000.0),
        ("1.2T",      1_200_000_000_000.0),
        ("3.14",      3.14),
        ("-0.5",     -0.5),
        ("1,234.5",   1234.5),
        ("0",         0.0),
    ])
    def test_valid_strings(self, val, expected):
        assert clean_value(val) == pytest.approx(expected)

    @pytest.mark.parametrize("val", [None, "", "   ", "-", "abc", 42])
    def test_non_parseable_returns_none(self, val):
        assert clean_value(val) is None

    @pytest.mark.parametrize("val", ["7-0-2", "3-0-6", "9-0", " 7-0-2 "])
    def test_vote_split_is_none_without_a_warning(self, val, caplog):
        with caplog.at_level(logging.WARNING, logger="macro_calendar_engine"):
            assert clean_value(val, "BoE Monetary Policy Summary") is None
        assert caplog.records == []

    def test_unparseable_value_warning_names_the_event(self, caplog):
        with caplog.at_level(logging.WARNING, logger="macro_calendar_engine"):
            assert clean_value("abc", "German Ifo") is None
        assert "'abc'" in caplog.text and "'German Ifo'" in caplog.text

    def test_strips_whitespace_before_parsing(self):
        assert clean_value("  3.5%  ") == pytest.approx(3.5)

    def test_comma_stripped_before_multiplier(self):
        assert clean_value("1,500K") == pytest.approx(1_500_000.0)

    def test_negative_thousands(self):
        assert clean_value("-200K") == pytest.approx(-200_000.0)


class TestVoteSplitText:

    @pytest.mark.parametrize("val, expected", [
        ("7-0-2", "7-0-2"), (" 3-0-6 ", "3-0-6"), ("9-0", "9-0"),
    ])
    def test_vote_splits_are_returned_as_written(self, val, expected):
        assert vote_split_text(val) == expected

    @pytest.mark.parametrize("val", [None, "", "-", "-0.5", "3.1%", "250K", "1-2-3-4", "7-0-x", "abc", 42])
    def test_other_values_are_not_vote_splits(self, val):
        assert vote_split_text(val) is None


_FEED_XML = """<weeklyevents>
<event><title>MPC Vote</title><country>GBP</country><date>06-18-2026</date><time>11:00am</time><impact>High</impact>
<forecast>7-0-2</forecast><previous>3-0-6</previous><actual></actual></event>
<event><title>CPI y/y</title><country>USD</country><date>06-18-2026</date><time>8:30am</time><impact>High</impact>
<forecast>3.1%</forecast><previous>3.0%</previous><actual></actual></event>
</weeklyevents>"""


class TestFetchAndProcessCalendar:

    def _events(self):
        response = type("R", (), {"content": _FEED_XML.encode(), "raise_for_status": lambda self: None})()
        with patch("macro_calendar_engine.requests.get", return_value=response):
            return {row[4]: row for row in fetch_and_process_calendar()}

    def test_vote_split_goes_to_the_text_columns_and_leaves_the_numbers_null(self):
        row = self._events()["MPC Vote"]
        assert row[5:8] == (None, None, None)
        assert row[12:15] == ("7-0-2", "3-0-6", None)

    def test_numeric_values_leave_the_text_columns_null(self):
        row = self._events()["CPI y/y"]
        assert row[5] == pytest.approx(3.1) and row[6] == pytest.approx(3.0)
        assert row[12:15] == (None, None, None)


# ──────────────────────────────────────────────────────────────────────────────
# 2. generate_event_id()
# ──────────────────────────────────────────────────────────────────────────────

class TestGenerateEventId:

    def test_returns_sha256_hex(self):
        result = generate_event_id("2026-06-07 08:30:00", "USD", "CPI")
        expected = hashlib.sha256("2026-06-07 08:30:00_USD_CPI".encode()).hexdigest()
        assert result == expected

    def test_deterministic(self):
        a = generate_event_id("2026-06-07 08:30:00", "USD", "CPI")
        b = generate_event_id("2026-06-07 08:30:00", "USD", "CPI")
        assert a == b

    def test_different_inputs_give_different_ids(self):
        id1 = generate_event_id("2026-06-07 08:30:00", "USD", "CPI")
        id2 = generate_event_id("2026-06-07 08:30:00", "GBP", "CPI")
        id3 = generate_event_id("2026-06-07 08:30:00", "USD", "NFP")
        assert len({id1, id2, id3}) == 3

    def test_returns_64_char_hex_string(self):
        result = generate_event_id("2026-06-07", "USD", "CPI")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


# ──────────────────────────────────────────────────────────────────────────────
# 3. ET → UTC timezone conversion (via localize_naive_to_utc)
# ──────────────────────────────────────────────────────────────────────────────

class TestEasternToUTC:

    def test_08_30_et_becomes_12_30_utc_during_edt(self):
        # During EDT (UTC-4): 08:30 ET == 12:30 UTC
        naive = datetime(2026, 6, 7, 8, 30, 0)   # summer — EDT
        utc_str = localize_naive_to_utc(naive, "NYSE").strftime("%Y-%m-%d %H:%M:%S")
        assert utc_str == "2026-06-07 12:30:00"

    def test_08_30_et_becomes_13_30_utc_during_est(self):
        # During EST (UTC-5): 08:30 ET == 13:30 UTC
        naive = datetime(2026, 1, 7, 8, 30, 0)   # winter — EST
        utc_str = localize_naive_to_utc(naive, "NYSE").strftime("%Y-%m-%d %H:%M:%S")
        assert utc_str == "2026-01-07 13:30:00"

    def test_midnight_et_date_only_event_is_offset(self):
        # A date-only event (no time) is stored as midnight ET → UTC offset applied
        naive = datetime(2026, 6, 7, 0, 0, 0)
        utc_str = localize_naive_to_utc(naive, "NYSE").strftime("%Y-%m-%d %H:%M:%S")
        assert utc_str == "2026-06-07 04:00:00"   # EDT offset


# ──────────────────────────────────────────────────────────────────────────────
# 4. upsert_calendar_events()
# ──────────────────────────────────────────────────────────────────────────────

_CAL_DDL = """
    CREATE TABLE macro_calendar (
        event_id          TEXT PRIMARY KEY,
        event_date        TEXT,
        currency          TEXT,
        impact            TEXT,
        event_name        TEXT,
        forecast_val      REAL,
        previous_val      REAL,
        actual_val        REAL,
        post_event_spy_gap REAL,
        ai_volatility_warning REAL,
        is_event_passed   INTEGER DEFAULT 0,
        alert_dispatched  INTEGER DEFAULT 0,
        forecast_text     TEXT,
        previous_text     TEXT,
        actual_text       TEXT
    )
"""

@pytest.fixture
def cal_db_path(tmp_path):
    """Temp-file SQLite DB so tests can open a fresh connection after upsert closes its own."""
    db_file = tmp_path / "cal_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(_CAL_DDL)
    conn.commit()
    conn.close()
    return str(db_file)


def _make_row(event_id="abc", event_date="2026-06-07 08:30:00",
              currency="USD", event_name="CPI",
              forecast=3.1, previous=3.0, actual=None,
              spy_gap=None, ai_warn=0, is_passed=0, dispatched=0,
              forecast_text=None, previous_text=None, actual_text=None):
    return (event_id, event_date, currency, "High", event_name,
            forecast, previous, actual, spy_gap, ai_warn, is_passed, dispatched,
            forecast_text, previous_text, actual_text)


def _query(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return row


class TestUpsertCalendarEvents:

    def test_empty_list_does_nothing(self, cal_db_path):
        def _conn():
            return sqlite3.connect(cal_db_path)
        with patch("macro_calendar_engine.get_connection", side_effect=_conn):
            upsert_calendar_events([])
        conn = sqlite3.connect(cal_db_path)
        count = conn.execute("SELECT COUNT(*) FROM macro_calendar").fetchone()[0]
        conn.close()
        assert count == 0

    def test_inserts_new_row(self, cal_db_path):
        def _conn():
            return sqlite3.connect(cal_db_path)
        with patch("macro_calendar_engine.get_connection", side_effect=_conn):
            upsert_calendar_events([_make_row()])
        row = _query(cal_db_path, "SELECT * FROM macro_calendar WHERE event_id='abc'")
        assert row is not None
        assert row["currency"] == "USD"
        assert row["forecast_val"] == pytest.approx(3.1)

    def test_upsert_updates_forecast_and_is_passed(self, cal_db_path):
        def _conn():
            return sqlite3.connect(cal_db_path)
        with patch("macro_calendar_engine.get_connection", side_effect=_conn):
            upsert_calendar_events([_make_row(forecast=3.1, is_passed=0)])
            upsert_calendar_events([_make_row(forecast=3.2, is_passed=1)])
        row = _query(cal_db_path, "SELECT * FROM macro_calendar WHERE event_id='abc'")
        assert row["forecast_val"] == pytest.approx(3.2)
        assert row["is_event_passed"] == 1

    def test_coalesce_preserves_existing_actual_when_new_actual_is_none(self, cal_db_path):
        def _conn():
            return sqlite3.connect(cal_db_path)
        with patch("macro_calendar_engine.get_connection", side_effect=_conn):
            upsert_calendar_events([_make_row(actual=3.5)])
            upsert_calendar_events([_make_row(actual=None)])
        row = _query(cal_db_path, "SELECT actual_val FROM macro_calendar WHERE event_id='abc'")
        assert row["actual_val"] == pytest.approx(3.5)

    def test_new_actual_overwrites_none(self, cal_db_path):
        def _conn():
            return sqlite3.connect(cal_db_path)
        with patch("macro_calendar_engine.get_connection", side_effect=_conn):
            upsert_calendar_events([_make_row(actual=None)])
            upsert_calendar_events([_make_row(actual=3.8)])
        row = _query(cal_db_path, "SELECT actual_val FROM macro_calendar WHERE event_id='abc'")
        assert row["actual_val"] == pytest.approx(3.8)

    def test_vote_split_text_is_stored_and_refreshed(self, cal_db_path):
        def _conn():
            return sqlite3.connect(cal_db_path)
        with patch("macro_calendar_engine.get_connection", side_effect=_conn):
            upsert_calendar_events([_make_row(forecast=None, previous=None, forecast_text="7-0-2", previous_text="3-0-6")])
            upsert_calendar_events([_make_row(forecast=None, previous=None, forecast_text="8-0-1", previous_text="3-0-6")])
        row = _query(cal_db_path, "SELECT * FROM macro_calendar WHERE event_id='abc'")
        assert (row["forecast_text"], row["previous_text"]) == ("8-0-1", "3-0-6")
        assert row["forecast_val"] is None

    def test_coalesce_preserves_existing_actual_text(self, cal_db_path):
        def _conn():
            return sqlite3.connect(cal_db_path)
        with patch("macro_calendar_engine.get_connection", side_effect=_conn):
            upsert_calendar_events([_make_row(actual=None, actual_text="6-0-3")])
            upsert_calendar_events([_make_row(actual=None, actual_text=None)])
        row = _query(cal_db_path, "SELECT actual_text FROM macro_calendar WHERE event_id='abc'")
        assert row["actual_text"] == "6-0-3"
