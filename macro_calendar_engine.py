import logging
import hashlib
import re
import requests
import xml.etree.ElementTree as ET
import pandas as pd
from typing import Optional, List, Tuple
from datetime import datetime, timedelta, timezone
from database import get_connection, write_or_defer
from time_engine import localize_naive_to_utc, parse_stored_utc
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

# GUI name: "Macroeconomic Automation Schedulers (Calendar)". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
TARGET_CURRENCIES = {'USD', 'GBP'}
TARGET_IMPACT = 'High'
VOTE_SPLIT_RE = re.compile(r'\d+-\d+(-\d+)?')

def vote_split_text(val_str: Optional[str]) -> Optional[str]:
    """Return a central-bank vote split such as '7-0-2' as written in the feed, or None for any other value."""
    if not isinstance(val_str, str):
        return None
    val_str = val_str.strip()
    return val_str if VOTE_SPLIT_RE.fullmatch(val_str) else None

def clean_value(val_str: Optional[str], event_name: str = "") -> Optional[float]:
    """Parse financial magnitude strings (K/M/B/T/%) to raw float, or None if unparseable (a vote split is text, not a number)."""
    if not val_str or not isinstance(val_str, str):
        return None

    val_str = val_str.strip()
    if val_str in ('', '-') or vote_split_text(val_str):
        return None

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
        logger.warning("Failed to parse numeric value from string %r for event %r", val_str, event_name)
        return None

def generate_event_id(date_str: str, currency: str, event_name: str) -> str:
    raw_string = f"{date_str}_{currency}_{event_name}"
    return hashlib.sha256(raw_string.encode('utf-8')).hexdigest()

