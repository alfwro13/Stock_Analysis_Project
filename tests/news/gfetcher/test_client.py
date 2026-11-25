"""client: Google requests go through fetch_body over the Advanced Network routes (IPv4/IPv6 alternating), spaced 3-6 s apart at random,
budgeted and logged; a refusal cools down only the route it came on, and the run ends only when every route is cooling."""
import socket
from datetime import timedelta

import pytest
import requests

from db.database import get_connection
from news.gfetcher import client, store
from news.gfetcher.client import IPV4, IPV6, Route
from outbound_fetch_helpers import FetchRedirected, FetchRefused
from tools.network_engine import NetworkRouting

from .helpers import TABLES, FakeGoogle, http_error, quote_page

pytestmark = pytest.mark.restore_tables(*TABLES)

V4 = Route(IPV4, socket.AF_INET)
V6 = Route(IPV6, socket.AF_INET6, "2001:db8::10")
_REAL_CONFIGURED_ROUTES = client.configured_routes


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
        return [dict(r) for r in conn.execute(
            "SELECT ticker, interface, http_status, outcome, retry_after_s FROM gfetcher_request_log ORDER BY id")]
    finally:
        conn.close()


def _client(routes=(V4,), delay=3, budget=100):
    sleeps = []
    return client.GoogleClient(delay, budget, routes=list(routes), sleep=sleeps.append, clock=lambda: 0.0), sleeps


def test_a_page_is_fetched_through_the_vetted_fetch_over_ipv4(monkeypatch):
    fake = FakeGoogle({"AAPL:NASDAQ": quote_page()})
    monkeypatch.setattr(client, "fetch_body", fake)
    c, _ = _client()
    assert c.get_quote_page("AAPL", "AAPL", "NASDAQ") == quote_page()
    assert fake.urls == ["https://www.google.com/finance/quote/AAPL:NASDAQ?hl=en"]
    assert fake.calls[0]["family"] == socket.AF_INET and fake.calls[0]["source_address"] is None
    assert _log() == [{"ticker": "AAPL", "interface": "ipv4", "http_status": 200, "outcome": "ok", "retry_after_s": None}]


def test_dual_stack_alternates_and_binds_ipv6_to_its_address(monkeypatch):
    fake = FakeGoogle({"": quote_page()})
    monkeypatch.setattr(client, "fetch_body", fake)
    c, _ = _client((V4, V6))
    for t in ("A", "B", "C", "D"):
        c.get_quote_page(t, t, "NYSE")
    assert [(k["family"], k["source_address"]) for k in fake.calls] == [
        (socket.AF_INET, None), (socket.AF_INET6, "2001:db8::10")] * 2
    assert [r["interface"] for r in _log()] == ["ipv4", "ipv6", "ipv4", "ipv6"]


def test_the_gap_is_random_between_the_delay_and_twice_it(monkeypatch):
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"": quote_page()}))
    c, sleeps = _client(delay=0.5)
    for t in ("A", "B", "C", "D", "E", "F"):
        c.get_quote_page(t, t, "NYSE")
    assert c.delay == client.MIN_DELAY_SECONDS
    assert len(sleeps) == 5 and all(3.0 <= s <= 6.0 for s in sleeps)
    assert len(set(sleeps)) > 1


@pytest.mark.parametrize("answer,status,reason", [
    (http_error(429, retry_after=120), 429, "HTTP 429"),
    (http_error(503), 503, "HTTP 503"),
    (FetchRedirected("redirected", "https://www.google.com/sorry/index?continue=x"), None, "CAPTCHA page"),
    ("<html><body>Our systems have detected unusual traffic from your computer network.</body></html>", 200, "CAPTCHA page"),
])
def test_a_refusal_on_the_only_route_ends_the_run_and_cools_it_down(monkeypatch, answer, status, reason):
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"": answer}))
    c, _ = _client()
    with pytest.raises(client.GoogleRefused) as raised:
        c.get_quote_page("AAPL", "AAPL", "NASDAQ")
    assert raised.value.reason == f"ipv4: {reason}"
    state = store.get_state(IPV4)
    assert state.consecutive_refusals == 1 and state.cooldown_until == raised.value.until
    assert _log()[0]["http_status"] == status


def test_a_refusal_on_one_route_moves_the_run_to_the_other(monkeypatch):
    fake = FakeGoogle({"": {socket.AF_INET: http_error(429), socket.AF_INET6: quote_page()}})
    monkeypatch.setattr(client, "fetch_body", fake)
    c, _ = _client((V4, V6))
    assert c.get_quote_page("A", "A", "NYSE") is None
    assert c.get_quote_page("B", "B", "NYSE") == quote_page()
    assert c.get_quote_page("C", "C", "NYSE") == quote_page()
    assert [k["family"] for k in fake.calls] == [socket.AF_INET, socket.AF_INET6, socket.AF_INET6]
    assert store.get_state(IPV4).cooldown_until is not None
    assert store.get_state(IPV6).cooldown_until is None


