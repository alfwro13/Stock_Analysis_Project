# macro_calendar_engine.py
import sqlite3
import logging
import requests
import xml.etree.ElementTree as ET
import hashlib
import pandas as pd
import yfinance as yf
from typing import Optional, List, Tuple
from datetime import datetime, timedelta

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
                time_clean = time_str.replace(' ', '').lower()
                dt_obj = datetime.strptime(f"{date_str} {time_clean}", "%m-%d-%Y %I:%M%p")
                formatted_date = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    dt_obj = datetime.strptime(date_str, "%m-%d-%Y")
                    formatted_date = dt_obj.strftime("%Y-%m-%d 00:00:00")
                except ValueError:
                    logger.warning(f"Could not parse date: {date_str} {time_str}")
                    formatted_date = f"{date_str} {time_str}"
                
            event_id = generate_event_id(formatted_date, currency, event_name)
            
            forecast_val = clean_value(event.findtext('forecast'))
            previous_val = clean_value(event.findtext('previous'))
            
            # Attempt to pull 'actual' if the event just occurred
            actual_val = clean_value(event.findtext('actual'))
            
            # Check if event has passed based on current time
            try:
                is_passed = 1 if datetime.now() > datetime.strptime(formatted_date, "%Y-%m-%d %H:%M:%S") else 0
            except ValueError:
                is_passed = 0
            
            processed_events.append((
                event_id,
                formatted_date,
                currency,
                impact,
                event_name,
                forecast_val,
                previous_val,
                actual_val,
                None, # post_event_spy_gap (calculated later)
                0,    # ai_volatility_warning default
                is_passed, 
                0     # alert_dispatched default
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
        # Note: We use COALESCE to preserve existing ML warnings or actuals if ForexFactory feed changes
        cursor.executemany('''
            INSERT INTO macro_calendar (
                event_id, event_date, currency, impact, event_name, 
                forecast_val, previous_val, actual_val, post_event_spy_gap,
                ai_volatility_warning, is_event_passed, alert_dispatched
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                forecast_val = excluded.forecast_val,
                previous_val = excluded.previous_val,
                actual_val = COALESCE(excluded.actual_val, macro_calendar.actual_val),
                is_event_passed = excluded.is_event_passed
        ''', events)
        conn.commit()
        logger.info(f"Successfully upserted {cursor.rowcount} macro events.")
    except sqlite3.Error as e:
        logger.error(f"Database error during upsert: {e}")
        conn.rollback()
    finally:
        conn.close()

def reconcile_past_events() -> None:
    """
    Finds events that have passed in the last 7 days but have no SPY gap data.
    Downloads 5-minute intraday data via yfinance and calculates the maximum 
    absolute percentage swing within a 30-minute window post-release.
    """
    logger.info("Reconciling ground-truth SPY gaps for passed macro events...")
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Target events within the last 7 days (yf 5m data limit is 60d, 7d is very safe)
    cutoff = datetime.now() - timedelta(days=7)
    
    cursor.execute('''
        SELECT event_id, event_date FROM macro_calendar 
        WHERE is_event_passed = 1 
        AND post_event_spy_gap IS NULL
        AND event_date > ?
    ''', (cutoff.strftime("%Y-%m-%d %H:%M:%S"),))
    
    events_to_reconcile = cursor.fetchall()
    
    if not events_to_reconcile:
        logger.info("No pending past events require reconciliation.")
        conn.close()
        return

    # Fetch 7 days of 5m SPY data once to save API hits
    logger.info(f"Fetching SPY intraday data to reconcile {len(events_to_reconcile)} events.")
    try:
        spy_df = yf.download("SPY", period="7d", interval="5m", progress=False, auto_adjust=True)
        if spy_df.empty:
            logger.error("Failed to fetch SPY data for reconciliation.")
            conn.close()
            return
            
        if isinstance(spy_df.columns, pd.MultiIndex):
            spy_df.columns = spy_df.columns.get_level_values(0)
            
        spy_df.index = spy_df.index.tz_localize(None)
    except Exception as e:
        logger.error(f"YFinance error during reconciliation: {e}")
        conn.close()
        return

    updates = []
    for row in events_to_reconcile:
        try:
            event_dt = datetime.strptime(row['event_date'], "%Y-%m-%d %H:%M:%S")
            # We look at the window from exact event time up to 30 minutes after
            window_end = event_dt + timedelta(minutes=30)
            
            # Filter SPY dataframe
            event_window = spy_df[(spy_df.index >= event_dt) & (spy_df.index <= window_end)]
            
            if not event_window.empty and len(event_window) >= 1:
                # The pre-release anchor is the open of the very first 5m candle
                anchor_price = event_window['Open'].iloc[0]
                
                # Maximum variance (high or low) within the 30m window
                max_price = event_window['High'].max()
                min_price = event_window['Low'].min()
                
                gap_up = ((max_price - anchor_price) / anchor_price) * 100.0
                gap_down = ((anchor_price - min_price) / anchor_price) * 100.0
                
                # We care about the absolute magnitude of the shock
                max_abs_gap = max(abs(gap_up), abs(gap_down))
                
                updates.append((max_abs_gap, row['event_id']))
                logger.info(f"Reconciled {row['event_id']} - SPY Gap: {max_abs_gap:.2f}%")
            else:
                logger.debug(f"Event {row['event_id']} at {event_dt} fell outside market hours. Assigning 0.0 gap.")
                updates.append((0.0, row['event_id']))
                
        except Exception as e:
            logger.error(f"Failed to process reconciliation for event {row['event_id']}: {e}")

    if updates:
        cursor.executemany('''
            UPDATE macro_calendar SET post_event_spy_gap = ? WHERE event_id = ?
        ''', updates)
        conn.commit()
        logger.info(f"Successfully committed {len(updates)} ground-truth gap calculations.")
        
    conn.close()

def update_macro_calendar() -> None:
    """Master function to execute the calendar ingestion pipeline."""
    logger.info("Starting Macro Calendar Ingestion...")
    data = fetch_and_process_calendar()
    upsert_calendar_events(data)
    reconcile_past_events()
    logger.info("Macro Calendar Ingestion Complete.")

if __name__ == "__main__":
    update_macro_calendar()