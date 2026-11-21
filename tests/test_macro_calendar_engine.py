"""
tests/test_macro_calendar_engine.py  ── MACRO CALENDAR ENGINE

Tests for deterministic pure functions:

  clean_value()         — financial magnitude string → float conversion
  generate_event_id()   — deterministic SHA-256 dedup hash
  upsert_calendar_events() — DB write / upsert behaviour
  feed times            — the feed's UTC times stored as they are
  correct_eastern_event_times() — one-time shift of rows stored by the Eastern-time parser
  resolve_actual() / fill_released_actuals() — the released events' actuals from BLS, ONS, FRED and the Bank of England
"""

import hashlib
import logging
import sqlite3
import sys
import tempfile
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import uuid
from datetime import date, datetime, timedelta, timezone

from constants import MACRO_ACTUALS_BACKFILL_DAYS
from db import schema as db_schema
from db.database import get_connection
import macro_calendar_engine
from macro_calendar_engine import (
    ACTUAL_SOURCES, _fetch_bls_series, _fetch_ons_cpi_rate, _fetch_rate_series, _load_actual_series, _shift_month,
    clean_value, clear_unmeasured_spy_gaps, correct_eastern_event_times, fetch_and_process_calendar, fill_released_actuals, generate_event_id,
    has_actual_source, remove_synthetic_seed_rows, resolve_actual, update_macro_calendar, upsert_calendar_events, vote_split_text,
)
from time_engine import localize_naive_to_utc, parse_stored_utc, utc_to_exchange_local

pytestmark = pytest.mark.restore_tables("macro_calendar", "data_migrations")


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
<event><title>FOMC Meeting Minutes</title><country>USD</country><date>11-18-2026</date><time>7:00pm</time><impact>High</impact>
<forecast></forecast><previous></previous><actual></actual></event>
<event><title>Bank Holiday Speech</title><country>GBP</country><date>11-19-2026</date><time>All Day</time><impact>High</impact>
<forecast></forecast><previous></previous><actual></actual></event>
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

    def test_feed_times_are_utc_and_stored_as_they_are(self):
        events = self._events()
        assert events["CPI y/y"][1] == "2026-06-18 08:30:00"
        assert events["MPC Vote"][1] == "2026-06-18 11:00:00"
        assert events["FOMC Meeting Minutes"][1] == "2026-11-18 19:00:00"
        assert events["Bank Holiday Speech"][1] == "2026-11-19 00:00:00"
        assert events["FOMC Meeting Minutes"][0] == generate_event_id("2026-11-18 19:00:00", "USD", "FOMC Meeting Minutes")


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

class TestUtcToExchangeLocal:

    def test_inverts_localize_naive_to_utc_in_summer_and_winter(self):
        for naive in (datetime(2026, 6, 10, 12, 30), datetime(2026, 11, 18, 7, 0), datetime(2026, 3, 8, 12, 0)):
            stamp = localize_naive_to_utc(naive, "NYSE")
            assert utc_to_exchange_local(stamp, "NYSE").replace(tzinfo=None) == naive

    def test_new_york_summer_and_winter_offsets(self):
        assert utc_to_exchange_local(datetime(2026, 6, 10, 16, 30, tzinfo=timezone.utc), "NYSE").hour == 12
        assert utc_to_exchange_local(datetime(2026, 11, 6, 18, 30, tzinfo=timezone.utc), "NYSE").hour == 13


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


