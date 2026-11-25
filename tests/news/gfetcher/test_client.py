"""client: Google requests go through fetch_body, are spaced and budgeted, logged, and a refusal ends the run with an escalating cooldown."""
from datetime import timedelta

import pytest

from db.database import get_connection
from news.gfetcher import client, store
from outbound_fetch_helpers import FetchRedirected, FetchRefused

from .helpers import TABLES, FakeGoogle, http_error, quote_page

pytestmark = pytest.mark.restore_tables(*TABLES)


@pytest.fixture(autouse=True)
def _clean():
    conn = get_connection()
    try:
        conn.execute("DELETE FROM gfetcher_request_log")
        conn.execute("DELETE FROM gfetcher_state")
        conn.commit()
    finally:
        conn.close()


def _log():
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT ticker, http_status, outcome, retry_after_s FROM gfetcher_request_log ORDER BY id")]
    finally:
        conn.close()


def _client(**kwargs):
    sleeps = []
    c = client.GoogleClient(kwargs.pop("delay", 3), kwargs.pop("budget", 100), sleep=sleeps.append, clock=lambda: 0.0)
    return c, sleeps


def test_a_page_is_fetched_through_the_vetted_fetch_with_the_consent_cookie(monkeypatch):
    fake = FakeGoogle({"AAPL:NASDAQ": quote_page()})
    monkeypatch.setattr(client, "fetch_body", fake)
    c, _ = _client()
    assert c.get_quote_page("AAPL", "AAPL", "NASDAQ") == quote_page()
    assert fake.urls == ["https://www.google.com/finance/quote/AAPL:NASDAQ?hl=en"]
    assert _log() == [{"ticker": "AAPL", "http_status": 200, "outcome": "ok", "retry_after_s": None}]


def test_requests_are_spaced_by_at_least_the_floor(monkeypatch):
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"": quote_page()}))
    c, sleeps = _client(delay=0.5)
    c.get_quote_page("A", "A", "NYSE")
    c.get_quote_page("B", "B", "NYSE")
    assert c.delay == client.MIN_DELAY_SECONDS
    assert len(sleeps) == 1 and sleeps[0] >= client.MIN_DELAY_SECONDS * client.JITTER[0]


@pytest.mark.parametrize("answer,status,reason", [
    (http_error(429, retry_after=120), 429, "HTTP 429"),
    (http_error(503), 503, "HTTP 503"),
    (FetchRedirected("redirected", "https://www.google.com/sorry/index?continue=x"), None, "CAPTCHA page"),
    ("<html><body>Our systems have detected unusual traffic from your computer network.</body></html>", 200, "CAPTCHA page"),
])
def test_a_refusal_raises_and_starts_a_cooldown(monkeypatch, answer, status, reason):
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"": answer}))
    c, _ = _client()
    with pytest.raises(client.GoogleRefused) as raised:
        c.get_quote_page("AAPL", "AAPL", "NASDAQ")
    assert raised.value.reason == reason
    state = store.get_state()
    assert state.consecutive_refusals == 1 and state.cooldown_until == raised.value.until
    assert _log()[0]["http_status"] == status


@pytest.mark.parametrize("retry_after,expected", [(7200, timedelta(hours=2)), (60, timedelta(hours=1))])
def test_retry_after_lengthens_the_cooldown_but_never_shortens_it(retry_after, expected):
    now = client._now().replace(microsecond=0)
    assert client.enter_cooldown("HTTP 429", retry_after, now) - now == expected


def test_consecutive_refusals_escalate_the_cooldown():
    now = client._now().replace(microsecond=0)
    waits = [client.enter_cooldown("HTTP 429", None, now) - now for _ in range(4)]
    assert waits == [timedelta(hours=1), timedelta(hours=6), timedelta(hours=24), timedelta(hours=24)]


@pytest.mark.parametrize("error", [http_error(404), FetchRedirected("redirected", "https://consent.google.com/x"),
                                   FetchRefused("too big"), OSError("dns")])
def test_other_failures_are_logged_errors_not_refusals(monkeypatch, error):
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"": error}))
    c, _ = _client()
    assert c.get_quote_page("AAPL", "AAPL", "NASDAQ") is None
    assert _log()[0]["outcome"] == "error"
    assert store.get_state().cooldown_until is None


def test_the_daily_budget_counts_requests_already_logged(monkeypatch):
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"": quote_page()}))
    for _ in range(2):
        store.record_request(client._now(), "X", "quote_page", 200, "ok", 1)
    c, _ = _client(budget=3)
    c.get_quote_page("A", "A", "NYSE")
    with pytest.raises(client.BudgetSpent):
        c.get_quote_page("B", "B", "NYSE")
