"""article_text: publisher pages through fetch_body (2 redirects), each failure mapped to a text status, the page's own date read."""
from datetime import date

import pytest
import requests

from news.gfetcher import article_text
from outbound_fetch_helpers import FetchRefused

from .helpers import article_html, http_error


def test_a_long_body_is_full_and_carries_the_page_date(monkeypatch):
    calls = []
    monkeypatch.setattr(article_text, "fetch_body", lambda url, headers, **kw: calls.append((url, headers, kw)) or article_html(date="2026-11-20T10:00:00Z"))
    got = article_text.fetch_article("https://zacks.com/a")
    assert got.status == "full" and len(got.text) >= article_text.MIN_FULL_CHARS
    assert got.page_date == date(2026, 11, 20)
    assert calls[0][2] == {"max_redirects": 2}
    assert "Cookie" not in calls[0][1]


def test_a_short_body_is_short(monkeypatch):
    monkeypatch.setattr(article_text, "fetch_body", lambda *a, **k: article_html(words=16))
    assert article_text.fetch_article("https://x.test/a").status == "short"


@pytest.mark.parametrize("error,status", [
    (http_error(401), "paywall"), (http_error(402), "paywall"), (http_error(403), "blocked"), (http_error(429), "blocked"),
    (http_error(404), "error"), (requests.ConnectionError(), "unreachable"), (requests.Timeout(), "unreachable"),
    (FetchRefused("private address"), "error"), (OSError("dns"), "error"),
])
def test_failures_map_to_a_status(monkeypatch, error, status):
    def raise_it(*a, **k):
        raise error
    monkeypatch.setattr(article_text, "fetch_body", raise_it)
    got = article_text.fetch_article("https://x.test/a")
    assert (got.text, got.status) == (None, status)


def test_a_page_in_another_language_is_marked_non_english(monkeypatch):
    monkeypatch.setattr(article_text, "fetch_body", lambda *a, **k: article_html(lang="de"))
    assert article_text.fetch_article("https://x.test/a").status == "non_english"


def test_a_page_without_an_article_body_is_no_text(monkeypatch):
    monkeypatch.setattr(article_text, "fetch_body", lambda *a, **k: "<html lang='en'><body></body></html>")
    assert article_text.fetch_article("https://x.test/a").status == "no_text"
