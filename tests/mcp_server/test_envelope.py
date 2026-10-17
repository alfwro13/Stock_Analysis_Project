"""Response envelope shared by every tool: fields, stale marker, per-tool stale limit, non-finite numbers."""
from unittest.mock import patch

import pytest

from mcp_server import envelope as envelope_module
from mcp_server.envelope import NIGHTLY_STALE_AFTER_SECONDS, STALE_AFTER_SECONDS, date_epoch, dated_part, envelope, iso_from_stored_utc


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


class TestDateEpoch:
    @pytest.mark.parametrize("day, epoch", [("2023-11-14", 1_699_920_000.0), ("2023-11-14 22:13:20", 1_699_920_000.0), (None, None), ("", None)])
    def test_date_is_midnight_utc(self, day, epoch):
        assert date_epoch(day) == epoch


class TestIsoFromStoredUtc:
    @pytest.mark.parametrize("stored, iso", [("2023-11-14 22:13:20", "2023-11-14T22:13:20Z"), (None, None), ("", None), ("not a date", None)])
    def test_stored_utc_string_becomes_iso_or_null(self, stored, iso):
        assert iso_from_stored_utc(stored) == iso


class TestDatedPart:
    NOW = 2_000_000_000.0

    def part(self, age, **kwargs):
        with patch("mcp_server.envelope.time.time", return_value=self.NOW):
            return dated_part(self.NOW - age, {"x": 1}, **kwargs)

    def test_as_of_is_iso_of_the_epoch_and_payload_follows(self):
        result = self.part(10)
        assert result == {"as_of": "2033-05-18T03:33:10Z", "stale": False, "x": 1}
        assert list(result) == ["as_of", "stale", "x"]

    @pytest.mark.parametrize("age, stale", [(NIGHTLY_STALE_AFTER_SECONDS - 1, False), (NIGHTLY_STALE_AFTER_SECONDS + 1, True)])
    def test_stale_after_defaults_to_the_nightly_limit(self, age, stale):
        assert self.part(age)["stale"] is stale

    def test_stale_after_can_be_narrowed(self):
        assert self.part(3 * 86400, stale_after=2 * 86400)["stale"] is True

    def test_as_of_can_be_the_raw_data_date(self):
        assert self.part(10, as_of="2033-05-18")["as_of"] == "2033-05-18"

    def test_part_that_does_not_age_is_never_stale(self):
        assert self.part(100 * 86400, ages=False)["stale"] is False

    def test_no_epoch_is_null_and_not_stale_by_age(self):
        assert dated_part(None, {"x": 1}) == {"as_of": None, "stale": False, "x": 1}
