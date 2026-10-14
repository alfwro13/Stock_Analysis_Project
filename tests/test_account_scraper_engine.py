import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

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

PUBLIC_IP = "93.184.216.34"


def _resolves_to(*addresses):
    return patch.object(scraper, "_resolve", return_value=list(addresses))


def _allow(*hosts):
    return patch.object(scraper, "load_config", return_value={"SCRAPER_ALLOWED_HOSTS": list(hosts)})


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
    with patch.object(scraper, "_fetch_body", return_value=AVIVA_HTML):
        result = scraper.fetch_and_extract("https://example.test/aviva_price.html", "#gf-price")
    assert result == {"status": "success", "price": 1.5649}


def test_fetch_and_extract_http_error():
    with _resolves_to(PUBLIC_IP), _allow(), patch.object(scraper, "_get_pinned", side_effect=Exception("HTTP 500")):
        result = scraper.fetch_and_extract("https://example.test/x.html", "#gf-price")
    assert result["status"] == "error"


def test_fetch_and_extract_network_exception():
    """The raw exception text must not reach the caller (CWE-209) — only a generic message."""
    with patch.object(scraper, "_fetch_body", side_effect=Exception("connection refused")):
        result = scraper.fetch_and_extract("https://example.test/x.html", "#gf-price")
    assert result["status"] == "error"
    assert "connection refused" not in result["message"]


def test_fetch_and_extract_selector_error_keeps_detail():
    """A ValueError from extract_price (bad selector/no number) is a deliberate, developer-authored
    validation message, not an internal-detail leak, so it's still surfaced to the caller verbatim."""
    with patch.object(scraper, "_fetch_body", return_value='<div id="gf-price">n/a</div>'):
        result = scraper.fetch_and_extract("https://example.test/x.html", "#gf-price")
    assert result["status"] == "error"
    assert "no number" in result["message"]


def test_no_number_error_does_not_echo_page_text():
    html = '<div id="gf-price">internal-admin-token-abc</div>'
    with patch.object(scraper, "_fetch_body", return_value=html):
        result = scraper.fetch_and_extract("https://example.test/x.html", "#gf-price")
    assert result["status"] == "error"
    assert "internal-admin-token-abc" not in result["message"]


@pytest.mark.parametrize("url", [
    "http://example.test/x.html",
    "ftp://example.test/x.html",
    "file:///etc/passwd",
    "//example.test/x.html",
    "example.test/x.html",
    "https:///nohost",
    "https://example.test:notaport/x",
    "https://user:secret@example.test/x",
])
def test_non_https_or_malformed_url_is_refused_before_any_lookup(url):
    with patch.object(scraper, "_resolve", side_effect=AssertionError("must not resolve")):
        result = scraper.fetch_and_extract(url, "#gf-price")
    assert result["status"] == "error"


@pytest.mark.parametrize("address", [
    "127.0.0.1", "127.8.8.8", "::1", "10.0.0.5", "172.16.4.4", "192.168.1.71", "169.254.169.254",
    "fe80::1", "fd00::1", "100.64.0.1", "0.0.0.0", "224.0.0.251", "ff02::fb", "::ffff:127.0.0.1",
    "::ffff:192.168.1.71",
])
def test_non_public_address_is_refused_and_never_connected_to(address):
    with _resolves_to(address), _allow(), patch.object(scraper, "_get_pinned") as pinned:
        result = scraper.fetch_and_extract("https://scrape.example.test/x.html", "#gf-price")
    assert result["status"] == "error"
    assert "not allowed" in result["message"]
    pinned.assert_not_called()


def test_hostname_with_one_private_answer_is_refused():
    with _resolves_to(PUBLIC_IP, "10.0.0.9"), _allow(), patch.object(scraper, "_get_pinned") as pinned:
        result = scraper.fetch_and_extract("https://scrape.example.test/x.html", "#gf-price")
    assert result["status"] == "error"
    pinned.assert_not_called()


def test_ip_literal_in_url_is_vetted_like_a_name():
    with _allow(), patch.object(scraper, "_get_pinned") as pinned:
        result = scraper.fetch_and_extract("https://127.0.0.1/x.html", "#gf-price")
    assert result["status"] == "error"
    pinned.assert_not_called()


