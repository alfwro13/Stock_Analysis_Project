# database.py
import sqlite3
from typing import List
from config import DB_PATH

def get_connection():
    """
    Creates and returns a connection to the local SQLite database.
    Using sqlite3.Row allows us to access columns by name (e.g., row['ticker']).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row 
    return conn

def init_db():
    """
    Initializes the master database schema for the Quantamental dashboard.
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    # Base table creation for quantitative analysis data
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_signals (
            ticker TEXT PRIMARY KEY,
            last_updated TIMESTAMP,
            company_name TEXT,
            sector TEXT,
            currency TEXT,
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

    # New table for Quantitative Signals Tracking (Enricher Engine)
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
    
    # New table for the Expanded Market Universe (4,000+ Tickers)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS market_universe (
            ticker TEXT PRIMARY KEY,
            company_name TEXT,
            sector TEXT,
            industry TEXT,
            last_updated TEXT
        )
    ''')
    
    conn.commit()
    
    # Run the dynamic migration script to inject any missing columns safely
    migrate_db(conn, cursor)
    
    conn.close()
    print("Database connection verified and schema is fully up-to-date.")

def migrate_db(conn, cursor):
    """
    Dynamically checks the existing table against a master list of required columns.
    """
    cursor.execute("PRAGMA table_info(stock_signals)")
    existing_columns = [info['name'] for info in cursor.fetchall()]

    required_columns = {
        'company_name': 'TEXT', 'sector': 'TEXT', 'currency': 'TEXT', 'quote_type': 'TEXT',
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

    for col_name, data_type in required_columns.items():
        if col_name not in existing_columns:
            print(f"[MIGRATION] Adding missing column: '{col_name}'...")
            cursor.execute(f"ALTER TABLE stock_signals ADD COLUMN {col_name} {data_type}")
    
    conn.commit()

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
        print(f"[ERROR] Failed to fetch universe tickers: {e}")
        return []

if __name__ == "__main__":
    init_db()