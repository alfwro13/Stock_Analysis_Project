import logging
import hashlib
import os
import re
import requests
import xml.etree.ElementTree as ET
import pandas as pd
from typing import Dict, Optional, List, Tuple
from datetime import date, datetime, timedelta, timezone
from constants import MACRO_ACTUALS_LOOKBACK_DAYS
from db.database import get_connection, write_or_defer
from macro_data_engine import ONS_TAXONOMY, fetch_boe_data, fetch_fred_api, get_retry_session
from time_engine import parse_stored_utc, utc_to_exchange_local
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

# GUI name: "Macroeconomic Automation Schedulers (Calendar)". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
EASTERN_PARSER_FIRST_EVENT = "2026-06-08 00:00:00"
TARGET_CURRENCIES = {'USD', 'GBP'}
TARGET_IMPACT = 'High'
VOTE_SPLIT_RE = re.compile(r'\d+-\d+(-\d+)?')
BLS_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
FED_TARGET_SERIES = "DFEDTARU"
BOE_RATE_SERIES = "IUDBEDR"
ONS_CPI_SERIES = "D7G7"
ACTUAL_SOURCES = {
    ("USD", "CPI m/m"): ("bls", "CUSR0000SA0", "mom"),
    ("USD", "Core CPI m/m"): ("bls", "CUSR0000SA0L1E", "mom"),
    ("USD", "CPI y/y"): ("bls", "CUUR0000SA0", "yoy"),
    ("USD", "Core CPI y/y"): ("bls", "CUUR0000SA0L1E", "yoy"),
    ("USD", "Unemployment Rate"): ("bls", "LNS14000000", "level"),
    ("USD", "Non-Farm Employment Change"): ("bls", "CES0000000001", "change"),
    ("USD", "Average Hourly Earnings m/m"): ("bls", "CES0500000003", "mom"),
    ("GBP", "CPI y/y"): ("ons", ONS_CPI_SERIES, "level"),
    ("USD", "Federal Funds Rate"): ("fred", FED_TARGET_SERIES, "day_after"),
    ("GBP", "Official Bank Rate"): ("boe", BOE_RATE_SERIES, "same_day"),
}

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
            # The feed's times are UTC (US Retail Sales 8:30 ET is listed at 1:30pm, UK CPI 07:00 London at 7:00am), so a feed time is stored as it is.
            try:
                time_clean = time_str.replace(' ', '').lower()
                dt_naive = datetime.strptime(f"{date_str} {time_clean}", "%m-%d-%Y %I:%M%p")
                formatted_date = dt_naive.strftime(STAMP_FORMAT)
            except ValueError:
                try:
                    dt_naive = datetime.strptime(date_str, "%m-%d-%Y")
                    formatted_date = dt_naive.strftime(STAMP_FORMAT)
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

def correct_eastern_event_times() -> int:
    """Moves the events stored since the Eastern-time parser (first event 2026-06-08) back to the feed's own UTC time, under their new event_id, and clears their SPY gap, which was measured in the wrong window."""
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT event_id, event_date, currency, event_name FROM macro_calendar WHERE event_date >= ?", (EASTERN_PARSER_FIRST_EVENT,)
        ).fetchall()
        corrected = 0
        for row in rows:
            stored = parse_stored_utc(row["event_date"])
            if stored is None:
                continue
            event_date = utc_to_exchange_local(stored, "NYSE").replace(tzinfo=None).strftime(STAMP_FORMAT)
            conn.execute(
                "UPDATE macro_calendar SET event_id = ?, event_date = ?, post_event_spy_gap = NULL WHERE event_id = ?",
                (generate_event_id(event_date, row["currency"], row["event_name"]), event_date, row["event_id"]),
            )
            corrected += 1
        conn.commit()
        return corrected
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

def has_actual_source(currency: str, event_name: str) -> bool:
    return (currency, event_name) in ACTUAL_SOURCES

def _shift_month(period: Tuple[int, int], months_back: int) -> Tuple[int, int]:
    index = period[0] * 12 + period[1] - 1 - months_back
    return index // 12, index % 12 + 1

def _fetch_bls_series(series_ids: List[str], year: int) -> Dict[str, dict]:
    payload = {"seriesid": sorted(series_ids), "startyear": str(year - 1), "endyear": str(year)}
    try:
        response = requests.post(BLS_URL, json=payload, timeout=20)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as e:
        logger.error("Failed to fetch BLS series: %s", e)
        return {}
    if body.get("status") != "REQUEST_SUCCEEDED":
        logger.error("BLS refused the request: %s", body.get("message"))
        return {}
    out: Dict[str, dict] = {}
    for series in body.get("Results", {}).get("series", []):
        values = out.setdefault(series["seriesID"], {})
        for row in series.get("data", []):
            period = row.get("period", "")
            if not re.fullmatch(r"M(0[1-9]|1[0-2])", period):
                continue
            try:
                values[(int(row["year"]), int(period[1:]))] = float(row["value"])
            except ValueError:
                continue
    return out

