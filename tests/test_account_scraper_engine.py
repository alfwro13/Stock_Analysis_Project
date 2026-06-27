from unittest.mock import MagicMock, patch

import pytest

import account_scraper_engine as scraper
from database import create_account, get_price_history


AVIVA_HTML = """<html><body>
    <div id="gf-price" class="price">1.5649</div>
    <div class="date">Last Updated: 2026-06-26 23:00:04</div>
</body></html>"""

ZOOPLA_HTML = """<html><body>
    <div id="gf-price" class="price">487000</div>
    <div class="date">Last Updated: 2026-06-26 00:12:15</div>
</body></html>"""


def _mock_resp(status_code: int, text: str) -> MagicMock:
    m = MagicMock()
    m.status_code = status_code
    m.text = text
    m.raise_for_status = MagicMock()
    if status_code >= 400:
        m.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return m


def test_extract_price_aviva_sample():
    assert scraper.extract_price(AVIVA_HTML, "#gf-price") == 1.5649


def test_extract_price_zoopla_sample():
    assert scraper.extract_price(ZOOPLA_HTML, "#gf-price") == 487000.0


def test_extract_price_strips_currency_and_commas():
    html = '<div id="gf-price">£1,234.56</div>'
    assert scraper.extract_price(html, "#gf-price") == 1234.56


def test_extract_price_no_selector_match_raises():
    with pytest.raises(ValueError):
        scraper.extract_price(AVIVA_HTML, "#nope")


def test_extract_price_no_number_raises():
    with pytest.raises(ValueError):
        scraper.extract_price('<div id="gf-price">n/a</div>', "#gf-price")


def test_fetch_and_extract_success():
    with patch("requests.get", return_value=_mock_resp(200, AVIVA_HTML)):
        result = scraper.fetch_and_extract("http://example.test/aviva_price.html", "#gf-price")
    assert result == {"status": "success", "price": 1.5649}


def test_fetch_and_extract_http_error():
    with patch("requests.get", return_value=_mock_resp(500, "")):
        result = scraper.fetch_and_extract("http://example.test/x.html", "#gf-price")
    assert result["status"] == "error"


def test_fetch_and_extract_network_exception():
    with patch("requests.get", side_effect=Exception("connection refused")):
        result = scraper.fetch_and_extract("http://example.test/x.html", "#gf-price")
    assert result["status"] == "error"
    assert "connection refused" in result["message"]


@pytest.mark.db
def test_run_scrape_for_account_persists_price():
    aid = create_account("ScrapeAcc", "GBP", account_type="House")
    from database import update_account
    update_account(aid, scraper_url="http://example.test/house.html", scraper_selector="#gf-price")
    with patch("requests.get", return_value=_mock_resp(200, ZOOPLA_HTML)):
        result = scraper.run_scrape_for_account(aid)
    assert result == {"status": "success", "price": 487000.0}
    history = get_price_history(aid)
    assert len(history) == 1
    assert history[0]["price"] == 487000.0
    assert history[0]["source"] == "scrape"


@pytest.mark.db
def test_run_scrape_for_account_not_configured():
    aid = create_account("UnconfiguredAcc", "GBP", account_type="House")
    result = scraper.run_scrape_for_account(aid)
    assert result["status"] == "error"


@pytest.mark.db
def test_import_price_csv_valid_rows():
    aid = create_account("CsvImportAcc", "GBP", account_type="Pension")
    csv_text = "date;marketPrice\n2026-01-01;1.5000\n2026-01-02;1.5100\n"
    result = scraper.import_price_csv(aid, csv_text)
    assert result == {"imported": 2, "skipped": 0}
    history = get_price_history(aid)
    assert [h["price"] for h in history] == [1.5, 1.51]
    assert all(h["source"] == "csv_import" for h in history)


@pytest.mark.db
def test_import_price_csv_skips_malformed_rows():
    aid = create_account("CsvSkipAcc", "GBP", account_type="Pension")
    csv_text = "date;marketPrice\n2026-01-01;1.5000\nnot-a-date;oops\n"
    result = scraper.import_price_csv(aid, csv_text)
    assert result == {"imported": 1, "skipped": 1}


@pytest.mark.db
def test_latest_price_and_price_as_of():
    aid = create_account("PriceLookupAcc", "GBP", account_type="Pension")
    scraper.import_price_csv(aid, "date;marketPrice\n2026-01-01;1.00\n2026-01-10;2.00\n")
    price, currency = scraper.latest_price(aid)
    assert price == 2.0
    assert currency == "GBP"
    assert scraper.price_as_of(aid, "2026-01-05") == 1.0
    assert scraper.price_as_of(aid, "2026-01-15") == 2.0
    assert scraper.price_as_of(aid, "2025-12-31") is None


@pytest.mark.db
def test_latest_price_no_history_returns_none():
    aid = create_account("NoHistoryAcc", "GBP", account_type="House")
    assert scraper.latest_price(aid) is None


@pytest.mark.db
def test_price_series_is_date_indexed():
    aid = create_account("SeriesAcc", "GBP", account_type="Pension")
    scraper.import_price_csv(aid, "date;marketPrice\n2026-01-01;1.00\n2026-01-02;1.10\n")
    series = scraper.price_series(aid)
    assert len(series) == 2
    assert series.iloc[-1] == 1.10


def test_pension_ticker_roundtrip():
    assert scraper.pension_ticker(42) == "PENSION-42"
    assert scraper.parse_pension_account_id("PENSION-42") == 42
    assert scraper.parse_pension_account_id("AAPL") is None
    assert scraper.parse_pension_account_id(None) is None
