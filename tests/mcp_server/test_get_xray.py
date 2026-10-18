"""get_xray tool: summary, findings, missing risk cache, curated errors, caps."""
from unittest.mock import patch

import pytest

from database import get_connection
from mcp_server.tools import get_xray as get_xray_tool
from xray_engine import XRayScopeUnavailable

from .helpers import call, error_text, table_counts, tool_data


def _xray_cache(ticker, beta, cache_date):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO xray_risk_cache (ticker, benchmark, last_updated, beta, annualized_vol) VALUES (?, 'SWDA.L', ?, ?, 0.2)",
            (ticker, cache_date, beta),
        )
        conn.commit()
    finally:
        conn.close()


def _fake_report(**overrides):
    report = {
        "account_id": "all", "portfolio_total_value": 1000.0, "portfolio_total_investment": 900.0,
        "holdings": [
            {"symbol": f"T{i}", "name": f"Tick {i}", "weight": 0.1, "value": 100.0, "max_pairwise_correlation": 0.5 + i / 100}
            for i in range(12)
        ],
        "concentration": {"hhi": 0.1, "top5_weight": 0.5, "top10_weight": 0.9},
        "sector_allocation": [{"name": f"S{i}", "weight": 0.05} for i in range(12)],
        "geographic_allocation": [{"name": "Europe", "weight": 1.0}],
        "country_allocation": [
            {"name": f"Country {i}", "code": f"C{i}", "continent": "Europe", "weight": 0.1} for i in range(12)
        ],
        "fx_exposure": [{"currency": "GBP", "weight": 1.0}],
        "risk_metrics": {
            "portfolio_beta": 1.1, "annualized_vol": 0.2, "max_drawdown": -0.1, "var_95_1d": 20.0, "sharpe_ratio": 0.5,
            "benchmark": "SWDA.L", "cache_date": "2026-10-15", "avg_pairwise_correlation": 0.3, "max_pairwise_correlation": 0.6,
        },
        "recommendations": {
            "sector": [
                {"category": f"S{i}", "status": "exceeds", "current_value": 20.0, "min": None, "max": 10.0, "unit": "%", "message": f"S{i} high"}
                for i in range(7)
            ] + [{"category": "Fine", "status": "within", "current_value": 5.0, "min": 1.0, "max": 10.0, "unit": "%", "message": "ok"}],
            "concentration": [
                {"category": f"C{i}", "status": "below", "current_value": 1.0, "min": 5.0, "max": None, "unit": "%", "message": f"C{i} low"}
                for i in range(5)
            ],
        },
        "data_warnings": ["one warning"],
    }
    report.update(overrides)
    return report


