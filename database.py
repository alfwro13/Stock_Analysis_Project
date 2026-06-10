# database.py
import sqlite3
import logging
from typing import List, Optional

from config import DB_PATH, load_config

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    """sqlite3.Row enables column-name access (row['ticker'])."""
    # timeout=20.0 gracefully handles background thread write collisions
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.execute('PRAGMA journal_mode=WAL;')   # concurrent reads + writes
    conn.execute('PRAGMA synchronous=NORMAL;')  # significant write-perf gain in WAL mode
    conn.row_factory = sqlite3.Row
    return conn


def log_notification(message_type: str, message_text: str) -> None:
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
        logger.error("Failed to log notification: %s", e)
    finally:
        if conn:
            conn.close()


def init_db() -> None:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
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

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS quant_scan_states (
                scan_date TEXT,
                scan_type TEXT,
                last_processed_ticker TEXT,
                status TEXT,
                PRIMARY KEY (scan_date, scan_type)
            )
        ''')

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

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ticker_metadata (
                ticker TEXT PRIMARY KEY,
                sector TEXT,
                beta REAL,
                market_cap REAL
            )
        ''')

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

        # Weighted daily portfolio return series used for historical VaR/CVaR,
        # tracking error, Sharpe, and skewness/kurtosis.  One row per benchmark.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS xray_portfolio_returns_cache (
                benchmark TEXT PRIMARY KEY,
                last_updated TEXT NOT NULL,
                dates_json TEXT NOT NULL,
                returns_json TEXT NOT NULL,
                benchmark_returns_json TEXT NOT NULL
            )
        ''')

        # Score history — one row per (ticker, trading_date); upserted on every scan run.
        # Accumulates over time so a forward-returns analysis can be run after 6+ months.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS score_history (
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                score INTEGER NOT NULL,
                signal TEXT NOT NULL,
                close_price REAL,
                PRIMARY KEY (ticker, date)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_contagion_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_ts TEXT NOT NULL,
                leader_count INTEGER NOT NULL DEFAULT 0,
                etf_count INTEGER NOT NULL DEFAULT 0,
                alert_fired INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS news_articles (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id   TEXT    UNIQUE NOT NULL,
                ticker       TEXT    NOT NULL,
                company_name TEXT,
                source_list  TEXT    NOT NULL,
                headline     TEXT    NOT NULL,
                summary      TEXT,
                full_text    TEXT,
                body_fetched INTEGER DEFAULT 0,
                url          TEXT,
                publisher    TEXT,
                published_at    INTEGER NOT NULL,
                is_premium      INTEGER DEFAULT 0,
                fetched_at      INTEGER NOT NULL,
                sentiment_score REAL,
                sentiment_label TEXT
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_news_published
            ON news_articles(published_at DESC)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_news_ticker
            ON news_articles(ticker)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_news_source
            ON news_articles(source_list)
        ''')

        # --- INTRADAY DIP RADAR ---
        # Tracks which tickers are armed for today's session (one row per ticker).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS intraday_monitors (
                ticker       TEXT PRIMARY KEY,
                date_added   DATE NOT NULL,
                is_active    INTEGER NOT NULL DEFAULT 1,
                activated_by TEXT
            )
        ''')

        # Persists the latest scan result per ticker so the UI can poll without waiting.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS intraday_monitor_results (
                ticker          TEXT PRIMARY KEY,
                scan_ts         DATETIME NOT NULL,
                current_price   REAL,
                reversal_score  INTEGER,
                is_bottoming    INTEGER,
                reasons_json    TEXT,
                rsi             REAL,
                bb_lower        REAL,
                vwap            REAL,
                vwap_lower      REAL,
                vwap_deviation  REAL,
                vol_climax      INTEGER
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS model_training_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name      TEXT    NOT NULL,
                trained_at      TEXT    NOT NULL,
                n_samples       INTEGER,
                cv_score_mean   REAL,
                cv_score_std    REAL,
                score_metric    TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS smgb_predictions (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_date          TEXT NOT NULL,
                target_date              TEXT NOT NULL,
                prediction_type          TEXT NOT NULL DEFAULT 'next_open',
                predicted_price          REAL,
                actual_open              REAL,
                predicted_change_pct     REAL,
                actual_change_pct        REAL,
                last_smgb_close          REAL,
                holdings_predicted_price REAL,
                regression_predicted_price REAL,
                signal_source            TEXT,
                data_source              TEXT,
                fx_rate                  REAL,
                r_squared                REAL,
                absolute_error           REAL,
                pct_error                REAL,
                direction_correct        INTEGER,
                created_at               TEXT DEFAULT (datetime('now')),
                UNIQUE(target_date, prediction_type)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trap_monitor_results (
                ticker               TEXT PRIMARY KEY,
                phase                TEXT,
                bull_trap_level      TEXT,
                bull_trap_vol_ratio  REAL,
                bull_trap_notes      TEXT,
                bear_trap_level      TEXT,
                bear_trap_notes      TEXT,
                cap_level            TEXT,
                cap_vol_zscore       REAL,
                cap_notes            TEXT,
                wyckoff_level        TEXT,
                wyckoff_bb_width     REAL,
                wyckoff_notes        TEXT,
                ema_distance         REAL,
                rsi                  REAL,
                scan_ts              TEXT
            )
        ''')

        conn.commit()

        migrate_db(conn, cursor)

        logger.info("Database connection verified and schema is fully up-to-date.")

    except Exception as e:
        logger.error("Failed to initialize database schema: %s", e)
    finally:
        conn.close()


