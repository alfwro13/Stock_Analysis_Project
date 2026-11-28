import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from config import FUNDAMENTALS_DIR
from constants import (
    FUNDAMENTALS_DEGRADED_MIN_DUMPS, FUNDAMENTALS_FRESHNESS_LIST_MAX, FUNDAMENTALS_MAX_AGE_DAYS, FUNDAMENTALS_OVERDUE_DAYS, FUNDAMENTALS_REFRESH_DAYS,
    FUNDAMENTALS_REFRESH_MAX_RATE_LIMITS, FUNDAMENTALS_REFRESH_SETTLE_SECONDS,
)
from yahoo_engine import yahoo_engine
from db.database import get_connection, log_notification
from db.helpers import get_latest_fundamentals_refresh_run, get_portfolio_watchlist_tickers, record_fundamentals_refresh_run
from fundamentals_helpers import calculate_peter_lynch_peg, currency_safe_price_ratios, dividend_yield_fraction
from notification_engine import notify
from profile_engine import load_blacklist
from time_engine import fmt_stored_utc, parse_stored_utc
from tools.network_engine import wait_for_yahoo_rate_limit_reset, yahoo_rate_limit_trips
from universe_deep_sync_engine import _get_universe_target_tickers
from utils import (
    contained_ticker_path, epoch_to_iso_date, fundamentals_dump_age, ignored_tickers_set, is_excluded_from_yahoo_fetch,
    next_earnings_iso_date, normalize_ticker, save_fundamentals_dump,
)

logger = logging.getLogger(__name__)

# Holdings composition changes slowly (quarterly rebalances at most) — a 30-day cache window
# avoids re-fetching on every quant_analysis_job run while still catching drift eventually.
_HOLDINGS_REFRESH_DAYS = 30


def _fetch_info(ticker: str) -> dict:
    """The cached `.info` dump while younger than FUNDAMENTALS_REFRESH_DAYS, else a fresh fetch; a failed fetch falls back to the old dump only up to FUNDAMENTALS_MAX_AGE_DAYS."""
    cache_path = contained_ticker_path(FUNDAMENTALS_DIR, ticker, ".json")
    if cache_path is None:
        return {}
    cached: dict = {}
    age = timedelta(0)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r') as f:
                cached = json.load(f)
        except Exception:
            logger.warning("Corrupted fundamentals cache for %s, re-fetching", ticker, exc_info=True)
        age = fundamentals_dump_age(cache_path)
        if cached and age <= timedelta(days=FUNDAMENTALS_REFRESH_DAYS):
            return cached
    try:
        info = yahoo_engine.get_ticker_info(ticker) or {}
    except Exception as e:
        logger.warning("[%s] yfinance fetch failed: %s", ticker, e)
        info = {}
    if save_fundamentals_dump(ticker, info):
        return info
    if not cached:
        return info
    return cached if age <= timedelta(days=FUNDAMENTALS_MAX_AGE_DAYS) else {}


def _due_fundamentals_tickers(scope: set) -> list:
    """Scan-scope tickers whose dump is older than FUNDAMENTALS_REFRESH_DAYS plus index, Portfolio and Watchlist stocks with none; missing first, then oldest."""
    due = {}
    for path in FUNDAMENTALS_DIR.glob("*.json"):
        if normalize_ticker(path.stem) in scope:
            age = fundamentals_dump_age(path)
            if age > timedelta(days=FUNDAMENTALS_REFRESH_DAYS):
                due[path.stem] = age
    for ticker in set(_get_universe_target_tickers(freetrade_firewall=False)) | set(get_portfolio_watchlist_tickers()):
        path = contained_ticker_path(FUNDAMENTALS_DIR, ticker, ".json")
        if path is not None and not os.path.exists(path):
            due[ticker] = timedelta.max
    blacklist = load_blacklist()
    ignored = ignored_tickers_set()
    fetchable = [t for t in sorted(due) if t not in blacklist and not is_excluded_from_yahoo_fetch(t, ignored)]
    return sorted(fetchable, key=due.__getitem__, reverse=True)