class TestUpsertCalendarEventsUnderLock:
    """A write that hits 'database is locked' is queued and retried, not dropped; other errors reach the job runner."""

    @pytest.fixture
    def deferred_queue(self, monkeypatch):
        from db import connection
        monkeypatch.setattr(connection, "_start_deferred_worker", lambda: None)
        monkeypatch.setattr(connection, "_deferred", [])
        return connection._deferred

    def test_locked_batch_is_queued_and_written_on_retry(self, cal_db_path, deferred_queue):
        from db import connection
        attempts = []

        def _conn():
            attempts.append(1)
            if len(attempts) == 1:
                raise sqlite3.OperationalError("database is locked")
            return sqlite3.connect(cal_db_path)

        with patch("macro_calendar_engine.get_connection", side_effect=_conn):
            upsert_calendar_events([_make_row(forecast=3.1)])
            assert len(deferred_queue) == 1
            assert _query(cal_db_path, "SELECT * FROM macro_calendar WHERE event_id='abc'") is None
            connection._run_due_deferred(now=float("inf"))

        assert deferred_queue == []
        row = _query(cal_db_path, "SELECT forecast_val FROM macro_calendar WHERE event_id='abc'")
        assert row["forecast_val"] == pytest.approx(3.1)

    def test_non_lock_error_propagates_and_is_not_queued(self, cal_db_path, deferred_queue):
        with patch("macro_calendar_engine.get_connection", side_effect=sqlite3.OperationalError("no such table: macro_calendar")):
            with pytest.raises(sqlite3.OperationalError, match="no such table"):
                upsert_calendar_events([_make_row()])
        assert deferred_queue == []


def _shifted_row(event_date, name, currency="USD", **kw):
    return _make_row(event_id=generate_event_id(event_date, currency, name), event_date=event_date, currency=currency, event_name=name, **kw)


class TestCorrectEasternEventTimes:

    def _run(self, cal_db_path, rows):
        def _conn():
            conn = sqlite3.connect(cal_db_path)
            conn.row_factory = sqlite3.Row
            return conn
        conn = sqlite3.connect(cal_db_path)
        conn.executemany("INSERT INTO macro_calendar VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        conn.close()
        with patch("macro_calendar_engine.get_connection", side_effect=_conn):
            return correct_eastern_event_times()

    def _ids(self, cal_db_path):
        conn = sqlite3.connect(cal_db_path)
        conn.row_factory = sqlite3.Row
        rows = {r["event_name"] + "|" + r["event_date"]: dict(r) for r in conn.execute("SELECT * FROM macro_calendar")}
        conn.close()
        return rows

    def test_rows_from_the_eastern_parser_return_to_the_feed_time_with_a_new_id_and_no_gap(self, cal_db_path):
        rows = [
            _shifted_row("2026-11-18 12:00:00", "CPI y/y", "GBP", spy_gap=0.4, ai_warn=1.5, actual=3.4),
            _shifted_row("2026-11-06 18:30:00", "Non-Farm Employment Change", spy_gap=0.2),
            _shifted_row("2026-08-07 16:30:00", "Non-Farm Employment Change", spy_gap=0.1),
        ]
        assert self._run(cal_db_path, rows) == 3
        found = self._ids(cal_db_path)
        cpi = found["CPI y/y|2026-11-18 07:00:00"]
        assert cpi["event_id"] == generate_event_id("2026-11-18 07:00:00", "GBP", "CPI y/y")
        assert cpi["post_event_spy_gap"] is None and cpi["ai_volatility_warning"] == pytest.approx(1.5) and cpi["actual_val"] == pytest.approx(3.4)
        assert set(found) == {"CPI y/y|2026-11-18 07:00:00", "Non-Farm Employment Change|2026-11-06 13:30:00", "Non-Farm Employment Change|2026-08-07 12:30:00"}

    def test_rows_stored_before_the_eastern_parser_are_left_alone(self, cal_db_path):
        rows = [_shifted_row("2026-06-05 12:30:00", "Non-Farm Employment Change", spy_gap=0.0), _shifted_row("2026-05-08 12:30:00", "Non-Farm Employment Change", spy_gap=0.3, actual=115000.0)]
        assert self._run(cal_db_path, rows) == 0
        found = self._ids(cal_db_path)
        assert found["Non-Farm Employment Change|2026-05-08 12:30:00"]["post_event_spy_gap"] == pytest.approx(0.3)
        assert found["Non-Farm Employment Change|2026-06-05 12:30:00"]["post_event_spy_gap"] == 0.0

    def test_an_unparseable_event_date_is_skipped(self, cal_db_path):
        rows = [_shifted_row("11-19-2026 All Day", "Bank Holiday Speech", "GBP")]
        assert self._run(cal_db_path, rows) == 0


class TestRemoveSyntheticSeedRows:

    def test_rows_with_a_uuid_event_id_are_deleted_and_feed_rows_are_kept(self, cal_db_path):
        feed_id = generate_event_id("2026-11-06 13:30:00", "USD", "CPI y/y")
        conn = sqlite3.connect(cal_db_path)
        conn.executemany("INSERT INTO macro_calendar VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            _make_row(event_id=str(uuid.uuid4()), event_name="Fed Interest Rate Decision", actual=4.5),
            _make_row(event_id=str(uuid.uuid4()), event_name="BoE Official Bank Rate", actual=4.0),
            _make_row(event_id=feed_id, event_name="CPI y/y"),
        ])
        conn.commit()
        conn.close()

        def _conn():
            conn = sqlite3.connect(cal_db_path)
            conn.row_factory = sqlite3.Row
            return conn
        with patch("macro_calendar_engine.get_connection", side_effect=_conn):
            assert remove_synthetic_seed_rows() == 2
            assert remove_synthetic_seed_rows() == 0
        assert [r[0] for r in sqlite3.connect(cal_db_path).execute("SELECT event_id FROM macro_calendar")] == [feed_id]


