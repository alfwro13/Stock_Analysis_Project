"""pipeline: one run end to end against fake Google and publisher answers.

Covers ticker selection (scopes, fair rotation, refetch floor, unfetchable tickers), the cooldown gate,
stopping at the first refusal, merging, market-wide articles, the publisher policy, stored rows and pruning.
"""
import socket
from datetime import datetime, timedelta, timezone

import pytest

from db.database import get_connection
from news.gfetcher import article_text, client, pipeline, store
from news.gfetcher.client import IPV4, IPV6, Route
from news.gfetcher.pipeline import FetchSettings, run_fetch

from .helpers import NOT_FOUND_PAGE, TABLES, FakeGoogle, article_html, card, http_error, quote_page

pytestmark = pytest.mark.restore_tables(*TABLES)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    conn = get_connection()
    try:
        for table in TABLES:
            if table == "news_articles":
                conn.execute("DELETE FROM news_articles WHERE feed_source = 'google'")
            else:
                conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(pipeline, "get_mutual_fund_tickers", lambda tickers: set())
    monkeypatch.setattr(pipeline, "ignored_tickers_set", lambda: set())
    monkeypatch.setattr("news.news_feed_engine._get_company_name", lambda ticker: None)


def _lists(monkeypatch, ticker_map, indexes=()):
    monkeypatch.setattr("news.news_feed_engine._build_ticker_source_map", lambda: dict(ticker_map))
    monkeypatch.setattr("market_pulse.get_index_tickers", lambda: {t: t for t in indexes})


def _publishers(monkeypatch, answer=None):
    calls = []

    def fake(url, headers, **kwargs):
        calls.append(url)
        if isinstance(answer, BaseException):
            raise answer
        return answer if answer is not None else article_html()
    monkeypatch.setattr(article_text, "fetch_body", fake)
    return calls


def _rows():
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT ticker, source_list, headline, summary, full_text, body_fetched, url, publisher, published_at, "
            "feed_source, published_precision FROM news_articles WHERE feed_source = 'google' ORDER BY ticker, url"
        )]
    finally:
        conn.close()


def _run(**kwargs):
    return run_fetch(FetchSettings(**kwargs), sleep=lambda s: None)


def test_a_run_stores_one_row_per_ticker_and_article(monkeypatch):
    _lists(monkeypatch, {"AAPL": "portfolio", "MSFT": "watchlist"}, indexes=["^FTSE", "GBPUSD=X"])
    shared = card("https://zacks.com/shared", "Big tech rallies")
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({
        "AAPL:NASDAQ": quote_page(cards=[card("https://zacks.com/aapl", "Apple beats", "Zacks"), shared]),
        "MSFT:NASDAQ": quote_page(cards=[shared]),
        "UKX:INDEXFTSE": quote_page(title="FTSE 100 - Google Finance", cards=[card("https://cnbc.com/ftse", "FTSE falls", "CNBC")]),
    }))
    calls = _publishers(monkeypatch)
    result = _run()
    assert result.status == "ok" and result.tickers_fetched == 3 and result.articles_new == 4
    assert sorted(calls) == ["https://cnbc.com/ftse", "https://zacks.com/aapl", "https://zacks.com/shared"]
    rows = _rows()
    assert [(r["ticker"], r["source_list"], r["url"]) for r in rows] == [
        ("AAPL", "portfolio", "https://zacks.com/aapl"), ("AAPL", "portfolio", "https://zacks.com/shared"),
        ("MSFT", "watchlist", "https://zacks.com/shared"), ("^FTSE", "index", "https://cnbc.com/ftse"),
    ]
    first = rows[0]
    assert first["body_fetched"] == 1 and first["summary"] == first["full_text"][:pipeline.SUMMARY_CHARS]
    assert (first["publisher"], first["published_precision"]) == ("Zacks", "hour")
    assert store.get_state("ipv4").last_success_at is not None


def test_a_second_run_downloads_nothing_already_stored(monkeypatch):
    _lists(monkeypatch, {"AAPL": "portfolio"})
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"AAPL:NASDAQ": quote_page(cards=[card("https://zacks.com/a", "A")])}))
    calls = _publishers(monkeypatch)
    _run()
    assert _run(min_refetch_minutes=0).articles_new == 0
    assert calls == ["https://zacks.com/a"]


def test_paywalled_articles_are_dropped_and_bot_blocked_ones_kept_without_a_body(monkeypatch):
    _lists(monkeypatch, {"AAPL": "portfolio"})
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"AAPL:NASDAQ": quote_page(cards=[
        card("https://www.barrons.com/a", "Paywalled"), card("https://www.benzinga.com/b", "Bot blocked"),
        card("https://new.example/c", "Login wall"),
    ])}))
    calls = _publishers(monkeypatch, http_error(401))
    result = _run()
    assert [r["headline"] for r in _rows()] == ["Bot blocked"]
    assert _rows()[0]["full_text"] is None and _rows()[0]["body_fetched"] == 0 and _rows()[0]["summary"] is None
    assert calls == ["https://new.example/c"]
    assert result.text_statuses == {"dropped_paywall": 1, "skipped": 1, "paywall": 1}


