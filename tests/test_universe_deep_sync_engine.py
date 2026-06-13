"""Tests for universe_deep_sync_engine orchestration logic."""
import pytest
from unittest.mock import patch, MagicMock

import database as _db_module
from universe_deep_sync_engine import _get_universe_target_tickers, run_universe_deep_sync


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_universe():
    """Wipe universe, notification, and scan-state rows before each test."""
    conn = _db_module.get_connection()
    conn.execute("DELETE FROM market_universe")
    conn.execute("DELETE FROM system_notifications")
    conn.execute("DELETE FROM quant_scan_states")
    conn.commit()
    conn.close()


def _insert_tickers(tickers, is_index=1, is_freetrade=1):
    conn = _db_module.get_connection()
    for t in tickers:
        conn.execute(
            """
            INSERT OR IGNORE INTO market_universe (ticker, company_name, is_index, is_freetrade)
            VALUES (?, ?, ?, ?)
            """,
            (t, t, is_index, is_freetrade),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# _get_universe_target_tickers
# ---------------------------------------------------------------------------

class TestGetUniverseTargetTickers:
    def test_returns_is_index_tickers(self):
        _insert_tickers(["AAPL", "MSFT"], is_index=1)
        _insert_tickers(["JUNK"], is_index=0)
        result = _get_universe_target_tickers(freetrade_firewall=False)
        assert "AAPL" in result
        assert "MSFT" in result
        assert "JUNK" not in result

    def test_freetrade_firewall_filters_non_freetrade(self):
        _insert_tickers(["AAPL"], is_index=1, is_freetrade=1)
        conn = _db_module.get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO market_universe (ticker, company_name, is_index, is_freetrade) VALUES (?,?,1,0)",
            ("NOFT", "NOFT"),
        )
        conn.commit()
        conn.close()
        result = _get_universe_target_tickers(freetrade_firewall=True)
        assert "AAPL" in result
        assert "NOFT" not in result

    def test_freetrade_firewall_off_includes_non_freetrade(self):
        conn = _db_module.get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO market_universe (ticker, company_name, is_index, is_freetrade) VALUES (?,?,1,0)",
            ("NOFT", "NOFT"),
        )
        conn.commit()
        conn.close()
        result = _get_universe_target_tickers(freetrade_firewall=False)
        assert "NOFT" in result

    def test_returns_sorted(self):
        _insert_tickers(["ZZZ", "AAA", "MMM"], is_index=1)
        result = _get_universe_target_tickers(freetrade_firewall=False)
        # Filter to our three test tickers and verify order
        subset = [t for t in result if t in {"ZZZ", "AAA", "MMM"}]
        assert subset == ["AAA", "MMM", "ZZZ"]

    def test_empty_universe_returns_empty_list(self):
        result = _get_universe_target_tickers(freetrade_firewall=False)
        assert result == []


# ---------------------------------------------------------------------------
# run_universe_deep_sync orchestration
# ---------------------------------------------------------------------------


def _run_with_mocked_stages(tickers, stage_exceptions=None):
    """
    Seeds DB with tickers, patches all 5 stage callables, and runs the pipeline.
    stage_exceptions is a dict {stage_name: Exception} to simulate partial failures.
    """
    stage_exceptions = stage_exceptions or {}
    _insert_tickers(tickers, is_index=1)

    fund_exc = stage_exceptions.get("fundamentals")
    meta_exc = stage_exceptions.get("metadata")
    tech_exc = stage_exceptions.get("technicals")
    mom_exc  = stage_exceptions.get("momentum_backfill")
    ml_exc   = stage_exceptions.get("ml_inference")

    def _maybe_raise(exc):
        if exc:
            raise exc

    with (
        patch("universe_fundamentals_engine.run_universe_fundamentals_sync",
              side_effect=lambda *a, **kw: _maybe_raise(fund_exc)),
        patch("ai_prediction_engine.sync_ticker_metadata",
              side_effect=lambda *a, **kw: _maybe_raise(meta_exc)),
        patch("quant_engine.run_daily_quant_scan",
              side_effect=lambda *a, **kw: _maybe_raise(tech_exc)),
        patch("ai_prediction_engine.run_historical_backfill",
              side_effect=lambda *a, **kw: _maybe_raise(mom_exc)),
        patch("ai_prediction_engine.update_daily_ml_predictions",
              side_effect=lambda *a, **kw: _maybe_raise(ml_exc)),
    ):
        run_universe_deep_sync()