def refresh_due_fundamentals() -> None:
    """Refetches every due dump ahead of the universe Quant Scan; never raises, and a ticker that fails stays due for the next run."""
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        from quant_signals import QuantEngine
        scope = QuantEngine._scan_scope()
        due = _due_fundamentals_tickers(scope) if scope else []
    except Exception:
        logger.error("Fundamentals refresh: could not list the due dumps.", exc_info=True)
        _record_refresh_run(started_at, None, None, False, None)
        return
    if not due:
        logger.info("Fundamentals refresh: nothing due.")
        _record_refresh_run(started_at, 0, 0, False, 0)
        check_fundamentals_refresh_degraded()
        return

    logger.info("Fundamentals refresh: %s dump(s) due.", len(due))
    trips_at_start = trips_seen = yahoo_rate_limit_trips()
    refreshed = 0
    stopped_early = False
    for ticker in due:
        try:
            _fetch_info(ticker)
            path = contained_ticker_path(FUNDAMENTALS_DIR, ticker, ".json")
            if path and os.path.exists(path) and fundamentals_dump_age(path) <= timedelta(days=FUNDAMENTALS_REFRESH_DAYS):
                refreshed += 1
        except Exception as e:
            logger.warning("[%s] Fundamentals refresh failed: %s", ticker, e)

        trips = yahoo_rate_limit_trips()
        if trips - trips_at_start >= FUNDAMENTALS_REFRESH_MAX_RATE_LIMITS:
            logger.warning("Fundamentals refresh stopped after %s Yahoo rate limits; the rest are retried next run.", trips - trips_at_start)
            stopped_early = True
            break
        if trips != trips_seen:
            wait_for_yahoo_rate_limit_reset()
            time.sleep(FUNDAMENTALS_REFRESH_SETTLE_SECONDS)
            trips_seen = yahoo_rate_limit_trips()
        else:
            time.sleep(0.4)
    logger.info("Fundamentals refresh: %s of %s due dump(s) refreshed.", refreshed, len(due))
    _record_refresh_run(started_at, len(due), refreshed, stopped_early, yahoo_rate_limit_trips() - trips_at_start)
    check_fundamentals_refresh_degraded()


def _record_refresh_run(started_at: str, due: Optional[int], refreshed: Optional[int], stopped_early: bool, rate_limits: Optional[int]) -> None:
    try:
        record_fundamentals_refresh_run(started_at, due, refreshed, stopped_early, rate_limits)
    except Exception:
        logger.error("Fundamentals refresh: could not record the run.", exc_info=True)


_FRESHNESS_GROUPS = (
    ("portfolio_watchlist", "Portfolio & Watchlist"),
    ("index", "Index universe (others)"),
    ("other", "Other scan scope"),
)
_FRESHNESS_BUCKETS = ("current", "due", "overdue", "expired", "no_dump")
_STATUS_RANK = {"green": 0, "amber": 1, "red": 2}


def _freshness_bucket(age: Optional[timedelta]) -> str:
    if age is None:
        return "no_dump"
    if age <= timedelta(days=FUNDAMENTALS_REFRESH_DAYS):
        return "current"
    if age <= timedelta(days=FUNDAMENTALS_OVERDUE_DAYS):
        return "due"
    if age <= timedelta(days=FUNDAMENTALS_MAX_AGE_DAYS):
        return "overdue"
    return "expired"


def _freshness_status(counts: dict, missing_counts: bool) -> str:
    if counts["expired"] or (missing_counts and counts["no_dump"]):
        return "red"
    return "amber" if counts["overdue"] else "green"


