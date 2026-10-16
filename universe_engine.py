import re
import logging
from ftplib import FTP
from io import BytesIO
from datetime import datetime, timezone

from database import get_connection, log_notification
from outbound_fetch_helpers import fetch_body
from utils import is_valid_ticker

logger = logging.getLogger(__name__)

# GUI name: "Legacy File Sideloading & Nasdaq Sync". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

# Warrants, units and rights descriptions still say "Common Stock" ("Warrants to purchase one share of Common Stock").
_NON_COMMON_SECURITY = re.compile(r"\b(warrants?|units?|rights?)\b", re.IGNORECASE)
_NAME_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f<>]")
_MAX_NAME_LENGTH = 200

_LISTING_FILES = ("nasdaqlisted", "otherlisted")
_HTTPS_BASE_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/"
_FTP_HOST = "ftp.nasdaqtrader.com"
_FTP_TIMEOUT_SECONDS = 30


def _is_tradable_common_stock(symbol: str, description: str, test_issue: str) -> bool:
    if not symbol or not description or test_issue == "Y" or "$" in symbol:
        return False
    return "Common Stock" in description and not _NON_COMMON_SECURITY.search(description)


def _is_storable_listing(symbol: str, name: str) -> bool:
    return is_valid_ticker(symbol) and 0 < len(name) <= _MAX_NAME_LENGTH and not _NAME_FORBIDDEN.search(name)


def _download_https() -> dict:
    return {name: fetch_body(f"{_HTTPS_BASE_URL}{name}.txt", {}) for name in _LISTING_FILES}


def _download_ftp() -> dict:
    listings = {}
    with FTP(_FTP_HOST, timeout=_FTP_TIMEOUT_SECONDS) as ftp:
        ftp.login()
        ftp.cwd("SymbolDirectory")
        for name in _LISTING_FILES:
            buf = BytesIO()
            ftp.retrbinary(f"RETR {name}.txt", buf.write)
            listings[name] = buf.getvalue().decode("utf-8", errors="replace")
    return listings


def _download_listings() -> dict | None:
    try:
        return _download_https()
    except Exception as e:
        logger.warning("Nasdaq symbol list HTTPS download failed, falling back to FTP: %s", e)
    try:
        return _download_ftp()
    except Exception as e:
        logger.error("Failed to download Nasdaq symbol list over FTP: %s", e)
        return None


def update_market_universe() -> None:
    logger.info("Initiating Market Universe Update...")
    log_notification("Info", "Market Universe Update initiated. Fetching master list from Nasdaq.")

    listings = _download_listings()
    if listings is None:
        log_notification("Error", "Market Universe Update failed during download phase (HTTPS and FTP).")
        return

    tickers_to_insert = []
    skipped = []
    last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    logger.info("Parsing listed files and filtering for tradable Common Stock...")
    try:
        for filename, text in listings.items():
            test_issue_index = None
            for i, line in enumerate(text.splitlines()):
                line_data = line.strip().split("|")
                if i == 0:
                    # Test Issue sits at a different position in each file, so locate it from the header.
                    test_issue_index = line_data.index("Test Issue")
                    continue

                if len(line_data) <= test_issue_index:
                    continue

                symbol = line_data[0]
                description = line_data[1]

                if not _is_tradable_common_stock(symbol, description, line_data[test_issue_index]):
                    continue

                clean_symbol = symbol.replace(".", "-")  # BRK.B → BRK-B for Yahoo Finance
                clean_name = description.replace(" - Common Stock", "").replace(" Common Stock", "").strip()

                if not _is_storable_listing(clean_symbol, clean_name):
                    skipped.append(clean_symbol)
                    continue

                exchange_label = "NASDAQ" if filename == "nasdaqlisted" else "NYSE/AMEX"

                tickers_to_insert.append((
                    clean_symbol,
                    clean_name,
                    None,
                    None,
                    'US',
                    exchange_label,
                    last_updated
                ))
    except Exception as e:
        logger.error("Error parsing Nasdaq symbol lists: %s", e)
        log_notification("Error", f"Market Universe Update failed during parsing: {e}")
        return

    if skipped:
        logger.warning("Skipped %d Nasdaq listings with an invalid symbol or name: %s", len(skipped), repr(skipped[:20]))

    if not tickers_to_insert:
        logger.warning("No valid tickers found to insert.")
        return

    logger.info("Preparing to bulk insert %s equities into the database...", len(tickers_to_insert))

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.executemany('''
            INSERT INTO market_universe (ticker, company_name, sector, industry, country, exchange, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                company_name  = excluded.company_name,
                sector        = excluded.sector,
                industry      = excluded.industry,
                country       = excluded.country,
                exchange      = excluded.exchange,
                last_updated  = excluded.last_updated
        ''', tickers_to_insert)
        conn.commit()
        logger.info("Database bulk insert complete.")
        skipped_note = f" Skipped {len(skipped):,} listings with an invalid symbol or name." if skipped else ""
        log_notification("Success", f"Market Universe updated successfully. Engine is now tracking {len(tickers_to_insert):,} US Equities.{skipped_note}")
    except Exception as e:
        logger.error("Database insertion failed: %s", e)
        log_notification("Error", f"Market Universe DB Insert failed: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    update_market_universe()