def test_public_host_is_pinned_to_the_vetted_address_with_host_header():
    with _resolves_to(PUBLIC_IP), _allow(), \
            patch.object(scraper, "_get_pinned", return_value=AVIVA_HTML) as pinned:
        result = scraper.fetch_and_extract("https://Scrape.Example.test/a/b.html?q=1#frag", "#gf-price")
    assert result["status"] == "success"
    hostname, port, address, target, headers = pinned.call_args.args
    assert (hostname, port, address, target) == ("scrape.example.test", 443, PUBLIC_IP, "/a/b.html?q=1")
    assert headers["Host"] == "scrape.example.test"


def test_non_default_port_is_kept_in_host_header():
    with _resolves_to(PUBLIC_IP), _allow(), \
            patch.object(scraper, "_get_pinned", return_value=AVIVA_HTML) as pinned:
        scraper.fetch_and_extract("https://scrape.example.test:8443/", "#gf-price")
    assert pinned.call_args.args[1] == 8443
    assert pinned.call_args.args[4]["Host"] == "scrape.example.test:8443"


@pytest.mark.parametrize("host,url_host", [
    ("192.168.1.71", "192.168.1.71"),
    ("ha.lan", "HA.lan"),
    ("ha.lan", "ha.lan."),
    ("::1", "[::1]"),
])
def test_allow_listed_host_may_resolve_to_a_private_address(host, url_host):
    address = "::1" if ":" in host else "192.168.1.71"
    with _resolves_to(address), _allow(host), \
            patch.object(scraper, "_get_pinned", return_value=AVIVA_HTML) as pinned:
        result = scraper.fetch_and_extract(f"https://{url_host}:8123/house.html", "#gf-price")
    assert result["status"] == "success"
    assert pinned.call_args.args[2] == address


def test_allow_list_matches_the_requested_host_not_the_resolved_address():
    with _resolves_to("192.168.1.71"), _allow("192.168.1.71"), patch.object(scraper, "_get_pinned") as pinned:
        result = scraper.fetch_and_extract("https://evil.example.test/", "#gf-price")
    assert result["status"] == "error"
    pinned.assert_not_called()


def test_next_address_is_tried_when_a_connection_fails():
    import requests
    calls = []

    def fake(hostname, port, address, target, headers):
        calls.append(address)
        if address == "2606:2800::1":
            raise requests.ConnectionError("unreachable")
        return AVIVA_HTML

    with _resolves_to("2606:2800::1", PUBLIC_IP), _allow(), patch.object(scraper, "_get_pinned", side_effect=fake):
        result = scraper.fetch_and_extract("https://scrape.example.test/", "#gf-price")
    assert result["status"] == "success"
    assert calls == ["2606:2800::1", PUBLIC_IP]


@pytest.mark.parametrize("headers", [
    {"Host": "internal.example"}, {"host": "internal.example"}, {"X-Forwarded-For": "127.0.0.1"},
    {"Content-Length": "5"}, {"Transfer-Encoding": "chunked"},
])
def test_disallowed_headers_are_refused(headers):
    with patch.object(scraper, "_fetch_body") as fetch:
        result = scraper.fetch_and_extract("https://example.test/x.html", "#gf-price", headers)
    assert result["status"] == "error"
    assert "not allowed" in result["message"]
    fetch.assert_not_called()


def test_allowed_headers_are_sent():
    sent = {"Cookie": "a=b", "authorization": "Bearer t", "User-Agent": "X", "Accept": "text/html", "Accept-Language": "en"}
    with patch.object(scraper, "_fetch_body", return_value=AVIVA_HTML) as fetch:
        result = scraper.fetch_and_extract("https://example.test/x.html", "#gf-price", sent)
    assert result["status"] == "success"
    assert fetch.call_args.args[1] == sent


def test_config_error_rejects_http_and_bad_headers():
    assert scraper.config_error("https://example.test/x", {"Cookie": "a=b"}) is None
    assert "https" in scraper.config_error("http://example.test/x", None)
    assert "not allowed" in scraper.config_error("https://example.test/x", {"Host": "x"})