def _freshness_snapshot() -> tuple:
    """(scope, rows, attention): dump-age buckets for the tickers the refresh would fetch, grouped like its own due list (other-scope tickers get no missing dump fetched, so their No dump is neutral); attention is every flagged ticker, oldest first."""
    from quant_signals import QuantEngine
    scope = QuantEngine._scan_scope()
    blacklist = load_blacklist()
    ignored = ignored_tickers_set()

    def fetchable(tickers) -> set:
        return {
            normalize_ticker(t) for t in tickers
            if t not in blacklist and not is_excluded_from_yahoo_fetch(t, ignored)
        }

    portfolio_watchlist = fetchable(get_portfolio_watchlist_tickers())
    index = fetchable(_get_universe_target_tickers(freetrade_firewall=False)) - portfolio_watchlist
    other = fetchable(scope or ()) - portfolio_watchlist - index
    members = {"portfolio_watchlist": portfolio_watchlist, "index": index, "other": other}

    rows, attention = [], []
    total = dict.fromkeys(_FRESHNESS_BUCKETS, 0)
    total_status = "green"
    for key, label in _FRESHNESS_GROUPS:
        counts = dict.fromkeys(_FRESHNESS_BUCKETS, 0)
        for ticker in members[key]:
            path = contained_ticker_path(FUNDAMENTALS_DIR, ticker, ".json")
            age = fundamentals_dump_age(path) if path is not None and os.path.exists(path) else None
            bucket = _freshness_bucket(age)
            counts[bucket] += 1
            if bucket in ("overdue", "expired") or (bucket == "no_dump" and key != "other"):
                attention.append({
                    "ticker": ticker, "group": label, "state": bucket,
                    "age_days": round(age / timedelta(days=1), 1) if age is not None else None,
                })
        status = _freshness_status(counts, missing_counts=key != "other")
        rows.append({"key": key, "label": label, "tickers": len(members[key]), **counts, "status": status})
        for bucket in _FRESHNESS_BUCKETS:
            total[bucket] += counts[bucket]
        if _STATUS_RANK[status] > _STATUS_RANK[total_status]:
            total_status = status
    rows.append({"key": "total", "label": "Total", "tickers": sum(len(m) for m in members.values()), **total, "status": total_status})

    attention.sort(key=lambda r: r["ticker"])
    attention.sort(key=lambda r: float("inf") if r["age_days"] is None else r["age_days"], reverse=True)
    return scope, rows, attention


def fundamentals_freshness() -> dict:
    """The Fundamentals Freshness table for System Diagnostics."""
    scope, rows, attention = _freshness_snapshot()
    last_run = get_latest_fundamentals_refresh_run()
    if last_run is not None:
        last_run["finished_display"] = fmt_stored_utc(last_run["finished_at"])
    return {
        "rows": rows,
        "attention": attention[:FUNDAMENTALS_FRESHNESS_LIST_MAX],
        "attention_total": len(attention),
        "scope_available": scope is not None,
        "thresholds": {
            "refresh_days": FUNDAMENTALS_REFRESH_DAYS,
            "overdue_days": FUNDAMENTALS_OVERDUE_DAYS,
            "max_age_days": FUNDAMENTALS_MAX_AGE_DAYS,
        },
        "last_run": last_run,
    }


_DEGRADED_GROUPS = ("portfolio_watchlist", "index")
_DEGRADED_BUCKETS = ("overdue", "expired", "no_dump")
_DEGRADED_ENGINE = "fundamentals_refresh"
_DEGRADED_KEY = "ALL"
_DEGRADED_SAMPLE = 10


