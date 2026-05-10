# database.py
import sqlite3
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
    Defines the complete structure required for Technicals, Fundamentals, and Sentiment.
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    # Base table creation (Executes only if the table does not exist)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_signals (
            ticker TEXT PRIMARY KEY,
            last_updated TIMESTAMP,
            company_name TEXT,
            sector TEXT,
            currency TEXT,
            
            -- Core Technicals
            current_price REAL,
            ma_5_day REAL,
            ma_10_day REAL,
            ma_21_day REAL,
            trend_50d TEXT,
            trend_200d TEXT,
            rsi_14 REAL,
            atr_stop_loss REAL,
            
            -- Fundamental Valuation
            trailing_pe REAL,
            forward_pe REAL,
            peg_ratio REAL,
            peter_lynch_peg REAL,
            price_to_book REAL,
            
            -- Profitability & Health
            profit_margin REAL,
            roe REAL,
            revenue_growth REAL,
            debt_to_equity REAL,
            current_ratio REAL,
            operating_cash_flow REAL,
            
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
            educational_notes TEXT
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
    If you add new metrics in the future, simply add them to the master list here,
    and this function will gracefully upgrade your Debian database without data loss.
    """
    # Ask SQLite for the current columns in the database
    cursor.execute("PRAGMA table_info(stock_signals)")
    existing_columns = [info['name'] for info in cursor.fetchall()]

    # Master list of all required columns and their SQLite data types
    required_columns = {
        'company_name': 'TEXT', 'sector': 'TEXT', 'currency': 'TEXT',
        'trend_50d': 'TEXT', 'trend_200d': 'TEXT',
        'trailing_pe': 'REAL', 'forward_pe': 'REAL', 'peg_ratio': 'REAL',
        'peter_lynch_peg': 'REAL', 'price_to_book': 'REAL',
        'profit_margin': 'REAL', 'roe': 'REAL', 'revenue_growth': 'REAL',
        'debt_to_equity': 'REAL', 'current_ratio': 'REAL', 'operating_cash_flow': 'REAL',
        'dividend_yield': 'REAL', 'ex_dividend_date': 'TEXT', 'target_price': 'REAL',
        'analyst_rating': 'TEXT', 'next_earnings_date': 'TEXT',
        'short_interest': 'REAL', 'institutional_ownership': 'REAL', 'beta': 'REAL'
    }

    # Iterate and inject any missing columns
    for col_name, data_type in required_columns.items():
        if col_name not in existing_columns:
            print(f"[MIGRATION] Adding missing column: '{col_name}'...")
            cursor.execute(f"ALTER TABLE stock_signals ADD COLUMN {col_name} {data_type}")
    
    conn.commit()

if __name__ == "__main__":
    print("Initializing Database Engine...")
    init_db()