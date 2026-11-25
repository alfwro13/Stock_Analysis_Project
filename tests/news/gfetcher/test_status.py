"""status: the notification texts after a run (problems and recoveries only, each once) and the health read for the Settings tile."""
import socket
from datetime import datetime, timedelta, timezone

import pytest

from db.database import get_connection
from news.gfetcher import client, status, store
from news.gfetcher.client import IPV4, IPV6, Route
from news.gfetcher.pipeline import FetchSettings, RunResult, settings_from_config

from .helpers import TABLES

pytestmark = pytest.mark.restore_tables(*TABLES)

UNTIL = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean():
    conn = get_connection()
    try:
        for table in ("gfetcher_request_log", "gfetcher_state", "gfetcher_publishers"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM news_articles WHERE feed_source = 'google'")
        conn.commit()
    finally:
        conn.close()


def test_a_normal_run_notifies_nothing():
    assert status.run_notices(RunResult("ok", tickers_fetched=5, articles_new=3)) == []
    assert status.run_notices(RunResult("cooling_down", refused_reason="ipv4: HTTP 429", cooldown_until=UNTIL)) == []
    assert status.run_notices(RunResult("no_tickers")) == []
    assert status.run_notices(RunResult("budget_spent", google_requests=0)) == []


def test_a_refusal_of_one_route_is_a_warning_naming_it():
    notices = status.run_notices(RunResult("ok", refused_routes=[{"route": "ipv4", "reason": "HTTP 429", "until": UNTIL}]))
    assert len(notices) == 1 and notices[0][0] == "Warning"
    assert "refused ipv4 (HTTP 429)" in notices[0][1] and "carried on over the other route" in notices[0][1]


def test_every_route_refused_is_one_warning():
    result = RunResult("refused", refused_reason="ipv4: HTTP 429; ipv6: CAPTCHA page", cooldown_until=UNTIL,
                       refused_routes=[{"route": "ipv6", "reason": "CAPTCHA page", "until": UNTIL}])
    notices = status.run_notices(result)
    assert len(notices) == 1 and notices[0][0] == "Warning"
    assert "refused every route (ipv4: HTTP 429; ipv6: CAPTCHA page)" in notices[0][1]


@pytest.mark.parametrize("result,kind,text", [
    (RunResult("unreachable", unreachable_routes=["ipv6"]), "Error", "no route to Google could connect (ipv6)"),
    (RunResult("budget_spent", google_requests=4), "Warning", "daily request budget is spent"),
    (RunResult("ok", layout_suspects=["AAPL", "KO"]), "Warning", "three runs in a row for AAPL, KO"),
    (RunResult("ok", recovered_routes=["ipv4"]), "Info", "ipv4 answered Google requests again"),
])
def test_other_events(result, kind, text):
    notices = status.run_notices(result)
    assert len(notices) == 1 and notices[0][0] == kind and text in notices[0][1]


def test_status_reports_routes_requests_and_articles(monkeypatch):
    monkeypatch.setattr("news.gfetcher.status.configured_routes",
                        lambda: [Route(IPV4, socket.AF_INET), Route(IPV6, socket.AF_INET6, "2001:db8::10")])
    now = datetime.now(timezone.utc)
    store.set_cooldown(IPV4, now + timedelta(hours=1), "HTTP 429", 2)
    store.mark_success(IPV6, now)
    for outcome in ("ok", "ok", "rate_limited"):
        store.record_request(now, "AAPL", IPV4, "quote_page", 200, outcome, 10)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO news_articles (article_id, ticker, source_list, headline, published_at, fetched_at, feed_source, body_fetched) "
            "VALUES ('zzgf-st1', 'AAPL', 'portfolio', 'H', 1, ?, 'google', 1), ('zzgf-st2', 'AAPL', 'portfolio', 'H2', 1, 1, 'google', 0)",
            (int(now.timestamp()),),
        )
        conn.commit()
    finally:
        conn.close()
    got = status.health_status(now)
    v4, v6 = got["routes"]
    assert (v4["route"], v4["cooling"], v4["cooldown_reason"], v4["consecutive_refusals"]) == ("ipv4", True, "HTTP 429", 2)
    assert (v6["route"], v6["cooling"], v6["consecutive_refusals"]) == ("ipv6", False, 0) and v6["last_success_at"]
    assert (got["requests_24h"], got["refusals_24h"]) == (3, 1)
    assert got["articles"] == {"stored": 2, "added": 1, "with_text": 1}


def test_an_expired_cooldown_reads_as_available():
    now = datetime.now(timezone.utc)
    store.set_cooldown(IPV4, now - timedelta(minutes=1), "HTTP 429", 1)
    route = status.health_status(now)["routes"][0]
    assert route["cooling"] is False and route["cooldown_until"] is None and route["consecutive_refusals"] == 1


def test_settings_come_from_the_config_block():
    got = settings_from_config({"SCOPES": ["portfolio", "bogus"], "MAX_TICKERS_PER_RUN": 5, "GOOGLE_DELAY_SECONDS": 4.5,
                                "FETCH_FULL_TEXT": False, "MAX_PER_TICKER": 8, "MAX_AGE_DAYS": 3, "MAX_REQUESTS_PER_DAY": 50})
    assert got == FetchSettings(scopes=("portfolio",), max_tickers_per_run=5, max_requests_per_day=50, google_delay_seconds=4.5,
                                fetch_full_text=False, max_per_ticker=8, max_age_days=3)
    assert settings_from_config({}) == FetchSettings()