def test_articles_seen_only_in_fallback_lists_go_to_market(monkeypatch):
    etfs = {f"ETF{i}.L": "portfolio" for i in range(3)}
    _lists(monkeypatch, etfs)
    generic = [card(f"https://cnbc.com/g{i}", f"Markets {i}") for i in range(6)]
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({f"ETF{i}:LON": quote_page(cards=generic) for i in range(3)}))
    _publishers(monkeypatch)
    _run()
    rows = _rows()
    assert {r["ticker"] for r in rows} == {pipeline.MARKET_TICKER} and len(rows) == 6
    assert {r["source_list"] for r in rows} == {"market"}


def test_the_run_stops_at_the_first_refusal_and_keeps_what_it_got(monkeypatch):
    _lists(monkeypatch, {"AAPL": "portfolio", "KO": "portfolio", "MSFT": "portfolio"})
    fake = FakeGoogle({"AAPL:NASDAQ": quote_page(cards=[card("https://zacks.com/a", "A")]), "KO:": http_error(429)})
    monkeypatch.setattr(client, "fetch_body", fake)
    _publishers(monkeypatch)
    result = _run()
    assert result.status == "refused" and result.refused_reason == "ipv4: HTTP 429"
    assert not any("MSFT" in u for u in fake.urls)
    assert [r["ticker"] for r in _rows()] == ["AAPL"]
    assert [r["route"] for r in result.refused_routes] == ["ipv4"]


def test_a_run_during_a_cooldown_sends_nothing(monkeypatch):
    _lists(monkeypatch, {"AAPL": "portfolio"})
    store.set_cooldown("ipv4", datetime.now(timezone.utc) + timedelta(hours=1), "HTTP 429", 1)
    result = _run()
    assert result.status == "cooling_down" and result.refused_reason == "ipv4: HTTP 429"


def test_scopes_filter_the_lists(monkeypatch):
    _lists(monkeypatch, {"AAPL": "portfolio", "KO": "both", "MSFT": "watchlist"}, indexes=["^FTSE"])
    now = datetime.now(timezone.utc)
    pick = lambda scopes: sorted(t for t, _ in pipeline.select_tickers(FetchSettings(scopes=scopes), now))
    assert pick(("portfolio",)) == ["AAPL", "KO"]
    assert pick(("watchlist",)) == ["KO", "MSFT"]
    assert pick(("indexes",)) == ["^FTSE"]


def test_unfetchable_tickers_are_never_selected(monkeypatch):
    _lists(monkeypatch, {"AAPL": "portfolio", "0P0000X.L": "portfolio", "IGN": "portfolio", "TBILL-1": "portfolio"},
           indexes=["^FTAS", "UK10YG"])
    monkeypatch.setattr(pipeline, "get_mutual_fund_tickers", lambda tickers: {"0P0000X.L"})
    monkeypatch.setattr(pipeline, "ignored_tickers_set", lambda: {"IGN"})
    monkeypatch.setattr(pipeline, "is_synthetic_ticker", lambda t: t.startswith("TBILL-"))
    assert [t for t, _ in pipeline.select_tickers(FetchSettings(), datetime.now(timezone.utc))] == ["AAPL"]


def test_rotation_is_fair_and_respects_the_refetch_floor(monkeypatch):
    _lists(monkeypatch, {"AAPL": "portfolio", "KO": "portfolio", "MSFT": "watchlist"}, indexes=["^FTSE"])
    now = datetime.now(timezone.utc)
    store.save_ticker_fetch("AAPL", "portfolio", now - timedelta(hours=3), 5)
    store.save_ticker_fetch("KO", "portfolio", now - timedelta(minutes=10), 5)
    store.save_ticker_fetch("MSFT", "watchlist", now - timedelta(hours=5), 5)
    picked = [t for t, _ in pipeline.select_tickers(FetchSettings(max_tickers_per_run=3, min_refetch_minutes=60), now)]
    assert picked == ["^FTSE", "MSFT", "AAPL"]


def test_old_articles_are_not_stored_and_expired_google_rows_are_pruned(monkeypatch):
    _lists(monkeypatch, {"AAPL": "portfolio"})
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"AAPL:NASDAQ": quote_page(cards=[
        card("https://zacks.com/new", "New", age="1 hour ago"), card("https://zacks.com/old", "Old", age="2 weeks ago"),
    ])}))
    _publishers(monkeypatch)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO news_articles (article_id, ticker, source_list, headline, published_at, fetched_at, feed_source) "
            "VALUES ('zzgf-expired', 'AAPL', 'portfolio', 'Expired', 1, 1, 'google'), "
            "('zzgf-yahoo', 'AAPL', 'portfolio', 'Yahoo row', 1, 1, 'yahoo')"
        )
        conn.commit()
        result = _run()
        assert [r["headline"] for r in _rows()] == ["New"]
        assert result.articles_pruned == 1
        assert conn.execute("SELECT COUNT(*) FROM news_articles WHERE article_id = 'zzgf-yahoo'").fetchone()[0] == 1
    finally:
        conn.execute("DELETE FROM news_articles WHERE article_id = 'zzgf-yahoo'")
        conn.commit()
        conn.close()


