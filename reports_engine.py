# reports_engine.py
import logging
import sqlite3
from datetime import datetime
from typing import List, Dict, Any
from database import get_connection

logger = logging.getLogger(__name__)

def get_sector_trends() -> List[Dict[str, Any]]:
    """
    Calculates aggregated momentum and trend health metrics grouped by market sector and exchange.
    Requires joining the latest quantitative signals with the market universe table.
    Strictly filters for EQUITIES to prevent Mutual Funds (NAV priced) from skewing RSI momentum averages.
    """
    logger.info("Generating Sector Trends Report...")
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Uses positional grouping (GROUP BY 1, 2) to avoid SQLite 'ambiguous column'
        # errors when grouping by our generated 'exchange' alias.
        query = """
        WITH latest AS (
            SELECT ticker, MAX(date) AS max_date
            FROM quant_signals
            GROUP BY ticker
        )
        SELECT
            CASE
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NMS' THEN 'NASDAQ'
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NYQ' THEN 'NYSE/AMEX'
                WHEN COALESCE(m.exchange, p.exchange) IS NOT NULL THEN UPPER(COALESCE(m.exchange, p.exchange))
                WHEN UPPER(q.ticker) LIKE '%.L' THEN 'LSE'
                ELSE 'US'
            END as exchange,
            COALESCE(p.sector, s.sector, m.sector, 'Unclassified') as sector,
            COUNT(q.ticker) as total_stocks,
            ROUND(AVG(q.rsi_14), 2) as avg_rsi,
            ROUND(SUM(CASE WHEN q.close_price > q.sma_50 THEN 1 ELSE 0 END) * 100.0 / COUNT(q.ticker), 2) as pct_above_50d,
            ROUND(SUM(CASE WHEN q.bullish_cross = 1 THEN 1 ELSE 0 END) * 100.0 /
                  NULLIF(SUM(CASE WHEN q.bullish_cross IS NOT NULL THEN 1 ELSE 0 END), 0), 2) as pct_bullish_cross
        FROM quant_signals q
        INNER JOIN latest l ON q.ticker = l.ticker AND q.date = l.max_date
        INNER JOIN market_universe m ON q.ticker = m.ticker
        LEFT JOIN asset_profiles p ON q.ticker = p.ticker
        LEFT JOIN stock_signals s ON q.ticker = s.ticker
        WHERE COALESCE(p.sector, s.sector, m.sector, 'Unclassified') != 'None'
          AND COALESCE(p.sector, s.sector, m.sector, 'Unclassified') != ''
          AND COALESCE(p.quote_type, s.quote_type, 'EQUITY') = 'EQUITY'
        GROUP BY 1, 2
        ORDER BY 1 ASC, 4 DESC
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to generate Sector Trends: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_mean_reversion_setups(max_rsi: float = 30.0, min_sma_distance: float = 0.0) -> List[Dict[str, Any]]:
    """
    Identifies stocks that are fundamentally in a long-term uptrend (Price > 200D SMA)
    but are experiencing a severe short-term sell-off (RSI < max_rsi).
    Strictly filters for EQUITIES to prevent illiquid funds from appearing.
    """
    logger.info(f"Generating Mean Reversion Report (Max RSI: {max_rsi})...")
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        query = """
        WITH latest AS (
            SELECT ticker, MAX(date) AS max_date
            FROM quant_signals
            GROUP BY ticker
        )
        SELECT
            q.ticker,
            COALESCE(p.company_name, m.company_name, q.ticker) as company_name,
            COALESCE(m.country, p.country, 'US') as country,
            COALESCE(p.sector, s.sector, m.sector, 'Unclassified') as sector,
            COALESCE(p.currency, s.currency, 'USD') as currency,
            q.close_price,
            ROUND(q.rsi_14, 2) as rsi_14,
            ROUND(q.sma_200, 2) as sma_200,
            ROUND(((q.close_price - q.sma_200) / q.sma_200) * 100.0, 2) as distance_from_200d_pct
        FROM quant_signals q
        INNER JOIN latest l ON q.ticker = l.ticker AND q.date = l.max_date
        INNER JOIN market_universe m ON q.ticker = m.ticker
        LEFT JOIN asset_profiles p ON q.ticker = p.ticker
        LEFT JOIN stock_signals s ON q.ticker = s.ticker
        WHERE q.rsi_14 <= ?
          AND q.close_price > q.sma_200
          AND COALESCE(p.quote_type, s.quote_type, 'EQUITY') = 'EQUITY'
        ORDER BY q.rsi_14 ASC
        LIMIT 500
        """
        cursor.execute(query, (max_rsi,))
        rows = cursor.fetchall()
        # Optionally filter out stocks that aren't far enough above their 200D
        return [dict(row) for row in rows if row['distance_from_200d_pct'] >= min_sma_distance]
    except Exception as e:
        logger.error(f"Failed to generate Mean Reversion Setups: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_leaders_laggards() -> List[Dict[str, Any]]:
    """
    Identifies the strongest momentum leaders in the market right now.
    Filters for stocks above their 50D SMA, sorted by highest RSI and MACD Histogram.
    Strictly filters for EQUITIES to prevent indexing ETFs from dominating the leaderboards.
    """
    logger.info("Generating Momentum Leaders Report...")
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        query = """
        WITH latest AS (
            SELECT ticker, MAX(date) AS max_date
            FROM quant_signals
            GROUP BY ticker
        )
        SELECT
            q.ticker,
            COALESCE(p.company_name, m.company_name, q.ticker) as company_name,
            COALESCE(m.country, p.country, 'US') as country,
            COALESCE(p.sector, s.sector, m.sector, 'Unclassified') as sector,
            CASE
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NMS' THEN 'NASDAQ'
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NYQ' THEN 'NYSE/AMEX'
                WHEN COALESCE(m.exchange, p.exchange) IS NOT NULL THEN UPPER(COALESCE(m.exchange, p.exchange))
                WHEN UPPER(q.ticker) LIKE '%.L' THEN 'LSE'
                ELSE 'US'
            END as exchange,
            COALESCE(p.currency, s.currency, 'USD') as currency,
            q.close_price,
            ROUND(q.rsi_14, 2) as rsi_14,
            ROUND(q.macd_hist, 3) as macd_hist,
            q.volume_surge
        FROM quant_signals q
        INNER JOIN latest l ON q.ticker = l.ticker AND q.date = l.max_date
        INNER JOIN market_universe m ON q.ticker = m.ticker
        LEFT JOIN asset_profiles p ON q.ticker = p.ticker
        LEFT JOIN stock_signals s ON q.ticker = s.ticker
        WHERE q.close_price > q.sma_50
          AND q.rsi_14 IS NOT NULL
          AND COALESCE(p.quote_type, s.quote_type, 'EQUITY') = 'EQUITY'
        ORDER BY q.rsi_14 DESC, q.macd_hist DESC
        LIMIT 500
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to generate Leaders & Laggards: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_dividend_harvest_setups(min_yield: float = 0.02, min_score: int = 50) -> List[Dict[str, Any]]:
    """
    Fetches high-yield dividend stocks, filtering out potential 'Yield Traps' 
    using a minimum quantitative score. Parses YF Unix timestamps dynamically.
    
    ARCHITECTURAL NOTE ON SQL JOINS: 
    Unlike the momentum-based reports which use an INNER JOIN to restrict results 
    strictly to the standard equity screener universe, this function deliberately 
    uses a LEFT JOIN on `market_universe`. This ensures that obscure high-yield 
    ETFs, Mutual Funds, or international OTC assets synced directly from the user's 
    Ghostfolio portfolio are not dropped from the income report.
    """
    logger.info(f"Generating Dividend Harvest Report (Min Yield: {min_yield}, Min Score: {min_score})...")
    conn = None
    try:
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = """
        WITH latest AS (
            SELECT ticker, MAX(date) AS max_date
            FROM quant_signals
            GROUP BY ticker
        )
        SELECT
            q.ticker,
            COALESCE(p.company_name, m.company_name, q.ticker) as company_name,
            COALESCE(p.country, m.country, 'US') as country,
            COALESCE(p.sector, s.sector, m.sector, 'Unclassified') as sector,
            CASE
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NMS' THEN 'NASDAQ'
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NYQ' THEN 'NYSE/AMEX'
                WHEN COALESCE(m.exchange, p.exchange) IS NOT NULL THEN UPPER(COALESCE(m.exchange, p.exchange))
                WHEN UPPER(q.ticker) LIKE '%.L' THEN 'LSE'
                ELSE 'US'
            END as exchange,
            COALESCE(p.currency, s.currency, 'USD') as currency,
            q.close_price,
            s.dividend_yield,
            s.ex_dividend_date,
            s.composite_score,
            q.ml_confidence_score
        FROM quant_signals q
        INNER JOIN latest l ON q.ticker = l.ticker AND q.date = l.max_date
        LEFT JOIN market_universe m ON q.ticker = m.ticker
        LEFT JOIN asset_profiles p ON q.ticker = p.ticker
        LEFT JOIN stock_signals s ON q.ticker = s.ticker
        WHERE s.dividend_yield >= ?
          AND s.composite_score >= ?
          AND s.ex_dividend_date IS NOT NULL
          AND s.ex_dividend_date != 'Unknown'
          AND s.ex_dividend_date != ''
        ORDER BY s.composite_score DESC
        LIMIT 500
        """

        cursor.execute(query, (min_yield, min_score))
        rows = cursor.fetchall()

        results = []
        for row in rows:
            row_dict = dict(row)
            ex_date_raw = row_dict['ex_dividend_date']
            parsed_date = None
            try:
                ts = float(ex_date_raw)
                # Ensure it is a valid, modern timestamp (post-2000)
                if ts > 946684800:
                    parsed_date = datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                # Already a date string from the DB — validate it is ISO-formatted
                if isinstance(ex_date_raw, str) and len(ex_date_raw) == 10:
                    parsed_date = ex_date_raw

            if parsed_date is None:
                # Drop rows whose date cannot be normalised to YYYY-MM-DD
                continue

            row_dict['ex_dividend_date'] = parsed_date
            results.append(row_dict)

        # Sort ascending so the soonest ex-div date is first (most actionable)
        return sorted(results, key=lambda x: x['ex_dividend_date'])

    except Exception as e:
        logger.error(f"Failed to fetch dividend harvest setups: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_quality_compounders() -> List[Dict[str, Any]]:
    """
    Identifies 'buy and hold' quality compounders.
    Filters for high capital efficiency, strong margins, low debt, 
    consistent growth, and reasonable valuations.
    """
    logger.info("Generating Quality Compounders Report...")
    conn = None
    try:
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = """
        WITH latest_price AS (
            SELECT q.ticker, q.close_price
            FROM quant_signals q
            INNER JOIN (SELECT ticker, MAX(date) AS max_date FROM quant_signals GROUP BY ticker) l
                ON q.ticker = l.ticker AND q.date = l.max_date
        )
        SELECT
            s.ticker,
            COALESCE(p.company_name, m.company_name, s.ticker) as company_name,
            COALESCE(p.sector, s.sector, m.sector, 'Unclassified') as sector,
            COALESCE(p.country, m.country, 'US') as country,
            CASE
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NMS' THEN 'NASDAQ'
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NYQ' THEN 'NYSE/AMEX'
                WHEN COALESCE(m.exchange, p.exchange) IS NOT NULL THEN UPPER(COALESCE(m.exchange, p.exchange))
                WHEN UPPER(s.ticker) LIKE '%.L' THEN 'LSE'
                ELSE 'US'
            END as exchange,
            COALESCE(p.currency, s.currency, 'USD') as currency,
            lp.close_price,
            ROUND(s.roe * 100.0, 2) as roe_pct,
            ROUND(s.profit_margin * 100.0, 2) as margin_pct,
            ROUND(s.debt_to_equity, 2) as debt_to_equity,
            ROUND(s.trailing_pe, 2) as trailing_pe,
            s.composite_score
        FROM stock_signals s
        LEFT JOIN market_universe m ON s.ticker = m.ticker
        LEFT JOIN asset_profiles p ON s.ticker = p.ticker
        LEFT JOIN latest_price lp ON s.ticker = lp.ticker
        WHERE s.quote_type = 'EQUITY'
          AND s.roe > 0.15
          AND s.debt_to_equity < 100
          AND s.profit_margin > 0.10
          AND s.revenue_growth > 0.05
          AND s.current_ratio > 1.5
          AND s.composite_score >= 60
          AND s.trailing_pe BETWEEN 10 AND 35
        ORDER BY s.composite_score DESC, s.roe DESC
        LIMIT 500
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    except Exception as e:
        logger.error(f"Failed to generate Quality Compounders: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_quality_on_sale() -> List[Dict[str, Any]]:
    """
    "Quality on Sale" — 52-Week Low Bargains.
    Surfaces high-quality businesses the market has thrown out with the bathwater.
    Distinct from Mean Reversion (RSI-based, short-term panic) — this is structural
    value: good fundamentals at multi-month price lows.

    Filter criteria:
      - close_price <= fifty_two_week_low * 1.15  (within 15% of 52-week low)
      - roe > 10%                                  (still a quality business)
      - debt_to_equity < 150                       (< 1.5x ratio; yfinance stores as %)
      - profit_margin > 5%
      - trailing_pe between 0 and 25               (profitable, not overvalued)
      - composite_score >= 50
      - quote_type = EQUITY
    """
    logger.info("Generating Quality on Sale Report...")
    conn = None
    try:
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = """
        WITH latest_price AS (
            SELECT q.ticker, q.close_price
            FROM quant_signals q
            INNER JOIN (SELECT ticker, MAX(date) AS max_date FROM quant_signals GROUP BY ticker) l
                ON q.ticker = l.ticker AND q.date = l.max_date
        )
        SELECT
            s.ticker,
            COALESCE(p.company_name, m.company_name, s.ticker) as company_name,
            COALESCE(p.sector, s.sector, m.sector, 'Unclassified') as sector,
            COALESCE(p.country, m.country, 'US') as country,
            CASE
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NMS' THEN 'NASDAQ'
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NYQ' THEN 'NYSE/AMEX'
                WHEN COALESCE(m.exchange, p.exchange) IS NOT NULL THEN UPPER(COALESCE(m.exchange, p.exchange))
                WHEN UPPER(s.ticker) LIKE '%.L' THEN 'LSE'
                ELSE 'US'
            END as exchange,
            COALESCE(p.currency, s.currency, 'USD') as currency,
            lp.close_price,
            ROUND(s.fifty_two_week_low, 4) as fifty_two_week_low,
            ROUND((lp.close_price / s.fifty_two_week_low - 1.0) * 100.0, 2) as pct_above_52w_low,
            ROUND(s.roe * 100.0, 2) as roe_pct,
            ROUND(s.debt_to_equity, 2) as debt_to_equity,
            ROUND(s.profit_margin * 100.0, 2) as margin_pct,
            ROUND(s.trailing_pe, 2) as trailing_pe,
            s.composite_score
        FROM stock_signals s
        LEFT JOIN market_universe m ON s.ticker = m.ticker
        LEFT JOIN asset_profiles p ON s.ticker = p.ticker
        INNER JOIN latest_price lp ON s.ticker = lp.ticker
        WHERE s.quote_type = 'EQUITY'
          AND s.fifty_two_week_low IS NOT NULL AND s.fifty_two_week_low > 0
          AND lp.close_price > 0
          AND lp.close_price <= s.fifty_two_week_low * 1.15
          AND s.roe > 0.10
          AND (s.debt_to_equity IS NULL OR s.debt_to_equity < 150)
          AND s.profit_margin > 0.05
          AND s.trailing_pe > 0 AND s.trailing_pe < 25
          AND s.composite_score >= 50
        ORDER BY s.composite_score DESC, pct_above_52w_low ASC
        LIMIT 500
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    except Exception as e:
        logger.error(f"Failed to generate Quality on Sale: {e}")
        return []
    finally:
        if conn:
            conn.close()


def get_garp_tenbaggers() -> List[Dict[str, Any]]:
    """
    GARP "Peter Lynch Tenbaggers" — Growth-At-Reasonable-Price screener.

    Filter criteria (all hard gates, applied server-side):
      - peter_lynch_peg in (0, 1.0]   (yield-adjusted PEG, Lynch's fair-value line)
      - revenue_growth > 15% YoY
      - roe > 10%
      - forward_pe between 10 and 40
      - market_cap > $500M             (excludes micro-cap pump-and-dumps)
      - quote_type = EQUITY
      - is_index = 1                   (universe scope — market_cap is universe-only data)

    ML confidence is NOT a server-side gate — returned as a nullable column for
    client-side filtering. NULL displays as "—" in the UI.

    Sort: peter_lynch_peg ASC (cheapest growth first), ml_confidence_score DESC tiebreak.
    """
    logger.info("Generating GARP Tenbaggers Report...")
    conn = None
    try:
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = """
        WITH latest_quant AS (
            SELECT q.ticker, q.close_price, q.ml_confidence_score
            FROM quant_signals q
            INNER JOIN (SELECT ticker, MAX(date) AS max_date FROM quant_signals GROUP BY ticker) l
                ON q.ticker = l.ticker AND q.date = l.max_date
        )
        SELECT
            s.ticker,
            COALESCE(p.company_name, m.company_name, s.ticker) as company_name,
            COALESCE(p.sector, s.sector, m.sector, 'Unclassified') as sector,
            COALESCE(p.country, m.country, 'US') as country,
            CASE
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NMS' THEN 'NASDAQ'
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NYQ' THEN 'NYSE/AMEX'
                WHEN COALESCE(m.exchange, p.exchange) IS NOT NULL THEN UPPER(COALESCE(m.exchange, p.exchange))
                WHEN UPPER(s.ticker) LIKE '%.L' THEN 'LSE'
                ELSE 'US'
            END as exchange,
            COALESCE(p.currency, s.currency, 'USD') as currency,
            lq.close_price,
            ROUND(s.peter_lynch_peg, 3) as peter_lynch_peg,
            ROUND(s.revenue_growth * 100.0, 2) as revenue_growth_pct,
            ROUND(s.roe * 100.0, 2) as roe_pct,
            ROUND(s.forward_pe, 2) as forward_pe,
            tm.market_cap,
            ROUND(lq.ml_confidence_score, 1) as ml_confidence_score
        FROM stock_signals s
        INNER JOIN market_universe m ON s.ticker = m.ticker
        LEFT JOIN asset_profiles p ON s.ticker = p.ticker
        LEFT JOIN latest_quant lq ON s.ticker = lq.ticker
        LEFT JOIN ticker_metadata tm ON s.ticker = tm.ticker
        WHERE m.is_index = 1
          AND COALESCE(p.quote_type, s.quote_type, 'EQUITY') = 'EQUITY'
          AND s.peter_lynch_peg > 0
          AND s.peter_lynch_peg <= 1.0
          AND s.revenue_growth > 0.15
          AND s.roe > 0.10
          AND s.forward_pe BETWEEN 10 AND 40
          AND COALESCE(tm.market_cap, 0) > 500000000
        ORDER BY s.peter_lynch_peg ASC, lq.ml_confidence_score DESC
        LIMIT 500
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    except Exception as e:
        logger.error(f"Failed to generate GARP Tenbaggers: {e}")
        return []
    finally:
        if conn:
            conn.close()