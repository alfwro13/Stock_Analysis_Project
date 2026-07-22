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
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import scheduler_jobs
from database import get_connection
from risk_orchestrator_engine import (
    _sub_score,
    _tier_for,
    get_critical_scopes,
    persist_heat_index,
    persist_ticker_contributions,
    run_scan,
    evaluate_pretrade_check,
    TIER_GREEN,
    TIER_YELLOW,
    TIER_RED,
)


def _ro_config():
    return {
        "SCHEDULING": {"RISK_ORCHESTRATOR": {
            "THRESHOLDS": {
                "PHI_YELLOW": 40, "PHI_RED": 75,
                "VAR_PCT_YELLOW": 2.0, "VAR_PCT_RED": 4.0,
                "MAX_CORR_YELLOW": 0.5, "MAX_CORR_RED": 0.75,
                "DRAWDOWN_PCT_YELLOW": 5.0, "DRAWDOWN_PCT_RED": 10.0,
            },
            "WEIGHTS": {"VAR": 0.4, "CORRELATION": 0.3, "DRAWDOWN": 0.3},
        }},
    }


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


@pytest.mark.db
class TestGetCriticalScopes:
    def test_returns_every_persisted_scope_row(self):
        persist_heat_index({
            "scope": "all", "scope_label": "All Accounts", "phi_score": 80.0, "tier": "RED",
            "var_pct_of_equity": 3.0, "var_tier": "YELLOW", "max_correlation": 0.9,
            "correlation_tier": "RED", "drawdown_pct": 2.0, "drawdown_tier": "GREEN",
            "breakdown_json": "[]",
        })
        rows = get_critical_scopes()
        matching = [r for r in rows if r["scope"] == "all"]
        assert len(matching) == 1
        assert matching[0]["tier"] == "RED"
        assert matching[0]["correlation_tier"] == "RED"
        assert matching[0]["phi_score"] == 80.0
        assert matching[0]["max_correlation"] == 0.9


class TestFireRiskOrchestratorCriticalAlerts:
    def _config(self, enabled=True):
        return {"NOTIFICATIONS": {"RISK_ORCHESTRATOR_ALERTS": {"ENABLED": enabled}}}

    def test_noop_when_disabled(self):
        with patch("scheduler_jobs.load_config", return_value=self._config(enabled=False)), \
             patch("scheduler_jobs.IntradayOrchestrator") as mock_orch_cls:
            scheduler_jobs._fire_risk_orchestrator_critical_alerts(
                [{"scope": "all", "scope_label": "All Accounts", "phi_score": 90.0, "tier": "RED",
                  "max_correlation": 0.9, "correlation_tier": "RED"}]
            )
        mock_orch_cls.assert_not_called()

    def test_fires_phi_and_correlation_alerts_when_both_red(self):
        mock_orch = MagicMock()
        mock_orch._evaluate_alert_gate.return_value = False  # fire
        with patch("scheduler_jobs.load_config", return_value=self._config()), \
             patch("scheduler_jobs.IntradayOrchestrator", return_value=mock_orch), \
             patch("scheduler_jobs.get_connection"), \
             patch("scheduler_jobs.notify", return_value=True) as mock_notify:
            scheduler_jobs._fire_risk_orchestrator_critical_alerts(
                [{"scope": "all", "scope_label": "All Accounts", "phi_score": 90.0, "tier": "RED",
                  "max_correlation": 0.9, "correlation_tier": "RED"}]
            )
        fired_sources = {c.args[0] for c in mock_notify.call_args_list}
        assert fired_sources == {"risk_orchestrator_phi_critical", "risk_orchestrator_correlation_spike"}
        assert mock_orch.record_alert_fired.call_count == 2

    def test_skips_scope_whose_tiers_are_not_red(self):
        mock_orch = MagicMock()
        with patch("scheduler_jobs.load_config", return_value=self._config()), \
             patch("scheduler_jobs.IntradayOrchestrator", return_value=mock_orch), \
             patch("scheduler_jobs.get_connection"), \
             patch("scheduler_jobs.notify") as mock_notify:
            scheduler_jobs._fire_risk_orchestrator_critical_alerts(
                [{"scope": "all", "scope_label": "All Accounts", "phi_score": 20.0, "tier": "GREEN",
                  "max_correlation": 0.3, "correlation_tier": "GREEN"}]
            )
        mock_notify.assert_not_called()

    def test_gated_alert_gate_suppresses_notify(self):
        mock_orch = MagicMock()
        mock_orch._evaluate_alert_gate.return_value = True  # suppress
        with patch("scheduler_jobs.load_config", return_value=self._config()), \
             patch("scheduler_jobs.IntradayOrchestrator", return_value=mock_orch), \
             patch("scheduler_jobs.get_connection"), \
             patch("scheduler_jobs.notify") as mock_notify:
            scheduler_jobs._fire_risk_orchestrator_critical_alerts(
                [{"scope": "all", "scope_label": "All Accounts", "phi_score": 90.0, "tier": "RED",
                  "max_correlation": 0.9, "correlation_tier": "RED"}]
            )
        mock_notify.assert_not_called()
        mock_orch.record_alert_fired.assert_not_called()


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


