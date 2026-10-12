# GUI name: "Sovereign Debt Auction Monitor". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.
import logging
import requests
from datetime import date, timedelta
from typing import Optional

import time_engine
from database import get_connection
from notification_engine import notify

logger = logging.getLogger(__name__)

_API_BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query"
_FIELDS = (
    "cusip,security_type,security_term,original_security_term,floating_rate,inflation_index_security,"
    "auction_date,high_yield,avg_med_yield,high_discnt_rate,avg_med_discnt_rate,"
    "bid_to_cover_ratio,direct_bidder_accepted,indirect_bidder_accepted,"
    "primary_dealer_accepted,comp_accepted,offering_amt"
)

_TERM_MAP: dict[str, str] = {
    "4-Week":  "4W",  "8-Week":   "8W",  "13-Week": "3M",  "17-Week": "4M",
    "26-Week": "6M",  "52-Week":  "1Y",  "6-Week":  "6W",  "2-Year":  "2Y",  "3-Year":  "3Y",
    "5-Year":  "5Y",  "7-Year":   "7Y",  "10-Year": "10Y", "20-Year": "20Y",
    "30-Year": "30Y",
}

_WEAK_BTC_THRESHOLD = 0.2
_WEAK_TAIL_THRESHOLD = 2.0
# Bills, FRNs and TIPS move more week to week than nominal notes/bonds (prod bid-to-cover stdev 0.22/0.27
# vs 0.09), so the coupon thresholds flagged about one in four bill auctions.
_WIDE_WEAK_BTC_THRESHOLD = 0.4
_WIDE_WEAK_TAIL_THRESHOLD = 4.0
_BILL_LABELS = frozenset(label for term, label in _TERM_MAP.items() if term.endswith("-Week"))

_AUCTION_EXCHANGE = "NYSE"
_MAX_LOOKBACK_DAYS = 180
_OVERLAP_DAYS = 3


def _clean(val) -> str:
    return "" if val in (None, "null") else str(val)


def _maturity_label(rec: dict) -> str:
    term = _clean(rec.get("security_term"))
    if rec.get("security_type") != "Bill":
        term = _clean(rec.get("original_security_term")) or term
    label = _TERM_MAP.get(term, term)
    if rec.get("floating_rate") == "Yes":
        return f"{label} FRN"
    if rec.get("inflation_index_security") == "Yes":
        return f"{label} TIPS"
    return label


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


def _record_tail_bp(rec: dict) -> Optional[float]:
    if rec.get("security_type") == "Bill":
        return _tail_bp(_safe_float(rec.get("high_discnt_rate")), _safe_float(rec.get("avg_med_discnt_rate")))
    return _tail_bp(_safe_float(rec.get("high_yield")), _safe_float(rec.get("avg_med_yield")))


def _window_start(today: date) -> str:
    floor = today - timedelta(days=_MAX_LOOKBACK_DAYS)
    conn = None
    try:
        conn = get_connection()
        newest = conn.execute("SELECT MAX(auction_date) FROM treasury_auction_results").fetchone()[0]
    finally:
        if conn:
            conn.close()
    if not newest:
        return floor.isoformat()
    return max(floor, date.fromisoformat(newest) - timedelta(days=_OVERLAP_DAYS)).isoformat()