def _fetch_ons_cpi_rate() -> Dict[str, dict]:
    try:
        response = get_retry_session().get(f"https://www.ons.gov.uk{ONS_TAXONOMY[ONS_CPI_SERIES]}", timeout=15)
        response.raise_for_status()
        months = response.json().get("months", [])
    except (requests.RequestException, ValueError) as e:
        logger.error("Failed to fetch ONS %s: %s", ONS_CPI_SERIES, e)
        return {}
    values = {}
    for obs in months:
        try:
            observed = datetime.strptime(obs["date"], "%Y %b")
            values[(observed.year, observed.month)] = float(obs["value"])
        except (KeyError, ValueError):
            continue
    return {ONS_CPI_SERIES: values}

def _daily_values(frame: pd.DataFrame, column: str) -> Dict[date, float]:
    if frame.empty or column not in frame.columns:
        return {}
    return {stamp.date(): float(value) for stamp, value in frame[column].items() if pd.notna(value)}

def _fetch_rate_series(sources: set, now: datetime) -> Dict[str, dict]:
    start = now - timedelta(days=MACRO_ACTUALS_LOOKBACK_DAYS + 7)
    session = get_retry_session()
    out: Dict[str, dict] = {}
    if "fred" in sources:
        api_key = os.environ.get("FRED_API_KEY", "")
        if api_key:
            out[FED_TARGET_SERIES] = _daily_values(fetch_fred_api(session, FED_TARGET_SERIES, start, now, api_key), FED_TARGET_SERIES)
        else:
            logger.warning("FRED_API_KEY is not configured: the Federal Funds Rate actual cannot be read.")
    if "boe" in sources:
        out[BOE_RATE_SERIES] = _daily_values(fetch_boe_data(session, BOE_RATE_SERIES, start, now, lag_days=0), BOE_RATE_SERIES)
    return out

def _load_actual_series(specs: set, now: datetime) -> Dict[str, dict]:
    sources = {source for source, _, _ in specs}
    data: Dict[str, dict] = {}
    if "bls" in sources:
        data.update(_fetch_bls_series([series for source, series, _ in specs if source == "bls"], now.year))
    if "ons" in sources:
        data.update(_fetch_ons_cpi_rate())
    data.update(_fetch_rate_series(sources, now))
    return data

def resolve_actual(spec: Tuple[str, str, str], event_dt: datetime, data: Dict[str, dict]) -> Optional[float]:
    """A release's result from its source: the monthly series for the month before the release month, or the policy rate on the decision's effective day; None until the source holds it."""
    _, series, how = spec
    values = data.get(series) or {}
    if how in ("day_after", "same_day"):
        return values.get(event_dt.date() + timedelta(days=1 if how == "day_after" else 0))
    reference = _shift_month((event_dt.year, event_dt.month), 1)
    current = values.get(reference)
    if current is None or how == "level":
        return current
    prior = values.get(_shift_month(reference, 12 if how == "yoy" else 1))
    if prior is None:
        return None
    if how == "change":
        return float(round((current - prior) * 1000))
    return round((current / prior - 1) * 100, 1)

def _write_actuals(updates: List[Tuple[float, str]]) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.executemany("UPDATE macro_calendar SET actual_val = ? WHERE event_id = ? AND actual_val IS NULL", updates)
        conn.commit()
    finally:
        if conn:
            conn.close()

def fill_released_actuals() -> int:
    """The feed carries no actual, so the released High events of the last MACRO_ACTUALS_LOOKBACK_DAYS days get theirs from BLS, ONS, FRED and the Bank of England; returns the number filled."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=MACRO_ACTUALS_LOOKBACK_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT event_id, event_date, currency, event_name FROM macro_calendar "
            "WHERE actual_val IS NULL AND actual_text IS NULL AND event_date BETWEEN ? AND ?",
            (since.strftime(STAMP_FORMAT), now.strftime(STAMP_FORMAT)),
        ).fetchall()
    finally:
        if conn:
            conn.close()
    pending = []
    for row in rows:
        spec = ACTUAL_SOURCES.get((row["currency"], row["event_name"]))
        event_dt = parse_stored_utc(row["event_date"])
        if spec and event_dt:
            pending.append((row["event_id"], event_dt, spec))
    if not pending:
        return 0
    data = _load_actual_series({spec for _, _, spec in pending}, now)
    updates = []
    for event_id, event_dt, spec in pending:
        actual = resolve_actual(spec, event_dt, data)
        if actual is not None:
            updates.append((actual, event_id))
    if updates:
        write_or_defer("Macro calendar actuals", lambda: _write_actuals(updates))
    logger.info("Macro actuals: %s of %s released events filled.", len(updates), len(pending))
    return len(updates)

def update_macro_calendar() -> None:
    logger.info("Starting Macro Calendar Ingestion...")
    data = fetch_and_process_calendar()
    upsert_calendar_events(data)
    fill_released_actuals()
    reconcile_past_events()
    logger.info("Macro Calendar Ingestion Complete.")

if __name__ == "__main__":
    update_macro_calendar()
