# GUI name: "Account Price Scraper". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
import csv
import io
import ipaddress
import logging
import re
import socket
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import lxml.html
import pandas as pd
import requests
from requests.adapters import HTTPAdapter

from config import load_config
from database import (
    add_price_history, get_account, get_latest_price, get_price_as_of, get_price_history,
    get_scraper_headers,
)

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; QuantamentalAccountScraper/1.0)"}
_ALLOWED_HEADER_NAMES = frozenset({"cookie", "authorization", "user-agent"})
_MAX_BODY_BYTES = 2 * 1024 * 1024
_REQUEST_TIMEOUT_SECONDS = 15
_MAX_ADDRESS_ATTEMPTS = 3
_NUMERIC_RE = re.compile(r"-?\d[\d,]*\.?\d*")
_PENSION_TICKER_RE = re.compile(r"^PENSION-(\d+)$")


def pension_ticker(account_id: int) -> str:
    return f"PENSION-{account_id}"


def parse_pension_account_id(ticker: Optional[str]) -> Optional[int]:
    if not ticker:
        return None
    match = _PENSION_TICKER_RE.match(ticker)
    return int(match.group(1)) if match else None


class ScrapeRefused(ValueError):
    pass


class _PinnedAdapter(HTTPAdapter):
    def __init__(self, hostname: str):
        self._hostname = hostname
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, server_hostname=self._hostname, assert_hostname=self._hostname, **kwargs)


def _normalise_host(value: str) -> str:
    host = value.strip().strip("[]").rstrip(".").lower()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host


def _resolve(host: str, port: int) -> list:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _parse_https_url(url: str):
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        return None, None, "The URL is not valid."
    if parts.scheme != "https":
        return None, None, "Only https:// URLs are allowed."
    if not parts.hostname:
        return None, None, "The URL has no host name."
    if parts.username is not None or parts.password is not None:
        return None, None, "Put credentials in an Authorization header, not in the URL."
    return parts, port or 443, None


def header_error(headers: Optional[dict]) -> Optional[str]:
    for name in headers or {}:
        lowered = str(name).strip().lower()
        if lowered not in _ALLOWED_HEADER_NAMES and not lowered.startswith("accept"):
            return f"Header {name!r} is not allowed. Allowed headers: Cookie, Authorization, User-Agent and Accept*."
    return None


def config_error(url: str, headers: Optional[dict]) -> Optional[str]:
    return _parse_https_url(url)[2] or header_error(headers)


def _get_pinned(hostname: str, port: int, address: str, target: str, headers: dict) -> str:
    netloc = f"[{address}]" if ":" in address else address
    with requests.Session() as session:
        session.mount("https://", _PinnedAdapter(hostname))
        resp = session.get(
            f"https://{netloc}:{port}{target}", headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS,
            allow_redirects=False, stream=True,
        )
        if 300 <= resp.status_code < 400:
            raise ScrapeRefused("The page redirected. Redirects are not followed, so use the final URL.")
        resp.raise_for_status()
        body = bytearray()
        for chunk in resp.iter_content(chunk_size=65536):
            body.extend(chunk)
            if len(body) > _MAX_BODY_BYTES:
                raise ScrapeRefused("The page is larger than 2 MB.")
        return bytes(body).decode(resp.encoding or "utf-8", errors="replace")


def _fetch_body(url: str, headers: dict) -> str:
    parts, port, error = _parse_https_url(url)
    if error:
        raise ScrapeRefused(error)
    host = _normalise_host(parts.hostname)
    addresses = _resolve(host, port)
    allowed = {_normalise_host(h) for h in load_config().get("SCRAPER_ALLOWED_HOSTS") or []}
    if host not in allowed and not all(_is_public(a) for a in addresses):
        raise ScrapeRefused(
            "That address is not allowed. To scrape a page on your own network, add its host to Settings → "
            "Core System & Currencies → Account Price Scraper Allowed Hosts."
        )
    host_header = f"[{host}]" if ":" in host else host
    request_headers = {
        **_DEFAULT_HEADERS, **headers,
        "Host": host_header if port == 443 else f"{host_header}:{port}",
    }
    target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    last_error = None
    for address in addresses[:_MAX_ADDRESS_ATTEMPTS]:
        try:
            return _get_pinned(host, port, address, target, request_headers)
        except requests.ConnectionError as e:
            last_error = e
    raise last_error