def check_fundamentals_refresh_degraded() -> None:
    """Warns once while FUNDAMENTALS_DEGRADED_MIN_DUMPS or more Portfolio/Watchlist or index dumps are Overdue, Expired or missing, which takes repeated failed nights rather than one, and re-arms when they fall below it; never raises."""
    try:
        _, rows, attention = _freshness_snapshot()
    except Exception:
        logger.error("Fundamentals refresh: could not read the dump ages for the degraded check.", exc_info=True)
        return
    by_key = {row["key"]: row for row in rows}
    counts = {bucket: sum(by_key[group][bucket] for group in _DEGRADED_GROUPS) for bucket in _DEGRADED_BUCKETS}
    stale = sum(counts.values())
    labels = {label for key, label in _FRESHNESS_GROUPS if key in _DEGRADED_GROUPS}
    flagged = [r["ticker"] for r in attention if r["group"] in labels]

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM alert_state WHERE engine = ? AND ticker = ?", (_DEGRADED_ENGINE, _DEGRADED_KEY))
        warned = cursor.fetchone() is not None
        now = datetime.now(timezone.utc)
        if stale < FUNDAMENTALS_DEGRADED_MIN_DUMPS:
            if warned:
                cursor.execute("DELETE FROM alert_state WHERE engine = ? AND ticker = ?", (_DEGRADED_ENGINE, _DEGRADED_KEY))
                logger.info("Fundamentals refresh recovered: %s Portfolio/Watchlist or index dump(s) stale or missing.", stale)
        elif warned:
            cursor.execute(
                "UPDATE alert_state SET state_date = ? WHERE engine = ? AND ticker = ?",
                (now.date().isoformat(), _DEGRADED_ENGINE, _DEGRADED_KEY),
            )
        else:
            shown = flagged[:_DEGRADED_SAMPLE]
            extra = f" (+{len(flagged) - len(shown)} more)" if len(flagged) > len(shown) else ""
            notify(
                "fundamentals_refresh_degraded", "Warning",
                f"{stale} Portfolio, Watchlist or index fundamentals dumps are older than {FUNDAMENTALS_OVERDUE_DAYS} days "
                f"or missing ({counts['overdue']} Overdue, {counts['expired']} Expired, {counts['no_dump']} No dump), "
                f"so the nightly fundamentals refresh keeps failing for them. Past {FUNDAMENTALS_MAX_AGE_DAYS} days a dump's "
                f"figures (P/E, target price, earnings date) are blank. Oldest first: {', '.join(shown)}{extra}. "
                f"See Fundamentals Freshness in System Diagnostics.",
                level="warning",
            )
            cursor.execute(
                "INSERT INTO alert_state (engine, ticker, last_fired_utc, state_date) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(engine, ticker) DO UPDATE SET last_fired_utc = excluded.last_fired_utc, state_date = excluded.state_date",
                (_DEGRADED_ENGINE, _DEGRADED_KEY, now.strftime("%Y-%m-%d %H:%M:%S"), now.date().isoformat()),
            )
        conn.commit()
    except Exception:
        logger.error("Fundamentals refresh degraded check failed.", exc_info=True)
    finally:
        if conn:
            conn.close()


