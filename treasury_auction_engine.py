# GUI name: "Sovereign Debt Auction Monitor". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.
import logging
import requests
from datetime import datetime, timezone
from typing import Optional

from database import get_connection
from notification_engine import notify

logger = logging.getLogger(__name__)

_API_BASE = "https://api.fiscaldata.treasury.gov/services/api/v1/accounting/od/auctions_query"
_FIELDS = (
    "cusip,security_term,security_type,auction_date,high_yield,median_yield,"
    "bid_to_cover_ratio,direct_bidder_accepted,indirect_bidder_accepted,"
    "primary_dealer_accepted,competitive_accepted,offering_amt"
)

_TERM_MAP: dict[str, str] = {
    "4-Week":  "4W",  "8-Week":   "8W",  "13-Week": "3M",  "17-Week": "4M",
    "26-Week": "6M",  "52-Week":  "1Y",  "2-Year":  "2Y",  "3-Year":  "3Y",
    "5-Year":  "5Y",  "7-Year":   "7Y",  "10-Year": "10Y", "20-Year": "20Y",
    "30-Year": "30Y",
}

_WEAK_BTC_THRESHOLD = 0.2
_WEAK_TAIL_THRESHOLD = 2.0


def _maturity_label(security_term: str) -> str:
    return _TERM_MAP.get(security_term, security_term)


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _pct(part, total: Optional[float]) -> Optional[float]:
    n = _safe_float(part)
    if n is None or total is None or total == 0:
        return None
    return round(n / total * 100.0, 2)


def _tail_bp(high_yield: Optional[float], median_yield: Optional[float]) -> Optional[float]:
    if high_yield is None or median_yield is None:
        return None
    return round((high_yield - median_yield) * 100.0, 2)


def fetch_todays_auctions() -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    params = {
        "filters": f"auction_date:eq:{today}",
        "fields": _FIELDS,
        "sort": "-auction_date",
        "page[size]": 25,
    }
    try:
        resp = requests.get(_API_BASE, params=params, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Treasury auction API request failed: %s", e)
        return []
    payload = resp.json()
    data = payload.get("data")
    return data if isinstance(data, list) else []


def _get_baseline(conn, maturity_label: str, exclude_cusip: str, exclude_date: str) -> tuple[Optional[float], Optional[float]]:
    """Mean bid-to-cover and tail_bp over the 6 most recent prior auctions for this maturity."""
    cursor = conn.cursor()
    cursor.execute(
        """SELECT bid_to_cover, tail_bp FROM treasury_auction_results
           WHERE maturity_label = ? AND cusip != ? AND auction_date < ?
           ORDER BY auction_date DESC LIMIT 6""",
        (maturity_label, exclude_cusip, exclude_date),
    )
    rows = cursor.fetchall()
    if not rows:
        return None, None
    btcs = [r[0] for r in rows if r[0] is not None]
    tails = [r[1] for r in rows if r[1] is not None]
    mean_btc = sum(btcs) / len(btcs) if btcs else None
    mean_tail = sum(tails) / len(tails) if tails else None
    return mean_btc, mean_tail


def _is_weak(btc: Optional[float], mean_btc: Optional[float],
             tail: Optional[float], mean_tail: Optional[float]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if btc is not None and mean_btc is not None and btc < mean_btc - _WEAK_BTC_THRESHOLD:
        reasons.append(f"bid-to-cover {btc:.2f} vs 6-auction mean {mean_btc:.2f}")
    if tail is not None and mean_tail is not None and tail > mean_tail + _WEAK_TAIL_THRESHOLD:
        reasons.append(f"tail {tail:.1f}bp vs 6-auction mean {mean_tail:.1f}bp")
    return bool(reasons), reasons


def check_auction_results() -> int:
    """Fetch today's Treasury auction results, persist new rows, alert on demand weakness. Returns new-row count."""
    records = fetch_todays_auctions()
    if not records:
        logger.info("No Treasury auction results found for today.")
        return 0

    new_count = 0
    conn = None
    try:
        conn = get_connection()
        for rec in records:
            cusip = rec.get("cusip") or ""
            security_term = rec.get("security_term") or ""
            auction_date = rec.get("auction_date") or ""
            if not cusip or not auction_date:
                continue

            maturity = _maturity_label(security_term)
            high_yield = _safe_float(rec.get("high_yield"))
            median_yield = _safe_float(rec.get("median_yield"))
            bid_to_cover = _safe_float(rec.get("bid_to_cover_ratio"))
            tail = _tail_bp(high_yield, median_yield)
            competitive_accepted = _safe_float(rec.get("competitive_accepted"))
            direct_pct = _pct(rec.get("direct_bidder_accepted"), competitive_accepted)
            indirect_pct = _pct(rec.get("indirect_bidder_accepted"), competitive_accepted)
            dealer_pct = _pct(rec.get("primary_dealer_accepted"), competitive_accepted)
            offering_amt = _safe_float(rec.get("offering_amt"))

            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO treasury_auction_results
                   (cusip, maturity_label, auction_date, high_yield, bid_to_cover, tail_bp,
                    direct_pct, indirect_pct, dealer_pct, offering_amt, alert_fired)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                   ON CONFLICT(cusip, auction_date) DO NOTHING""",
                (cusip, maturity, auction_date, high_yield, bid_to_cover, tail,
                 direct_pct, indirect_pct, dealer_pct, offering_amt),
            )
            if cursor.rowcount > 0:
                new_count += 1
            conn.commit()

            row = conn.execute(
                "SELECT alert_fired FROM treasury_auction_results WHERE cusip = ? AND auction_date = ?",
                (cusip, auction_date),
            ).fetchone()
            if row and row[0]:
                continue

            mean_btc, mean_tail = _get_baseline(conn, maturity, cusip, auction_date)
            weak, reasons = _is_weak(bid_to_cover, mean_btc, tail, mean_tail)
            if weak:
                msg = f"Weak {maturity} Treasury auction ({auction_date}): {'; '.join(reasons)}"
                notify("treasury_auction_alert", "Auction Weakness", msg, level="warning")
                conn.execute(
                    "UPDATE treasury_auction_results SET alert_fired = 1 WHERE cusip = ? AND auction_date = ?",
                    (cusip, auction_date),
                )
                conn.commit()
                logger.warning("Treasury auction weakness alert fired: %s", msg)

    except Exception as e:
        logger.error("Treasury auction results check failed: %s", e)
    finally:
        if conn:
            conn.close()

    logger.info("Treasury auction check complete: %d new result(s) stored.", new_count)
    return new_count