class TestRunUniverseDeepSyncEmptyUniverse:
    def test_empty_tickers_returns_early_with_warning_notification(self):
        # No tickers → should post a Warning notification and return
        run_universe_deep_sync()
        conn = _db_module.get_connection()
        rows = conn.execute(
            "SELECT * FROM system_notifications WHERE message_type = 'Warning'"
        ).fetchall()
        conn.close()
        assert len(rows) >= 1
        assert any("aborted" in r["message_text"].lower() for r in rows)


class TestRunUniverseDeepSyncAllOk:
    def test_success_posts_success_notification(self):
        _run_with_mocked_stages(["AAPL", "MSFT"])
        conn = _db_module.get_connection()
        rows = conn.execute(
            "SELECT * FROM system_notifications WHERE message_type = 'Success'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert "COMPLETED" in rows[0]["message_text"]

    def test_success_notification_contains_all_stages(self):
        _run_with_mocked_stages(["AAPL"])
        conn = _db_module.get_connection()
        row = conn.execute(
            "SELECT message_text FROM system_notifications WHERE message_type = 'Success'"
        ).fetchone()
        conn.close()
        for key in ("fundamentals", "metadata", "technicals", "momentum_backfill", "ml_inference"):
            assert key in row["message_text"]


class TestRunUniverseDeepSyncPartialFailure:
    def test_stage1_failure_does_not_abort_remaining_stages(self):
        """A fundamentals failure must not prevent metadata/technicals/ML from running."""
        _run_with_mocked_stages(["AAPL"], stage_exceptions={"fundamentals": RuntimeError("yf timeout")})
        conn = _db_module.get_connection()
        # Should post a Warning (not Success) for partial failure
        rows = conn.execute(
            "SELECT * FROM system_notifications WHERE message_type = 'Warning'"
        ).fetchall()
        conn.close()
        assert any("FAILURES" in r["message_text"] for r in rows)

    def test_partial_failure_includes_failed_stage_in_summary(self):
        _run_with_mocked_stages(["AAPL"], stage_exceptions={"metadata": ValueError("bad data")})
        conn = _db_module.get_connection()
        row = conn.execute(
            "SELECT message_text FROM system_notifications WHERE message_type = 'Warning'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert "metadata=FAILED" in row["message_text"]

    def test_multiple_failures_captured_independently(self):
        _run_with_mocked_stages(
            ["AAPL"],
            stage_exceptions={
                "technicals": RuntimeError("scan failed"),
                "ml_inference": RuntimeError("model missing"),
            },
        )
        conn = _db_module.get_connection()
        row = conn.execute(
            "SELECT message_text FROM system_notifications WHERE message_type = 'Warning'"
        ).fetchone()
        conn.close()
        assert "technicals=FAILED" in row["message_text"]
        assert "ml_inference=FAILED" in row["message_text"]
        # Passing stages should still show OK
        assert "fundamentals=OK" in row["message_text"]
        assert "metadata=OK" in row["message_text"]

    def test_scheduler_start_notification_posted(self):
        _run_with_mocked_stages(["AAPL"])
        conn = _db_module.get_connection()
        rows = conn.execute(
            "SELECT * FROM system_notifications WHERE message_type = 'Scheduler'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert "pipeline started" in rows[0]["message_text"].lower()


# ---------------------------------------------------------------------------
# Stage checkpointing / resume-on-restart
# ---------------------------------------------------------------------------

def _today_str():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _seed_stage_completed(scan_type: str):
    conn = _db_module.get_connection()
    conn.execute(
        "INSERT INTO quant_scan_states (scan_date, scan_type, last_processed_ticker, status) "
        "VALUES (?, ?, '', 'COMPLETED')",
        (_today_str(), scan_type),
    )
    conn.commit()
    conn.close()


class TestDeepSyncStageCheckpointing:

    def test_completed_stage1_is_skipped(self):
        """If deep_sync_s1 is COMPLETED, run_universe_fundamentals_sync must not be called."""
        _insert_tickers(["AAPL"])
        _seed_stage_completed('deep_sync_s1')

        with (
            patch("universe_fundamentals_engine.run_universe_fundamentals_sync") as mock_s1,
            patch("ai_prediction_engine.sync_ticker_metadata"),
            patch("quant_engine.run_daily_quant_scan"),
            patch("ai_prediction_engine.run_historical_backfill"),
            patch("ai_prediction_engine.update_daily_ml_predictions"),
        ):
            run_universe_deep_sync()
            mock_s1.assert_not_called()

    def test_completed_stage2_is_skipped(self):
        """If deep_sync_s2 is COMPLETED, sync_ticker_metadata must not be called."""
        _insert_tickers(["AAPL"])
        _seed_stage_completed('deep_sync_s2')

        with (
            patch("universe_fundamentals_engine.run_universe_fundamentals_sync"),
            patch("ai_prediction_engine.sync_ticker_metadata") as mock_s2,
            patch("quant_engine.run_daily_quant_scan"),
            patch("ai_prediction_engine.run_historical_backfill"),
            patch("ai_prediction_engine.update_daily_ml_predictions"),
        ):
            run_universe_deep_sync()
            mock_s2.assert_not_called()

    def test_completed_stage3_is_skipped(self):
        """If universe_deep_sync is COMPLETED in quant_scan_states, run_daily_quant_scan must not be called."""
        _insert_tickers(["AAPL"])
        _seed_stage_completed('universe_deep_sync')

        with (
            patch("universe_fundamentals_engine.run_universe_fundamentals_sync"),
            patch("ai_prediction_engine.sync_ticker_metadata"),
            patch("quant_engine.run_daily_quant_scan") as mock_s3,
            patch("ai_prediction_engine.run_historical_backfill"),
            patch("ai_prediction_engine.update_daily_ml_predictions"),
        ):
            run_universe_deep_sync()
            mock_s3.assert_not_called()

    def test_completed_stage4_is_skipped(self):
        """If deep_sync_s4 is COMPLETED, run_historical_backfill must not be called."""
        _insert_tickers(["AAPL"])
        _seed_stage_completed('deep_sync_s4')

        with (
            patch("universe_fundamentals_engine.run_universe_fundamentals_sync"),
            patch("ai_prediction_engine.sync_ticker_metadata"),
            patch("quant_engine.run_daily_quant_scan"),
            patch("ai_prediction_engine.run_historical_backfill") as mock_s4,
            patch("ai_prediction_engine.update_daily_ml_predictions"),
        ):
            run_universe_deep_sync()
            mock_s4.assert_not_called()

    def test_completed_stage5_is_skipped(self):
        """If deep_sync_s5 is COMPLETED, update_daily_ml_predictions must not be called."""
        _insert_tickers(["AAPL"])
        _seed_stage_completed('deep_sync_s5')

        with (
            patch("universe_fundamentals_engine.run_universe_fundamentals_sync"),
            patch("ai_prediction_engine.sync_ticker_metadata"),
            patch("quant_engine.run_daily_quant_scan"),
            patch("ai_prediction_engine.run_historical_backfill"),
            patch("ai_prediction_engine.update_daily_ml_predictions") as mock_s5,
        ):
            run_universe_deep_sync()
            mock_s5.assert_not_called()

    def test_successful_stage1_marks_completed(self):
        """A successful Stage 1 must write deep_sync_s1=COMPLETED into quant_scan_states."""
        _insert_tickers(["AAPL"])

        with (
            patch("universe_fundamentals_engine.run_universe_fundamentals_sync"),
            patch("ai_prediction_engine.sync_ticker_metadata"),
            patch("quant_engine.run_daily_quant_scan"),
            patch("ai_prediction_engine.run_historical_backfill"),
            patch("ai_prediction_engine.update_daily_ml_predictions"),
        ):
            run_universe_deep_sync()

        conn = _db_module.get_connection()
        row = conn.execute(
            "SELECT status FROM quant_scan_states WHERE scan_type = 'deep_sync_s1'",
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["status"] == "COMPLETED"

    def test_failed_stage1_does_not_mark_completed(self):
        """A failed Stage 1 must NOT write deep_sync_s1=COMPLETED."""
        _insert_tickers(["AAPL"])
        _run_with_mocked_stages(["AAPL"], stage_exceptions={"fundamentals": RuntimeError("fail")})

        conn = _db_module.get_connection()
        row = conn.execute(
            "SELECT status FROM quant_scan_states WHERE scan_type = 'deep_sync_s1'",
        ).fetchone()
        conn.close()
        assert row is None

    def test_uncompleted_stages_are_still_called_after_partial_checkpoint(self):
        """With stages 1+2 COMPLETED, stages 3, 4, 5 must still be called."""
        _insert_tickers(["AAPL"])
        _seed_stage_completed('deep_sync_s1')
        _seed_stage_completed('deep_sync_s2')

        with (
            patch("universe_fundamentals_engine.run_universe_fundamentals_sync") as mock_s1,
            patch("ai_prediction_engine.sync_ticker_metadata") as mock_s2,
            patch("quant_engine.run_daily_quant_scan") as mock_s3,
            patch("ai_prediction_engine.run_historical_backfill") as mock_s4,
            patch("ai_prediction_engine.update_daily_ml_predictions") as mock_s5,
        ):
            run_universe_deep_sync()
            mock_s1.assert_not_called()
            mock_s2.assert_not_called()
            mock_s3.assert_called_once()
            mock_s4.assert_called_once()
            mock_s5.assert_called_once()