class TestEvaluatePretradeCheck:
    """Pillar A pre-trade gatekeeper: verdict/breach/suggestion logic, isolated from the
    (separately-tested) VaR/correlation simulation in xray_engine."""

    def test_approve_when_all_metrics_green(self):
        with patch("risk_orchestrator_engine.assemble_xray_report",
                   return_value={"risk_metrics": {"max_drawdown": -0.01}}), \
             patch("risk_orchestrator_engine.simulate_scope_with_hypothetical_holding",
                   return_value={
                       "var_95_1d": 100.0, "var_pct_of_equity": 0.5, "portfolio_vol": 0.1,
                       "avg_pairwise_correlation": 0.1, "max_pairwise_correlation": 0.1,
                       "portfolio_total_value": 20000.0, "hypothetical_weight": 0.05,
                       "data_warnings": [],
                   }):
            result = evaluate_pretrade_check("all", "AAPL", 1000.0, config=_ro_config())

        assert result["verdict"] == "approve"
        assert result["breached_constraint"] is None
        assert result["suggested_reduced_value"] is None
        assert result["tier"] == TIER_GREEN

    def test_reject_with_var_breach_and_convergent_suggestion(self):
        def _sim(scope, ticker, value):
            var_pct = value / 200.0
            corr = min(0.9, value / 10000.0)
            return {
                "var_95_1d": var_pct * 100, "var_pct_of_equity": var_pct, "portfolio_vol": 0.2,
                "avg_pairwise_correlation": corr, "max_pairwise_correlation": corr,
                "portfolio_total_value": 20000.0 + value,
                "hypothetical_weight": value / (20000.0 + value),
                "data_warnings": [],
            }

        with patch("risk_orchestrator_engine.assemble_xray_report",
                   return_value={"risk_metrics": {"max_drawdown": -0.20}}), \
             patch("risk_orchestrator_engine.simulate_scope_with_hypothetical_holding", side_effect=_sim):
            result = evaluate_pretrade_check("all", "AAPL", 5000.0, config=_ro_config())

        assert result["verdict"] == "reject"
        assert result["tier"] == TIER_RED
        assert result["breached_constraint"] == "VaR"
        assert result["suggested_reduced_value"] is not None
        assert 0 < result["suggested_reduced_value"] < 5000.0

        thresholds = _ro_config()["SCHEDULING"]["RISK_ORCHESTRATOR"]["THRESHOLDS"]
        weights = _ro_config()["SCHEDULING"]["RISK_ORCHESTRATOR"]["WEIGHTS"]
        sim = _sim("all", "AAPL", result["suggested_reduced_value"])
        var_sub = _sub_score(sim["var_pct_of_equity"], thresholds["VAR_PCT_YELLOW"], thresholds["VAR_PCT_RED"])
        corr_sub = _sub_score(sim["max_pairwise_correlation"], thresholds["MAX_CORR_YELLOW"], thresholds["MAX_CORR_RED"])
        dd_sub = _sub_score(20.0, thresholds["DRAWDOWN_PCT_YELLOW"], thresholds["DRAWDOWN_PCT_RED"])
        phi = weights["VAR"] * var_sub + weights["CORRELATION"] * corr_sub + weights["DRAWDOWN"] * dd_sub
        assert _tier_for(phi, thresholds["PHI_YELLOW"], thresholds["PHI_RED"]) != TIER_RED

    def test_no_suggestion_when_risk_is_not_size_dependent(self):
        # simulate() ignores `value` entirely — reducing the size can never help, so the
        # binary search must correctly report "no smaller size fixes this" rather than
        # fabricating a number.
        with patch("risk_orchestrator_engine.assemble_xray_report",
                   return_value={"risk_metrics": {"max_drawdown": -0.20}}), \
             patch("risk_orchestrator_engine.simulate_scope_with_hypothetical_holding",
                   return_value={
                       "var_95_1d": 900.0, "var_pct_of_equity": 4.5, "portfolio_vol": 0.3,
                       "avg_pairwise_correlation": 0.8, "max_pairwise_correlation": 0.8,
                       "portfolio_total_value": 25000.0, "hypothetical_weight": 0.2,
                       "data_warnings": [],
                   }):
            result = evaluate_pretrade_check("all", "AAPL", 5000.0, config=_ro_config())

        assert result["verdict"] == "reject"
        assert result["suggested_reduced_value"] is None

    def test_propagates_runtime_error_for_empty_scope(self):
        with patch("risk_orchestrator_engine.assemble_xray_report", side_effect=RuntimeError("no holdings")):
            with pytest.raises(RuntimeError):
                evaluate_pretrade_check("all", "AAPL", 1000.0, config=_ro_config())