def test_the_page_date_replaces_a_coarse_estimate(monkeypatch):
    _lists(monkeypatch, {"AAPL": "portfolio"})
    day = (datetime.now(timezone.utc) - timedelta(days=2)).date()
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"AAPL:NASDAQ": quote_page(cards=[card("https://zacks.com/a", "A", age="3 days ago")])}))
    _publishers(monkeypatch, article_html(date=f"{day.isoformat()}T08:00:00Z"))
    _run()
    row = _rows()[0]
    assert row["published_precision"] == "day"
    assert datetime.fromtimestamp(row["published_at"], tz=timezone.utc).date() == day


def test_a_valid_page_with_no_news_three_runs_running_is_a_layout_suspect(monkeypatch):
    _lists(monkeypatch, {"AAPL": "portfolio"})
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"AAPL:NASDAQ": quote_page()}))
    results = [_run(min_refetch_minutes=0) for _ in range(4)]
    assert [r.layout_suspects for r in results] == [[], [], ["AAPL"], []]


def test_scoring_runs_only_when_articles_were_added(monkeypatch):
    _lists(monkeypatch, {"AAPL": "portfolio"})
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"AAPL:NASDAQ": quote_page(cards=[card("https://zacks.com/a", "A")])}))
    _publishers(monkeypatch)
    scored = []
    monkeypatch.setattr(store, "score_unscored_articles", lambda: scored.append(1))
    _run()
    _run(min_refetch_minutes=0)
    assert scored == [1]


def test_a_dual_stack_run_carries_on_over_ipv6_after_ipv4_is_refused(monkeypatch):
    monkeypatch.setattr(client, "configured_routes", lambda: [Route(IPV4, socket.AF_INET), Route(IPV6, socket.AF_INET6, "2001:db8::10")])
    _lists(monkeypatch, {"AAPL": "portfolio", "KO": "portfolio", "MSFT": "portfolio"})
    page = quote_page(cards=[card("https://zacks.com/x", "X")])
    fake = FakeGoogle({"": {socket.AF_INET: http_error(429), socket.AF_INET6: page}})
    monkeypatch.setattr(client, "fetch_body", fake)
    _publishers(monkeypatch)
    result = _run()
    assert result.status == "ok" and [r["route"] for r in result.refused_routes] == ["ipv4"]
    assert [k["family"] for k in fake.calls] == [socket.AF_INET, socket.AF_INET6, socket.AF_INET6]
    assert result.tickers_fetched == 2
    assert store.get_state(IPV4).cooldown_until is not None
    assert store.get_state(IPV6).last_success_at is not None


def test_the_probe_checks_indexes_and_unmapped_tickers_and_ignores_the_not_found_week(monkeypatch):
    _lists(monkeypatch, {"AAPL": "portfolio", "KO": "portfolio", "ZZZZ": "watchlist"}, indexes=["^FTSE"])
    now = datetime.now(timezone.utc)
    store.save_symbol("KO", "KO", "NYSE", "listing", now, "Coca-Cola", "found")
    store.save_symbol("ZZZZ", None, None, "listing", now, None, "not_found")
    fake = FakeGoogle({"AAPL:NASDAQ": quote_page(), "UKX:INDEXFTSE": quote_page(title="FTSE 100 - Google Finance"), "ZZZZ:": NOT_FOUND_PAGE})
    monkeypatch.setattr(client, "fetch_body", fake)
    summary = pipeline.probe_symbols(FetchSettings(), sleep=lambda s: None)
    assert summary["status"] == "ok" and summary["checked"] == 3
    assert summary["found"] == ["AAPL -> AAPL:NASDAQ", "^FTSE -> UKX:INDEXFTSE"] and summary["not_found"] == ["ZZZZ"]
    assert not any("KO:" in u for u in fake.urls)
    assert _rows() == []


def test_the_probe_stops_at_a_refusal(monkeypatch):
    _lists(monkeypatch, {"AAPL": "portfolio", "MSFT": "portfolio"})
    monkeypatch.setattr(client, "fetch_body", FakeGoogle({"": http_error(429)}))
    summary = pipeline.probe_symbols(FetchSettings(), sleep=lambda s: None)
    assert summary["status"] == "refused" and summary["reason"] == "ipv4: HTTP 429" and summary["checked"] == 0