class TestReconcilePastEvents:

    def _conn(self, db_path):
        def connect():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn
        return connect

    def _stamp(self, hour, minute, days_ago=1):
        day = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

    def _bars(self, start, opens, highs, lows):
        index = pd.date_range(start=pd.Timestamp(start, tz="UTC"), periods=len(opens), freq="5min")
        return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": opens, "Volume": 1000}, index=index)

    def _run(self, db_path, rows, spy_df):
        conn = sqlite3.connect(db_path)
        conn.executemany("INSERT INTO macro_calendar VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        conn.close()
        fake_yahoo = MagicMock()
        fake_yahoo.get_intraday.return_value = {"SPY": spy_df}
        with patch("macro_calendar_engine.get_connection", side_effect=self._conn(db_path)), patch("macro_calendar_engine.yahoo_engine", fake_yahoo):
            macro_calendar_engine.reconcile_past_events()
        conn = sqlite3.connect(db_path)
        gaps = dict(conn.execute("SELECT event_id, post_event_spy_gap FROM macro_calendar").fetchall())
        conn.close()
        return gaps, fake_yahoo

    def test_a_release_before_the_open_is_measured_on_the_pre_market_bars(self, cal_db_path):
        release = self._stamp(12, 30)
        spy = self._bars(release, [100.0] * 6, [100.0, 100.5, 100.2, 100.1, 100.0, 100.0], [100.0, 99.9, 99.7, 99.8, 99.9, 99.9])
        gaps, fake_yahoo = self._run(cal_db_path, [_make_row(event_id="nfp", event_date=release, is_passed=1)], spy)
        assert gaps["nfp"] == pytest.approx(0.5)
        assert fake_yahoo.get_intraday.call_args.kwargs["prepost"] is True

    def test_a_release_with_no_spy_bar_in_its_window_keeps_a_null_gap(self, cal_db_path):
        release = self._stamp(6, 0)
        measured = self._stamp(14, 0)
        spy = self._bars(measured, [100.0, 100.0], [101.0, 100.4], [99.5, 99.9])
        gaps, _ = self._run(cal_db_path, [
            _make_row(event_id="uk_cpi", event_date=release, currency="GBP", is_passed=1),
            _make_row(event_id="ism", event_date=measured, is_passed=1),
        ], spy)
        assert gaps["uk_cpi"] is None
        assert gaps["ism"] == pytest.approx(1.0)

    def test_an_event_with_a_stored_gap_is_not_measured_again(self, cal_db_path):
        release = self._stamp(14, 0)
        spy = self._bars(release, [100.0], [105.0], [95.0])
        gaps, _ = self._run(cal_db_path, [_make_row(event_id="done", event_date=release, spy_gap=0.3, is_passed=1)], spy)
        assert gaps["done"] == pytest.approx(0.3)


class TestClearUnmeasuredSpyGaps:

    def test_only_gaps_of_exactly_zero_are_cleared(self, cal_db_path):
        conn = sqlite3.connect(cal_db_path)
        conn.executemany("INSERT INTO macro_calendar VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            _make_row(event_id="zero", spy_gap=0.0, is_passed=1),
            _make_row(event_id="measured", spy_gap=0.31, is_passed=1),
            _make_row(event_id="pending", spy_gap=None, is_passed=1),
        ])
        conn.commit()
        conn.close()

        def _conn():
            conn = sqlite3.connect(cal_db_path)
            conn.row_factory = sqlite3.Row
            return conn
        with patch("macro_calendar_engine.get_connection", side_effect=_conn):
            assert clear_unmeasured_spy_gaps() == 1
            assert clear_unmeasured_spy_gaps() == 0
        gaps = dict(sqlite3.connect(cal_db_path).execute("SELECT event_id, post_event_spy_gap FROM macro_calendar").fetchall())
        assert gaps == {"zero": None, "measured": pytest.approx(0.31), "pending": None}


