"""
tests/test_markets_engine.py  ── MARKETS PAGE ENGINE

Exercises the core business logic behind the Markets page and the dynamic Market Pulse mode:
  - get_exchange_state / get_region_state: tri-state open/pre/closed classification
  - dynamic_region_order: tier ranking + most-recently-opened tie-break + Commodities_FX pin
  - resolve_tile: spot/future auto-swap for the 5 dual-instrument indexes
  - assemble_markets_payload / select_pulse_tickers: shape and static-mode parity with today
"""

import sys
from datetime import datetime, timedelta, timezone, time as dtime
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import markets_engine


# ── get_exchange_state ──────────────────────────────────────────────────────────

class TestGetExchangeState:
    def test_open_when_regular_session_is_open(self):
        with patch("markets_engine.market_pulse.is_exchange_open", return_value=True):
            assert markets_engine.get_exchange_state("NYSE") == "open"

    def test_pre_when_only_premarket_flag_is_open(self):
        def fake(exchange, include_premarket=False):
            return include_premarket
        with patch("markets_engine.market_pulse.is_exchange_open", side_effect=fake):
            assert markets_engine.get_exchange_state("NYSE") == "pre"

    def test_closed_when_neither_flag_is_open(self):
        with patch("markets_engine.market_pulse.is_exchange_open", return_value=False):
            assert markets_engine.get_exchange_state("NYSE") == "closed"


# ── get_region_exchanges ────────────────────────────────────────────────────────

class TestGetRegionExchanges:
    def test_us_region_maps_to_nyse_only(self):
        assert markets_engine.get_region_exchanges("US") == ["NYSE"]

    def test_europe_region_includes_lse_xetra_euronext(self):
        assert set(markets_engine.get_region_exchanges("Europe")) == {"LSE", "XETRA", "Euronext"}

    def test_asia_region_includes_all_four_seeded_exchanges(self):
        assert set(markets_engine.get_region_exchanges("Asia")) == {"ASX", "HKEX", "SSE", "TSE"}

    def test_commodities_fx_has_no_exchange(self):
        assert markets_engine.get_region_exchanges("Commodities_FX") == []


# ── get_region_state ────────────────────────────────────────────────────────────

class TestGetRegionState:
    def test_open_when_all_constituent_exchanges_open(self):
        with patch("markets_engine.market_pulse.is_exchange_open", return_value=True):
            state = markets_engine.get_region_state("Europe")
        assert state["state"] == "open"
        assert state["recency_seconds"] >= 0

    def test_partial_when_only_some_constituent_exchanges_open(self):
        with patch("markets_engine.market_pulse.is_exchange_open", side_effect=lambda ex, include_premarket=False: ex == "LSE"):
            state = markets_engine.get_region_state("Europe")
        assert state["state"] == "partial"
        assert state["recency_seconds"] >= 0

    def test_pre_when_no_exchange_open_but_one_is_premarket(self):
        def fake(exchange, include_premarket=False):
            return include_premarket and exchange == "LSE"
        with patch("markets_engine.market_pulse.is_exchange_open", side_effect=fake):
            state = markets_engine.get_region_state("Europe")
        assert state["state"] == "pre"

    def test_closed_when_no_exchange_open_or_premarket(self):
        with patch("markets_engine.market_pulse.is_exchange_open", return_value=False):
            state = markets_engine.get_region_state("Europe")
        assert state["state"] == "closed"
        assert state["recency_seconds"] > 0

    def test_commodities_fx_is_always_open_with_zero_recency(self):
        state = markets_engine.get_region_state("Commodities_FX")
        assert state == {"state": "open", "recency_seconds": 0.0}


# ── _seconds_since_open / _seconds_until_open ───────────────────────────────────

class TestSecondsHelpers:
    def test_seconds_since_open_measures_elapsed_time_today(self):
        fake_open, fake_close = dtime(9, 0), dtime(17, 0)
        now = datetime(2026, 7, 8, 10, 30, tzinfo=timezone.utc)
        with patch("markets_engine.time_engine.market_window_utc", return_value=(fake_open, fake_close)):
            secs = markets_engine._seconds_since_open("XETRA", now)
        assert secs == pytest.approx(90 * 60, abs=1)

    def test_seconds_until_open_same_day_before_open(self):
        fake_open, fake_close = dtime(9, 0), dtime(17, 0)
        now = datetime(2026, 7, 8, 7, 0, tzinfo=timezone.utc)
        with patch("markets_engine.time_engine.market_window_utc", return_value=(fake_open, fake_close)):
            secs = markets_engine._seconds_until_open("XETRA", now)
        assert secs == pytest.approx(2 * 3600, abs=1)

    def test_seconds_until_open_skips_the_weekend(self):
        fake_open, fake_close = dtime(9, 0), dtime(17, 0)
        today = datetime.now(timezone.utc).date()
        days_to_saturday = (5 - today.weekday()) % 7 or 7
        saturday = today + timedelta(days=days_to_saturday)
        now = datetime.combine(saturday, dtime(12, 0), tzinfo=timezone.utc)
        monday = saturday + timedelta(days=2)
        expected = (datetime.combine(monday, fake_open, tzinfo=timezone.utc) - now).total_seconds()
        with patch("markets_engine.time_engine.market_window_utc", return_value=(fake_open, fake_close)):
            secs = markets_engine._seconds_until_open("XETRA", now)
        assert secs == pytest.approx(expected, abs=1)