def migrate_db(conn: sqlite3.Connection, cursor: sqlite3.Cursor) -> None:
    # quant_scan_states PK normalization
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
        logger.error("[MIGRATION ERROR] Failed on quant_scan_states recreation: %s", e)

    # market_pulse_cache TEXT→REAL column type migration
    try:
        cursor.execute("PRAGMA table_info(market_pulse_cache)")
        cache_columns = cursor.fetchall()
        for col in cache_columns:
            if col['name'] == 'price' and col['type'].upper() == 'TEXT':
                logger.info("[MIGRATION] Rebuilding market_pulse_cache to strictly enforce REAL numeric types...")
                # SQLite has no ALTER COLUMN — rename/recreate/copy/drop is the standard workaround
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
        logger.error("[MIGRATION ERROR] Failed on market_pulse_cache numeric enforcement: %s", e)

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
                logger.info("[MIGRATION] Adding column: %s to stock_signals...", col_name)
                cursor.execute(f"ALTER TABLE stock_signals ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error("[MIGRATION ERROR] Failed on stock_signals: %s", e)
                continue

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
        'week52_pct': 'REAL',
        'anomaly_score': 'REAL',
    }

    for col_name, data_type in required_quant_columns.items():
        if col_name not in existing_quant_columns:
            try:
                logger.info("[MIGRATION] Adding column: %s to quant_signals...", col_name)
                cursor.execute(f"ALTER TABLE quant_signals ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error("[MIGRATION ERROR] Failed on quant_signals: %s", e)
                continue

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
                logger.info("[MIGRATION] Adding column: %s to market_universe...", col_name)
                cursor.execute(f"ALTER TABLE market_universe ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error("[MIGRATION ERROR] Failed on market_universe: %s", e)
                continue

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
                logger.info("[MIGRATION] Expanding market_regimes schema: %s...", col_name)
                cursor.execute(f"ALTER TABLE market_regimes ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error("[MIGRATION ERROR] Failed on market_regimes: %s", e)
                continue

    cursor.execute("PRAGMA table_info(macro_regimes)")
    existing_macro_columns = [info['name'] for info in cursor.fetchall()]

    required_macro_columns = {
        'us_yield_velocity': 'REAL',
        'us_threat_level': 'TEXT',
        'uk_yield_velocity': 'REAL',
        'uk_threat_level': 'TEXT',
        'yield_curve_inverted': 'INTEGER',
        'days_inverted': 'INTEGER',
        'regime_label': 'TEXT'
    }

    for col_name, data_type in required_macro_columns.items():
        if col_name not in existing_macro_columns:
            try:
                logger.info("[MIGRATION] Adding dual-region risk column %s...", col_name)
                cursor.execute(f"ALTER TABLE macro_regimes ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error("[MIGRATION ERROR] Failed on macro_regimes: %s", e)
                continue

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
                logger.info("[MIGRATION] Adding column %s to macro_calendar...", col_name)
                cursor.execute(f"ALTER TABLE macro_calendar ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error("[MIGRATION ERROR] Failed on macro_calendar: %s", e)
                continue

    cursor.execute("PRAGMA table_info(macro_indicators)")
    existing_indicator_columns = [info['name'] for info in cursor.fetchall()]

    required_indicator_columns = {
        'us_m2': 'REAL', 'us_jobless_claims': 'REAL', 'us_high_yield_spread': 'REAL',
        'us_yield_curve': 'REAL', 'uk_m4': 'REAL', 'uk_corporate_spread': 'REAL',
        'uk_cpi_inflation': 'REAL', 'uk_claimant_count': 'REAL',
        'us_cpi_inflation': 'REAL',
        'us_fed_funds_rate': 'REAL',
        'us_real_yield_10y': 'REAL',
        'uk_base_rate': 'REAL'
    }

    for col_name, data_type in required_indicator_columns.items():
        if col_name not in existing_indicator_columns:
            try:
                logger.info("[MIGRATION] Adding Phase 1 Yield Curve column %s to macro_indicators...", col_name)
                cursor.execute(f"ALTER TABLE macro_indicators ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error("[MIGRATION ERROR] Failed on macro_indicators: %s", e)
                continue

    # news_articles — add sentiment columns if not present
    try:
        cursor.execute("PRAGMA table_info(news_articles)")
        news_cols = [c['name'] for c in cursor.fetchall()]
        for col, dtype in [("sentiment_score", "REAL"), ("sentiment_label", "TEXT")]:
            if col not in news_cols:
                cursor.execute(f"ALTER TABLE news_articles ADD COLUMN {col} {dtype}")
                logger.info("[MIGRATION] Added column %s to news_articles.", col)
        conn.commit()
    except Exception as e:
        logger.error("[MIGRATION ERROR] news_articles sentiment columns: %s", e)

    # ai_contagion_snapshots (guard for pre-feature DBs)
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_contagion_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_ts TEXT NOT NULL,
                leader_count INTEGER NOT NULL DEFAULT 0,
                etf_count INTEGER NOT NULL DEFAULT 0,
                alert_fired INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT
            )
        ''')
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to create ai_contagion_snapshots: %s", e)

    # intraday dip radar tables (guard for pre-feature DBs)
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS intraday_monitors (
                ticker       TEXT PRIMARY KEY,
                date_added   DATE NOT NULL,
                is_active    INTEGER NOT NULL DEFAULT 1,
                activated_by TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS intraday_monitor_results (
                ticker          TEXT PRIMARY KEY,
                scan_ts         DATETIME NOT NULL,
                current_price   REAL,
                reversal_score  INTEGER,
                is_bottoming    INTEGER,
                reasons_json    TEXT,
                rsi             REAL,
                bb_lower        REAL,
                vwap            REAL,
                vwap_lower      REAL,
                vwap_deviation  REAL,
                vol_climax      INTEGER
            )
        ''')
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to create intraday dip radar tables: %s", e)

    # intraday_monitor_results — add bb_lower, vwap_lower, vol_climax if missing
    cursor.execute("PRAGMA table_info(intraday_monitor_results)")
    existing_imr_columns = [info['name'] for info in cursor.fetchall()]
    required_imr_columns = {
        'bb_lower':   'REAL',
        'vwap_lower': 'REAL',
        'vol_climax': 'INTEGER',
    }
    for col_name, data_type in required_imr_columns.items():
        if col_name not in existing_imr_columns:
            try:
                logger.info("[MIGRATION] Adding column %s to intraday_monitor_results...", col_name)
                cursor.execute(f"ALTER TABLE intraday_monitor_results ADD COLUMN {col_name} {data_type}")
            except Exception as e:
                logger.error("[MIGRATION ERROR] Failed adding %s to intraday_monitor_results: %s", col_name, e)

    # ticker_metadata (guard for pre-feature DBs)
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ticker_metadata (
                ticker TEXT PRIMARY KEY,
                sector TEXT,
                beta REAL,
                market_cap REAL
            )
        ''')
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to create ticker_metadata: %s", e)

    # model_training_log (guard for DBs that pre-date init_db ownership of this table)
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS model_training_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name      TEXT    NOT NULL,
                trained_at      TEXT    NOT NULL,
                n_samples       INTEGER,
                cv_score_mean   REAL,
                cv_score_std    REAL,
                score_metric    TEXT
            )
        ''')
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to create model_training_log: %s", e)

    # covering index on quant_signals for efficient latest-date lookups
    try:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_qs_ticker_date
            ON quant_signals(ticker, date)
        """)
        logger.info("[MIGRATION] Verified index idx_qs_ticker_date on quant_signals(ticker, date).")
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to create idx_qs_ticker_date: %s", e)

    # smgb_predictions: add prediction_type column + composite unique (target_date, prediction_type)
    try:
        cursor.execute("PRAGMA table_info(smgb_predictions)")
        existing_sp_cols = {row['name'] for row in cursor.fetchall()}
        if 'prediction_type' not in existing_sp_cols:
            logger.info("[MIGRATION] Rebuilding smgb_predictions to add prediction_type column...")
            cursor.execute("""
                CREATE TABLE smgb_predictions_new (
                    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                    prediction_date          TEXT NOT NULL,
                    target_date              TEXT NOT NULL,
                    prediction_type          TEXT NOT NULL DEFAULT 'next_open',
                    predicted_price          REAL,
                    actual_open              REAL,
                    predicted_change_pct     REAL,
                    actual_change_pct        REAL,
                    last_smgb_close          REAL,
                    holdings_predicted_price REAL,
                    regression_predicted_price REAL,
                    signal_source            TEXT,
                    data_source              TEXT,
                    fx_rate                  REAL,
                    r_squared                REAL,
                    absolute_error           REAL,
                    pct_error                REAL,
                    direction_correct        INTEGER,
                    created_at               TEXT DEFAULT (datetime('now')),
                    UNIQUE(target_date, prediction_type)
                )
            """)
            cursor.execute("""
                INSERT INTO smgb_predictions_new (
                    id, prediction_date, target_date, prediction_type,
                    predicted_price, actual_open, predicted_change_pct, actual_change_pct,
                    last_smgb_close, holdings_predicted_price, regression_predicted_price,
                    signal_source, data_source, fx_rate, r_squared,
                    absolute_error, pct_error, direction_correct, created_at
                )
                SELECT
                    id, prediction_date, target_date, 'next_open',
                    predicted_price, actual_open, predicted_change_pct, actual_change_pct,
                    last_smgb_close, holdings_predicted_price, regression_predicted_price,
                    signal_source, data_source, fx_rate, r_squared,
                    absolute_error, pct_error, direction_correct, created_at
                FROM smgb_predictions
            """)
            cursor.execute("DROP TABLE smgb_predictions")
            cursor.execute("ALTER TABLE smgb_predictions_new RENAME TO smgb_predictions")
            logger.info("[MIGRATION] smgb_predictions rebuilt with prediction_type column.")
    except Exception as e:
        logger.error("[MIGRATION ERROR] smgb_predictions rebuild failed: %s", e)

    try:
        conn.commit()
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to commit migration changes: %s", e)
        conn.rollback()


def log_score_event(ticker: str, date: str, score: int, signal: str, close_price: Optional[float]) -> None:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO score_history (ticker, date, score, signal, close_price)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(ticker, date) DO UPDATE SET
                   score = excluded.score,
                   signal = excluded.signal,
                   close_price = COALESCE(excluded.close_price, score_history.close_price)""",
            (ticker, date, score, signal, close_price)
        )
        conn.commit()
    except Exception as e:
        logger.error("Failed to log score event for %s on %s: %s", ticker, date, e)
    finally:
        if conn:
            conn.close()


def log_smgb_prediction(result: dict) -> None:
    """ON CONFLICT DO NOTHING — safe to call on every page load."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        reg = result.get("regression_engine") or {}
        hold = result.get("holdings_engine") or {}
        signal = result.get("signal_source", "daily_close")
        prediction_type = (
            "us_open_impact"
            if signal in ("intraday_premarket", "intraday_live")
            else "next_open"
        )
        cursor.execute(
            """INSERT INTO smgb_predictions (
                   prediction_date, target_date, prediction_type,
                   predicted_price, predicted_change_pct,
                   last_smgb_close, holdings_predicted_price, regression_predicted_price,
                   signal_source, data_source, fx_rate, r_squared
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(target_date, prediction_type) DO NOTHING""",
            (
                result.get("as_of_utc", "")[:10],
                result.get("next_open_date"),
                prediction_type,
                result.get("predicted_price"),
                result.get("predicted_change_pct"),
                result.get("last_smgb_close"),
                hold.get("predicted_price"),
                reg.get("predicted_price"),
                signal,
                result.get("data_source"),
                result.get("fx_rate_gbpusd"),
                reg.get("r_squared"),
            )
        )
        conn.commit()
    except Exception as e:
        logger.error("Failed to log SMGB prediction: %s", e)
    finally:
        if conn:
            conn.close()


def fill_smgb_actual(target_date: str, actual_price: float, prediction_type: str = 'next_open') -> None:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT predicted_price, last_smgb_close FROM smgb_predictions
               WHERE target_date = ? AND prediction_type = ? AND actual_open IS NULL""",
            (target_date, prediction_type)
        )
        row = cursor.fetchone()
        if row is None:
            return
        predicted = row["predicted_price"]
        last_close = row["last_smgb_close"]
        absolute_error = round(abs(predicted - actual_price), 4) if predicted is not None else None
        pct_error = round(abs(predicted - actual_price) / actual_price * 100, 4) if predicted and actual_price else None
        actual_change_pct = round((actual_price - last_close) / last_close * 100, 4) if last_close else None
        predicted_change_sign = predicted - last_close if predicted and last_close else None
        actual_change_sign = actual_price - last_close if last_close else None
        direction_correct = None
        if predicted_change_sign is not None and actual_change_sign is not None:
            direction_correct = 1 if (predicted_change_sign >= 0) == (actual_change_sign >= 0) else 0
        cursor.execute(
            """UPDATE smgb_predictions SET
                   actual_open = ?, actual_change_pct = ?,
                   absolute_error = ?, pct_error = ?, direction_correct = ?
               WHERE target_date = ? AND prediction_type = ?""",
            (actual_price, actual_change_pct, absolute_error, pct_error, direction_correct,
             target_date, prediction_type)
        )
        conn.commit()
    except Exception as e:
        logger.error("Failed to fill SMGB actual for %s (%s): %s", target_date, prediction_type, e)
    finally:
        if conn:
            conn.close()


def get_smgb_accuracy() -> dict:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        def _type_stats(ptype: str) -> dict:
            cursor.execute(
                """SELECT * FROM smgb_predictions WHERE prediction_type = ?
                   ORDER BY target_date DESC LIMIT 60""",
                (ptype,)
            )
            rows = [dict(r) for r in cursor.fetchall()]
            cursor.execute(
                """SELECT COUNT(*) as total,
                          SUM(CASE WHEN actual_open IS NOT NULL THEN 1 ELSE 0 END) as resolved,
                          AVG(CASE WHEN direction_correct IS NOT NULL THEN direction_correct END) as dir_acc,
                          AVG(CASE WHEN absolute_error IS NOT NULL THEN absolute_error END) as mae,
                          AVG(CASE WHEN pct_error IS NOT NULL THEN pct_error END) as mape
                   FROM smgb_predictions WHERE prediction_type = ?""",
                (ptype,)
            )
            agg = dict(cursor.fetchone())

            def _window_dir(n: int) -> Optional[float]:
                cursor.execute(
                    """SELECT AVG(direction_correct) FROM (
                           SELECT direction_correct FROM smgb_predictions
                           WHERE prediction_type = ? AND direction_correct IS NOT NULL
                           ORDER BY target_date DESC LIMIT ?
                       )""",
                    (ptype, n)
                )
                val = cursor.fetchone()[0]
                return round(val * 100, 1) if val is not None else None

            return {
                "rows": rows,
                "summary": {
                    "total_predictions": agg["total"] or 0,
                    "resolved_count": agg["resolved"] or 0,
                    "direction_accuracy_pct": round(agg["dir_acc"] * 100, 1) if agg["dir_acc"] is not None else None,
                    "mae_gbp": round(agg["mae"], 4) if agg["mae"] is not None else None,
                    "mape_pct": round(agg["mape"], 2) if agg["mape"] is not None else None,
                    "last_10_direction_pct": _window_dir(10),
                    "last_30_direction_pct": _window_dir(30),
                },
            }

        return {
            "next_open": _type_stats("next_open"),
            "us_open_impact": _type_stats("us_open_impact"),
        }
    except Exception as e:
        logger.error("Failed to get SMGB accuracy: %s", e)
        def _empty():
            return {"rows": [], "summary": {
                "total_predictions": 0, "resolved_count": 0,
                "direction_accuracy_pct": None, "mae_gbp": None, "mape_pct": None,
                "last_10_direction_pct": None, "last_30_direction_pct": None,
            }}
        return {"next_open": _empty(), "us_open_impact": _empty()}
    finally:
        if conn:
            conn.close()


def get_universe_tickers() -> List[str]:
    """Respects FREETRADE_ONLY_MODE: returns only is_freetrade=1 tickers when enabled."""
    conn = None
    try:
        config_data = load_config()
        freetrade_only = config_data.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)
        conn = get_connection()
        cursor = conn.cursor()
        if freetrade_only:
            cursor.execute("SELECT ticker FROM market_universe WHERE is_freetrade = 1")
        else:
            cursor.execute("SELECT ticker FROM market_universe")
        return [row['ticker'] for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Failed to fetch universe tickers: %s", e)
        return []
    finally:
        if conn:
            conn.close()


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
    conn = None
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
        logger.error("Database insertion failed for quant_signal (%s on %s): %s", ticker, date, e)
        return False
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    init_db()