class TestMigrationRunsOnce:

    def test_migrate_db_clears_the_zero_gaps_and_the_volatility_model_once(self, tmp_path, monkeypatch):
        models = tmp_path / "models"
        models.mkdir()
        for name in ("macro_rf.joblib", "macro_xgb.joblib", "macro_xgb.joblib.xgboost-version"):
            (models / name).write_text("x")
        monkeypatch.setattr(db_schema, "MODELS_DIR", models)
        conn = get_connection()
        try:
            conn.execute("DELETE FROM data_migrations WHERE name = 'macro_calendar_unmeasured_spy_gaps_cleared'")
            conn.executemany(
                "INSERT INTO macro_calendar (event_id, event_date, currency, impact, event_name, post_event_spy_gap, is_event_passed) VALUES (?, ?, 'USD', 'High', ?, ?, 1)",
                [("zz_zero", "2031-06-05 12:30:00", "ZZ Zero", 0.0), ("zz_measured", "2031-06-05 14:00:00", "ZZ Measured", 0.4)],
            )
            conn.commit()
            db_schema.migrate_db(conn, conn.cursor())
            gaps = dict(conn.execute("SELECT event_id, post_event_spy_gap FROM macro_calendar WHERE event_id LIKE 'zz_%'").fetchall())
            assert gaps == {"zz_zero": None, "zz_measured": pytest.approx(0.4)}
            assert sorted(p.name for p in models.iterdir()) == ["macro_rf.joblib"]
            assert conn.execute("SELECT COUNT(*) FROM data_migrations WHERE name = 'macro_calendar_unmeasured_spy_gaps_cleared'").fetchone()[0] == 1
            conn.execute("UPDATE macro_calendar SET post_event_spy_gap = 0.0 WHERE event_id = 'zz_zero'")
            conn.commit()
            db_schema.migrate_db(conn, conn.cursor())
            assert conn.execute("SELECT post_event_spy_gap FROM macro_calendar WHERE event_id = 'zz_zero'").fetchone()[0] == 0.0
        finally:
            conn.close()

    def test_migrate_db_removes_the_synthetic_rows_and_their_models_once(self, tmp_path, monkeypatch):
        models = tmp_path / "models"
        models.mkdir()
        for name in ("macro_rf.joblib", "macro_rf.joblib.sklearn-version", "macro_xgb.joblib", "macro_xgb.joblib.xgboost-version", "macro_hmm.joblib"):
            (models / name).write_text("x")
        monkeypatch.setattr(db_schema, "MODELS_DIR", models)
        feed_id = generate_event_id("2031-11-18 07:00:00", "GBP", "ZZ Seed CPI y/y")
        conn = get_connection()
        try:
            conn.execute("DELETE FROM data_migrations WHERE name = 'macro_calendar_synthetic_rows_removed'")
            conn.execute("INSERT INTO macro_calendar (event_id, event_date, currency, impact, event_name, actual_val) VALUES (?, '2031-01-01 14:00:00', 'USD', 'High', 'ZZ Seed Fed', 4.5)", (str(uuid.uuid4()),))
            conn.execute("INSERT INTO macro_calendar (event_id, event_date, currency, impact, event_name) VALUES (?, '2031-11-18 07:00:00', 'GBP', 'High', 'ZZ Seed CPI y/y')", (feed_id,))
            conn.commit()
            db_schema.migrate_db(conn, conn.cursor())
            assert conn.execute("SELECT COUNT(*) FROM macro_calendar WHERE event_name = 'ZZ Seed Fed'").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM macro_calendar WHERE event_id = ?", (feed_id,)).fetchone()[0] == 1
            assert sorted(p.name for p in models.iterdir()) == ["macro_hmm.joblib"]
            assert conn.execute("SELECT COUNT(*) FROM data_migrations WHERE name = 'macro_calendar_synthetic_rows_removed'").fetchone()[0] == 1
            (models / "macro_rf.joblib").write_text("retrained")
            db_schema.migrate_db(conn, conn.cursor())
            assert (models / "macro_rf.joblib").exists()
        finally:
            conn.close()

    def test_migrate_db_corrects_the_stored_times_once(self):
        shifted = "2031-11-18 12:00:00"
        conn = get_connection()
        try:
            conn.execute("DELETE FROM data_migrations WHERE name = 'macro_calendar_utc_event_times'")
            conn.execute(
                "INSERT INTO macro_calendar (event_id, event_date, currency, impact, event_name, post_event_spy_gap) VALUES (?, ?, 'GBP', 'High', 'ZZ CPI y/y', 0.4)",
                (generate_event_id(shifted, "GBP", "ZZ CPI y/y"), shifted),
            )
            conn.commit()
            db_schema.migrate_db(conn, conn.cursor())
            assert conn.execute("SELECT event_date FROM macro_calendar WHERE event_name = 'ZZ CPI y/y'").fetchone()[0] == "2031-11-18 07:00:00"
            assert conn.execute("SELECT COUNT(*) FROM data_migrations WHERE name = 'macro_calendar_utc_event_times'").fetchone()[0] == 1
            db_schema.migrate_db(conn, conn.cursor())
            assert conn.execute("SELECT event_date FROM macro_calendar WHERE event_name = 'ZZ CPI y/y'").fetchone()[0] == "2031-11-18 07:00:00"
        finally:
            conn.close()