# ── dynamic_region_order ────────────────────────────────────────────────────────

class TestDynamicRegionOrder:
    def test_open_tier_ranks_above_pre_and_closed_regardless_of_recency(self, monkeypatch):
        def fake_state(region):
            return {
                "US": {"state": "closed", "recency_seconds": 100},
                "Europe": {"state": "pre", "recency_seconds": 50},
                "Asia": {"state": "open", "recency_seconds": 99999},
            }[region]
        monkeypatch.setattr(markets_engine, "get_region_state", fake_state)
        assert markets_engine.dynamic_region_order() == ["Asia", "Commodities_FX", "Europe", "US"]

    def test_open_tier_most_recently_opened_ranks_first(self, monkeypatch):
        def fake_state(region):
            return {
                "US": {"state": "open", "recency_seconds": 100},        # just opened
                "Europe": {"state": "open", "recency_seconds": 20000},  # opened hours ago
                "Asia": {"state": "closed", "recency_seconds": 5000},
            }[region]
        monkeypatch.setattr(markets_engine, "get_region_state", fake_state)
        assert markets_engine.dynamic_region_order() == ["US", "Commodities_FX", "Europe", "Asia"]

    def test_closed_tier_orders_by_soonest_to_open(self, monkeypatch):
        def fake_state(region):
            return {
                "US": {"state": "closed", "recency_seconds": 5000},
                "Europe": {"state": "closed", "recency_seconds": 1000},
                "Asia": {"state": "closed", "recency_seconds": 3000},
            }[region]
        monkeypatch.setattr(markets_engine, "get_region_state", fake_state)
        assert markets_engine.dynamic_region_order() == ["Europe", "Commodities_FX", "Asia", "US"]

    def test_commodities_fx_always_pinned_at_index_one(self, monkeypatch):
        def fake_state(region):
            return {"US": {"state": "open", "recency_seconds": 1},
                    "Europe": {"state": "closed", "recency_seconds": 1},
                    "Asia": {"state": "closed", "recency_seconds": 2}}[region]
        monkeypatch.setattr(markets_engine, "get_region_state", fake_state)
        order = markets_engine.dynamic_region_order()
        assert order[1] == "Commodities_FX"
        assert len(order) == 4


class TestStaticRegionOrder:
    def test_is_always_europe_us_asia_commodities_regardless_of_state(self, monkeypatch):
        monkeypatch.setattr(markets_engine, "get_region_state", lambda r: {"state": "closed", "recency_seconds": 0})
        assert markets_engine.static_region_order() == ["Europe", "US", "Asia", "Commodities_FX"]


# ── resolve_tile ─────────────────────────────────────────────────────────────────

class TestResolveTile:
    def test_no_future_ticker_always_returns_spot(self):
        row = {"ticker": "^FTSE", "display_name": "UK FTSE 100", "future_ticker": None,
               "future_display_name": None, "exchange": "LSE"}
        with patch("markets_engine.market_pulse.is_exchange_open", return_value=False):
            assert markets_engine.resolve_tile(row) == ("^FTSE", "UK FTSE 100", False)

    def test_shows_spot_when_exchange_open(self):
        row = {"ticker": "^GSPC", "display_name": "US S&P 500", "future_ticker": "ES=F",
               "future_display_name": "S&P 500 Futures", "exchange": "NYSE"}
        with patch("markets_engine.market_pulse.is_exchange_open", return_value=True) as mock_open:
            result = markets_engine.resolve_tile(row)
        assert result == ("^GSPC", "US S&P 500", False)
        mock_open.assert_called_once_with("NYSE", include_premarket=False)

    def test_shows_future_when_exchange_closed(self):
        row = {"ticker": "^GSPC", "display_name": "US S&P 500", "future_ticker": "ES=F",
               "future_display_name": "S&P 500 Futures", "exchange": "NYSE"}
        with patch("markets_engine.market_pulse.is_exchange_open", return_value=False):
            result = markets_engine.resolve_tile(row)
        assert result == ("ES=F", "S&P 500 Futures", True)

    def test_falls_back_to_display_name_when_future_name_missing(self):
        row = {"ticker": "^N225", "display_name": "Nikkei 225", "future_ticker": "NIY=F",
               "future_display_name": None, "exchange": "TSE"}
        with patch("markets_engine.market_pulse.is_exchange_open", return_value=False):
            result = markets_engine.resolve_tile(row)
        assert result == ("NIY=F", "Nikkei 225", True)

    @pytest.mark.parametrize("spot,future", [
        ("^GSPC", "ES=F"), ("^NDX", "NQ=F"), ("^DJI", "YM=F"), ("^RUT", "RTY=F"), ("^N225", "NIY=F"),
    ])
    def test_all_five_dual_instrument_indexes_swap_when_closed(self, spot, future):
        from database import get_ticker_registry_row
        row = get_ticker_registry_row(spot)
        assert row is not None
        with patch("markets_engine.market_pulse.is_exchange_open", return_value=False):
            ticker, _, is_future = markets_engine.resolve_tile(row)
        assert ticker == future
        assert is_future is True


