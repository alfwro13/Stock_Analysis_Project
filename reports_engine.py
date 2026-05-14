# reports_engine.py
import logging
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
    Calculates aggregated momentum and trend health metrics grouped by market sector.
    Requires joining the latest quantitative signals with the market universe table.
    """
    logger.info("Generating Sector Trends Report...")
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Uses COALESCE to fallback to 'Unclassified' since Nasdaq FTP provides no sectors.
        query = """
        SELECT 
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
        GROUP BY COALESCE(p.sector, s.sector, 'Unclassified')
        ORDER BY avg_rsi DESC
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
            COALESCE(p.sector, s.sector, 'Unclassified') as sector, 
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
        LIMIT 50
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to generate Leaders & Laggards: {e}")
        return []