def _utc(year, month, day, hour=12):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


class TestResolveActual:

    def test_shift_month_crosses_the_year(self):
        assert _shift_month((2027, 1), 1) == (2026, 12)
        assert _shift_month((2026, 11), 12) == (2025, 11)
        assert _shift_month((2026, 3), 0) == (2026, 3)

    def test_month_over_month_reads_the_month_before_the_release_month(self):
        data = {"CUSR0000SA0": {(2026, 9): 100.0, (2026, 10): 100.3, (2026, 11): 150.0}}
        assert resolve_actual(ACTUAL_SOURCES[("USD", "CPI m/m")], _utc(2026, 11, 18), data) == pytest.approx(0.3)

    def test_year_over_year_compares_the_same_month_a_year_earlier(self):
        data = {"CUUR0000SA0": {(2025, 10): 100.0, (2026, 9): 90.0, (2026, 10): 103.6}}
        assert resolve_actual(ACTUAL_SOURCES[("USD", "CPI y/y")], _utc(2026, 11, 18), data) == pytest.approx(3.6)

    def test_a_january_release_reads_december_of_the_previous_year(self):
        data = {"CUSR0000SA0": {(2026, 11): 100.0, (2026, 12): 99.0}}
        assert resolve_actual(ACTUAL_SOURCES[("USD", "CPI m/m")], _utc(2027, 1, 13), data) == pytest.approx(-1.0)

    def test_a_level_series_is_returned_as_published(self):
        data = {"LNS14000000": {(2026, 10): 4.2}}
        assert resolve_actual(ACTUAL_SOURCES[("USD", "Unemployment Rate")], _utc(2026, 11, 6), data) == 4.2
        assert resolve_actual(ACTUAL_SOURCES[("GBP", "CPI y/y")], _utc(2026, 11, 18), {"D7G7": {(2026, 10): 3.6}}) == 3.6

    def test_payroll_change_is_in_persons_from_thousands(self):
        data = {"CES0000000001": {(2026, 9): 159_000.0, (2026, 10): 159_065.0}}
        assert resolve_actual(ACTUAL_SOURCES[("USD", "Non-Farm Employment Change")], _utc(2026, 11, 6), data) == 65_000.0

    def test_a_month_the_source_has_not_published_is_none_never_an_older_month(self):
        data = {"CUSR0000SA0": {(2026, 8): 99.0, (2026, 9): 100.0}}
        assert resolve_actual(ACTUAL_SOURCES[("USD", "CPI m/m")], _utc(2026, 11, 18), data) is None

    def test_a_missing_prior_month_is_none(self):
        data = {"CUSR0000SA0": {(2026, 10): 100.3}}
        assert resolve_actual(ACTUAL_SOURCES[("USD", "CPI m/m")], _utc(2026, 11, 18), data) is None
        assert resolve_actual(ACTUAL_SOURCES[("USD", "CPI m/m")], _utc(2026, 11, 18), {}) is None

    def test_the_fed_rate_is_the_value_on_the_day_after_the_decision(self):
        spec = ACTUAL_SOURCES[("USD", "Federal Funds Rate")]
        assert resolve_actual(spec, _utc(2026, 9, 16, 19), {"DFEDTARU": {date(2026, 9, 16): 3.75, date(2026, 9, 17): 4.0}}) == 4.0
        assert resolve_actual(spec, _utc(2026, 9, 16, 19), {"DFEDTARU": {date(2026, 9, 16): 3.75}}) is None

    def test_the_bank_rate_is_the_value_on_the_decision_day(self):
        spec = ACTUAL_SOURCES[("GBP", "Official Bank Rate")]
        assert resolve_actual(spec, _utc(2026, 11, 5, 12), {"IUDBEDR": {date(2026, 11, 4): 3.75, date(2026, 11, 5): 4.0}}) == 4.0

    def test_has_actual_source_names_only_the_core_events(self):
        assert has_actual_source("USD", "CPI y/y") and has_actual_source("GBP", "CPI y/y")
        assert not has_actual_source("USD", "FOMC Meeting Minutes")
        assert not has_actual_source("GBP", "GDP m/m")


