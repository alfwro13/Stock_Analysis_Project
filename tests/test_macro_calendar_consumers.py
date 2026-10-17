"""
tests/test_macro_calendar_consumers.py  ── MACRO CALENDAR READERS

Covers the consumers that read upcoming events through db_helpers.get_upcoming_macro_events():
  • ai_engine's 7-day Tier-1 radar: High impact only, soonest first, at most 12 rows, window [now, now + 7d]
  • the Market Sentiment page: urgent (48h, every currency), US and UK lists from one 7-day read
  • the Market Sentiment prompts: per-currency list in [now, now + 7d], highest surprise probability first (NULL last), at most 5
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import page_routes_macro
from ai_engine import AIPromptEngine
from ai_sentiment_engine import AISentimentPromptEngine
from database import get_connection

_STAMP = "%Y-%m-%d %H:%M:%S"


def _at(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime(_STAMP)


def _insert(conn, event_id, when, name, currency="USD", impact="High", forecast=None, previous=None, miss=None, vol=0.0):
    conn.execute(
        "INSERT INTO macro_calendar (event_id, event_date, currency, impact, event_name, forecast_val, previous_val, "
        "ai_consensus_miss_prob, ai_volatility_warning) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, when, currency, impact, name, forecast, previous, miss, vol),
    )


@pytest.fixture
def calendar():
    conn = get_connection()
    try:
        saved = [dict(r) for r in conn.execute("SELECT * FROM macro_calendar")]
        conn.execute("DELETE FROM macro_calendar")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_connection()
    try:
        conn.execute("DELETE FROM macro_calendar")
        for row in saved:
            conn.execute(
                f"INSERT INTO macro_calendar ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values())
            )
        conn.commit()
    finally:
        conn.close()


def _seed(events):
    conn = get_connection()
    try:
        for index, event in enumerate(events):
            _insert(conn, f"zz-consumer-{index}", **event)
        conn.commit()
    finally:
        conn.close()


class TestAiEngineRadar:
    def _radar(self) -> list:
        text = AIPromptEngine()._get_macro_context()
        return [line for line in text.splitlines() if line.startswith("  - [")]

    def test_lists_only_high_impact_events_inside_the_window_soonest_first(self, calendar):
        _seed([
            {"when": _at(days=3), "name": "ZZ Later High", "currency": "GBP", "forecast": 4.5, "previous": 4.25},
            {"when": _at(hours=2), "name": "ZZ Sooner High", "forecast": 3.1, "previous": 3.0},
            {"when": _at(days=1), "name": "ZZ Medium", "impact": "Medium"},
            {"when": _at(days=9), "name": "ZZ Beyond Window"},
            {"when": _at(hours=-3), "name": "ZZ Already Past"},
        ])
        radar = self._radar()
        assert len(radar) == 2
        assert "USD ZZ Sooner High (Est: 3.10 | Prev: 3.00)" in radar[0]
        assert "GBP ZZ Later High" in radar[1]

    def test_caps_the_radar_at_twelve_rows(self, calendar):
        _seed([{"when": _at(hours=1 + n), "name": f"ZZ High {n:02d}"} for n in range(15)])
        radar = self._radar()
        assert len(radar) == 12
        assert "ZZ High 00" in radar[0] and "ZZ High 11" in radar[-1]

    def test_no_events_leaves_the_heading_out(self, calendar):
        assert "7-Day Radar" not in AIPromptEngine()._get_macro_context()


class TestMarketSentimentPage:
    def _context(self, client) -> dict:
        real = page_routes_macro.templates.TemplateResponse
        captured = {}

        def spy(*args, **kwargs):
            captured.update(args[2] if len(args) > 2 else kwargs.get("context", {}))
            return real(*args, **kwargs)

        with patch.object(page_routes_macro.templates, "TemplateResponse", side_effect=spy):
            assert client.get("/market-sentiment").status_code == 200
        return captured

    def test_one_week_read_is_split_into_urgent_us_and_uk_lists(self, client, calendar):
        _seed([
            {"when": _at(hours=2), "name": "ZZ Urgent USD", "forecast": 3.1, "previous": 3.0, "miss": 0.62, "vol": 1.4},
            {"when": _at(hours=5), "name": "ZZ Urgent GBP", "currency": "GBP"},
            {"when": _at(days=4), "name": "ZZ Midweek USD"},
            {"when": _at(days=4, hours=1), "name": "ZZ Midweek GBP", "currency": "GBP", "impact": "Medium"},
            {"when": _at(days=9), "name": "ZZ Beyond Window"},
        ])
        context = self._context(client)
        names = lambda key: [e["event_name"] for e in context[key]]
        assert names("urgent_events") == ["ZZ Urgent USD", "ZZ Urgent GBP"]
        assert names("us_events") == ["ZZ Urgent USD", "ZZ Midweek USD"]
        assert names("uk_events") == ["ZZ Urgent GBP", "ZZ Midweek GBP"]

    def test_events_are_enriched_for_the_template(self, client, calendar):
        _seed([{"when": _at(hours=2), "name": "ZZ Urgent USD", "forecast": 3.1, "previous": 3.0, "miss": 0.62, "vol": 1.4}])
        event = self._context(client)["urgent_events"][0]
        assert event["display_date"] and event["insight"]
        assert event["ai_consensus_miss_prob"] == pytest.approx(0.62)
        assert event["ai_volatility_warning"] == pytest.approx(1.4)

    def test_an_empty_calendar_yields_empty_lists(self, client, calendar):
        context = self._context(client)
        assert context["urgent_events"] == [] and context["us_events"] == [] and context["uk_events"] == []


class TestSentimentPromptEvents:
    def test_each_currency_gets_its_own_events_highest_miss_probability_first(self, calendar):
        _seed([
            {"when": _at(days=1), "name": "ZZ Low USD", "miss": 0.2},
            {"when": _at(days=2), "name": "ZZ High USD", "miss": 0.9},
            {"when": _at(days=3), "name": "ZZ Unscored USD"},
            {"when": _at(days=4), "name": "ZZ Mid USD", "miss": 0.5},
            {"when": _at(days=1), "name": "ZZ Only GBP", "currency": "GBP", "miss": 0.1},
            {"when": _at(days=9), "name": "ZZ Beyond Window", "miss": 0.99},
            {"when": _at(hours=-3), "name": "ZZ Already Past", "miss": 0.99},
        ])
        engine = AISentimentPromptEngine()
        assert [e["event_name"] for e in engine._upcoming_events("USD")] == [
            "ZZ High USD", "ZZ Mid USD", "ZZ Low USD", "ZZ Unscored USD",
        ]
        assert [e["event_name"] for e in engine._upcoming_events("GBP")] == ["ZZ Only GBP"]

    def test_keeps_the_five_highest_and_includes_every_impact_level(self, calendar):
        _seed([{"when": _at(hours=1 + n), "name": f"ZZ Event {n}", "miss": n / 10, "impact": "Low" if n == 9 else "High"} for n in range(10)])
        names = [e["event_name"] for e in AISentimentPromptEngine()._upcoming_events("USD")]
        assert names == [f"ZZ Event {n}" for n in (9, 8, 7, 6, 5)]

    def test_prompt_data_carries_the_events(self, calendar):
        _seed([{"when": _at(days=1), "name": "ZZ Prompt USD", "miss": 0.4}, {"when": _at(days=1), "name": "ZZ Prompt GBP", "currency": "GBP"}])
        engine = AISentimentPromptEngine()
        assert [e["event_name"] for e in engine._gather_us_data()["us_events"]] == ["ZZ Prompt USD"]
        assert [e["event_name"] for e in engine._gather_uk_data()["uk_events"]] == ["ZZ Prompt GBP"]

    def test_a_failed_read_leaves_an_empty_list(self):
        with patch("ai_sentiment_engine.get_upcoming_macro_events", side_effect=RuntimeError("db down")):
            assert AISentimentPromptEngine()._upcoming_events("USD") == []
