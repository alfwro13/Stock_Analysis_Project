import os
import logging
from ftplib import FTP
from pathlib import Path
from datetime import datetime, timezone

from database import get_connection, log_notification

logger = logging.getLogger(__name__)

# GUI name: "Legacy File Sideloading & Nasdaq Sync". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

def _download_ftp_files(filenames: dict) -> bool:
    logger.info("Connecting to ftp.nasdaqtrader.com...")
    try:
        ftp = FTP("ftp.nasdaqtrader.com")
        ftp.login()
        logger.info("FTP Welcome: " + ftp.getwelcome().replace('\n', ' '))
        ftp.cwd("SymbolDirectory")

        for filename, filepath in filenames.items():
            logger.info("Downloading %s.txt to %s...", filename, filepath)
            with open(filepath, "wb") as f:
                ftp.retrbinary(f"RETR {filename}.txt", f.write)

        ftp.quit()
        return True
    except Exception as e:
        logger.error("Failed to download FTP files: %s", e)
        return False

def update_market_universe() -> None:
    logger.info("Initiating Market Universe Update...")
    log_notification("Info", "Market Universe Update initiated. Fetching master list from Nasdaq FTP.")

    temp_dir = Path("data/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    filenames = {
        "otherlisted": str(temp_dir / "otherlisted.txt"),
        "nasdaqlisted": str(temp_dir / "nasdaqlisted.txt"),
    }

    if not _download_ftp_files(filenames):
        log_notification("Error", "Market Universe Update failed during FTP download phase.")
        return

    tickers_to_insert = []
    last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    logger.info("Parsing listed files and filtering for tradable Common Stock...")
    try:
        for filename, filepath in filenames.items():
            with open(filepath, "r", encoding="utf-8") as file_reader:
                for i, line in enumerate(file_reader):
                    if i == 0:
                        continue # Skip header
                    
                    line_data = line.strip().split("|")
                    if len(line_data) < 2:
                        continue

                    symbol = line_data[0]
                    description = line_data[1]

                    # Nasdaq format: column 6 (nasdaqlisted) or 4 (otherlisted) is a test-issue flag.
                    is_test_nasdaq = (filename == "nasdaqlisted" and len(line_data) > 6 and line_data[6] == "Y")
                    is_test_other = (filename == "otherlisted" and len(line_data) > 4 and line_data[4] == "Y")

                    if symbol == "" or description == "" or is_test_nasdaq or is_test_other or "$" in symbol:
                        continue

                    if "Common Stock" not in description:
                        continue

                    clean_symbol = symbol.replace(".", "-")  # BRK.B → BRK-B for Yahoo Finance
                    clean_name = description.replace(" - Common Stock", "").replace(" Common Stock", "").strip()

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
        logger.error("Error parsing FTP files: %s", e)
        log_notification("Error", f"Market Universe Update failed during parsing: {e}")
        return
    finally:
        for filepath in filenames.values():
            if os.path.exists(filepath):
                os.remove(filepath)

    if not tickers_to_insert:
        logger.warning("No valid tickers found to insert.")
        return

    logger.info("Preparing to bulk insert %s equities into the database...", len(tickers_to_insert))

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.executemany('''
            INSERT OR REPLACE INTO market_universe (ticker, company_name, sector, industry, country, exchange, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', tickers_to_insert)
        conn.commit()
        logger.info("Database bulk insert complete.")
        log_notification("Success", f"Market Universe updated successfully. Engine is now tracking {len(tickers_to_insert):,} US Equities.")
    except Exception as e:
        logger.error("Database insertion failed: %s", e)
        log_notification("Error", f"Market Universe DB Insert failed: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    update_market_universe()