class TestActualReaders:

    def _post(self, body=None, error=None):
        response = MagicMock()
        response.json.return_value = body
        if error:
            return patch("macro_calendar_engine.requests.post", side_effect=error)
        return patch("macro_calendar_engine.requests.post", return_value=response)

    def test_bls_series_are_keyed_by_year_and_month_and_annual_rows_are_skipped(self):
        body = {"status": "REQUEST_SUCCEEDED", "Results": {"series": [{"seriesID": "LNS14000000", "data": [
            {"year": "2026", "period": "M10", "value": "4.2"},
            {"year": "2026", "period": "M13", "value": "4.0"},
            {"year": "2026", "period": "M09", "value": "-"},
        ]}]}}
        with self._post(body) as post:
            assert _fetch_bls_series(["LNS14000000"], 2026, 2026) == {"LNS14000000": {(2026, 10): 4.2}}
        assert post.call_args.kwargs["json"]["startyear"] == "2024"
        assert post.call_args.kwargs["json"]["endyear"] == "2026"

    def test_a_refused_or_failed_bls_request_is_empty(self):
        import requests
        with self._post({"status": "REQUEST_NOT_PROCESSED", "message": ["daily threshold"]}):
            assert _fetch_bls_series(["X"], 2026, 2026) == {}
        with self._post(error=requests.ConnectionError("down")):
            assert _fetch_bls_series(["X"], 2026, 2026) == {}

    def test_ons_cpi_rate_is_keyed_by_year_and_month(self):
        session = MagicMock()
        session.get.return_value.json.return_value = {"months": [{"date": "2026 OCT", "value": "3.6"}, {"date": "bad", "value": "1"}]}
        with patch("macro_calendar_engine.get_retry_session", return_value=session):
            assert _fetch_ons_cpi_rate() == {"D7G7": {(2026, 10): 3.6}}

    def test_the_fed_rate_is_skipped_without_a_fred_key_and_the_bank_rate_is_still_read(self):
        boe = pd.DataFrame({"IUDBEDR": [4.0]}, index=pd.to_datetime(["2026-11-05"]))
        with patch.dict(os.environ, {"FRED_API_KEY": ""}), patch("macro_calendar_engine.get_retry_session"), \
                patch("macro_calendar_engine.fetch_fred_api") as fred, patch("macro_calendar_engine.fetch_boe_data", return_value=boe) as boe_read:
            out = _fetch_rate_series({"fred", "boe"}, _utc(2026, 11, 1), _utc(2026, 11, 6))
        fred.assert_not_called()
        assert out == {"IUDBEDR": {date(2026, 11, 5): 4.0}}
        assert boe_read.call_args.kwargs["lag_days"] == 0
        assert boe_read.call_args.args[2] == _utc(2026, 10, 25)

    def test_the_fed_rate_is_keyed_by_observation_date(self):
        fed = pd.DataFrame({"DFEDTARU": [3.75, 4.0]}, index=pd.to_datetime(["2026-09-16", "2026-09-17"]))
        with patch.dict(os.environ, {"FRED_API_KEY": "key"}), patch("macro_calendar_engine.get_retry_session"), \
                patch("macro_calendar_engine.fetch_fred_api", return_value=fed):
            out = _fetch_rate_series({"fred"}, _utc(2026, 9, 10), _utc(2026, 9, 20))
        assert out == {"DFEDTARU": {date(2026, 9, 16): 3.75, date(2026, 9, 17): 4.0}}

    def test_only_the_sources_an_event_needs_are_read(self):
        with patch("macro_calendar_engine._fetch_bls_series", return_value={}) as bls, patch("macro_calendar_engine._fetch_ons_cpi_rate", return_value={}) as ons, \
                patch("macro_calendar_engine._fetch_rate_series", return_value={}) as rates:
            _load_actual_series({ACTUAL_SOURCES[("GBP", "CPI y/y")]}, _utc(2026, 11, 1), _utc(2026, 11, 19))
        bls.assert_not_called()
        ons.assert_called_once()
        assert rates.call_args.args == ({"ons"}, _utc(2026, 11, 1), _utc(2026, 11, 19))

    def test_bls_is_read_from_two_years_before_the_earliest_event(self):
        with patch("macro_calendar_engine._fetch_bls_series", return_value={}) as bls, patch("macro_calendar_engine._fetch_rate_series", return_value={}):
            _load_actual_series({ACTUAL_SOURCES[("USD", "CPI y/y")]}, _utc(2026, 1, 13), _utc(2026, 11, 19))
        assert bls.call_args.args == (["CUUR0000SA0"], 2026, 2026)


