"""Shared fixtures for the Google News Fetcher tests: synthetic quote pages and fake HTTP answers (no test reaches the network)."""
import html

import requests

TABLES = (
    "news_articles", "gfetcher_request_log", "gfetcher_state", "gfetcher_symbol_map",
    "gfetcher_publishers", "gfetcher_ticker_state",
)


def card(url, title, source="Example News", age="2 hours ago"):
    return (url, title, source, age)


def quote_page(title="Apple Inc (AAPL) Stock Price & News - Google Finance", cards=()):
    items = "".join(
        f'<div class="LIT7If"><a href="{html.escape(url)}"><div class="WrUjhf">{html.escape(src)}</div>'
        f'<div class="TQWIEd">{html.escape(t)}</div><div class="JQ8Czd">{html.escape(age)}</div></a></div>'
        for url, t, src, age in cards
    )
    return f"<html><head><title>{html.escape(title)}</title></head><body>{items}</body></html>"


NOT_FOUND_PAGE = "<html><head><title>Google Finance</title></head><body></body></html>"


def http_error(status, retry_after=None):
    response = requests.Response()
    response.status_code = status
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return requests.HTTPError(f"{status}", response=response)


def article_html(words=120, lang="en", date=None):
    meta = f'<meta property="article:published_time" content="{date}">' if date else ""
    body = " ".join(["Shares rose after the company reported strong results."] * (words // 8))
    return f'<html lang="{lang}"><head><title>T</title>{meta}</head><body><article><p>{body}</p></article></body></html>'


class FakeGoogle:
    """Answers quote-page URLs from a dict; a value may be HTML, an exception to raise, or a dict keyed by socket family for an answer per IP stack."""

    def __init__(self, answers):
        self.answers = answers
        self.urls = []
        self.calls = []

    def __call__(self, url, headers, **kwargs):
        self.urls.append(url)
        self.calls.append(kwargs)
        for key, answer in self.answers.items():
            if isinstance(answer, dict):
                answer = answer.get(kwargs.get("family"), answer.get(None))
            if key in url:
                if isinstance(answer, BaseException):
                    raise answer
                return answer
        raise AssertionError(f"unexpected Google URL {url}")