def _compute_fundamental_score(info: dict) -> tuple:
    # Fundamentals-only composite score (0-100) — mirrors Quality Compounders report filters so eligible stocks score ≥ 60.
    score = 0
    breakdown = []

    roe            = info.get('returnOnEquity')
    profit_margin  = info.get('profitMargins')
    debt_to_equity = info.get('debtToEquity')   # Yahoo Finance scale: MSFT≈30 means 30% D/E
    revenue_growth = info.get('revenueGrowth')
    current_ratio  = info.get('currentRatio')
    trailing_pe    = info.get('trailingPE')

    # ROE (max +20)
    if roe is not None:
        if roe > 0.30:
            score += 20; breakdown.append("+20: ROE > 30% (Exceptional)")
        elif roe > 0.20:
            score += 15; breakdown.append("+15: ROE > 20% (Strong)")
        elif roe > 0.15:
            score += 10; breakdown.append("+10: ROE > 15% (Solid)")
        elif roe > 0.05:
            score += 5;  breakdown.append("+5: ROE > 5% (Positive)")
        else:
            score -= 10; breakdown.append("-10: ROE Negative or Weak")

    # Profit Margin (max +20)
    if profit_margin is not None:
        if profit_margin > 0.25:
            score += 20; breakdown.append("+20: Margin > 25% (World-class)")
        elif profit_margin > 0.15:
            score += 15; breakdown.append("+15: Margin > 15% (Strong)")
        elif profit_margin > 0.10:
            score += 10; breakdown.append("+10: Margin > 10% (Solid)")
        elif profit_margin > 0.05:
            score += 5;  breakdown.append("+5: Margin > 5% (Positive)")
        else:
            score -= 10; breakdown.append("-10: Margin Negative or Weak")

    # Debt/Equity (max +20) — Yahoo Finance stores as percentage (e.g. 30 = 30% = 0.30 ratio)
    if debt_to_equity is not None:
        if debt_to_equity < 20:
            score += 20; breakdown.append("+20: D/E < 20% (Near debt-free)")
        elif debt_to_equity < 50:
            score += 15; breakdown.append("+15: D/E < 50% (Conservative)")
        elif debt_to_equity < 100:
            score += 10; breakdown.append("+10: D/E < 100% (Manageable)")
        elif debt_to_equity < 200:
            score += 5;  breakdown.append("+5: D/E < 200% (Elevated)")
        else:
            score -= 5;  breakdown.append("-5: D/E > 200% (High Leverage)")

    # Revenue Growth (max +15)
    if revenue_growth is not None:
        if revenue_growth > 0.20:
            score += 15; breakdown.append("+15: Revenue Growth > 20%")
        elif revenue_growth > 0.10:
            score += 10; breakdown.append("+10: Revenue Growth > 10%")
        elif revenue_growth > 0.05:
            score += 5;  breakdown.append("+5: Revenue Growth > 5%")
        elif revenue_growth > 0:
            score += 2;  breakdown.append("+2: Revenue Growth Positive")
        else:
            score -= 5;  breakdown.append("-5: Revenue Declining")

    # Current Ratio (max +15)
    if current_ratio is not None:
        if current_ratio > 2.0:
            score += 15; breakdown.append("+15: Current Ratio > 2.0 (Strong Liquidity)")
        elif current_ratio > 1.5:
            score += 10; breakdown.append("+10: Current Ratio > 1.5 (Healthy)")
        elif current_ratio > 1.0:
            score += 5;  breakdown.append("+5: Current Ratio > 1.0 (Adequate)")
        else:
            score -= 5;  breakdown.append("-5: Current Ratio < 1.0 (Liquidity Risk)")

    # P/E Valuation (max +10)
    if trailing_pe is not None and trailing_pe > 0:
        if 12 <= trailing_pe <= 25:
            score += 10; breakdown.append("+10: P/E 12-25 (Fair Value)")
        elif 10 <= trailing_pe <= 35:
            score += 5;  breakdown.append("+5: P/E 10-35 (Reasonable)")
        elif trailing_pe > 50:
            score -= 5;  breakdown.append("-5: P/E > 50 (Premium/Overvalued)")

    score = max(0, min(score, 100))

    if score >= 70:   signal = "STRONG BUY"
    elif score >= 50: signal = "BULLISH / HOLD"
    elif score >= 30: signal = "NEUTRAL"
    elif score >= 10: signal = "BEARISH / CAUTION"
    else:             signal = "STRONG SELL"

    notes_html = "<strong>Fundamental Score Breakdown:</strong><br><ul class='algo-breakdown-list'>"
    for item in breakdown:
        notes_html += f"<li>{item}</li>"
    notes_html += "</ul><em>Note: Technical signals unavailable (universe stock — no price history file).</em>"

    return score, signal, notes_html


def _clean(v):
    """Sanitise NaN / Inf / string variants before SQLite insert."""
    if v is None:
        return None
    if isinstance(v, str):
        if v.lower() in ('nan', 'inf', '-inf', 'infinity', '-infinity'):
            return None
        try:
            fv = float(v)
            return None if (math.isnan(fv) or math.isinf(fv)) else fv
        except ValueError:
            return v
    try:
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
    except Exception:
        pass
    return v


