"""get_market_regime tool: every part with its own as_of and stale flag, missing parts, the HMM row that has a state, Market Pulse scope and extended-hours separation."""
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from database import get_connection
from mcp_server.tools import get_market_regime as tool

from .helpers import call, table_counts, tool_data

_TABLES = ("market_regimes", "macro_regimes", "treasury_auction_results")
_PULSE = {"ZZMRA": "Test Index A", "ZZMRB": "Test Index B"}
_NO_FEAR_GREED = {"value": None, "label": None, "as_of": None}


def _day(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


@pytest.fixture
def regime_tables():
    conn = get_connection()
    try:
        saved = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in _TABLES}
        for t in _TABLES:
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()
    try:
        yield
    finally:
        conn = get_connection()
        try:
            for t in _TABLES:
                conn.execute(f"DELETE FROM {t}")
                for row in saved[t]:
                    conn.execute(f"INSERT INTO {t} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            for ticker in _PULSE:
                conn.execute("DELETE FROM market_pulse_cache WHERE ticker = ?", (ticker,))
                conn.execute("DELETE FROM stock_signals WHERE ticker = ?", (ticker,))
            conn.commit()
        finally:
            conn.close()


@pytest.fixture(autouse=True)
def _fixed_sources(regime_tables):
    with patch("regime_engine.get_latest_fear_greed", return_value=_NO_FEAR_GREED), \
         patch("mcp_server.tools.get_market_regime.get_pulse_index_tickers", return_value=_PULSE):
        yield


def _hmm(date, label="Bull", prob=0.87, state=0, us="Normal", uk="Volatile"):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO market_regimes (date, price_hmm_state, price_hmm_label, price_hmm_prob, us_regime_label, uk_regime_label) VALUES (?, ?, ?, ?, ?, ?)",
            (date, state, label, prob, us, uk),
        )
        conn.commit()
    finally:
        conn.close()


def _stress(date, score=0.42):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO market_regimes (date, market_stress_score, market_stress_features) VALUES (?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET market_stress_score = excluded.market_stress_score, market_stress_features = excluded.market_stress_features",
            (date, score, json.dumps({"vix_level": 17.5, "spy_return": 0.4})),
        )
        conn.commit()
    finally:
        conn.close()


def _macro(date, us="YELLOW", uk="GREEN", tyx=4.9, dxy=104.2, gilt=4.1, gbpusd=1.27, uk_velocity=4.2):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO macro_regimes (date, tyx_close, tnx_close, dxy_close, uk_gilt_close, gbpusd_close, us_yield_velocity, us_threat_level, uk_yield_velocity, uk_threat_level) "
            "VALUES (?, ?, 4.3, ?, ?, ?, 18.5, ?, ?, ?)",
            (date, tyx, dxy, gilt, gbpusd, us, uk_velocity, uk),
        )
        conn.commit()
    finally:
        conn.close()


def _auction(cusip, auction_date, alert):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO treasury_auction_results (cusip, maturity_label, auction_date, high_yield, bid_to_cover, tail_bp, direct_pct, indirect_pct, dealer_pct, offering_amt, alert_fired) "
            "VALUES (?, '10Y', ?, 4.31, 2.45, 1.2, 18.0, 65.0, 17.0, 39000, ?)",
            (cusip, auction_date, alert),
        )
        conn.commit()
    finally:
        conn.close()


def _pulse(ticker, price=5000.0, change_pct=0.5, updated=None, extended=None):
    extended_price, extended_pct, session = extended or (None, None, None)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated, extended_price, extended_change_pct, extended_session) "
            "VALUES (?, ?, ?, 1.0, ?, 1, ?, ?, ?, ?)",
            (ticker, _PULSE[ticker], price, change_pct, updated or time.time() - 60, extended_price, extended_pct, session),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_everything():
    today = _day()
    _hmm(today)
    _stress(today)
    _macro(today)
    _auction("ZZMRC1", today, 0)
    _pulse("ZZMRA")
    _pulse("ZZMRB", price=1.27, change_pct=-0.1)


