import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from config import FUNDAMENTALS_DIR
from yahoo_engine import yahoo_engine
from database import get_connection, log_notification
from fundamentals_helpers import calculate_peter_lynch_peg

logger = logging.getLogger(__name__)

# Holdings composition changes slowly (quarterly rebalances at most) — a 30-day cache window
# avoids re-fetching on every quant_analysis_job run while still catching drift eventually.
_HOLDINGS_REFRESH_DAYS = 30


def _fetch_info(ticker: str) -> dict:
    """Load from local JSON cache first; fall back to live yfinance fetch and cache result."""
    cache_path = FUNDAMENTALS_DIR / f"{ticker}.json"
    if cache_path.exists():
        try:
            with open(cache_path, 'r') as f:
                return json.load(f)
        except Exception:
            logger.warning("Corrupted fundamentals cache for %s, re-fetching", ticker, exc_info=True)
    try:
        info = yahoo_engine.get_ticker_info(ticker) or {}
        if info.get('quoteType'):
            FUNDAMENTALS_DIR.mkdir(parents=True, exist_ok=True)
            with open(cache_path, 'w') as f:
                json.dump(info, f)
        return info
    except Exception as e:
        logger.warning("[%s] yfinance fetch failed: %s", ticker, e)
        return {}


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
    price_to_book  = info.get('priceToBook')
    price_to_sales = info.get('priceToSalesTrailing12Months')
    free_cash_flow = info.get('freeCashflow')
    profit_margin  = info.get('profitMargins')
    roe            = info.get('returnOnEquity')
    revenue_growth = info.get('revenueGrowth')
    earnings_growth = info.get('earningsGrowth')
    debt_to_equity = info.get('debtToEquity')
    current_ratio  = info.get('currentRatio')
    operating_cash_flow = info.get('operatingCashflow')
    dividend_yield = info.get('dividendYield')

    # Pence misquote correction (same guard used in quant_signals.py)
    if dividend_yield is not None and currency in ('GBp', 'GBP') and dividend_yield > 0.15:
        dividend_yield /= 100.0

    ex_div_ts = info.get('exDividendDate')
    ex_dividend_date = None
    if ex_div_ts:
        try:
            ex_dividend_date = datetime.fromtimestamp(float(ex_div_ts), tz=timezone.utc).strftime('%Y-%m-%d')
        except Exception:
            ex_dividend_date = str(ex_div_ts) if len(str(ex_div_ts)) == 10 else None

    target_price          = info.get('targetMeanPrice')
    analyst_rating        = (info.get('recommendationKey') or 'none').upper()
    short_interest        = info.get('shortPercentOfFloat')
    institutional_ownership = info.get('heldPercentInstitutions')
    beta                  = info.get('beta')
    fifty_two_week_low    = info.get('fiftyTwoWeekLow')
    fifty_two_week_high   = info.get('fiftyTwoWeekHigh')

    earnings_ts = info.get('earningsTimestamp')
    next_earnings_date = datetime.fromtimestamp(earnings_ts, tz=timezone.utc).strftime('%Y-%m-%d') if earnings_ts else 'Unknown'

    # Compute PEG after pence misquote correction so dividend_yield is already in decimal form.
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
        cursor.execute("""
            INSERT OR REPLACE INTO stock_signals (
                ticker, last_updated, company_name, sector, country, currency, quote_type,
                current_price,
                fifty_two_week_low, fifty_two_week_high,
                trailing_pe, forward_pe, peg_ratio, peter_lynch_peg, price_to_book,
                price_to_sales, free_cash_flow,
                profit_margin, roe, revenue_growth, debt_to_equity, current_ratio,
                operating_cash_flow, dividend_yield, ex_dividend_date,
                target_price, analyst_rating, next_earnings_date,
                short_interest, institutional_ownership, beta,
                composite_score, overall_signal, educational_notes, setup_tags,
                score_method
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?,
                ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?
            )
        """, (
            ticker, timestamp, company_name, sector, country, currency, quote_type,
            _clean(current_price),
            _clean(fifty_two_week_low), _clean(fifty_two_week_high),
            _clean(trailing_pe), _clean(forward_pe), _clean(peg_ratio), _clean(peter_lynch_peg), _clean(price_to_book),
            _clean(price_to_sales), _clean(free_cash_flow),
            _clean(profit_margin), _clean(roe), _clean(revenue_growth), _clean(debt_to_equity), _clean(current_ratio),
            _clean(operating_cash_flow), _clean(dividend_yield), ex_dividend_date,
            _clean(target_price), analyst_rating, next_earnings_date,
            _clean(short_interest), _clean(institutional_ownership), _clean(beta),
            int(score), signal, notes_html, '[]',
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
    try:
        last = datetime.strptime(holdings_updated_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return last < datetime.now(timezone.utc) - timedelta(days=_HOLDINGS_REFRESH_DAYS)


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
