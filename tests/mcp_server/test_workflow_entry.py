"""Workflow Monitor nodes for the MCP Server and the Investment Policy input."""
from scheduler_manifest import JOB_GRAPH
from scheduler_monitor import _build_node, _derive_edges


class TestWorkflowEntry:
    def test_policy_is_a_manual_input_that_the_server_consumes(self):
        meta = JOB_GRAPH["investment_policy_source"]
        assert meta["label"] == "Investment Policy"
        assert meta["category"] == "manual" and meta["non_job"] is True
        assert meta["produces"] == ["investment_policy"] and meta["consumes"] == []
        assert "investment_policy" in JOB_GRAPH["claude_mcp_server_source"]["consumes"]

    def test_ticker_snapshot_artifacts_are_consumed_by_the_server(self):
        consumed = set(JOB_GRAPH["claude_mcp_server_source"]["consumes"])
        assert {"stock_signals", "quant_signals", "market_pulse_cache", "fundamentals", "ml_predictions", "watchlist_items", "yahoo_price_data"} <= consumed

    def test_alert_and_news_artifacts_are_consumed_and_produced_by_the_alert_jobs(self):
        consumed = set(JOB_GRAPH["claude_mcp_server_source"]["consumes"])
        assert {"system_notifications", "trap_monitor_results", "alert_referee_log", "news_articles"} <= consumed
        assert {"trap_monitor_results", "alert_referee_log", "system_notifications"} <= set(JOB_GRAPH["trap_monitor_job"]["produces"])
        assert "system_notifications" in JOB_GRAPH["intraday_orchestrator_job"]["produces"]
        assert "news_articles" in JOB_GRAPH["news_feed_job"]["produces"]

    def test_entry_is_an_external_node_with_the_canonical_label(self):
        meta = JOB_GRAPH["claude_mcp_server_source"]
        assert meta["label"] == "MCP Server"
        assert meta["category"] == "external"
        assert meta["produces"] == []

    def test_node_keeps_consumes_and_every_artifact_has_a_producer(self):
        meta = JOB_GRAPH["claude_mcp_server_source"]
        node = _build_node("claude_mcp_server_source", meta, None, {})
        assert node["consumes"] == meta["consumes"] and node["consumes"]
        produced = {artifact for other in JOB_GRAPH.values() for artifact in other["produces"]}
        assert set(node["consumes"]) <= produced

    def test_graph_draws_an_edge_into_the_node(self):
        nodes = [_build_node(job_id, meta, None, {}) for job_id, meta in JOB_GRAPH.items() if not meta.get("dynamic")]
        edges = [e for e in _derive_edges(nodes) if e["to"] == "claude_mcp_server_source"]
        assert {e["via"] for e in edges} == set(JOB_GRAPH["claude_mcp_server_source"]["consumes"])

    def test_yahoo_source_still_has_no_inputs(self):
        assert _build_node("yahoo_finance_source", JOB_GRAPH["yahoo_finance_source"], None, {})["consumes"] == []