class TestGetMarketRegime:
    def _regime(self, mcp_client):
        return tool_data(call(mcp_client, "get_market_regime"))

    def test_every_part_is_present_with_its_own_as_of(self, mcp_client):
        _seed_everything()
        with patch("regime_engine.get_latest_fear_greed", return_value={"value": 61.0, "label": "Greed", "as_of": _day()}):
            body = self._regime(mcp_client)
        data = body["data"]
        assert set(data) == {"market_regime", "market_stress", "macro_threat", "treasury_auctions", "fear_greed", "market_pulse"}
        assert body["missing"] == [] and body["stale"] is False
        assert data["market_regime"] == {
            "as_of": _day(), "stale": False, "label": "Bull", "probability": 0.87,
            "turbulence_class": {"us": "Normal", "uk": "Volatile"}, "last_change": None,
        }
        assert data["market_stress"]["score"] == 0.42 and data["market_stress"]["features"]["vix_level"] == 17.5
        assert data["market_stress"]["as_of"] == _day() and data["market_stress"]["stale"] is False
        assert data["macro_threat"]["us"] == {"threat_level": "YELLOW", "yield_velocity_bps": 18.5, "yield_pct": {"10y": 4.3, "30y": 4.9}, "dollar_index": 104.2}
        assert data["macro_threat"]["uk"] == {"threat_level": "GREEN", "yield_velocity_bps": 4.2, "yield_pct": 4.1, "gbp_usd": 1.27}
        assert data["treasury_auctions"]["healthy"] is True and data["treasury_auctions"]["as_of"] == _day()
        assert data["fear_greed"] == {"as_of": _day(), "stale": False, "value": 61.0, "label": "Greed"}
        assert [t["ticker"] for t in data["market_pulse"]["tiles"]] == list(_PULSE)
        assert data["market_pulse"]["stale"] is False

    def test_returns_conclusions_not_series(self, mcp_client):
        _seed_everything()
        body = self._regime(mcp_client)
        assert "history" not in body["data"]["market_stress"]
        assert set(body["data"]["market_stress"]) == {"as_of", "stale", "score", "crash_veto_threshold", "features"}

    def test_dollar_index_and_sterling_are_returned_next_to_the_yields(self, mcp_client):
        _seed_everything()
        body = self._regime(mcp_client)
        macro = body["data"]["macro_threat"]
        assert set(macro) == {"as_of", "stale", "us", "uk"}
        assert macro["us"]["dollar_index"] == 104.2 and macro["us"]["yield_pct"] == {"10y": 4.3, "30y": 4.9}
        assert macro["uk"]["gbp_usd"] == 1.27 and macro["uk"]["yield_pct"] == 4.1
        assert not [m for m in body["missing"] if "is not available for" in m]

    def test_unavailable_closes_are_null_and_listed_never_zero(self, mcp_client):
        _seed_everything()
        _macro(_day(), uk=None, tyx=None, dxy=None, gilt=None, gbpusd=None, uk_velocity=None)
        body = self._regime(mcp_client)
        macro = body["data"]["macro_threat"]
        assert macro["us"]["dollar_index"] is None and macro["us"]["yield_pct"]["30y"] is None
        assert macro["uk"] == {"threat_level": None, "yield_velocity_bps": None, "yield_pct": None, "gbp_usd": None}
        for label in ("US 30Y yield", "US Dollar Index (DXY)", "UK gilt yield and threat level", "GBP/USD"):
            assert f"{label} is not available for {_day()}" in body["missing"]

    def test_nothing_computed_yet_lists_every_part_as_missing(self, mcp_client):
        body = self._regime(mcp_client)
        assert body["data"] == {key: None for key in ("market_regime", "market_stress", "macro_threat", "treasury_auctions", "fear_greed", "market_pulse")}
        assert body["as_of"] is None and body["stale"] is True
        assert tool._MISSING_REGIME in body["missing"] and tool._MISSING_STRESS in body["missing"]
        assert tool._MISSING_MACRO in body["missing"] and tool._MISSING_AUCTIONS in body["missing"]
        assert tool._MISSING_FEAR_GREED in body["missing"] and tool._NO_TILES in body["missing"]
        assert [m for m in body["missing"] if m.startswith("Market Pulse ZZMR")] == [f"Market Pulse {t}: no cached quote" for t in _PULSE]

    def test_hmm_comes_from_the_newest_row_that_has_a_state(self, mcp_client):
        _hmm(_day(3), label="Chop", state=1, prob=0.6)
        _stress(_day(0), score=0.9)
        data = self._regime(mcp_client)["data"]
        assert data["market_regime"]["label"] == "Chop" and data["market_regime"]["as_of"] == _day(3)
        assert data["market_stress"]["as_of"] == _day(0) and data["market_stress"]["score"] == 0.9

    def test_last_change_is_reported(self, mcp_client):
        _hmm(_day(2), label="Bull", state=0)
        _hmm(_day(1), label="Chop", state=1)
        _hmm(_day(0), label="Chop", state=1)
        change = self._regime(mcp_client)["data"]["market_regime"]["last_change"]
        assert change == {"date": _day(1), "from_label": "Bull", "to_label": "Chop"}

    def test_part_older_than_a_missed_nightly_run_is_stale_and_sets_the_oldest_as_of(self, mcp_client):
        _hmm(_day(9))
        _stress(_day(0))
        body = self._regime(mcp_client)
        assert body["data"]["market_regime"]["stale"] is True
        assert body["data"]["market_stress"]["stale"] is False
        assert body["as_of"] == f"{_day(9)}T00:00:00Z"

    def test_missing_turbulence_class_is_listed(self, mcp_client):
        _hmm(_day(), us=None, uk="Normal")
        body = self._regime(mcp_client)
        assert body["data"]["market_regime"]["turbulence_class"] == {"us": None, "uk": "Normal"}
        assert f"US turbulence class is not available for {_day()}" in body["missing"]

    def test_weak_auction_makes_treasury_unhealthy_and_never_ages(self, mcp_client):
        _auction("ZZMRC2", _day(25), 1)
        treasury = self._regime(mcp_client)["data"]["treasury_auctions"]
        assert treasury["healthy"] is False and treasury["stale"] is False
        assert treasury["recent"][0]["maturity_label"] == "10Y"

    def test_crash_veto_threshold_follows_settings(self, mcp_client):
        _stress(_day())
        default = self._regime(mcp_client)["data"]["market_stress"]["crash_veto_threshold"]
        with patch("config.load_config", return_value={"META_SCORING": {"CRASH_VETO": {"MARKET_STRESS_THRESHOLD": 0.9}}}):
            configured = self._regime(mcp_client)["data"]["market_stress"]["crash_veto_threshold"]
        assert (default, configured) == (0.75, 0.9)

    def test_market_pulse_is_limited_to_the_registry_tiles(self, mcp_client):
        _pulse("ZZMRA")
        conn = get_connection()
        try:
            conn.execute("INSERT OR REPLACE INTO market_pulse_cache (ticker, name, price, last_updated) VALUES ('ZZMRHELD', 'A held stock', 10.0, ?)", (time.time(),))
            conn.commit()
        finally:
            conn.close()
        try:
            tiles = self._regime(mcp_client)["data"]["market_pulse"]["tiles"]
        finally:
            conn = get_connection()
            try:
                conn.execute("DELETE FROM market_pulse_cache WHERE ticker = 'ZZMRHELD'")
                conn.commit()
            finally:
                conn.close()
        assert [t["ticker"] for t in tiles] == ["ZZMRA"]

    def test_extended_hours_move_is_kept_apart_from_the_price(self, mcp_client):
        _pulse("ZZMRA", price=5000.0, change_pct=0.5, extended=(5050.0, 1.0, "post"))
        _pulse("ZZMRB", price=1.27, change_pct=-0.1)
        tiles = {t["ticker"]: t for t in self._regime(mcp_client)["data"]["market_pulse"]["tiles"]}
        assert tiles["ZZMRA"]["price"] == 5000.0 and tiles["ZZMRA"]["change_pct"] == 0.5
        assert tiles["ZZMRA"]["extended"] == {"session": "post", "price": 5050.0, "change_pct": 1.0}
        assert tiles["ZZMRB"]["extended"] is None

    def test_tile_as_of_is_the_time_of_the_price_shown(self, mcp_client):
        now = time.time()
        _pulse("ZZMRA", price=297.11, change_pct=35.0, updated=now - 7 * 86400)
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, last_updated) VALUES ('ZZMRA', 219.05, 'USD', ?)",
                (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now - 3600)),),
            )
            conn.commit()
        finally:
            conn.close()
        part = self._regime(mcp_client)["data"]["market_pulse"]
        tile = part["tiles"][0]
        assert tile["price"] == 219.05 and tile["change_pct"] is None
        assert tile["as_of"] == time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(now - 3600)))
        assert part["as_of"] == tile["as_of"]

    def test_tile_past_its_refresh_interval_makes_the_part_stale(self, mcp_client):
        _pulse("ZZMRA", updated=time.time() - 3600)
        with patch("market_pulse.is_trading_session", return_value=True):
            part = self._regime(mcp_client)["data"]["market_pulse"]
        assert part["stale"] is True and part["tiles"][0]["stale"] is True

    def test_a_registry_ticker_without_a_quote_is_listed(self, mcp_client):
        _pulse("ZZMRA")
        body = self._regime(mcp_client)
        assert [t["ticker"] for t in body["data"]["market_pulse"]["tiles"]] == ["ZZMRA"]
        assert "Market Pulse ZZMRB: no cached quote" in body["missing"]

    def test_leaves_the_database_unchanged(self, mcp_client):
        _seed_everything()
        before = table_counts()
        self._regime(mcp_client)
        assert table_counts() == before
