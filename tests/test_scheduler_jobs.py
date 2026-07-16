"""
tests/test_scheduler_jobs.py — scheduler_jobs.py runner-level tests

Covers:
  • run_ml_inference() — wires predicted_movers_engine.log_predictions() and
                          backfill_actual_outcomes() into the existing job, in order,
                          only when there are tickers to process.
"""

import sys
from pathlib import Path
from unittest.mock import patch

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
