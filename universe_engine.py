# universe_engine.py
import os
import logging
from ftplib import FTP
from pathlib import Path
from datetime import datetime

from database import get_connection

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - UNIVERSE_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def log_notification(message_type: str, message_text: str) -> None:
    """Helper function to log scan progress to the system notification center."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)",
            (message_type, message_text)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to log notification: {e}")
    finally:
        conn.close()

def _download_ftp_files(filenames: dict) -> bool:
    """Establishes an anonymous FTP connection to Nasdaq and retrieves master lists."""
    logger.info("Connecting to ftp.nasdaqtrader.com...")
    try:
        ftp = FTP("ftp.nasdaqtrader.com")
        ftp.login()
        logger.info("FTP Welcome: " + ftp.getwelcome().replace('\n', ' '))
        ftp.cwd("SymbolDirectory")

        for filename, filepath in filenames.items():
            logger.info(f"Downloading {filename}.txt to {filepath}...")
            with open(filepath, "wb") as f:
                ftp.retrbinary(f"RETR {filename}.txt", f.write)
        
        ftp.quit()
        return True
    except Exception as e:
        logger.error(f"Failed to download FTP files: {e}")
        return False

def update_market_universe() -> None:
    """
    Downloads the master ticker list, filters for common stocks, 
    and bulk inserts the universe into the SQLite database.
    """
    logger.info("Initiating Market Universe Update...")
    log_notification("Info", "Market Universe Update initiated. Fetching master list from Nasdaq FTP.")
    
    # Ensure a temporary data directory exists
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
    last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

                    # Execution Rules based on Nasdaq format specifications
                    # Exclude test issues, preferred stocks, and warrants.
                    is_test_nasdaq = (filename == "nasdaqlisted" and len(line_data) > 6 and line_data[6] == "Y")
                    is_test_other = (filename == "otherlisted" and len(line_data) > 4 and line_data[4] == "Y")
                    
                    if symbol == "" or description == "" or is_test_nasdaq or is_test_other or "$" in symbol:
                        continue

                    # Institutional filter: We only want equities, not ETFs, ETNs, or preferred shares
                    if "Common Stock" not in description:
                        continue

                    clean_symbol = symbol.replace(".", "-") # Normalize BRK.B to BRK-B for Yahoo Finance
                    
                    tickers_to_insert.append((
                        clean_symbol,
                        description,
                        None, # Sector
                        None, # Industry
                        last_updated
                    ))
    except Exception as e:
        logger.error(f"Error parsing FTP files: {e}")
        log_notification("Error", f"Market Universe Update failed during parsing: {e}")
        return
    finally:
        # Clean up temporary files
        for filepath in filenames.values():
            if os.path.exists(filepath):
                os.remove(filepath)

    if not tickers_to_insert:
        logger.warning("No valid tickers found to insert.")
        return

    logger.info(f"Preparing to bulk insert {len(tickers_to_insert)} equities into the database...")
    
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # We use executemany for rapid, transaction-safe bulk inserts
        cursor.executemany('''
            INSERT OR REPLACE INTO market_universe (ticker, company_name, sector, industry, last_updated)
            VALUES (?, ?, ?, ?, ?)
        ''', tickers_to_insert)
        
        conn.commit()
        logger.info("Database bulk insert complete.")
        log_notification("Success", f"Market Universe updated successfully. Engine is now tracking {len(tickers_to_insert):,} US Equities.")
        
    except Exception as e:
        logger.error(f"Database insertion failed: {e}")
        log_notification("Error", f"Market Universe DB Insert failed: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    update_market_universe()