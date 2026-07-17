"""
tests/test_scheduler_jobs.py — scheduler_jobs.py runner-level tests

Covers:
  • run_ml_inference() — wires predicted_movers_engine.log_predictions() and
                          backfill_actual_outcomes() into the existing job, in order,
                          only when there are tickers to process.
  • run_overnight_quant_scan() — wires earnings_vol_engine.log_near_earnings_predictions()
                                  and backfill_earnings_drift_outcomes() into the existing job.
  • run_weekend_earnings_scan() / _schedule_earnings_vol_retry() / _run_earnings_vol_retry_job()
    — one-off retry for tickers the main scan couldn't reach (Yahoo fetch failures).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import scheduler_jobs


class TestRunMlInferenceWiresPredictedMovers:
    def test_logs_and_backfills_when_tickers_present(self):
        call_order = []
        with patch("scheduler_jobs.get_universe_tickers", return_value=["AAPL"]), \
             patch("scheduler_jobs.update_daily_ml_predictions"), \
             patch("scheduler_jobs.score_quantile_predictions"), \
             patch("predicted_movers_engine.backfill_actual_outcomes",
                   side_effect=lambda: call_order.append("backfill") or 0) as mock_backfill, \
             patch("predicted_movers_engine.log_predictions",
                   side_effect=lambda: call_order.append("log") or 0) as mock_log, \
             patch("scheduler_jobs.log_sched_notification"):
            scheduler_jobs.run_ml_inference()

        mock_backfill.assert_called_once_with()
        mock_log.assert_called_once_with()
        assert call_order == ["backfill", "log"]

    def test_skips_predicted_movers_when_no_tickers(self):
        with patch("scheduler_jobs.get_universe_tickers", return_value=[]), \
             patch("scheduler_jobs.DataEngine") as mock_engine_cls, \
             patch("predicted_movers_engine.backfill_actual_outcomes") as mock_backfill, \
             patch("predicted_movers_engine.log_predictions") as mock_log, \
             patch("scheduler_jobs.log_sched_notification"):
            mock_engine_cls.return_value.get_all_tickers.return_value = []
            scheduler_jobs.run_ml_inference()

        mock_backfill.assert_not_called()
        mock_log.assert_not_called()


class TestRunOvernightQuantScanWiresEarningsDrift:
    def test_calls_log_near_earnings_and_backfill(self):
        call_order = []
        with patch("scheduler_jobs.DataEngine") as mock_engine_cls, \
             patch("scheduler_jobs.run_daily_quant_scan"), \
             patch("scheduler_jobs.update_all_tail_risks"), \
             patch("scheduler_jobs.log_near_earnings_predictions",
                   side_effect=lambda tickers: call_order.append("log") or 0) as mock_log, \
             patch("scheduler_jobs.backfill_earnings_drift_outcomes",
                   side_effect=lambda: call_order.append("backfill") or 0) as mock_backfill, \
             patch("scheduler_jobs.log_sched_notification"):
            mock_engine_cls.return_value.get_all_tickers.return_value = ["AAPL"]
            scheduler_jobs.run_overnight_quant_scan()

        mock_log.assert_called_once_with(["AAPL"])
        mock_backfill.assert_called_once_with()
        assert call_order == ["log", "backfill"]


class TestRunWeekendEarningsScanSchedulesRetry:
    def test_schedules_retry_when_tickers_failed(self):
        with patch("scheduler_jobs.DataEngine") as mock_engine_cls, \
             patch("scheduler_jobs.run_earnings_vol_scan", return_value=["GOOGL", "INTC"]) as mock_scan, \
             patch("scheduler_jobs._schedule_earnings_vol_retry") as mock_schedule, \
             patch("scheduler_jobs.log_sched_notification"):
            mock_engine_cls.return_value.get_all_tickers.return_value = ["GOOGL", "INTC", "AAPL"]
            scheduler_jobs.run_weekend_earnings_scan()

        mock_scan.assert_called_once_with(["GOOGL", "INTC", "AAPL"])
        mock_schedule.assert_called_once_with(["GOOGL", "INTC"])

    def test_no_retry_scheduled_when_nothing_failed(self):
        with patch("scheduler_jobs.DataEngine") as mock_engine_cls, \
             patch("scheduler_jobs.run_earnings_vol_scan", return_value=[]), \
             patch("scheduler_jobs._schedule_earnings_vol_retry") as mock_schedule, \
             patch("scheduler_jobs.log_sched_notification"):
            mock_engine_cls.return_value.get_all_tickers.return_value = ["AAPL"]
            scheduler_jobs.run_weekend_earnings_scan()

        mock_schedule.assert_called_once_with([])


class TestScheduleEarningsVolRetry:
    def test_noop_when_no_failed_tickers(self):
        with patch("scheduler_jobs.scheduler") as mock_scheduler:
            scheduler_jobs._schedule_earnings_vol_retry([])
        mock_scheduler.add_job.assert_not_called()

    def test_schedules_one_off_job_with_failed_tickers(self):
        with patch("scheduler_jobs.scheduler") as mock_scheduler:
            scheduler_jobs._schedule_earnings_vol_retry(["GOOGL", "INTC"])

        mock_scheduler.add_job.assert_called_once()
        call = mock_scheduler.add_job.call_args
        assert call.args[0] is scheduler_jobs._run_earnings_vol_retry_job
        assert call.kwargs["id"] == "earnings_vol_retry_job"
        assert call.kwargs["kwargs"] == {"tickers": ["GOOGL", "INTC"]}
        assert call.kwargs["replace_existing"] is True


class TestRunEarningsVolRetryJob:
    def test_calls_run_earnings_vol_scan_with_given_tickers(self):
        with patch("scheduler_jobs.run_earnings_vol_scan", return_value=[]) as mock_scan, \
             patch("scheduler_jobs.log_sched_notification"):
            scheduler_jobs._run_earnings_vol_retry_job(tickers=["GOOGL", "INTC"])
        mock_scan.assert_called_once_with(["GOOGL", "INTC"])

    def test_logs_warning_when_tickers_still_fail(self):
        with patch("scheduler_jobs.run_earnings_vol_scan", return_value=["INTC"]), \
             patch("scheduler_jobs.log_sched_notification") as mock_notify:
            scheduler_jobs._run_earnings_vol_retry_job(tickers=["GOOGL", "INTC"])
        assert any(call.args[0] == "Warning" for call in mock_notify.call_args_list)