class TestFillReleasedActuals:

    def _conn(self, db_path):
        def connect():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn
        return connect

    def _stamp(self, days_ago):
        return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")

    def _seed(self, db_path, rows):
        conn = sqlite3.connect(db_path)
        conn.executemany("INSERT INTO macro_calendar (event_id, event_date, currency, impact, event_name, actual_val, actual_text) VALUES (?, ?, ?, 'High', ?, ?, ?)", rows)
        conn.commit()
        conn.close()

    def _actuals(self, db_path):
        conn = sqlite3.connect(db_path)
        out = dict(conn.execute("SELECT event_id, actual_val FROM macro_calendar").fetchall())
        conn.close()
        return out

    def test_released_events_still_without_an_actual_get_it_and_nothing_else_changes(self, cal_db_path):
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        earlier = datetime.now(timezone.utc) - timedelta(days=40)
        ref = _shift_month((recent.year, recent.month), 1)
        earlier_ref = _shift_month((earlier.year, earlier.month), 1)
        self._seed(cal_db_path, [
            ("rate", self._stamp(1), "USD", "Unemployment Rate", None, None),
            ("backfill", self._stamp(40), "USD", "Unemployment Rate", None, None),
            ("speech", self._stamp(1), "USD", "Fed Chairman Warsh Speaks", None, None),
            ("known", self._stamp(1), "USD", "CPI y/y", 3.3, None),
            ("beyond", self._stamp(MACRO_ACTUALS_BACKFILL_DAYS + 5), "USD", "Unemployment Rate", None, None),
            ("future", self._stamp(-1), "USD", "Unemployment Rate", None, None),
            ("split", self._stamp(1), "GBP", "Official Bank Rate", None, "6-0-3"),
        ])
        with patch("macro_calendar_engine.get_connection", side_effect=self._conn(cal_db_path)), \
                patch("macro_calendar_engine._load_actual_series", return_value={"LNS14000000": {ref: 4.2, earlier_ref: 4.4}}) as load:
            assert fill_released_actuals() == 2
        assert self._actuals(cal_db_path) == {"rate": 4.2, "backfill": 4.4, "speech": None, "known": 3.3, "beyond": None, "future": None, "split": None}
        assert load.call_args.args[0] == {ACTUAL_SOURCES[("USD", "Unemployment Rate")]}
        assert load.call_args.args[1] == parse_stored_utc(self._stamp(40))

    def test_a_source_without_the_value_leaves_the_actual_null_for_the_next_run(self, cal_db_path):
        self._seed(cal_db_path, [("rate", self._stamp(1), "USD", "Unemployment Rate", None, None)])
        with patch("macro_calendar_engine.get_connection", side_effect=self._conn(cal_db_path)), \
                patch("macro_calendar_engine._load_actual_series", return_value={}):
            assert fill_released_actuals() == 0
        assert self._actuals(cal_db_path) == {"rate": None}

    def test_no_pending_event_reads_no_source(self, cal_db_path):
        self._seed(cal_db_path, [("speech", self._stamp(1), "USD", "Fed Chairman Warsh Speaks", None, None)])
        with patch("macro_calendar_engine.get_connection", side_effect=self._conn(cal_db_path)), \
                patch("macro_calendar_engine._load_actual_series") as load:
            assert fill_released_actuals() == 0
        load.assert_not_called()

    def test_a_later_feed_upsert_without_an_actual_keeps_the_filled_one(self, cal_db_path):
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        ref = _shift_month((recent.year, recent.month), 1)
        self._seed(cal_db_path, [("rate", self._stamp(1), "USD", "Unemployment Rate", None, None)])
        with patch("macro_calendar_engine.get_connection", side_effect=self._conn(cal_db_path)), \
                patch("macro_calendar_engine._load_actual_series", return_value={"LNS14000000": {ref: 4.2}}):
            fill_released_actuals()
            upsert_calendar_events([_make_row(event_id="rate", actual=None)])
        assert self._actuals(cal_db_path)["rate"] == 4.2

    def test_the_daily_run_fills_actuals_after_storing_the_feed_and_before_reconciling(self):
        calls = []
        with patch.object(macro_calendar_engine, "fetch_and_process_calendar", side_effect=lambda: calls.append("fetch") or []), \
                patch.object(macro_calendar_engine, "upsert_calendar_events", side_effect=lambda _: calls.append("upsert")), \
                patch.object(macro_calendar_engine, "fill_released_actuals", side_effect=lambda: calls.append("actuals")), \
                patch.object(macro_calendar_engine, "reconcile_past_events", side_effect=lambda: calls.append("reconcile")):
            update_macro_calendar()
        assert calls == ["fetch", "upsert", "actuals", "reconcile"]
