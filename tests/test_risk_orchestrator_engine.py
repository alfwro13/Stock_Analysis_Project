"""
tests/test_risk_orchestrator_engine.py — Risk Orchestrator (Portfolio Heat Index) tests

Covers:
  • _sub_score() / _tier_for() — normalization and tier-bucketing boundary conditions
  • persist_heat_index() / persist_ticker_contributions() — DB round-trip
  • run_scan() — wires compute + persist together
  • scheduler_jobs.run_risk_orchestrator_job() — runner-level wiring
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import scheduler_jobs
from database import get_connection
from risk_orchestrator_engine import (
    _sub_score,
    _tier_for,
    persist_heat_index,
    persist_ticker_contributions,
    run_scan,
    TIER_GREEN,
    TIER_YELLOW,
    TIER_RED,
)


class TestSubScore:
    def test_none_returns_zero(self):
        assert _sub_score(None, 2.0, 4.0) == 0.0

    def test_zero_returns_zero(self):
        assert _sub_score(0.0, 2.0, 4.0) == 0.0

    def test_below_yellow_scales_linearly_0_to_50(self):
        assert _sub_score(1.0, 2.0, 4.0) == 25.0

    def test_at_yellow_boundary_is_50(self):
        assert _sub_score(2.0, 2.0, 4.0) == 50.0

    def test_between_yellow_and_red_scales_50_to_100(self):
        assert _sub_score(3.0, 2.0, 4.0) == 75.0

    def test_at_red_boundary_is_100(self):
        assert _sub_score(4.0, 2.0, 4.0) == 100.0

    def test_beyond_red_is_clamped_at_100(self):
        assert _sub_score(10.0, 2.0, 4.0) == 100.0

    def test_uses_absolute_value(self):
        assert _sub_score(-3.0, 2.0, 4.0) == 75.0


class TestTierFor:
    def test_below_yellow_is_green(self):
        assert _tier_for(39.9, 40.0, 75.0) == TIER_GREEN

    def test_at_yellow_boundary_is_yellow(self):
        assert _tier_for(40.0, 40.0, 75.0) == TIER_YELLOW

    def test_at_red_boundary_is_red(self):
        assert _tier_for(75.0, 40.0, 75.0) == TIER_RED

    def test_above_red_is_red(self):
        assert _tier_for(99.0, 40.0, 75.0) == TIER_RED


@pytest.mark.db
class TestPersistHeatIndex:
    def test_insert_then_upsert_round_trip(self):
        result = {
            "scope": "all", "scope_label": "All Accounts", "phi_score": 42.5, "tier": "YELLOW",
            "var_pct_of_equity": 2.1, "var_tier": "YELLOW", "max_correlation": 0.6,
            "correlation_tier": "YELLOW", "drawdown_pct": 3.0, "drawdown_tier": "GREEN",
            "breakdown_json": "[]",
        }
        persist_heat_index(result)
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM portfolio_heat_index WHERE scope = 'all'").fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["phi_score"] == 42.5
        assert row["tier"] == "YELLOW"

        result["phi_score"] = 80.0
        result["tier"] = "RED"
        persist_heat_index(result)
        conn = get_connection()
        try:
            rows = conn.execute("SELECT * FROM portfolio_heat_index WHERE scope = 'all'").fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0]["phi_score"] == 80.0
        assert rows[0]["tier"] == "RED"


@pytest.mark.db
class TestPersistTickerContributions:
    def test_replaces_all_rows_on_each_call(self):
        persist_ticker_contributions([
            {"ticker": "AAA", "risk_score": 10.0, "risk_tier": "GREEN",
             "marginal_var_contribution_pct": 5.0, "max_pairwise_correlation": 0.2, "stop_distance_pct": 10.0},
        ])
        conn = get_connection()
        try:
            rows = conn.execute("SELECT * FROM ticker_risk_contribution").fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0]["ticker"] == "AAA"

        persist_ticker_contributions([
            {"ticker": "BBB", "risk_score": 90.0, "risk_tier": "RED",
             "marginal_var_contribution_pct": 60.0, "max_pairwise_correlation": 0.9, "stop_distance_pct": 0.5},
        ])
        conn = get_connection()
        try:
            rows = conn.execute("SELECT * FROM ticker_risk_contribution").fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0]["ticker"] == "BBB"


@pytest.mark.db
class TestRunScan:
    def test_skips_scopes_with_no_holdings(self):
        with patch("risk_orchestrator_engine.assemble_xray_report", side_effect=RuntimeError("no holdings")), \
             patch("accounts_engine.list_scope_accounts_with_values", return_value=([], 0.0)):
            result = run_scan()
        assert result["scopes_computed"] == 0
        assert result["scopes_skipped"] == 1
        assert result["tickers_scored"] == 0


class TestRunRiskOrchestratorJobRunner:
    def test_calls_engine_run_scan_and_notifies_success(self):
        with patch("risk_orchestrator_engine.run_scan",
                   return_value={"scopes_computed": 2, "scopes_skipped": 0, "tickers_scored": 5}) as mock_scan, \
             patch("scheduler_jobs.log_sched_notification") as mock_notify, \
             patch("scheduler_jobs._mark_job_started"), \
             patch("scheduler_jobs._mark_job_done"), \
             patch("scheduler_jobs.record_job_run"):
            scheduler_jobs.run_risk_orchestrator_job()

        mock_scan.assert_called_once_with()
        success_calls = [c for c in mock_notify.call_args_list if c.args[0] == "Success"]
        assert len(success_calls) == 1

    def test_logs_error_on_failure(self):
        with patch("risk_orchestrator_engine.run_scan", side_effect=Exception("boom")), \
             patch("scheduler_jobs.log_sched_notification") as mock_notify, \
             patch("scheduler_jobs._mark_job_started"), \
             patch("scheduler_jobs._mark_job_done"), \
             patch("scheduler_jobs.record_job_run"):
            scheduler_jobs.run_risk_orchestrator_job()

        error_calls = [c for c in mock_notify.call_args_list if c.args[0] == "Error"]
        assert len(error_calls) == 1
