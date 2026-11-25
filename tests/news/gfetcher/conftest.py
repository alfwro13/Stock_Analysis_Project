import pytest


def _no_network(*args, **kwargs):
    raise AssertionError("a Google News Fetcher test reached fetch_body without patching it")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr("news.gfetcher.client.fetch_body", _no_network)
    monkeypatch.setattr("news.gfetcher.article_text.fetch_body", _no_network)
    monkeypatch.setattr("news.gfetcher.store.score_unscored_articles", lambda: None)