# ── assemble_markets_payload ─────────────────────────────────────────────────────

class TestAssembleMarketsPayload:
    def test_static_view_returns_fixed_region_order(self):
        payload = markets_engine.assemble_markets_payload("static")
        assert payload["view"] == "static"
        assert [r["region"] for r in payload["regions"]] == ["Europe", "US", "Asia", "Commodities_FX"]

    def test_dynamic_view_includes_all_four_regions(self):
        payload = markets_engine.assemble_markets_payload("dynamic")
        assert payload["view"] == "dynamic"
        assert {r["region"] for r in payload["regions"]} == {"Europe", "US", "Asia", "Commodities_FX"}

    def test_unknown_view_defaults_to_dynamic(self):
        assert markets_engine.assemble_markets_payload("bogus")["view"] == "dynamic"

    def test_tile_shape_has_all_required_fields(self):
        payload = markets_engine.assemble_markets_payload("static")
        us_region = next(r for r in payload["regions"] if r["region"] == "US")
        assert len(us_region["tiles"]) > 0
        tile = us_region["tiles"][0]
        for field in ("ticker", "display_name", "region", "is_future", "price", "currency",
                      "change_pts", "change_pct", "is_positive", "invert_color", "asset_type",
                      "sentiment_score", "market_state", "is_stale", "stale_data", "needs_refresh",
                      "sparkline"):
            assert field in tile, f"tile missing field {field}"

    def test_stale_data_only_flagged_when_tile_market_is_open(self):
        with patch("markets_engine.market_pulse.is_exchange_open", return_value=False):
            payload = markets_engine.assemble_markets_payload("static")
        us_region = next(r for r in payload["regions"] if r["region"] == "US")
        tile = us_region["tiles"][0]
        assert tile["market_state"] != "open"
        assert tile["stale_data"] is False

    def test_commodities_fx_region_is_never_empty(self):
        payload = markets_engine.assemble_markets_payload("static")
        commodities = next(r for r in payload["regions"] if r["region"] == "Commodities_FX")
        assert len(commodities["tiles"]) > 0


# ── select_pulse_tickers ─────────────────────────────────────────────────────────

class TestSelectPulseTickers:
    LEGACY_TEN = {"^FTSE", "^FTMC", "GBPUSD=X", "BZ=F", "UK10YG",
                  "^GSPC", "^NDX", "^TYX", "^TNX", "DX-Y.NYB"}

    def test_static_mode_reproduces_todays_default_desktop_set(self):
        # Ticker SELECTION (is_pulse_tile membership) is under test here, not the spot/future
        # swap — force NYSE "open" so ^GSPC/^NDX resolve to spot, matching the legacy dict,
        # regardless of real wall-clock market hours when this test happens to run.
        with patch("markets_engine.market_pulse.is_exchange_open", return_value=True):
            result = markets_engine.select_pulse_tickers(dynamic=False)
        assert set(result["desktop"]) == self.LEGACY_TEN

    def test_static_mode_mobile_excludes_uk10yg_and_tyx(self):
        result = markets_engine.select_pulse_tickers(dynamic=False)
        assert len(result["mobile"]) == 8
        assert "UK10YG" not in result["mobile"]
        assert "^TYX" not in result["mobile"]

    def test_dynamic_mode_respects_requested_counts(self):
        result = markets_engine.select_pulse_tickers(dynamic=True, desktop_count=5, mobile_count=3)
        assert len(result["desktop"]) == 5
        assert len(result["mobile"]) <= 3

    def test_mobile_is_always_a_subset_of_desktop_selection(self):
        result = markets_engine.select_pulse_tickers(dynamic=True, desktop_count=6, mobile_count=6)
        desktop_set = set(result["desktop"])
        assert set(result["mobile"]).issubset(desktop_set) or len(result["mobile"]) == 0


# ── registry_lookup_tickers ───────────────────────────────────────────────────────

class TestRegistryLookupTickers:
    def test_returns_a_resolved_ticker_for_every_active_registry_row(self):
        from database import get_ticker_registry
        with patch("markets_engine.market_pulse.is_exchange_open", return_value=True):
            tickers = markets_engine.registry_lookup_tickers()
        assert len(tickers) == len(get_ticker_registry(enabled_only=True))
        assert "^GSPC" in tickers  # spot, since NYSE forced open above

    def test_swaps_to_future_ticker_when_exchange_closed(self):
        with patch("markets_engine.market_pulse.is_exchange_open", return_value=False):
            tickers = markets_engine.registry_lookup_tickers()
        assert "ES=F" in tickers
        assert "^GSPC" not in tickers
