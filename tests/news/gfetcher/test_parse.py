"""parse: news cards from a Google Finance quote page, the not-found page, relative ages and the English filter."""
from datetime import datetime, timedelta, timezone

import pytest

from news.gfetcher.parse import looks_english_title, parse_age, parse_quote_page

from .helpers import NOT_FOUND_PAGE, card, quote_page

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def test_cards_are_read_with_source_age_and_estimated_time():
    page = parse_quote_page(quote_page(cards=[card("https://a.test/1", "Apple beats", "Zacks", "3 hours ago")]), NOW)
    assert page.found and len(page.cards) == 1
    c = page.cards[0]
    assert (c.title, c.url, c.source, c.age_text) == ("Apple beats", "https://a.test/1", "Zacks", "3 hours ago")
    assert c.published == NOW - timedelta(hours=3) and c.precision == "hour"


def test_the_bare_site_title_means_google_has_no_page_for_the_symbol():
    page = parse_quote_page(NOT_FOUND_PAGE, NOW)
    assert not page.found and page.cards == []


def test_a_valid_page_without_cards_is_found_but_empty():
    page = parse_quote_page(quote_page(title="FTSE 250 Index Price - Google Finance"), NOW)
    assert page.found and page.cards == []


def test_duplicate_and_non_https_links_are_skipped():
    html = quote_page(cards=[card("https://a.test/1", "One"), card("https://a.test/1", "One again"), card("http://a.test/2", "Plain")])
    assert [c.title for c in parse_quote_page(html, NOW).cards] == ["One"]


@pytest.mark.parametrize("html", ["", "   "])
def test_an_empty_body_is_not_a_page(html):
    assert not parse_quote_page(html, NOW).found


@pytest.mark.parametrize("text,delta,unit", [
    ("34 minutes ago", timedelta(minutes=34), "minute"),
    ("1 day ago", timedelta(days=1), "day"),
    ("2 weeks ago", timedelta(days=14), "week"),
    ("3 months ago", timedelta(days=90), "month"),
])
def test_relative_ages_carry_their_unit(text, delta, unit):
    assert parse_age(text, NOW) == (NOW - delta, unit)


def test_an_unreadable_age_is_missing_not_now():
    assert parse_age("Yesterday", NOW) == (None, None)


def test_non_latin_titles_are_not_english():
    assert looks_english_title("Nikkei Surges 2,208 Points on Tuesday")
    assert not looks_english_title("巴士的報 日經指數大漲")
