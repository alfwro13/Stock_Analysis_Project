# database.py
import sqlite3
import logging
from typing import List, Optional
from config import DB_PATH

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - DATABASE_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_connection() -> sqlite3.Connection:
    """
    Creates and returns a connection to the local SQLite database.
    Using sqlite3.Row allows us to access columns by name (e.g., row['ticker']).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row 
    return conn

def init_db() -> None:
    """
    Initializes the master database schema for the Quantamental dashboard.
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Base table creation for quantitative analysis data
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_signals (
                ticker TEXT PRIMARY KEY,
                last_updated TIMESTAMP,
                company_name TEXT,
                sector TEXT,
                currency TEXT,
                country TEXT,
                quote_type TEXT,
                
                -- Core Technicals
                current_price REAL,
                ma_5_day REAL,
                ma_10_day REAL,
                ma_21_day REAL,
                trend_50d TEXT,
                trend_200d TEXT,
                rsi_14 REAL,
                atr_stop_loss REAL,
                
                -- Price Action
                fifty_two_week_low REAL,
                fifty_two_week_high REAL,
                
                -- Fundamental Valuation (Equities)
                trailing_pe REAL,
                forward_pe REAL,
                peg_ratio REAL,
                peter_lynch_peg REAL,
                price_to_book REAL,
                
                -- Profitability & Health (Equities)
                profit_margin REAL,
                roe REAL,
                revenue_growth REAL,
                debt_to_equity REAL,
                current_ratio REAL,
                operating_cash_flow REAL,
                
                -- Fundamental Valuation (Funds & ETFs)
                ytd_return REAL,
                total_assets REAL,
                nav_price REAL,
                expense_ratio REAL,
                top_holdings TEXT,
                sector_weightings TEXT,
                
                -- Sentiment & Dividends
                dividend_yield REAL,
                ex_dividend_date TEXT,
                target_price REAL,
                analyst_rating TEXT,
                next_earnings_date TEXT,
                short_interest REAL,
                institutional_ownership REAL,
                beta REAL,
                
                -- System Outputs
                composite_score INTEGER,
                overall_signal TEXT,
                educational_notes TEXT,
                setup_tags TEXT
            )
        ''')
        
        # New table for the Notification Center
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_type TEXT,
                message_text TEXT,
                is_read BOOLEAN DEFAULT 0
            )
        ''')
        
        # New table for the Live Market Pulse Database Cache
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_pulse_cache (
                ticker TEXT PRIMARY KEY,
                name TEXT,
                price TEXT,
                change_pts TEXT,
                change_pct TEXT,
                is_positive BOOLEAN,
                last_updated REAL
            )
        ''')

        # Quantitative Signals Tracking (Enricher Engine) - Includes ML/Risk fields
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS quant_signals (
                ticker TEXT,
                date TEXT,
                close_price REAL,
                volume INTEGER,
                rsi_14 REAL,
                macd REAL,
                macd_signal REAL,
                macd_hist REAL,
                sma_50 REAL,
                sma_200 REAL,
                volume_surge BOOLEAN,
                bullish_cross BOOLEAN,
                ml_confidence_score REAL,
                sentiment_score REAL,
                var_95 REAL,
                cvar_95 REAL,
                PRIMARY KEY (ticker, date)
            )
        ''')

        # New table for Quant Scan State / Resumability Tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS quant_scan_states (
                scan_date TEXT PRIMARY KEY,
                last_processed_ticker TEXT,
                status TEXT
            )
        ''')

        # New table for Quantitative Earnings Options Volatility
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS earnings_volatility (
                ticker TEXT PRIMARY KEY,
                next_earnings_date TEXT,
                implied_move_pct REAL,
                historical_avg_move_pct REAL,
                edge_score REAL,
                options_volume INTEGER,
                last_updated TEXT
            )
        ''')

        # Expanded Market Universe (4,000+ Tickers) supporting multi-national assets
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_universe (
                ticker TEXT PRIMARY KEY,
                company_name TEXT,
                sector TEXT,
                industry TEXT,
                country TEXT,
                last_updated TEXT
            )
        ''')
        
        # --- CENTRALIZED STATIC ASSET PROFILES (3NF NORMALIZATION) ---
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS asset_profiles (
                ticker TEXT PRIMARY KEY,
                company_name TEXT,
                sector TEXT,
                industry TEXT,
                country TEXT,
                exchange TEXT,
                currency TEXT,
                quote_type TEXT,
                business_summary TEXT,
                last_verified_date TIMESTAMP
            )
        ''')

        # --- PHASE 1: MARKET REGIMES (MACRO TURBULENCE Tracking) ---
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_regimes (
                date TEXT PRIMARY KEY,
                regime_label TEXT,
                turbulence_index REAL
            )
        ''')
        
        conn.commit()
        
        # Run the dynamic migration script to inject any missing columns safely
        migrate_db(conn, cursor)
        
        logger.info("Database connection verified and schema is fully up-to-date.")
    
    except Exception as e:
        logger.error(f"Failed to initialize database schema: {e}")
    finally:
        conn.close()

def migrate_db(conn: sqlite3.Connection, cursor: sqlite3.Cursor) -> None:
    """
    Dynamically checks the existing tables against a master list of required columns.
    Gracefully executes ALTER TABLE to apply schema updates without dropping data.
    Wraps individual ALTER statements in try/except blocks to ensure atomic migrations.
    """
    # 1. Migrate stock_signals
    cursor.execute("PRAGMA table_info(stock_signals)")
    existing_stock_columns = [info['name'] for info in cursor.fetchall()]

    required_stock_columns = {
        'company_name': 'TEXT', 'sector': 'TEXT', 'currency': 'TEXT', 'country': 'TEXT', 'quote_type': 'TEXT',
        'trend_50d': 'TEXT', 'trend_200d': 'TEXT',
        'fifty_two_week_low': 'REAL', 'fifty_two_week_high': 'REAL',
        'trailing_pe': 'REAL', 'forward_pe': 'REAL', 'peg_ratio': 'REAL',
        'peter_lynch_peg': 'REAL', 'price_to_book': 'REAL',
        'profit_margin': 'REAL', 'roe': 'REAL', 'revenue_growth': 'REAL',
        'debt_to_equity': 'REAL', 'current_ratio': 'REAL', 'operating_cash_flow': 'REAL',
        'ytd_return': 'REAL', 'total_assets': 'REAL', 'nav_price': 'REAL', 'expense_ratio': 'REAL',
        'top_holdings': 'TEXT', 'sector_weightings': 'TEXT',
        'dividend_yield': 'REAL', 'ex_dividend_date': 'TEXT', 'target_price': 'REAL',
        'analyst_rating': 'TEXT', 'next_earnings_date': 'TEXT',
        'short_interest': 'REAL', 'institutional_ownership': 'REAL', 'beta': 'REAL',
        'setup_tags': 'TEXT'
    }

    for col_name, data_type in required_stock_columns.items():
        if col_name not in existing_stock_columns:
            try:
                logger.info(f"[MIGRATION] Adding missing column: '{col_name}' to stock_signals...")
                cursor.execute(f"ALTER TABLE stock_signals ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error(f"[MIGRATION ERROR] Failed to add '{col_name}' to stock_signals: {e}")
                continue
            
    # 2. Migrate quant_signals (Adding ML and Risk Factor Columns)
    cursor.execute("PRAGMA table_info(quant_signals)")
    existing_quant_columns = [info['name'] for info in cursor.fetchall()]
    
    required_quant_columns = {
        'ml_confidence_score': 'REAL',
        'sentiment_score': 'REAL',
        'var_95': 'REAL',
        'cvar_95': 'REAL'
    }

    for col_name, data_type in required_quant_columns.items():
        if col_name not in existing_quant_columns:
            try:
                logger.info(f"[MIGRATION] Adding missing column: '{col_name}' to quant_signals...")
                cursor.execute(f"ALTER TABLE quant_signals ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error(f"[MIGRATION ERROR] Failed to add '{col_name}' to quant_signals: {e}")
                continue
                
    # 3. Migrate market_universe (Adding Country origin column)
    cursor.execute("PRAGMA table_info(market_universe)")
    existing_universe_columns = [info['name'] for info in cursor.fetchall()]
    
    required_universe_columns = {
        'country': 'TEXT'
    }

    for col_name, data_type in required_universe_columns.items():
        if col_name not in existing_universe_columns:
            try:
                logger.info(f"[MIGRATION] Adding missing column: '{col_name}' to market_universe...")
                cursor.execute(f"ALTER TABLE market_universe ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error(f"[MIGRATION ERROR] Failed to add '{col_name}' to market_universe: {e}")
                continue

    try:
        conn.commit()
    except Exception as e:
        logger.error(f"[MIGRATION ERROR] Failed to commit migration changes: {e}")
        conn.rollback()

def get_universe_tickers() -> List[str]:
    """
    Connects to the SQLite DB and extracts all tracked universe tickers.
    If the universe is empty, returns an empty list.
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ticker FROM market_universe")
        tickers = [row['ticker'] for row in cursor.fetchall()]
        conn.close()
        return tickers
    except Exception as e:
        logger.error(f"Failed to fetch universe tickers: {e}")
        return []

def upsert_quant_signal(
    ticker: str,
    date: str,
    close_price: float,
    volume: int,
    rsi_14: Optional[float] = None,
    macd: Optional[float] = None,
    macd_signal: Optional[float] = None,
    macd_hist: Optional[float] = None,
    sma_50: Optional[float] = None,
    sma_200: Optional[float] = None,
    volume_surge: Optional[bool] = None,
    bullish_cross: Optional[bool] = None,
    ml_confidence_score: Optional[float] = None,
    sentiment_score: Optional[float] = None,
    var_95: Optional[float] = None,
    cvar_95: Optional[float] = None
) -> bool:
    """
    Centralized, highly-typed function to insert or update daily quantitative signals.
    Gracefully accepts newly integrated Machine Learning and Risk (VaR) parameters.
    Returns True on success, False on failure.
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        query = '''
            INSERT OR REPLACE INTO quant_signals (
                ticker, date, close_price, volume, rsi_14, macd, macd_signal, macd_hist, 
                sma_50, sma_200, volume_surge, bullish_cross,
                ml_confidence_score, sentiment_score, var_95, cvar_95
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        '''
        
        cursor.execute(query, (
            ticker, date, close_price, volume, rsi_14, macd, macd_signal, macd_hist,
            sma_50, sma_200, volume_surge, bullish_cross,
            ml_confidence_score, sentiment_score, var_95, cvar_95
        ))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Database insertion failed for quant_signal ({ticker} on {date}): {e}")
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()