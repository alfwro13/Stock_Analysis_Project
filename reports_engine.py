import logging
from datetime import datetime, timezone
from typing import List, Dict, Any
from database import get_connection

logger = logging.getLogger(__name__)

def get_sector_trends() -> List[Dict[str, Any]]:
    """Aggregates RSI/SMA momentum by sector and exchange; EQUITY filter prevents NAV-priced funds from skewing averages."""
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
        logger.error("Failed to generate Sector Trends: %s", e)
        return []
    finally:
        if conn:
            conn.close()

def get_mean_reversion_setups(max_rsi: float = 30.0, min_sma_distance: float = 0.0) -> List[Dict[str, Any]]:
    """Stocks above 200D SMA with RSI < max_rsi; EQUITY filter prevents illiquid funds appearing."""
    logger.info("Generating Mean Reversion Report (Max RSI: %s)...", max_rsi)
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
        return [dict(row) for row in rows if row['distance_from_200d_pct'] >= min_sma_distance]
    except Exception as e:
        logger.error("Failed to generate Mean Reversion Setups: %s", e)
        return []
    finally:
        if conn:
            conn.close()

def get_leaders_laggards() -> List[Dict[str, Any]]:
    """Momentum leaders above 50D SMA sorted by RSI/MACD; EQUITY filter prevents ETFs dominating the leaderboard."""
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
        logger.error("Failed to generate Leaders & Laggards: %s", e)
        return []
    finally:
        if conn:
            conn.close()

def get_dividend_harvest_setups(min_yield: float = 0.02, min_score: int = 50) -> List[Dict[str, Any]]:
    """High-yield dividend stocks filtered for yield traps; LEFT JOIN on market_universe keeps portfolio-only OTC/ETF holdings."""
    logger.info("Generating Dividend Harvest Report (Min Yield: %s, Min Score: %s)...", min_yield, min_score)
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
                    parsed_date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')
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
        logger.error("Failed to fetch dividend harvest setups: %s", e)
        return []
    finally:
        if conn:
            conn.close()

def get_quality_compounders() -> List[Dict[str, Any]]:
    """High-ROCE, low-debt quality compounders: ROE>15%, margin>10%, D/E<100, growth>5%, PE 10-35, score>=60."""
    logger.info("Generating Quality Compounders Report...")
    conn = None
    try:
        conn = get_connection()
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
        logger.error("Failed to generate Quality Compounders: %s", e)
        return []
    finally:
        if conn:
            conn.close()

def get_quality_on_sale() -> List[Dict[str, Any]]:
    """Quality-on-sale: within 15% of 52W low + ROE>10%, margin>5%, PE<25, score>=50. Distinct from mean-reversion (RSI-based) — this is structural value."""
    logger.info("Generating Quality on Sale Report...")
    conn = None
    try:
        conn = get_connection()
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
        logger.error("Failed to generate Quality on Sale: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def get_garp_tenbaggers() -> List[Dict[str, Any]]:
    """GARP/Peter Lynch screener: PEG<=1.0, growth>15%, ROE>10%, fwd_PE 10-40, mktcap>$500M; ML confidence returned nullable for client-side filtering."""
    logger.info("Generating GARP Tenbaggers Report...")
    conn = None
    try:
        conn = get_connection()
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
        logger.error("Failed to generate GARP Tenbaggers: %s", e)
        return []
    finally:
        if conn:
            conn.close()