def extract_price(html: str, selector: str) -> float:
    tree = lxml.html.fromstring(html)
    matches = tree.cssselect(selector)
    if not matches:
        raise ValueError(f"Selector {selector!r} matched nothing.")
    text = matches[0].text_content()
    numeric_match = _NUMERIC_RE.search(text)
    if not numeric_match:
        logger.warning("Selector %r matched %r, which contains no number.", selector, text[:200])
        raise ValueError(f"Selector {selector!r} matched an element that contains no number.")
    return float(numeric_match.group(0).replace(",", ""))


def fetch_and_extract(url: str, selector: str, headers: Optional[dict] = None) -> dict:
    try:
        error = header_error(headers)
        if error:
            raise ScrapeRefused(error)
        price = extract_price(_fetch_body(url, headers or {}), selector)
        return {"status": "success", "price": price}
    except ValueError as e:
        logger.error("fetch_and_extract failed for %s (%s): %s", url, selector, e)
        return {"status": "error", "message": str(e)}
    except Exception as e:
        logger.error("fetch_and_extract failed for %s (%s): %s", url, selector, e, exc_info=True)
        return {"status": "error", "message": "Failed to fetch or parse the page. Check server logs for details."}


def test_scrape(url: str, selector: str, headers: Optional[dict] = None) -> dict:
    return fetch_and_extract(url, selector, headers)


def run_scrape_for_account(account_id: int) -> dict:
    acc = get_account(account_id)
    if not acc:
        return {"status": "error", "message": "Account not found."}
    if not acc.get("scraper_url") or not acc.get("scraper_selector"):
        return {"status": "error", "message": "Scraper is not configured for this account."}
    result = fetch_and_extract(acc["scraper_url"], acc["scraper_selector"], get_scraper_headers(account_id))
    if result["status"] != "success":
        return result
    today = datetime.now(timezone.utc).date().isoformat()
    add_price_history(account_id, today, result["price"], source="scrape")
    return result


def import_price_csv(account_id: int, csv_text: str) -> dict:
    reader = csv.reader(io.StringIO(csv_text), delimiter=";")
    rows = list(reader)
    if not rows:
        return {"imported": 0, "skipped": 0}
    header = [c.strip().lower() for c in rows[0]]
    data_rows = rows[1:] if header == ["date", "marketprice"] else rows

    imported = 0
    skipped = 0
    for row in data_rows:
        if len(row) < 2:
            skipped += 1
            continue
        date_str, price_str = row[0].strip(), row[1].strip()
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            price = float(price_str)
        except ValueError:
            skipped += 1
            continue
        if add_price_history(account_id, date_str, price, source="csv_import"):
            imported += 1
        else:
            skipped += 1
    return {"imported": imported, "skipped": skipped}


def latest_price(account_id: int) -> Optional[tuple]:
    row = get_latest_price(account_id)
    if not row:
        return None
    acc = get_account(account_id)
    return (row["price"], acc["currency"] if acc else None)


def price_as_of(account_id: int, date_str: str) -> Optional[float]:
    row = get_price_as_of(account_id, date_str)
    return row["price"] if row else None


def price_series(account_id: int) -> pd.Series:
    rows = get_price_history(account_id)
    if not rows:
        return pd.Series(dtype=float)
    series = pd.Series(
        {row["price_date"]: row["price"] for row in rows},
    )
    series.index = pd.to_datetime(series.index)
    return series.sort_index()