def _get_pending_tickers(batch_size: int, freetrade_firewall: bool = False) -> list:
    # Excludes tickers whose score_method != 'UNIVERSE_FUNDAMENTALS' so HARDCODED portfolio/watchlist scores are never overwritten.
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        firewall_clause = "AND m.is_freetrade = 1" if freetrade_firewall else ""
        query = f"""
            SELECT m.ticker
            FROM market_universe m
            LEFT JOIN stock_signals s ON m.ticker = s.ticker
            WHERE m.is_index = 1
              {firewall_clause}
              AND (
                  s.ticker IS NULL
                  OR s.score_method = 'UNIVERSE_FUNDAMENTALS'
              )
            ORDER BY m.ticker
            LIMIT ?
        """
        cursor.execute(query, (batch_size,))
        return [r['ticker'] for r in cursor.fetchall()]
    finally:
        if conn:
            conn.close()


def _upsert_fundamentals(ticker: str, info: dict) -> None:
    quote_type = info.get('quoteType', 'EQUITY')
    company_name = info.get('shortName') or info.get('longName') or ticker
    sector = info.get('sector') or info.get('category') or 'Unknown'

    country_raw = info.get('country', 'Unknown')
    country = "UK" if country_raw == "United Kingdom" else ("US" if country_raw == "United States" else country_raw)

    currency     = info.get('currency', 'USD')
    current_price = info.get('regularMarketPrice') or info.get('previousClose')

    trailing_pe    = info.get('trailingPE')
    forward_pe     = info.get('forwardPE')
    peg_ratio      = info.get('pegRatio')
    price_to_book, price_to_sales = currency_safe_price_ratios(info, currency)
    free_cash_flow = info.get('freeCashflow')
    market_cap     = info.get('marketCap')
    profit_margin  = info.get('profitMargins')
    roe            = info.get('returnOnEquity')
    revenue_growth = info.get('revenueGrowth')
    earnings_growth = info.get('earningsGrowth')
    debt_to_equity = info.get('debtToEquity')
    current_ratio  = info.get('currentRatio')
    operating_cash_flow = info.get('operatingCashflow')
    dividend_yield = dividend_yield_fraction(info)

    ex_dividend_date = epoch_to_iso_date(info.get('exDividendDate'))

    target_price          = info.get('targetMeanPrice')
    analyst_rating        = (info.get('recommendationKey') or 'none').upper()
    short_interest        = info.get('shortPercentOfFloat')
    institutional_ownership = info.get('heldPercentInstitutions')
    beta                  = info.get('beta')
    fifty_two_week_low    = info.get('fiftyTwoWeekLow')
    fifty_two_week_high   = info.get('fiftyTwoWeekHigh')

    next_earnings_date = next_earnings_iso_date(info)

    peter_lynch_peg = calculate_peter_lynch_peg(
        forward_pe=forward_pe,
        trailing_pe=trailing_pe,
        earnings_growth=earnings_growth,
        dividend_yield=dividend_yield,
    )

    score, signal, notes_html = _compute_fundamental_score(info)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        # ON CONFLICT DO UPDATE (not INSERT OR REPLACE) — the latter resets every column NOT in
        # this statement to its schema default, silently wiping out top_holdings/sector_weightings/
        # holdings_updated_at and piotroski_f_score/altman_z_score/beneish_m_score/
        # forensic_last_updated, which are owned by other engines' own targeted UPDATEs.
        # current_price is only filled while NULL: the Quant Scan owns it from the daily bar, and the
        # `.info` quote can be days old.
        cursor.execute("""
            INSERT INTO stock_signals (
                ticker, last_updated, company_name, sector, country, currency, quote_type,
                current_price,
                fifty_two_week_low, fifty_two_week_high,
                trailing_pe, forward_pe, peg_ratio, peter_lynch_peg, price_to_book,
                price_to_sales, free_cash_flow, market_cap,
                profit_margin, roe, revenue_growth, debt_to_equity, current_ratio,
                operating_cash_flow, dividend_yield, ex_dividend_date,
                target_price, analyst_rating, next_earnings_date,
                short_interest, institutional_ownership, beta,
                composite_score, overall_signal, educational_notes,
                score_method
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?,
                ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?
            )
            ON CONFLICT(ticker) DO UPDATE SET
                last_updated=excluded.last_updated, company_name=excluded.company_name, sector=excluded.sector,
                country=excluded.country, currency=excluded.currency, quote_type=excluded.quote_type,
                current_price=COALESCE(stock_signals.current_price, excluded.current_price),
                fifty_two_week_low=excluded.fifty_two_week_low, fifty_two_week_high=excluded.fifty_two_week_high,
                trailing_pe=excluded.trailing_pe, forward_pe=excluded.forward_pe, peg_ratio=excluded.peg_ratio,
                peter_lynch_peg=excluded.peter_lynch_peg, price_to_book=excluded.price_to_book,
                price_to_sales=excluded.price_to_sales, free_cash_flow=excluded.free_cash_flow,
                market_cap=excluded.market_cap,
                profit_margin=excluded.profit_margin, roe=excluded.roe, revenue_growth=excluded.revenue_growth,
                debt_to_equity=excluded.debt_to_equity, current_ratio=excluded.current_ratio,
                operating_cash_flow=excluded.operating_cash_flow, dividend_yield=excluded.dividend_yield,
                ex_dividend_date=excluded.ex_dividend_date,
                target_price=excluded.target_price, analyst_rating=excluded.analyst_rating,
                next_earnings_date=excluded.next_earnings_date,
                short_interest=excluded.short_interest, institutional_ownership=excluded.institutional_ownership,
                beta=excluded.beta,
                composite_score=excluded.composite_score, overall_signal=excluded.overall_signal,
                educational_notes=excluded.educational_notes,
                score_method=excluded.score_method
        """, (
            ticker, timestamp, company_name, sector, country, currency, quote_type,
            _clean(current_price),
            _clean(fifty_two_week_low), _clean(fifty_two_week_high),
            _clean(trailing_pe), _clean(forward_pe), _clean(peg_ratio), _clean(peter_lynch_peg), _clean(price_to_book),
            _clean(price_to_sales), _clean(free_cash_flow), _clean(market_cap),
            _clean(profit_margin), _clean(roe), _clean(revenue_growth), _clean(debt_to_equity), _clean(current_ratio),
            _clean(operating_cash_flow), _clean(dividend_yield), ex_dividend_date,
            _clean(target_price), analyst_rating, next_earnings_date,
            _clean(short_interest), _clean(institutional_ownership), _clean(beta),
            int(score), signal, notes_html,
            'UNIVERSE_FUNDAMENTALS',
        ))
        conn.commit()
    finally:
        if conn:
            conn.close()