def fetch_and_process_calendar() -> List[Tuple]:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36'
    }

    try:
        response = requests.get(FEED_URL, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Network error fetching calendar XML: %s", e)
        return []

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as e:
        logger.error("Failed to parse XML content: %s", e)
        return []

    processed_events = []

    for event in root.findall('event'):
        impact = event.findtext('impact', '').strip()
        currency = event.findtext('country', '').strip()

        if impact == TARGET_IMPACT and currency in TARGET_CURRENCIES:
            date_str = event.findtext('date', '').strip()
            time_str = event.findtext('time', '').strip()
            event_name = event.findtext('title', '').strip()

            formatted_date = ""
            try:
                time_clean = time_str.replace(' ', '').lower()
                dt_naive = datetime.strptime(f"{date_str} {time_clean}", "%m-%d-%Y %I:%M%p")
                formatted_date = localize_naive_to_utc(dt_naive, "NYSE").strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    dt_naive = datetime.strptime(date_str, "%m-%d-%Y")
                    formatted_date = localize_naive_to_utc(dt_naive, "NYSE").strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    logger.warning("Could not parse date: %s %s", date_str, time_str)
                    formatted_date = f"{date_str} {time_str}"

            event_id = generate_event_id(formatted_date, currency, event_name)

            forecast_raw = event.findtext('forecast')
            previous_raw = event.findtext('previous')
            actual_raw = event.findtext('actual')
            forecast_val = clean_value(forecast_raw, event_name)
            previous_val = clean_value(previous_raw, event_name)
            actual_val = clean_value(actual_raw, event_name)

            event_utc = parse_stored_utc(formatted_date)
            is_passed = 1 if event_utc is not None and datetime.now(timezone.utc) > event_utc else 0

            processed_events.append((
                event_id,
                formatted_date,
                currency,
                impact,
                event_name,
                forecast_val,
                previous_val,
                actual_val,
                None, # post_event_spy_gap (calculated later by reconcile_past_events)
                0,    # ai_volatility_warning (set by MacroAIEngine inference)
                is_passed,
                0,    # alert_dispatched default
                vote_split_text(forecast_raw),
                vote_split_text(previous_raw),
                vote_split_text(actual_raw),
            ))

    return processed_events

def _write_calendar_events(events: List[Tuple]) -> None:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        # COALESCE preserves existing ML warnings or actuals if the ForexFactory feed later omits them
        cursor.executemany('''
            INSERT INTO macro_calendar (
                event_id, event_date, currency, impact, event_name,
                forecast_val, previous_val, actual_val, post_event_spy_gap,
                ai_volatility_warning, is_event_passed, alert_dispatched,
                forecast_text, previous_text, actual_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                forecast_val = excluded.forecast_val,
                previous_val = excluded.previous_val,
                actual_val = COALESCE(excluded.actual_val, macro_calendar.actual_val),
                forecast_text = excluded.forecast_text,
                previous_text = excluded.previous_text,
                actual_text = COALESCE(excluded.actual_text, macro_calendar.actual_text),
                is_event_passed = excluded.is_event_passed
        ''', events)
        conn.commit()
        logger.info("Successfully upserted %s macro events.", cursor.rowcount)
    finally:
        if conn:
            conn.close()

def upsert_calendar_events(events: List[Tuple]) -> None:
    if not events:
        logger.info("No high-impact events found matching criteria. Exiting.")
        return
    write_or_defer("Macro calendar events", lambda: _write_calendar_events(events))

def reconcile_past_events() -> None:
    """Calculate post-release SPY gap for events passed in the last 7 days with no gap data."""
    logger.info("Reconciling ground-truth SPY gaps for passed macro events...")
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        # 7d is safe: yfinance 5m data limit is 60d
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        cursor.execute('''
            SELECT event_id, event_date FROM macro_calendar
            WHERE is_event_passed = 1
            AND post_event_spy_gap IS NULL
            AND event_date > ?
        ''', (cutoff.strftime("%Y-%m-%d %H:%M:%S"),))

        events_to_reconcile = cursor.fetchall()

        if not events_to_reconcile:
            logger.info("No pending past events require reconciliation.")
            return

        logger.info("Fetching SPY intraday data to reconcile %s events.", len(events_to_reconcile))
        _spy_result = yahoo_engine.get_intraday(["SPY"], period="7d", interval="5m")
        spy_df = _spy_result.get("SPY")
        if spy_df is None or spy_df.empty:
            logger.error("Failed to fetch SPY data for reconciliation.")
            return
        if spy_df.index.tz is None:
            spy_df.index = spy_df.index.tz_localize(timezone.utc)
        else:
            spy_df.index = spy_df.index.tz_convert(timezone.utc)

        updates = []
        for row in events_to_reconcile:
            event_dt = parse_stored_utc(row['event_date'])
            if event_dt is None:
                logger.error("Failed to process reconciliation for event %s: unparseable event_date", row['event_id'])
                continue
            try:
                window_end = event_dt + timedelta(minutes=30)

                event_window = spy_df[(spy_df.index >= event_dt) & (spy_df.index <= window_end)]

                if not event_window.empty and len(event_window) >= 1:
                    # anchor = open of the first 5m candle; measure max absolute swing within 30m window
                    anchor_price = event_window['Open'].iloc[0]
                    max_price = event_window['High'].max()
                    min_price = event_window['Low'].min()

                    gap_up = ((max_price - anchor_price) / anchor_price) * 100.0
                    gap_down = ((anchor_price - min_price) / anchor_price) * 100.0
                    max_abs_gap = max(abs(gap_up), abs(gap_down))

                    updates.append((max_abs_gap, row['event_id']))
                    logger.info("Reconciled %s - SPY Gap: %.2f%%", row['event_id'], max_abs_gap)
                else:
                    logger.debug("Event %s at %s fell outside market hours. Assigning 0.0 gap.", row['event_id'], event_dt)
                    updates.append((0.0, row['event_id']))

            except Exception as e:
                logger.error("Failed to process reconciliation for event %s: %s", row['event_id'], e)

        if updates:
            cursor.executemany('''
                UPDATE macro_calendar SET post_event_spy_gap = ? WHERE event_id = ?
            ''', updates)
            conn.commit()
            logger.info("Successfully committed %s ground-truth gap calculations.", len(updates))

    finally:
        if conn:
            conn.close()

def update_macro_calendar() -> None:
    logger.info("Starting Macro Calendar Ingestion...")
    data = fetch_and_process_calendar()
    upsert_calendar_events(data)
    reconcile_past_events()
    logger.info("Macro Calendar Ingestion Complete.")

if __name__ == "__main__":
    update_macro_calendar()
