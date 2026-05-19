# macro_calendar_engine.py
import sqlite3
import logging
import requests
import xml.etree.ElementTree as ET
import hashlib
from typing import Optional, List, Tuple
from datetime import datetime

# Configure module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - MACRO_CALENDAR - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DB_PATH = "data/analysis.db"
FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
TARGET_CURRENCIES = {'USD', 'GBP'}
TARGET_IMPACT = 'High'

def get_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)

def clean_value(val_str: Optional[str]) -> Optional[float]:
    """
    Converts standard financial magnitude strings to raw float values.
    Handles K (Thousands), M (Millions), B (Billions), and percentages.
    """
    if not val_str or not isinstance(val_str, str):
        return None
        
    val_str = val_str.strip()
    if val_str in ('', '-'):
        return None
        
    # Remove commas
    val_str = val_str.replace(',', '')
    multiplier = 1.0
    
    if val_str.endswith('%'):
        val_str = val_str[:-1]
    elif val_str.endswith('K'):
        multiplier = 1_000.0
        val_str = val_str[:-1]
    elif val_str.endswith('M'):
        multiplier = 1_000_000.0
        val_str = val_str[:-1]
    elif val_str.endswith('B'):
        multiplier = 1_000_000_000.0
        val_str = val_str[:-1]
    elif val_str.endswith('T'):
        multiplier = 1_000_000_000_000.0
        val_str = val_str[:-1]
        
    try:
        return float(val_str) * multiplier
    except ValueError:
        logger.warning(f"Failed to parse numeric value from string: {val_str}")
        return None

def generate_event_id(date_str: str, currency: str, event_name: str) -> str:
    """Creates a deterministic hash to prevent database duplicates."""
    raw_string = f"{date_str}_{currency}_{event_name}"
    return hashlib.sha256(raw_string.encode('utf-8')).hexdigest()

def fetch_and_process_calendar() -> List[Tuple]:
    """Fetches the XML feed, filters targets, and formats data for SQLite."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36'
    }
    
    try:
        response = requests.get(FEED_URL, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Network error fetching calendar XML: {e}")
        return []

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as e:
        logger.error(f"Failed to parse XML content: {e}")
        return []

    processed_events = []
    
    for event in root.findall('event'):
        impact = event.findtext('impact', '').strip()
        currency = event.findtext('country', '').strip()
        
        # Filter logic
        if impact == TARGET_IMPACT and currency in TARGET_CURRENCIES:
            date_str = event.findtext('date', '').strip()
            time_str = event.findtext('time', '').strip()
            event_name = event.findtext('title', '').strip()
            
            # --- BULLETPROOF DATE PARSING ---
            formatted_date = ""
            try:
                # Clean time string (e.g., '10:45 am' -> '10:45am')
                time_clean = time_str.replace(' ', '').lower()
                dt_obj = datetime.strptime(f"{date_str} {time_clean}", "%m-%d-%Y %I:%M%p")
                formatted_date = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    # Fallback for "All Day" or "Tentative" events
                    dt_obj = datetime.strptime(date_str, "%m-%d-%Y")
                    formatted_date = dt_obj.strftime("%Y-%m-%d 00:00:00")
                except ValueError:
                    logger.warning(f"Could not parse date: {date_str} {time_str}")
                    formatted_date = f"{date_str} {time_str}" # Emergency fallback
                
            event_id = generate_event_id(formatted_date, currency, event_name)
            
            forecast_val = clean_value(event.findtext('forecast'))
            previous_val = clean_value(event.findtext('previous'))
            
            processed_events.append((
                event_id,
                formatted_date,
                currency,
                impact,
                event_name,
                forecast_val,
                previous_val,
                0, # is_event_passed default
                0  # alert_dispatched default
            ))
            
    return processed_events

def upsert_calendar_events(events: List[Tuple]) -> None:
    """Executes a bulk INSERT OR REPLACE into the database."""
    if not events:
        logger.info("No high-impact events found matching criteria. Exiting.")
        return

    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.executemany('''
            INSERT OR REPLACE INTO macro_calendar (
                event_id, event_date, currency, impact, event_name, 
                forecast_val, previous_val, is_event_passed, alert_dispatched
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', events)
        conn.commit()
        logger.info(f"Successfully upserted {cursor.rowcount} macro events.")
    except sqlite3.Error as e:
        logger.error(f"Database error during upsert: {e}")
        conn.rollback()
    finally:
        conn.close()

def update_macro_calendar() -> None:
    """Master function to execute the calendar ingestion pipeline."""
    logger.info("Starting Macro Calendar Ingestion...")
    data = fetch_and_process_calendar()
    upsert_calendar_events(data)
    logger.info("Macro Calendar Ingestion Complete.")

if __name__ == "__main__":
    update_macro_calendar()