# yfinance's funds_data.sector_weightings keys, mapped to the same Title Case sector names
# already used everywhere else in the app (asset_profiles.sector uses this exact vocabulary),
# so an ETF's look-through sector blends into the same pie slices as its underlying stocks.
_SECTOR_KEY_LABELS = {
    "realestate": "Real Estate",
    "consumer_cyclical": "Consumer Cyclical",
    "basic_materials": "Basic Materials",
    "consumer_defensive": "Consumer Defensive",
    "technology": "Technology",
    "communication_services": "Communication Services",
    "financial_services": "Financial Services",
    "utilities": "Utilities",
    "industrials": "Industrials",
    "energy": "Energy",
    "healthcare": "Healthcare",
}


def _needs_holdings_refresh(top_holdings: Optional[str], holdings_updated_at: Optional[str]) -> bool:
    if not top_holdings or not holdings_updated_at:
        return True
    last = parse_stored_utc(holdings_updated_at)
    if last is None:
        return True
    return last <datetime.now(timezone.utc) - timedelta(days=_HOLDINGS_REFRESH_DAYS)


def sync_etf_holdings_cache(tickers: List[str]) -> None:
    """DB-caches ETF/mutual-fund top-holdings and sector weightings for crash_engine and xray_engine's fund look-through."""
    if not tickers:
        return

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(tickers))
        cursor.execute(
            f"SELECT ticker, top_holdings, holdings_updated_at FROM stock_signals "
            f"WHERE ticker IN ({placeholders}) AND quote_type IN ('ETF', 'MUTUALFUND')",
            tickers,
        )
        etf_rows = cursor.fetchall()
    finally:
        if conn:
            conn.close()

    for row in etf_rows:
        ticker = row['ticker']
        if not _needs_holdings_refresh(row['top_holdings'], row['holdings_updated_at']):
            continue
        try:
            df = yahoo_engine.get_fund_holdings(ticker)
            holdings = []
            if df is not None and not df.empty:
                holdings = [
                    {
                        "symbol": symbol,
                        "name": r.get('Name'),
                        "weight": _clean(r.get('Holding Percent')) or 0.0,
                    }
                    for symbol, r in df.head(10).iterrows()
                ]

            raw_weights = yahoo_engine.get_fund_sector_weightings(ticker)
            sector_weightings = []
            if raw_weights:
                sector_weightings = [
                    {"name": _SECTOR_KEY_LABELS.get(key, key), "weight": _clean(weight) or 0.0}
                    for key, weight in raw_weights.items()
                    if _clean(weight)
                ]

            if not holdings and not sector_weightings:
                continue

            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            conn = None
            try:
                conn = get_connection()
                conn.execute(
                    "UPDATE stock_signals SET top_holdings = ?, sector_weightings = ?, holdings_updated_at = ? "
                    "WHERE ticker = ?",
                    (json.dumps(holdings), json.dumps(sector_weightings), timestamp, ticker),
                )
                conn.commit()
            finally:
                if conn:
                    conn.close()
            logger.info("[%s] Fund top holdings and sector weightings cached.", ticker)
        except Exception as e:
            logger.error("[%s] Fund holdings/sector fetch failed: %s", ticker, e)

        # Polite rate limiting — yfinance is not a paid API
        time.sleep(0.4)


