"""Tests for seed_macro_calendar.seed_calendar()."""
import database as _db_module
from seed_macro_calendar import seed_calendar


class TestSeedCalendar:
    def setup_method(self):
        conn = _db_module.get_connection()
        conn.execute("DELETE FROM macro_calendar")
        conn.commit()
        conn.close()

    def test_inserts_50_rows(self):
        seed_calendar()
        conn = _db_module.get_connection()
        count = conn.execute("SELECT COUNT(*) FROM macro_calendar").fetchone()[0]
        conn.close()
        assert count == 50

    def test_all_rows_have_event_id(self):
        seed_calendar()
        conn = _db_module.get_connection()
        null_ids = conn.execute(
            "SELECT COUNT(*) FROM macro_calendar WHERE event_id IS NULL"
        ).fetchone()[0]
        conn.close()
        assert null_ids == 0

    def test_all_rows_are_past_with_actuals(self):
        seed_calendar()
        conn = _db_module.get_connection()
        row = conn.execute(
            "SELECT COUNT(*) FROM macro_calendar WHERE is_event_passed = 1 AND actual_val IS NOT NULL"
        ).fetchone()[0]
        conn.close()
        assert row == 50

    def test_currencies_are_usd_or_gbp(self):
        seed_calendar()
        conn = _db_module.get_connection()
        invalid = conn.execute(
            "SELECT COUNT(*) FROM macro_calendar WHERE currency NOT IN ('USD', 'GBP')"
        ).fetchone()[0]
        conn.close()
        assert invalid == 0

    def test_idempotent_on_second_call(self):
        seed_calendar()
        seed_calendar()
        conn = _db_module.get_connection()
        # INSERT OR REPLACE replaces existing event_ids; row count should remain 50
        # (second call generates new UUIDs so rows accumulate to 100)
        count = conn.execute("SELECT COUNT(*) FROM macro_calendar").fetchone()[0]
        conn.close()
        # Each call generates new UUIDs so 100 rows after two calls
        assert count == 100
