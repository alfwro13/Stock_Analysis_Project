"""Response envelope shared by every tool: fields, stale marker, per-tool stale limit, non-finite numbers."""
from unittest.mock import patch

import pytest

from mcp_server import envelope as envelope_module
from mcp_server.envelope import NIGHTLY_STALE_AFTER_SECONDS, STALE_AFTER_SECONDS, envelope


class TestEnvelope:
    def test_envelope_fields(self):
        with patch("mcp_server.envelope.get_exchange_session_state", return_value="closed"):
            result = envelope({"x": 1}, as_of=1_700_000_000.0, units={"u": "v"}, missing=[])
        assert set(result) == {"as_of", "age_seconds", "market_state", "base_currency", "units", "stale", "missing", "data"}
        assert result["as_of"] == "2023-11-14T22:13:20Z"
        assert result["market_state"] == {"LSE": "closed", "NYSE": "closed"}
        assert result["data"] == {"x": 1}
        assert result["base_currency"] == envelope_module.BASE_CURRENCY

    @pytest.mark.parametrize("state, age, missing, stale", [
        ("open", STALE_AFTER_SECONDS + 60, [], True),
        ("open", 10, [], False),
        ("closed", STALE_AFTER_SECONDS * 100, [], False),
        ("post", STALE_AFTER_SECONDS * 100, [], False),
        ("closed", 10, ["account 1: performance snapshot not computed yet"], True),
    ])
    def test_stale_marker(self, state, age, missing, stale):
        with patch("mcp_server.envelope.get_exchange_session_state", return_value=state), patch("mcp_server.envelope.time.time", return_value=2_000_000_000.0):
            result = envelope({}, as_of=2_000_000_000.0 - age, units={}, missing=missing)
        assert result["stale"] is stale
        assert result["missing"] == missing

    def test_no_data_point_is_not_stale_by_age(self):
        with patch("mcp_server.envelope.get_exchange_session_state", return_value="open"):
            result = envelope({}, as_of=None, units={}, missing=[])
        assert result["as_of"] is None and result["age_seconds"] is None and result["stale"] is False

    @pytest.mark.parametrize("stale_after, stale", [(STALE_AFTER_SECONDS, True), (NIGHTLY_STALE_AFTER_SECONDS, False)])
    def test_stale_after_can_be_widened_per_tool(self, stale_after, stale):
        with patch("mcp_server.envelope.get_exchange_session_state", return_value="open"), patch("mcp_server.envelope.time.time", return_value=2_000_000_000.0):
            result = envelope({}, as_of=2_000_000_000.0 - 2 * 86400, units={}, missing=[], stale_after_seconds=stale_after)
        assert result["stale"] is stale

    def test_nightly_tools_are_stale_only_after_a_missed_run(self):
        with patch("mcp_server.envelope.get_exchange_session_state", return_value="open"), patch("mcp_server.envelope.time.time", return_value=2_000_000_000.0):
            overdue = envelope(
                {}, as_of=2_000_000_000.0 - NIGHTLY_STALE_AFTER_SECONDS - 60, units={}, missing=[],
                stale_after_seconds=NIGHTLY_STALE_AFTER_SECONDS,
            )
        assert overdue["stale"] is True

    def test_non_finite_numbers_become_null(self):
        with patch("mcp_server.envelope.get_exchange_session_state", return_value="closed"):
            result = envelope({"a": float("nan"), "b": [float("inf")]}, as_of=None, units={}, missing=[])
        assert result["data"] == {"a": None, "b": [None]}