def run_universe_fundamentals_sync(batch_size: int = 50, freetrade_firewall: bool = False) -> None:
    # Refreshes fundamentals for up to batch_size universe stocks; skips tickers with HARDCODED technical scores.
    tickers = _get_pending_tickers(batch_size, freetrade_firewall=freetrade_firewall)
    if not tickers:
        log_notification("Info", "Universe Fundamentals Sync: all index stocks are already up to date.")
        logger.info("Universe Fundamentals Sync: nothing pending.")
        return

    total = len(tickers)
    log_notification("Info", f"Universe Fundamentals Sync started — processing {total} pending stocks (batch size: {batch_size}).")
    logger.info("Universe Fundamentals Sync: %s stocks to process this batch.", total)

    processed = errors = 0

    for i, ticker in enumerate(tickers):
        try:
            info = _fetch_info(ticker)
            if not info:
                logger.warning("[%s] No data returned, skipping.", ticker)
                errors += 1
                continue

            _upsert_fundamentals(ticker, info)
            processed += 1
            logger.info("[%s/%s] %s — written.", i + 1, total, ticker)

            if total >= 4 and processed % max(1, total // 4) == 0:
                pct = int((processed / total) * 100)
                log_notification("Info", f"Universe Fundamentals Sync: {pct}% ({processed}/{total} written).")

            # Polite rate limiting — yfinance is not a paid API
            time.sleep(0.4)

        except Exception as e:
            logger.error("[%s] Failed: %s", ticker, e)
            errors += 1

    log_notification(
        "Success",
        f"Universe Fundamentals Sync batch complete. "
        f"Written: {processed} | Errors: {errors}."
    )
    logger.info("Universe Fundamentals Sync batch done — written=%s, errors=%s.", processed, errors)
