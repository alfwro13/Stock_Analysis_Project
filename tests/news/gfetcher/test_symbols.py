"""symbols: app ticker -> Google symbol:exchange, the stored mapping first, a not-found ticker left alone for a week."""
from datetime import timedelta

import pytest

from db.database import get_connection
from news.gfetcher import client, store, symbols

from .helpers import NOT_FOUND_PAGE, TABLES, FakeGoogle, quote_page

pytestmark = pytest.mark.restore_tables(*TABLES)


@pytest.fixture(autouse=True)
def _clean():
    conn = get_connection()
    try:
        conn.execute("DELETE FROM gfetcher_symbol_map")
        conn.execute("DELETE FROM gfetcher_request_log")
        conn.commit()
    finally:
        conn.close()


def _client():
    return client.GoogleClient(3, 100, sleep=lambda s: None)


@pytest.mark.parametrize("ticker,expected", [
    ("LLOY.L", [("LLOY", "LON")]),
    ("^FTSE", [("UKX", "INDEXFTSE")]),
    ("000001.SS", [("000001", "SHA")]),
    ("AAPL", [("AAPL", ex) for ex in symbols.US_EXCHANGES]),
    ("^IBEX", []),
    ("GBPUSD=X", []),
    ("ABC.XX", []),
])
def test_candidates(ticker, expected):
    assert symbols.candidates(ticker) == expected


def test_the_first_exchange_with_a_page_is_stored_and_reused(monkeypatch):
    fake = FakeGoogle({"KO:NASDAQ": NOT_FOUND_PAGE, "KO:NYSE": quote_page(title="Coca-Cola (KO) - Google Finance")})
    monkeypatch.setattr(client, "fetch_body", fake)
    now = client._now()
    resolved = symbols.resolve("KO", _client(), now)
    assert (resolved.symbol, resolved.exchange) == ("KO", "NYSE")
    row = store.get_symbol("KO")
    assert (row.google_symbol, row.google_exchange, row.last_probe_status) == ("KO", "NYSE", "found")
    fake.urls.clear()
    symbols.resolve("KO", _client(), now)
    assert fake.urls == ["https://www.google.com/finance/quote/KO:NYSE?hl=en"]


def test_a_ticker_google_does_not_know_is_not_asked_again_for_a_week(monkeypatch):
    fake = FakeGoogle({"": NOT_FOUND_PAGE})
    monkeypatch.setattr(client, "fetch_body", fake)
    now = client._now()
    assert symbols.resolve("ZZZZ", _client(), now) is None
    assert len(fake.urls) == len(symbols.US_EXCHANGES)
    assert symbols.resolve("ZZZZ", _client(), now + timedelta(days=6)) is None
    assert len(fake.urls) == len(symbols.US_EXCHANGES)
    symbols.resolve("ZZZZ", _client(), now + timedelta(days=8))
    assert len(fake.urls) == 2 * len(symbols.US_EXCHANGES)


def test_a_transient_error_stores_nothing(monkeypatch):
    from .helpers import http_error
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"": http_error(500)}))
    assert symbols.resolve("LLOY.L", _client(), client._now()) is None
    assert store.get_symbol("LLOY.L") is None