class TestGetXray:
    def _xray(self, mcp_client, account="all"):
        return tool_data(call(mcp_client, "get_xray", {"account": account}))

    def _patched_targets(self):
        import xray_engine
        real = xray_engine.load_config
        return patch("xray_engine.load_config", side_effect=lambda: {**real(), "XRAY_TARGETS": {"concentration_targets": {"max_single_position_pct": 50.0}}})

    def test_envelope_units_and_summary_for_one_account(self, mcp_client, holdings_book):
        with self._patched_targets():
            data = self._xray(mcp_client, f"acct:{holdings_book['first']}")
        assert set(data) == {"as_of", "age_seconds", "market_state", "base_currency", "units", "stale", "missing", "data"}
        assert data["units"]["weights"].startswith("fraction of invested capital")
        body = data["data"]
        assert body["scope_id"] == f"acct:{holdings_book['first']}"
        assert (body["portfolio_total_value"], body["portfolio_total_investment"], body["positions"]) == (1450.0, 1200.0, 2)
        weights = (1200 / 1450, 250 / 1450)
        concentration = body["concentration"]
        assert [p["symbol"] for p in concentration["largest_positions"]] == ["ZZMCPA", "ZZMCPB"]
        assert concentration["largest_positions"][0]["weight"] == pytest.approx(weights[0], abs=1e-4)
        assert concentration["hhi"] == pytest.approx(sum(w * w for w in weights), abs=1e-4)
        assert concentration["top5_weight"] == pytest.approx(1.0, abs=1e-4)
        assert body["currency_exposure"] == [{"currency": "GBP", "weight": 1.0}]
        assert body["geographic_exposure"] and body["sector_exposure"]
        assert body["country_exposure"] and set(body["country_exposure"][0]) == {"name", "code", "weight"}
        assert "holdings" not in body and "correlation_matrix" not in body

    def test_target_breaches_become_findings(self, mcp_client, holdings_book):
        with self._patched_targets():
            body = self._xray(mcp_client, f"acct:{holdings_book['first']}")["data"]
        assert body["targets_checked"] >= 1
        finding = next(f for f in body["findings"] if f["target"] == "Max Single Position")
        assert finding["status"] == "exceeds" and finding["max"] == 50.0 and finding["unit"] == "%"
        assert finding["current_value"] == pytest.approx(1200 / 1450 * 100, abs=0.01)

    def test_without_a_risk_cache_the_risk_figures_are_null_and_marked_missing(self, mcp_client, holdings_book):
        data = self._xray(mcp_client, f"acct:{holdings_book['first']}")
        assert data["data"]["risk"]["portfolio_beta"] is None
        assert data["as_of"] is None and data["stale"] is True
        assert any(item.startswith("risk.portfolio_beta") for item in data["missing"])
        assert any(item.startswith("correlation.maximum") for item in data["missing"])
        assert data["data"]["data_warnings"]

    def test_risk_cache_date_is_the_as_of_and_a_nightly_figure_is_not_stale_in_session(self, mcp_client, holdings_book):
        from datetime import datetime, timedelta, timezone
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        _xray_cache("ZZMCPA", 1.2, yesterday)
        _xray_cache("ZZMCPB", 0.8, yesterday)
        with patch("mcp_server.envelope.get_exchange_session_state", return_value="open"):
            data = self._xray(mcp_client, f"acct:{holdings_book['first']}")
        assert data["as_of"] == f"{yesterday}T00:00:00Z"
        assert data["data"]["risk_cache_date"] == yesterday
        assert data["data"]["risk"]["portfolio_beta"] == pytest.approx((1200 * 1.2 + 250 * 0.8) / 1450, abs=0.001)
        assert data["data"]["risk"]["benchmark"] == "SWDA.L"
        assert not any(item.startswith("risk.portfolio_beta") for item in data["missing"])

    def test_empty_scope_returns_the_curated_message(self, mcp_client, holdings_book):
        text = error_text(call(mcp_client, "get_xray", {"account": f"acct:{holdings_book['empty']}"}))
        assert text == "No holdings found for this scope (Ghostfolio and/or built-in accounts may be empty)."

    def test_scope_unavailable_maps_to_its_own_curated_text(self, mcp_client):
        with patch("mcp_server.tools.get_xray.assemble_xray_report", side_effect=XRayScopeUnavailable("ghostfolio_not_configured")):
            text = error_text(call(mcp_client, "get_xray"))
        assert text == "Ghostfolio is not configured (check GHOSTFOLIO_URL / GHOSTFOLIO_TOKEN)."

    def test_other_failures_return_fixed_text_not_the_exception(self, mcp_client):
        with patch("mcp_server.tools.get_xray.assemble_xray_report", side_effect=RuntimeError("secret-path /etc/shadow")):
            response = call(mcp_client, "get_xray")
        assert response.json()["result"]["isError"] is True
        assert "secret-path" not in response.text and "/etc/shadow" not in response.text

    def test_default_scope_is_all(self, mcp_client):
        with patch("mcp_server.tools.get_xray.assemble_xray_report", return_value=_fake_report()) as xray:
            tool_data(call(mcp_client, "get_xray"))
        xray.assert_called_once_with("all")

    def test_fx_is_read_from_the_persisted_quote(self, mcp_client):
        from portfolio_service import _persisted_fx_reads
        seen = []

        def spy(scope):
            seen.append(_persisted_fx_reads.get())
            return _fake_report()

        with patch("mcp_server.tools.get_xray.assemble_xray_report", side_effect=spy):
            tool_data(call(mcp_client, "get_xray"))
        assert seen == [True]

    def test_summary_is_capped_and_ordered(self):
        body, missing = get_xray_tool._xray_summary(_fake_report())
        assert missing == []
        assert len(body["concentration"]["largest_positions"]) == get_xray_tool.XRAY_TOP_POSITIONS
        assert [h["symbol"] for h in body["correlation"]["most_correlated_holdings"]] == ["T11", "T10", "T9"]
        assert len(body["sector_exposure"]) == get_xray_tool.XRAY_TOP_EXPOSURES
        assert body["targets_checked"] == 13
        assert body["findings_total"] == 12 and len(body["findings"]) == get_xray_tool.XRAY_MAX_FINDINGS == body["findings_cap"]
        assert [f["target"] for f in body["findings"]][:2] == ["S0", "S1"]
        assert all(f["status"] in ("exceeds", "below") for f in body["findings"])
        assert body["data_warnings"] == ["one warning"]

    def test_country_exposure_is_capped_and_carries_only_name_code_weight(self):
        body, _ = get_xray_tool._xray_summary(_fake_report())
        assert len(body["country_exposure"]) == get_xray_tool.XRAY_TOP_EXPOSURES
        assert body["country_exposure"][0] == {"name": "Country 0", "code": "C0", "weight": 0.1}

    def test_unknown_country_share_is_listed_as_missing(self):
        report = _fake_report(country_allocation=[
            {"name": "Germany", "code": "DE", "continent": "Europe", "weight": 0.75},
            {"name": "Unknown", "code": "", "continent": "Other", "weight": 0.25},
        ])
        body, missing = get_xray_tool._xray_summary(report)
        assert missing == ["country_exposure.Unknown: 25.0% of invested capital has no known country"]
        assert [c["name"] for c in body["country_exposure"]] == ["Germany", "Unknown"]

    def test_null_risk_metrics_are_listed_as_missing(self):
        report = _fake_report()
        report["risk_metrics"].update(portfolio_beta=None, max_pairwise_correlation=None, cache_date=None)
        body, missing = get_xray_tool._xray_summary(report)
        assert body["risk"]["portfolio_beta"] is None and body["correlation"]["maximum"] is None
        assert missing == ["risk.portfolio_beta: not available (see data_warnings)", "correlation.maximum: not available (see data_warnings)"]

    def test_leaves_the_database_unchanged(self, mcp_client, holdings_book):
        before = table_counts()
        self._xray(mcp_client, f"acct:{holdings_book['first']}")
        assert table_counts() == before