@pytest.fixture(scope="module")
def tls_material(tmp_path_factory):
    d = tmp_path_factory.mktemp("tls")
    cert, key = d / "cert.pem", d / "key.pem"
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
             "-keyout", str(key), "-out", str(cert), "-subj", "/CN=scraper.test",
             "-addext", "subjectAltName=DNS:scraper.test"],
            check=True, capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("openssl not available to mint a test certificate")
    return str(cert), str(key)


class _TlsServer:
    def __init__(self, cert, key, handler):
        self.httpd = HTTPServer(("127.0.0.1", 0), handler)
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert, key)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def _handler(status=200, body=b"", headers=None, seen=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if seen is not None:
                seen.append({"host": self.headers.get("Host"), "path": self.path, "ua": self.headers.get("User-Agent")})
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def log_message(self, *args):
            pass
    return Handler


@pytest.fixture
def trust_test_cert(tls_material, monkeypatch):
    import requests.adapters
    monkeypatch.setattr(requests.adapters, "DEFAULT_CA_BUNDLE_PATH", tls_material[0])
    return tls_material


def _scrape_local(server, **kwargs):
    with _resolves_to("127.0.0.1"), _allow("scraper.test"):
        return scraper.fetch_and_extract(f"https://scraper.test:{server.port}/price.html?x=1", "#gf-price", **kwargs)


def test_pinned_https_fetch_verifies_the_requested_name_and_sends_host(trust_test_cert):
    seen = []
    with _TlsServer(*trust_test_cert, _handler(body=ZOOPLA_HTML.encode(), seen=seen)) as server:
        result = _scrape_local(server)
    assert result == {"status": "success", "price": 487000.0}
    assert seen[0]["host"] == f"scraper.test:{server.port}"
    assert seen[0]["path"] == "/price.html?x=1"
    assert "QuantamentalAccountScraper" in seen[0]["ua"]


def test_pinned_https_fetch_fails_when_certificate_is_for_another_name(trust_test_cert):
    with _TlsServer(*trust_test_cert, _handler(body=ZOOPLA_HTML.encode())) as server:
        with _resolves_to("127.0.0.1"), _allow("other.test"):
            result = scraper.fetch_and_extract(f"https://other.test:{server.port}/", "#gf-price")
    assert result["status"] == "error"


def test_redirect_is_not_followed(trust_test_cert):
    seen = []
    handler = _handler(status=302, headers={"Location": "https://127.0.0.1/secret"}, seen=seen)
    with _TlsServer(*trust_test_cert, handler) as server:
        result = _scrape_local(server)
    assert result["status"] == "error"
    assert "redirect" in result["message"].lower()
    assert len(seen) == 1


def test_oversized_body_is_refused(trust_test_cert):
    body = b"<div id='gf-price'>1</div>" + b" " * (scraper._MAX_BODY_BYTES + 10)
    with _TlsServer(*trust_test_cert, _handler(body=body)) as server:
        result = _scrape_local(server)
    assert result["status"] == "error"
    assert "2 MB" in result["message"]


def test_http_error_status_is_a_generic_error(trust_test_cert):
    with _TlsServer(*trust_test_cert, _handler(status=500, body=b"secret stack trace")) as server:
        result = _scrape_local(server)
    assert result["status"] == "error"
    assert "secret stack trace" not in result["message"]


@pytest.mark.db
def test_run_scrape_for_account_persists_price():
    aid = create_account("ScrapeAcc", "GBP", account_type="House")
    from database import update_account
    update_account(aid, scraper_url="https://example.test/house.html", scraper_selector="#gf-price")
    with patch.object(scraper, "_fetch_body", return_value=ZOOPLA_HTML):
        result = scraper.run_scrape_for_account(aid)
    assert result == {"status": "success", "price": 487000.0}
    history = get_price_history(aid)
    assert len(history) == 1
    assert history[0]["price"] == 487000.0
    assert history[0]["source"] == "scrape"


@pytest.mark.db
def test_run_scrape_for_account_refuses_a_stored_http_url():
    aid = create_account("StoredHttpAcc", "GBP", account_type="House")
    from database import update_account
    update_account(aid, scraper_url="http://example.test/house.html", scraper_selector="#gf-price")
    result = scraper.run_scrape_for_account(aid)
    assert result["status"] == "error"
    assert "https" in result["message"]
    assert get_price_history(aid) == []


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
