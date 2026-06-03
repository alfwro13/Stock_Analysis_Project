# database.py
import sqlite3
import logging
from typing import List, Optional

from config import DB_PATH, load_config

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    """
    Creates and returns a connection to the local SQLite database.
    Using sqlite3.Row allows us to access columns by name (e.g., row['ticker']).
    """
    # Added timeout=20.0 to gracefully handle background thread write collisions
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    
    # Enable Write-Ahead Logging (WAL) to allow concurrent reads and writes
    conn.execute('PRAGMA journal_mode=WAL;')
    # Optimize synchronization for WAL mode for significant write performance gain
    conn.execute('PRAGMA synchronous=NORMAL;')
    
    conn.row_factory = sqlite3.Row
    return conn


def log_notification(message_type: str, message_text: str) -> None:
    """
    Centralized helper function to log scan progress to the system notification center.
    Guards against NameError if get_connection() raises an exception.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)",
            (message_type, message_text)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to log notification: {e}")
    finally:
        if conn:
            conn.close()


def init_db() -> None:
    """
    Initializes the master database schema for the Quantamental dashboard.
    Includes the Event-Driven Macroeconomic tracking tables.
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
                ma_50_day REAL,
                ma_200_day REAL,
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
                yield_correlation REAL,
                
                -- System Outputs
                composite_score INTEGER,
                overall_signal TEXT,
                educational_notes TEXT,
                setup_tags TEXT,
                ml_confidence REAL,
                score_method TEXT DEFAULT 'HARDCODED'
            )
        ''')

        # New table for the Notification Center
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_type TEXT,
                message_text TEXT,
                is_read BOOLEAN DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'sent'
            )
        ''')
        # Migration: add status column to existing databases that predate this schema change
        try:
            cursor.execute("ALTER TABLE system_notifications ADD COLUMN status TEXT NOT NULL DEFAULT 'sent'")
        except Exception:
            pass  # Column already exists

        # Alert deduplication ledger — single source of truth for intraday suppression.
        # Keyed by (engine, ticker); one row per active condition, updated in-place on fire.
        # Decoupled from system_notifications so display logic and dedup logic never interfere.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alert_state (
                engine TEXT NOT NULL,
                ticker TEXT NOT NULL,
                fingerprint TEXT,
                last_price REAL,
                last_fired_utc TEXT,
                armed INTEGER NOT NULL DEFAULT 1,
                fire_count INTEGER NOT NULL DEFAULT 0,
                state_date TEXT,
                PRIMARY KEY (engine, ticker)
            )
        ''')

        # New table for the Live Market Pulse Database Cache
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_pulse_cache (
                ticker TEXT PRIMARY KEY,
                name TEXT,
                price REAL,
                change_pts REAL,
                change_pct REAL,
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
                mom_1m REAL,
                mom_3m REAL,
                mom_6m REAL,
                mom_12m_skip1m REAL,
                atr_pct REAL,
                hist_vol_20 REAL,
                rel_strength_5d REAL,
                rel_strength_20d REAL,
                composite_score INTEGER,
                overall_signal TEXT,
                PRIMARY KEY (ticker, date)
            )
        ''')

        # Proper Composite Primary Key for Scan State
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS quant_scan_states (
                scan_date TEXT,
                scan_type TEXT,
                last_processed_ticker TEXT,
                status TEXT,
                PRIMARY KEY (scan_date, scan_type)
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
                exchange TEXT,
                is_freetrade BOOLEAN DEFAULT 0,
                is_index BOOLEAN DEFAULT 0,
                index_membership TEXT,
                freetrade_subtitle TEXT,
                freetrade_url TEXT,
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
                vix_close REAL,
                spy_volatility REAL,
                us_turbulence REAL,
                us_regime_label TEXT,
                ftse_volatility REAL,
                uk_turbulence REAL,
                uk_regime_label TEXT
            )
        ''')

        # --- PHASE 2: SYSTEMIC MACRO RISK TRACKING (DUAL-REGION UPGRADE) ---
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS macro_regimes (
                date TEXT PRIMARY KEY,
                tyx_close REAL,
                tnx_close REAL,
                dxy_close REAL,
                uk_gilt_close REAL,
                gbpusd_close REAL,
                us_yield_velocity REAL,
                us_threat_level TEXT,
                uk_yield_velocity REAL,
                uk_threat_level TEXT
            )
        ''')

        # --- PHASE 3: MACROECONOMIC CALENDAR EVENTS ---
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS macro_calendar (
                event_id TEXT PRIMARY KEY,
                event_date TIMESTAMP,
                currency TEXT,
                impact TEXT,
                event_name TEXT,
                forecast_val REAL,
                previous_val REAL,
                actual_val REAL,
                post_event_spy_gap REAL,
                ai_volatility_warning REAL DEFAULT 0.0,
                is_event_passed BOOLEAN DEFAULT 0,
                alert_dispatched BOOLEAN DEFAULT 0
            )
        ''')

        # --- PHASE 3: STRUCTURAL MACRO INDICATORS ---
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS macro_indicators (
                date TEXT PRIMARY KEY,
                us_m2 REAL,
                us_jobless_claims REAL,
                us_high_yield_spread REAL,
                us_yield_curve REAL,
                uk_m4 REAL,
                uk_corporate_spread REAL,
                uk_cpi_inflation REAL,
                uk_claimant_count REAL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scheduler_run_log (
                job_id TEXT PRIMARY KEY,
                last_run TEXT NOT NULL
            )
        ''')

        # --- X-RAY RISK CACHE (Tier C — yfinance pre-compute) ---
        # Per-ticker beta and annualised volatility vs the configured benchmark.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS xray_risk_cache (
                ticker TEXT NOT NULL,
                benchmark TEXT NOT NULL,
                last_updated TEXT NOT NULL,
                beta REAL,
                annualized_vol REAL,
                PRIMARY KEY (ticker, benchmark)
            )
        ''')

        # Full pairwise correlation matrix stored as JSON blobs.
        # One row per benchmark — cheapest way to reconstruct the N×N matrix.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS xray_correlation_matrix (
                benchmark TEXT PRIMARY KEY,
                last_updated TEXT NOT NULL,
                tickers_json TEXT NOT NULL,
                matrix_json TEXT NOT NULL
            )
        ''')

        # Per-holding dividend yield cache (one live Ghostfolio call per holding,
        # done on the scheduler job so page load never blocks on it).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS xray_dividend_cache (
                ticker TEXT NOT NULL,
                data_source TEXT NOT NULL,
                last_updated TEXT NOT NULL,
                dividend_yield_pct REAL,
                dividend_in_base_currency REAL,
                PRIMARY KEY (ticker, data_source)
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
    # 0. Migrate quant_scan_states PK normalization
    try:
        cursor.execute("PRAGMA table_info(quant_scan_states)")
        existing_state_columns = [info['name'] for info in cursor.fetchall()]
        if 'scan_type' not in existing_state_columns:
            logger.info("[MIGRATION] Normalizing quant_scan_states schema (Recreating for Composite PK)...")
            cursor.execute("DROP TABLE quant_scan_states")
            cursor.execute('''
                CREATE TABLE quant_scan_states (
                    scan_date TEXT,
                    scan_type TEXT,
                    last_processed_ticker TEXT,
                    status TEXT,
                    PRIMARY KEY (scan_date, scan_type)
                )
            ''')
            conn.commit()
    except Exception as e:
        logger.error(f"[MIGRATION ERROR] Failed on quant_scan_states recreation: {e}")

    # 0.5 Migrate market_pulse_cache from TEXT to REAL
    try:
        cursor.execute("PRAGMA table_info(market_pulse_cache)")
        cache_columns = cursor.fetchall()
        for col in cache_columns:
            if col['name'] == 'price' and col['type'].upper() == 'TEXT':
                logger.info("[MIGRATION] Rebuilding market_pulse_cache to strictly enforce REAL numeric types...")
                
                # 4-Step Table Rebuild Migration (Standard SQLite workaround for changing types)
                cursor.execute("ALTER TABLE market_pulse_cache RENAME TO _legacy_market_pulse_cache")
                cursor.execute('''
                    CREATE TABLE market_pulse_cache (
                        ticker TEXT PRIMARY KEY,
                        name TEXT,
                        price REAL,
                        change_pts REAL,
                        change_pct REAL,
                        is_positive BOOLEAN,
                        last_updated REAL
                    )
                ''')
                cursor.execute('''
                    INSERT INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated)
                    SELECT ticker, name, 
                           CAST(REPLACE(REPLACE(price, '$', ''), ',', '') AS REAL), 
                           CAST(REPLACE(REPLACE(change_pts, '+', ''), ',', '') AS REAL), 
                           CAST(REPLACE(REPLACE(change_pct, '%', ''), '+', '') AS REAL), 
                           is_positive, last_updated
                    FROM _legacy_market_pulse_cache
                ''')
                cursor.execute("DROP TABLE _legacy_market_pulse_cache")
                conn.commit()
                break
    except Exception as e:
        logger.error(f"[MIGRATION ERROR] Failed on market_pulse_cache numeric enforcement: {e}")

    # 1. Migrate stock_signals
    cursor.execute("PRAGMA table_info(stock_signals)")
    existing_stock_columns = [info['name'] for info in cursor.fetchall()]

    required_stock_columns = {
        'company_name': 'TEXT', 'sector': 'TEXT', 'currency': 'TEXT',
        'country': 'TEXT', 'quote_type': 'TEXT',
        'ma_50_day': 'REAL', 'ma_200_day': 'REAL',
        'trend_50d': 'TEXT', 'trend_200d': 'TEXT',
        'fifty_two_week_low': 'REAL', 'fifty_two_week_high': 'REAL',
        'trailing_pe': 'REAL', 'forward_pe': 'REAL', 'peg_ratio': 'REAL',
        'peter_lynch_peg': 'REAL', 'price_to_book': 'REAL',
        'profit_margin': 'REAL', 'roe': 'REAL', 'revenue_growth': 'REAL',
        'debt_to_equity': 'REAL', 'current_ratio': 'REAL', 'operating_cash_flow': 'REAL',
        'ytd_return': 'REAL', 'total_assets': 'REAL', 'nav_price': 'REAL',
        'expense_ratio': 'REAL', 'top_holdings': 'TEXT', 'sector_weightings': 'TEXT',
        'dividend_yield': 'REAL', 'ex_dividend_date': 'TEXT', 'target_price': 'REAL',
        'analyst_rating': 'TEXT', 'next_earnings_date': 'TEXT',
        'short_interest': 'REAL', 'institutional_ownership': 'REAL', 'beta': 'REAL',
        'yield_correlation': 'REAL', 'setup_tags': 'TEXT',
        'ml_confidence': 'REAL', 'score_method': 'TEXT DEFAULT "HARDCODED"'
    }

    for col_name, data_type in required_stock_columns.items():
        if col_name not in existing_stock_columns:
            try:
                logger.info(f"[MIGRATION] Adding column: '{col_name}' to stock_signals...")
                cursor.execute(f"ALTER TABLE stock_signals ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error(f"[MIGRATION ERROR] Failed on stock_signals: {e}")
                continue

    # 2. Migrate quant_signals (Adding ML and Risk Factor Columns)
    cursor.execute("PRAGMA table_info(quant_signals)")
    existing_quant_columns = [info['name'] for info in cursor.fetchall()]

    required_quant_columns = {
        'ml_confidence_score': 'REAL',
        'sentiment_score': 'REAL',
        'var_95': 'REAL',
        'cvar_95': 'REAL',
        'mom_1m': 'REAL',
        'mom_3m': 'REAL',
        'mom_6m': 'REAL',
        'mom_12m_skip1m': 'REAL',
        'atr_pct': 'REAL',
        'hist_vol_20': 'REAL',
        'rel_strength_5d': 'REAL',
        'rel_strength_20d': 'REAL',
        'composite_score': 'INTEGER',
        'overall_signal': 'TEXT',
    }

    for col_name, data_type in required_quant_columns.items():
        if col_name not in existing_quant_columns:
            try:
                logger.info(f"[MIGRATION] Adding column: '{col_name}' to quant_signals...")
                cursor.execute(f"ALTER TABLE quant_signals ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error(f"[MIGRATION ERROR] Failed on quant_signals: {e}")
                continue

    # 3. Migrate market_universe
    cursor.execute("PRAGMA table_info(market_universe)")
    existing_universe_columns = [info['name'] for info in cursor.fetchall()]

    required_universe_columns = {
        'country': 'TEXT',
        'exchange': 'TEXT',
        'is_freetrade': 'BOOLEAN DEFAULT 0',
        'is_index': 'BOOLEAN DEFAULT 0',
        'index_membership': 'TEXT',
        'freetrade_subtitle': 'TEXT',
        'freetrade_url': 'TEXT',
        'quote_type': 'TEXT'
    }

    for col_name, data_type in required_universe_columns.items():
        if col_name not in existing_universe_columns:
            try:
                logger.info(f"[MIGRATION] Adding column: '{col_name}' to market_universe...")
                cursor.execute(f"ALTER TABLE market_universe ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error(f"[MIGRATION ERROR] Failed on market_universe: {e}")
                continue

    # 4. Migrate market_regimes (Dual-Region Refactor + Stacking UI Expansion)
    cursor.execute("PRAGMA table_info(market_regimes)")
    existing_regime_columns = [info['name'] for info in cursor.fetchall()]

    required_regime_columns = {
        'vix_close': 'REAL',
        'spy_volatility': 'REAL',
        'us_turbulence': 'REAL',
        'us_regime_label': 'TEXT',
        'ftse_volatility': 'REAL',
        'uk_turbulence': 'REAL',
        'uk_regime_label': 'TEXT',
        'ai_hmm_state': 'INTEGER'  # Surface standalone HMM clustering to UI
    }

    for col_name, data_type in required_regime_columns.items():
        if col_name not in existing_regime_columns:
            try:
                logger.info(f"[MIGRATION] Expanding market_regimes schema: '{col_name}'...")
                cursor.execute(f"ALTER TABLE market_regimes ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error(f"[MIGRATION ERROR] Failed on market_regimes: {e}")
                continue

    # 5. Migrate macro_regimes
    cursor.execute("PRAGMA table_info(macro_regimes)")
    existing_macro_columns = [info['name'] for info in cursor.fetchall()]

    required_macro_columns = {
        'us_yield_velocity': 'REAL',
        'us_threat_level': 'TEXT',
        'uk_yield_velocity': 'REAL',
        'uk_threat_level': 'TEXT'
    }

    for col_name, data_type in required_macro_columns.items():
        if col_name not in existing_macro_columns:
            try:
                logger.info(f"[MIGRATION] Adding dual-region risk column '{col_name}'...")
                cursor.execute(f"ALTER TABLE macro_regimes ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error(f"[MIGRATION ERROR] Failed on macro_regimes: {e}")
                continue

    # 6. Migrate macro_calendar (Adding Stacking surprise probability field)
    cursor.execute("PRAGMA table_info(macro_calendar)")
    existing_calendar_columns = [info['name'] for info in cursor.fetchall()]

    required_calendar_columns = {
        'event_date': 'TIMESTAMP', 'currency': 'TEXT', 'impact': 'TEXT',
        'event_name': 'TEXT', 'forecast_val': 'REAL', 'previous_val': 'REAL',
        'actual_val': 'REAL', 'post_event_spy_gap': 'REAL', 'ai_volatility_warning': 'REAL DEFAULT 0.0',
        'is_event_passed': 'BOOLEAN DEFAULT 0', 'alert_dispatched': 'BOOLEAN DEFAULT 0',
        'ai_consensus_miss_prob': 'REAL'  # Surface standalone RF probability to UI
    }

    for col_name, data_type in required_calendar_columns.items():
        if col_name not in existing_calendar_columns:
            try:
                logger.info(f"[MIGRATION] Adding column '{col_name}' to macro_calendar...")
                cursor.execute(f"ALTER TABLE macro_calendar ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error(f"[MIGRATION ERROR] Failed on macro_calendar: {e}")
                continue

    # 7. Migrate macro_indicators
    cursor.execute("PRAGMA table_info(macro_indicators)")
    existing_indicator_columns = [info['name'] for info in cursor.fetchall()]

    required_indicator_columns = {
        'us_m2': 'REAL', 'us_jobless_claims': 'REAL', 'us_high_yield_spread': 'REAL',
        'us_yield_curve': 'REAL', 'uk_m4': 'REAL', 'uk_corporate_spread': 'REAL',
        'uk_cpi_inflation': 'REAL', 'uk_claimant_count': 'REAL',
        'us_cpi_inflation': 'REAL'
    }

    for col_name, data_type in required_indicator_columns.items():
        if col_name not in existing_indicator_columns:
            try:
                logger.info(f"[MIGRATION] Adding Phase 1 Yield Curve column '{col_name}' to macro_indicators...")
                cursor.execute(f"ALTER TABLE macro_indicators ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error(f"[MIGRATION ERROR] Failed on macro_indicators: {e}")
                continue

    # 8. Ensure covering index on quant_signals for efficient latest-date lookups
    try:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_qs_ticker_date
            ON quant_signals(ticker, date)
        """)
        logger.info("[MIGRATION] Verified index idx_qs_ticker_date on quant_signals(ticker, date).")
    except Exception as e:
        logger.error(f"[MIGRATION ERROR] Failed to create idx_qs_ticker_date: {e}")

    try:
        conn.commit()
    except Exception as e:
        logger.error(f"[MIGRATION ERROR] Failed to commit migration changes: {e}")
        conn.rollback()


def get_universe_tickers() -> List[str]:
    """
    Connects to the SQLite DB and extracts all tracked universe tickers.
    Enforces the Freetrade Firewall if enabled in UI settings.
    """
    try:
        config_data = load_config()
        freetrade_only = config_data.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)

        conn = get_connection()
        cursor = conn.cursor()
        
        if freetrade_only:
            # FIREWALL: Only process assets you can actually trade
            cursor.execute("SELECT ticker FROM market_universe WHERE is_freetrade = 1")
        else:
            # LEGACY: Process everything
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
    Returns True on success, False on failure.
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()

        query = '''
            INSERT INTO quant_signals (
                ticker, date, close_price, volume, rsi_14, macd, macd_signal, macd_hist,
                sma_50, sma_200, volume_surge, bullish_cross,
                ml_confidence_score, sentiment_score, var_95, cvar_95
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET
                close_price       = excluded.close_price,
                volume            = excluded.volume,
                rsi_14            = excluded.rsi_14,
                macd              = excluded.macd,
                macd_signal       = excluded.macd_signal,
                macd_hist         = excluded.macd_hist,
                sma_50            = excluded.sma_50,
                sma_200           = excluded.sma_200,
                volume_surge      = excluded.volume_surge,
                bullish_cross     = excluded.bullish_cross,
                ml_confidence_score = COALESCE(excluded.ml_confidence_score, quant_signals.ml_confidence_score),
                sentiment_score   = COALESCE(excluded.sentiment_score, quant_signals.sentiment_score),
                var_95            = COALESCE(excluded.var_95, quant_signals.var_95),
                cvar_95           = COALESCE(excluded.cvar_95, quant_signals.cvar_95)
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