def test_the_run_ends_only_when_both_routes_are_refused(monkeypatch):
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"": http_error(429)}))
    c, _ = _client((V4, V6))
    assert c.get_quote_page("A", "A", "NYSE") is None
    with pytest.raises(client.GoogleRefused) as raised:
        c.get_quote_page("B", "B", "NYSE")
    assert raised.value.reason == "ipv4: HTTP 429; ipv6: HTTP 429"
    assert raised.value.until == min(store.get_state(IPV4).cooldown_until, store.get_state(IPV6).cooldown_until)


def test_a_cooling_route_is_not_used_by_the_next_run(monkeypatch):
    fake = FakeGoogle({"": quote_page()})
    monkeypatch.setattr(client, "fetch_body", fake)
    store.set_cooldown(IPV4, client._now() + timedelta(hours=1), "HTTP 429", 1)
    c, _ = _client((V4, V6))
    assert [r.name for r in c.active] == [IPV6]
    c.get_quote_page("A", "A", "NYSE")
    c.get_quote_page("B", "B", "NYSE")
    assert [k["family"] for k in fake.calls] == [socket.AF_INET6] * 2


def test_an_unreachable_route_is_dropped_for_the_run_without_a_cooldown(monkeypatch):
    fake = FakeGoogle({"": {socket.AF_INET6: requests.ConnectionError("Cannot assign requested address"), None: quote_page()}})
    monkeypatch.setattr(client, "fetch_body", fake)
    c, _ = _client((V4, V6))
    c.get_quote_page("A", "A", "NYSE")
    assert c.get_quote_page("B", "B", "NYSE") is None
    c.get_quote_page("C", "C", "NYSE")
    assert [k["family"] for k in fake.calls] == [socket.AF_INET, socket.AF_INET6, socket.AF_INET]
    assert c.dropped == {IPV6} and store.get_state(IPV6).cooldown_until is None


def test_with_every_route_unreachable_the_run_has_no_route(monkeypatch):
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"": OSError("network is unreachable")}))
    c, _ = _client()
    assert c.get_quote_page("A", "A", "NYSE") is None
    with pytest.raises(client.NoRoute):
        c.get_quote_page("B", "B", "NYSE")


def test_a_route_that_answered_resets_its_refusal_count(monkeypatch):
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"": quote_page()}))
    store.set_cooldown(IPV4, client._now() - timedelta(minutes=1), "HTTP 429", 2)
    c, _ = _client()
    c.get_quote_page("A", "A", "NYSE")
    c.finish()
    state = store.get_state(IPV4)
    assert (state.consecutive_refusals, state.cooldown_until) == (0, None) and state.last_success_at is not None


@pytest.mark.parametrize("retry_after,expected", [(7200, timedelta(hours=2)), (60, timedelta(hours=1))])
def test_retry_after_lengthens_the_cooldown_but_never_shortens_it(retry_after, expected):
    now = client._now().replace(microsecond=0)
    assert client.enter_cooldown(IPV4, "HTTP 429", retry_after, now) - now == expected


def test_consecutive_refusals_escalate_per_route():
    now = client._now().replace(microsecond=0)
    waits = [client.enter_cooldown(IPV4, "HTTP 429", None, now) - now for _ in range(4)]
    assert waits == [timedelta(hours=1), timedelta(hours=6), timedelta(hours=24), timedelta(hours=24)]
    assert client.enter_cooldown(IPV6, "HTTP 429", None, now) - now == timedelta(hours=1)


@pytest.mark.parametrize("error", [http_error(404), FetchRedirected("redirected", "https://consent.google.com/x"), FetchRefused("too big")])
def test_other_failures_are_logged_errors_not_refusals(monkeypatch, error):
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"": error}))
    c, _ = _client()
    assert c.get_quote_page("AAPL", "AAPL", "NASDAQ") is None
    assert _log()[0]["outcome"] == "error" and c.active == [V4]
    assert store.get_state(IPV4).cooldown_until is None


def test_the_daily_budget_counts_requests_on_both_routes(monkeypatch):
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"": quote_page()}))
    store.record_request(client._now(), "X", IPV4, "quote_page", 200, "ok", 1)
    store.record_request(client._now(), "Y", IPV6, "quote_page", 200, "ok", 1)
    c, _ = _client((V4, V6), budget=3)
    c.get_quote_page("A", "A", "NYSE")
    with pytest.raises(client.BudgetSpent):
        c.get_quote_page("B", "B", "NYSE")


@pytest.mark.parametrize("routing,expected", [
    (NetworkRouting(True, False, ""), [V4]),
    (NetworkRouting(True, True, "2001:db8::10"), [V4, V6]),
    (NetworkRouting(False, True, "2001:db8::10"), [V6]),
    (NetworkRouting(False, False, ""), [V4]),
])
def test_routes_follow_the_advanced_network_settings(monkeypatch, routing, expected):
    monkeypatch.setattr(client, "network_routing", lambda: routing)
    assert _REAL_CONFIGURED_ROUTES() == expected