def fetch_auctions(start: str, end: str) -> list[dict]:
    params = {
        "filter": f"auction_date:gte:{start},auction_date:lte:{end}",
        "fields": _FIELDS,
        "sort": "auction_date",
        "page[size]": 1000,
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


def _is_wide_series(maturity_label: str) -> bool:
    return maturity_label in _BILL_LABELS or maturity_label.endswith(("-Day", " FRN", " TIPS"))


def is_weak(btc: Optional[float], mean_btc: Optional[float],
            tail: Optional[float], mean_tail: Optional[float],
            maturity_label: str = "") -> tuple[bool, bool, bool]:
    wide = _is_wide_series(maturity_label)
    btc_threshold = _WIDE_WEAK_BTC_THRESHOLD if wide else _WEAK_BTC_THRESHOLD
    tail_threshold = _WIDE_WEAK_TAIL_THRESHOLD if wide else _WEAK_TAIL_THRESHOLD
    weak_btc = btc is not None and mean_btc is not None and btc < mean_btc - btc_threshold
    weak_tail = tail is not None and mean_tail is not None and tail > mean_tail + tail_threshold
    return weak_btc or weak_tail, weak_btc, weak_tail


def format_weakness_message(
    maturity: str, auction_date: str,
    btc: Optional[float], mean_btc: Optional[float],
    tail: Optional[float], mean_tail: Optional[float],
    weak_btc: bool, weak_tail: bool,
) -> str:
    """Plain-English alert text for non-finance readers — always covers both signals, flagging whichever tripped."""
    if btc is not None and mean_btc is not None:
        btc_line = (
            f"Demand (bid-to-cover): investors offered to buy ${btc:.2f} for every $1 of bonds on sale, "
            f"vs a recent average of ${mean_btc:.2f}"
            + (" — weaker than usual." if weak_btc else ".")
        )
    else:
        btc_line = "Demand (bid-to-cover): no recent baseline yet to compare against."

    if tail is not None and mean_tail is not None:
        tail_line = (
            f"Bid quality (yield tail): the gap between the priciest accepted bid and the average bid was "
            f"{tail:.1f} basis points, vs a recent average of {mean_tail:.1f}"
            + (" — wider than usual, meaning buyers demanded an extra premium." if weak_tail else ".")
        )
    else:
        tail_line = "Bid quality (yield tail): no recent baseline yet to compare against."

    return (
        f"⚠ Weak {maturity} Treasury auction ({auction_date}). {btc_line} {tail_line} "
        "Why it matters: weak demand can force the US government to offer higher interest rates to attract "
        "buyers next time, and rising rates often put pressure on stock valuations. One weak auction on its "
        "own isn't alarming — a repeated pattern is worth watching. Details: /treasury-auctions"
    )


def _refresh_weak_flags(conn, today: str) -> None:
    """Re-derive alert_fired for every stored auction from the current rule; only a newly weak auction dated today notifies."""
    rows = conn.execute(
        """SELECT cusip, maturity_label, auction_date, bid_to_cover, tail_bp, alert_fired
           FROM treasury_auction_results ORDER BY auction_date"""
    ).fetchall()
    for r in rows:
        cusip, maturity, auction_date, bid_to_cover, tail, flagged = (
            r["cusip"], r["maturity_label"], r["auction_date"], r["bid_to_cover"], r["tail_bp"], r["alert_fired"],
        )
        mean_btc, mean_tail = _get_baseline(conn, maturity, cusip, auction_date)
        weak, weak_btc, weak_tail = is_weak(bid_to_cover, mean_btc, tail, mean_tail, maturity)
        if weak == bool(flagged):
            continue
        if weak and auction_date == today:
            msg = format_weakness_message(
                maturity, auction_date, bid_to_cover, mean_btc, tail, mean_tail, weak_btc, weak_tail
            )
            notify("treasury_auction_alert", "Auction Weakness", msg, level="warning")
            logger.warning("Treasury auction weakness alert fired: %s", msg)
        conn.execute(
            "UPDATE treasury_auction_results SET alert_fired = ? WHERE cusip = ? AND auction_date = ?",
            (int(weak), cusip, auction_date),
        )
        conn.commit()


def check_auction_results() -> int:
    """Upsert every resulted auction since the newest stored one, then re-derive the weak flags. Returns new-row count."""
    today = time_engine.exchange_local_date(_AUCTION_EXCHANGE)
    records = [
        r for r in fetch_auctions(_window_start(today), today.isoformat())
        if _safe_float(r.get("bid_to_cover_ratio")) is not None
    ]
    if not records:
        logger.info("No resulted Treasury auctions found in the catch-up window.")
    records.sort(key=lambda r: r.get("auction_date") or "")

    new_count = 0
    conn = None
    try:
        conn = get_connection()
        for rec in records:
            cusip = rec.get("cusip") or ""
            auction_date = rec.get("auction_date") or ""
            if not cusip or not auction_date:
                continue

            maturity = _maturity_label(rec)
            high_yield = _safe_float(rec.get("high_yield"))
            bid_to_cover = _safe_float(rec.get("bid_to_cover_ratio"))
            tail = _record_tail_bp(rec)
            competitive_accepted = _safe_float(rec.get("comp_accepted"))
            direct_pct = _pct(rec.get("direct_bidder_accepted"), competitive_accepted)
            indirect_pct = _pct(rec.get("indirect_bidder_accepted"), competitive_accepted)
            dealer_pct = _pct(rec.get("primary_dealer_accepted"), competitive_accepted)
            offering_amt = _safe_float(rec.get("offering_amt"))

            is_new = conn.execute(
                "SELECT 1 FROM treasury_auction_results WHERE cusip = ? AND auction_date = ?",
                (cusip, auction_date),
            ).fetchone() is None
            conn.execute(
                """INSERT INTO treasury_auction_results
                   (cusip, maturity_label, auction_date, high_yield, bid_to_cover, tail_bp,
                    direct_pct, indirect_pct, dealer_pct, offering_amt, alert_fired)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                   ON CONFLICT(cusip, auction_date) DO UPDATE SET
                       maturity_label = excluded.maturity_label, high_yield = excluded.high_yield,
                       bid_to_cover = excluded.bid_to_cover, tail_bp = excluded.tail_bp,
                       direct_pct = excluded.direct_pct, indirect_pct = excluded.indirect_pct,
                       dealer_pct = excluded.dealer_pct, offering_amt = excluded.offering_amt""",
                (cusip, maturity, auction_date, high_yield, bid_to_cover, tail,
                 direct_pct, indirect_pct, dealer_pct, offering_amt),
            )
            conn.commit()
            if is_new:
                new_count += 1

        _refresh_weak_flags(conn, today.isoformat())
    except Exception as e:
        logger.error("Treasury auction results check failed: %s", e)
    finally:
        if conn:
            conn.close()

    logger.info("Treasury auction check complete: %d new result(s) stored.", new_count)
    return new_count
