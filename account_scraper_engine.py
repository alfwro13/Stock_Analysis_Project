# GUI name: "Account Price Scraper". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
import csv
import io
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import lxml.html
import pandas as pd
import requests

from database import (
    add_price_history, get_account, get_latest_price, get_price_as_of, get_price_history,
)

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; QuantamentalAccountScraper/1.0)"}
_NUMERIC_RE = re.compile(r"-?\d[\d,]*\.?\d*")
_PENSION_TICKER_RE = re.compile(r"^PENSION-(\d+)$")


def pension_ticker(account_id: int) -> str:
    return f"PENSION-{account_id}"


def parse_pension_account_id(ticker: Optional[str]) -> Optional[int]:
    if not ticker:
        return None
    match = _PENSION_TICKER_RE.match(ticker)
    return int(match.group(1)) if match else None


def extract_price(html: str, selector: str) -> float:
    tree = lxml.html.fromstring(html)
    matches = tree.cssselect(selector)
    if not matches:
        raise ValueError(f"Selector {selector!r} matched nothing.")
    text = matches[0].text_content()
    numeric_match = _NUMERIC_RE.search(text)
    if not numeric_match:
        raise ValueError(f"Selector {selector!r} matched {text!r}, which contains no number.")
    return float(numeric_match.group(0).replace(",", ""))


def fetch_and_extract(url: str, selector: str, headers: Optional[dict] = None) -> dict:
    try:
        resp = requests.get(url, headers={**_DEFAULT_HEADERS, **(headers or {})}, timeout=15)
        resp.raise_for_status()
        price = extract_price(resp.text, selector)
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
    try:
        headers = json.loads(acc.get("scraper_headers") or "{}")
    except Exception:
        headers = {}
    result = fetch_and_extract(acc["scraper_url"], acc["scraper_selector"], headers)
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
