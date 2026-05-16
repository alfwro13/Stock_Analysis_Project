# reports_engine.py
import logging
import sqlite3
from datetime import datetime
from typing import List, Dict, Any
from database import get_connection

# Configure module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - REPORTS_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_sector_trends() -> List[Dict[str, Any]]:
    """
    Calculates aggregated momentum and trend health metrics grouped by market sector and exchange.
    Requires joining the latest quantitative signals with the market universe table.
    """
    logger.info("Generating Sector Trends Report...")
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Uses COALESCE to fallback to 'Unclassified' since Nasdaq FTP provides no sectors,
        # and dynamically segments by the normalized Exchange origin.
        query = """
        SELECT 
            CASE 
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NMS' THEN 'NASDAQ'
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NYQ' THEN 'NYSE/AMEX'
                WHEN COALESCE(m.exchange, p.exchange) IS NOT NULL THEN UPPER(COALESCE(m.exchange, p.exchange))
                WHEN UPPER(q.ticker) LIKE '%.L' THEN 'LSE'
                ELSE 'US'
            END as exchange,
            COALESCE(p.sector, s.sector, 'Unclassified') as sector,
            COUNT(q.ticker) as total_stocks,
            ROUND(AVG(q.rsi_14), 2) as avg_rsi,
            ROUND(SUM(CASE WHEN q.close_price > q.sma_50 THEN 1 ELSE 0 END) * 100.0 / COUNT(q.ticker), 2) as pct_above_50d,
            ROUND(SUM(CASE WHEN q.bullish_cross = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(q.ticker), 2) as pct_bullish_cross
        FROM quant_signals q
        INNER JOIN market_universe m ON q.ticker = m.ticker
        LEFT JOIN asset_profiles p ON q.ticker = p.ticker
        LEFT JOIN stock_signals s ON q.ticker = s.ticker
        WHERE q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = q.ticker)
          AND COALESCE(p.sector, s.sector, 'Unclassified') != 'None' 
          AND COALESCE(p.sector, s.sector, 'Unclassified') != ''
        GROUP BY exchange, COALESCE(p.sector, s.sector, 'Unclassified')
        ORDER BY exchange ASC, avg_rsi DESC
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to generate Sector Trends: {e}")
        return []

def get_mean_reversion_setups(max_rsi: float = 30.0, min_sma_distance: float = 0.0) -> List[Dict[str, Any]]:
    """
    Identifies stocks that are fundamentally in a long-term uptrend (Price > 200D SMA)
    but are experiencing a severe short-term sell-off (RSI < max_rsi).
    """
    logger.info(f"Generating Mean Reversion Report (Max RSI: {max_rsi})...")
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        query = """
        SELECT 
            q.ticker, 
            COALESCE(p.company_name, m.company_name, q.ticker) as company_name, 
            COALESCE(m.country, p.country, 'US') as country,
            COALESCE(p.sector, s.sector, 'Unclassified') as sector, 
            q.close_price, 
            ROUND(q.rsi_14, 2) as rsi_14, 
            ROUND(q.sma_200, 2) as sma_200,
            ROUND(((q.close_price - q.sma_200) / q.sma_200) * 100.0, 2) as distance_from_200d_pct
        FROM quant_signals q
        INNER JOIN market_universe m ON q.ticker = m.ticker
        LEFT JOIN asset_profiles p ON q.ticker = p.ticker
        LEFT JOIN stock_signals s ON q.ticker = s.ticker
        WHERE q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = q.ticker)
          AND q.rsi_14 <= ? 
          AND q.close_price > q.sma_200
        ORDER BY q.rsi_14 ASC
        """
        cursor.execute(query, (max_rsi,))
        rows = cursor.fetchall()
        conn.close()
        
        # Optionally filter out stocks that aren't far enough above their 200D
        results = [dict(row) for row in rows if row['distance_from_200d_pct'] >= min_sma_distance]
        return results
    except Exception as e:
        logger.error(f"Failed to generate Mean Reversion Setups: {e}")
        return []

def get_leaders_laggards() -> List[Dict[str, Any]]:
    """
    Identifies the strongest momentum leaders in the market right now.
    Filters for stocks above their 50D SMA, sorted by highest RSI and MACD Histogram.
    """
    logger.info("Generating Momentum Leaders Report...")
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        query = """
        SELECT 
            q.ticker, 
            COALESCE(p.company_name, m.company_name, q.ticker) as company_name, 
            COALESCE(m.country, p.country, 'US') as country,
            COALESCE(p.sector, s.sector, 'Unclassified') as sector, 
            CASE 
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NMS' THEN 'NASDAQ'
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NYQ' THEN 'NYSE/AMEX'
                WHEN COALESCE(m.exchange, p.exchange) IS NOT NULL THEN UPPER(COALESCE(m.exchange, p.exchange))
                WHEN UPPER(q.ticker) LIKE '%.L' THEN 'LSE'
                ELSE 'US'
            END as exchange,
            q.close_price, 
            ROUND(q.rsi_14, 2) as rsi_14, 
            ROUND(q.macd_hist, 3) as macd_hist,
            q.volume_surge
        FROM quant_signals q
        INNER JOIN market_universe m ON q.ticker = m.ticker
        LEFT JOIN asset_profiles p ON q.ticker = p.ticker
        LEFT JOIN stock_signals s ON q.ticker = s.ticker
        WHERE q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = q.ticker)
          AND q.close_price > q.sma_50
          AND q.rsi_14 IS NOT NULL
        ORDER BY q.rsi_14 DESC, q.macd_hist DESC
        LIMIT 500
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to generate Leaders & Laggards: {e}")
        return []

def get_dividend_harvest_setups(min_yield: float = 0.02, min_score: int = 50) -> List[Dict[str, Any]]:
    """
    Fetches high-yield dividend stocks, filtering out potential 'Yield Traps' 
    using a minimum quantitative score. Parses YF Unix timestamps dynamically.
    """
    logger.info(f"Generating Dividend Harvest Report (Min Yield: {min_yield}, Min Score: {min_score})...")
    try:
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = """
        SELECT 
            q.ticker, 
            COALESCE(p.company_name, m.company_name, q.ticker) as company_name, 
            COALESCE(p.country, m.country, 'US') as country,
            COALESCE(p.sector, m.sector, 'Unclassified') as sector, 
            CASE 
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NMS' THEN 'NASDAQ'
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NYQ' THEN 'NYSE/AMEX'
                WHEN COALESCE(m.exchange, p.exchange) IS NOT NULL THEN UPPER(COALESCE(m.exchange, p.exchange))
                WHEN UPPER(q.ticker) LIKE '%.L' THEN 'LSE'
                ELSE 'US'
            END as exchange,
            q.close_price, 
            s.dividend_yield, 
            s.ex_dividend_date, 
            s.composite_score, 
            q.ml_confidence_score
        FROM quant_signals q
        LEFT JOIN market_universe m ON q.ticker = m.ticker
        LEFT JOIN asset_profiles p ON q.ticker = p.ticker
        LEFT JOIN stock_signals s ON q.ticker = s.ticker
        WHERE q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = q.ticker)
          AND s.dividend_yield >= ?
          AND s.composite_score >= ?
          AND s.ex_dividend_date IS NOT NULL 
          AND s.ex_dividend_date != 'Unknown'
          AND s.ex_dividend_date != ''
        """
        
        cursor.execute(query, (min_yield, min_score))
        rows = cursor.fetchall()
        conn.close()
        
        results = []
        for row in rows:
            row_dict = dict(row)
            
            ex_date_raw = row_dict['ex_dividend_date']
            try:
                ts = float(ex_date_raw)
                # Ensure it is a valid, modern timestamp
                if ts > 946684800: 
                    dt = datetime.fromtimestamp(ts)
                    row_dict['ex_dividend_date'] = dt.strftime('%Y-%m-%d')
            except ValueError:
                # If YF changes their API and returns a string, ignore the float cast
                pass
                
            results.append(row_dict)
            
        # Sort entirely in Python to guarantee descending chronological order
        results = sorted(results, key=lambda x: str(x['ex_dividend_date']), reverse=True)
        
        return results
        
    except Exception as e:
        logger.error(f"Failed to fetch dividend harvest setups: {e}")
        return []