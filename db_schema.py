import json
import os
import logging

from database import get_connection

logger = logging.getLogger(__name__)

_EXCHANGE_HOURS_PATH = os.path.join(os.path.dirname(__file__), "data", "exchange_hours.json")

_DEFAULT_EXCHANGE_HOURS = {
    "NYSE":    {"open":"09:30","close":"16:00","tz":"America/New_York",     "currency":"USD","suffixes":[],"premarket_open":"04:00"},
    "LSE":     {"open":"08:00","close":"16:30","tz":"Europe/London",        "currency":"GBP","suffixes":[".L"]},
    "XETRA":   {"open":"09:00","close":"17:30","tz":"Europe/Berlin",        "currency":"EUR","suffixes":[".DE",".F"]},
    "TSE":     {"open":"09:00","close":"15:30","tz":"Asia/Tokyo",           "currency":"JPY","suffixes":[".T"]},
    "ASX":     {"open":"10:00","close":"16:00","tz":"Australia/Sydney",     "currency":"AUD","suffixes":[".AX"]},
    "KRX":     {"open":"09:00","close":"15:30","tz":"Asia/Seoul",           "currency":"KRW","suffixes":[".KS",".KQ"]},
    "HKEX":    {"open":"09:30","close":"16:00","tz":"Asia/Hong_Kong",       "currency":"HKD","suffixes":[".HK"]},
    "SGX":     {"open":"09:00","close":"17:00","tz":"Asia/Singapore",       "currency":"SGD","suffixes":[".SI"]},
    "NSE":     {"open":"09:15","close":"15:30","tz":"Asia/Kolkata",         "currency":"INR","suffixes":[".NS"]},
    "BSE":     {"open":"09:15","close":"15:30","tz":"Asia/Kolkata",         "currency":"INR","suffixes":[".BO"]},
    "SSE":     {"open":"09:30","close":"15:00","tz":"Asia/Shanghai",        "currency":"CNY","suffixes":[".SS"]},
    "SZSE":    {"open":"09:30","close":"15:00","tz":"Asia/Shanghai",        "currency":"CNY","suffixes":[".SZ"]},
    "TWSE":    {"open":"09:00","close":"13:30","tz":"Asia/Taipei",          "currency":"TWD","suffixes":[".TW",".TWO"]},
    "TSX":     {"open":"09:30","close":"16:00","tz":"America/Toronto",      "currency":"CAD","suffixes":[".TO",".V"]},
    "BOVESPA": {"open":"10:00","close":"17:55","tz":"America/Sao_Paulo",    "currency":"BRL","suffixes":[".SA"]},
    "BMV":     {"open":"08:30","close":"15:00","tz":"America/Mexico_City",  "currency":"MXN","suffixes":[".MX"]},
    "Euronext":{"open":"09:00","close":"17:30","tz":"Europe/Paris",         "currency":"EUR","suffixes":[".PA",".AS",".BR",".LS"]},
    "SIX":     {"open":"09:00","close":"17:30","tz":"Europe/Zurich",        "currency":"CHF","suffixes":[".SW"]},
    "MIL":     {"open":"09:00","close":"17:30","tz":"Europe/Rome",          "currency":"EUR","suffixes":[".MI"]},
    "BME":     {"open":"09:00","close":"17:30","tz":"Europe/Madrid",        "currency":"EUR","suffixes":[".MC"]},
    "OMXS":    {"open":"09:00","close":"17:30","tz":"Europe/Stockholm",     "currency":"SEK","suffixes":[".ST"]},
    "OMXH":    {"open":"09:00","close":"18:30","tz":"Europe/Helsinki",      "currency":"EUR","suffixes":[".HE"]},
    "OMXC":    {"open":"09:00","close":"17:00","tz":"Europe/Copenhagen",    "currency":"DKK","suffixes":[".CO"]},
    "OSE":     {"open":"09:00","close":"16:30","tz":"Europe/Oslo",          "currency":"NOK","suffixes":[".OL"]},
    "WBAG":    {"open":"09:00","close":"17:30","tz":"Europe/Vienna",        "currency":"EUR","suffixes":[".VI"]},
    "WSE":     {"open":"09:00","close":"17:00","tz":"Europe/Warsaw",        "currency":"PLN","suffixes":[".WA"]},
    "JSE":     {"open":"09:00","close":"17:00","tz":"Africa/Johannesburg",  "currency":"ZAR","suffixes":[".JO"]},
    "TASE":    {"open":"09:59","close":"17:25","tz":"Asia/Jerusalem",       "currency":"ILS","suffixes":[".TA"]},
    "Tadawul": {"open":"10:00","close":"15:00","tz":"Asia/Riyadh",          "currency":"SAR","suffixes":[".SR"]},
}


def _seed_exchange_hours_json() -> None:
    if os.path.exists(_EXCHANGE_HOURS_PATH):
        return
    try:
        os.makedirs(os.path.dirname(_EXCHANGE_HOURS_PATH), exist_ok=True)
        with open(_EXCHANGE_HOURS_PATH, "w", encoding="utf-8") as f:
            json.dump(_DEFAULT_EXCHANGE_HOURS, f, indent=2)
        logger.info("Created default exchange_hours.json at %s", _EXCHANGE_HOURS_PATH)
    except Exception as exc:
        logger.warning("Could not write exchange_hours.json: %s", exc)


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
                price_to_sales REAL,
                free_cash_flow REAL,

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
                score_method TEXT DEFAULT 'HARDCODED',

                piotroski_f_score REAL,
                altman_z_score REAL,
                beneish_m_score REAL,
                forensic_last_updated TEXT
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
            CREATE TABLE IF NOT EXISTS company_name_overrides (
                ticker       TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                updated_at   TIMESTAMP
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
                last_run TEXT NOT NULL,
                last_started TEXT,
                last_duration_sec REAL,
                avg_duration_sec REAL,
                last_status TEXT
            )
        ''')

        cursor.execute("PRAGMA table_info(scheduler_run_log)")
        sched_log_cols = {row[1] for row in cursor.fetchall()}
        for col_name, data_type in (("last_started", "TEXT"), ("last_duration_sec", "REAL"), ("avg_duration_sec", "REAL"), ("last_status", "TEXT")):
            if col_name not in sched_log_cols:
                cursor.execute(f"ALTER TABLE scheduler_run_log ADD COLUMN {col_name} {data_type}")

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
                expire_date  DATE NOT NULL DEFAULT (date('now')),
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
            CREATE TABLE IF NOT EXISTS etf_predictor_configs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                etf_ticker      TEXT NOT NULL,
                constituents    TEXT NOT NULL,
                enabled         INTEGER NOT NULL DEFAULT 1,
                auto_schedule   INTEGER NOT NULL DEFAULT 0,
                pre_run_time    TEXT DEFAULT '13:30',
                post_run_time   TEXT DEFAULT '22:00',
                deleted_at      TEXT DEFAULT NULL,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS etf_predictor_predictions (
                id                         INTEGER PRIMARY KEY AUTOINCREMENT,
                config_id                  INTEGER NOT NULL,
                run_at                     TEXT NOT NULL,
                prediction_date            TEXT NOT NULL,
                target_date                TEXT NOT NULL,
                prediction_type            TEXT NOT NULL DEFAULT 'next_open',
                predicted_price            REAL,
                actual_open                REAL,
                predicted_change_pct       REAL,
                actual_change_pct          REAL,
                last_etf_close             REAL,
                holdings_predicted_price   REAL,
                regression_predicted_price REAL,
                signal_source              TEXT,
                data_source                TEXT,
                fx_rate                    REAL,
                r_squared                  REAL,
                absolute_error             REAL,
                pct_error                  REAL,
                direction_correct          INTEGER,
                constituent_snapshot       TEXT,
                created_at                 TEXT DEFAULT (datetime('now')),
                UNIQUE(config_id, target_date, prediction_type)
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

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trap_phase_history (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker                TEXT NOT NULL,
                phase                 TEXT NOT NULL,
                scan_date             TEXT NOT NULL,
                scan_ts               TEXT NOT NULL,
                close_price           REAL,
                actual_price_14d      REAL,
                actual_date_14d       TEXT,
                direction_correct_14d INTEGER,
                actual_price_30d      REAL,
                actual_date_30d       TEXT,
                direction_correct_30d INTEGER,
                UNIQUE(ticker, scan_date)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_hmm_states (
                date      TEXT PRIMARY KEY,
                state     INTEGER NOT NULL,
                label     TEXT NOT NULL,
                probability REAL NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bubble_radar_metrics (
                ticker          TEXT NOT NULL,
                scan_date       TEXT NOT NULL,
                bubble_score    REAL,
                flag            TEXT,
                sma_ext_pct     REAL,
                rsi_avg_20d     REAL,
                ps_ratio        REAL,
                peg_ratio       REAL,
                fcf_yield       REAL,
                riskfree_rate   REAL,
                iv_call_skew    REAL,
                spy_rsp_spread  REAL,
                PRIMARY KEY (ticker, scan_date)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bubble_radar_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker          TEXT NOT NULL,
                flagged_date    TEXT NOT NULL,
                flag_level      TEXT NOT NULL,
                price_at_flag   REAL,
                price_4w        REAL,
                price_8w        REAL,
                price_12w       REAL,
                outcome_4w      TEXT,
                outcome_8w      TEXT,
                outcome_12w     TEXT,
                UNIQUE(ticker, flagged_date)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                token_hash  TEXT PRIMARY KEY,
                expires_at  TEXT NOT NULL,
                used        INTEGER NOT NULL DEFAULT 0
            )
        ''')

        conn.commit()

        migrate_db(conn, cursor)
        _seed_exchange_hours_json()

        logger.info("Database connection verified and schema is fully up-to-date.")

    except Exception as e:
        logger.error("Failed to initialize database schema: %s", e)
    finally:
        if conn:
            conn.close()


def migrate_db(conn, cursor) -> None:
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
        'price_to_sales': 'REAL', 'free_cash_flow': 'REAL',
        'profit_margin': 'REAL', 'roe': 'REAL', 'revenue_growth': 'REAL',
        'debt_to_equity': 'REAL', 'current_ratio': 'REAL', 'operating_cash_flow': 'REAL',
        'ytd_return': 'REAL', 'total_assets': 'REAL', 'nav_price': 'REAL',
        'expense_ratio': 'REAL', 'top_holdings': 'TEXT', 'sector_weightings': 'TEXT',
        'dividend_yield': 'REAL', 'ex_dividend_date': 'TEXT', 'target_price': 'REAL',
        'analyst_rating': 'TEXT', 'next_earnings_date': 'TEXT',
        'short_interest': 'REAL', 'institutional_ownership': 'REAL', 'beta': 'REAL',
        'yield_correlation': 'REAL', 'setup_tags': 'TEXT',
        'ml_confidence': 'REAL', 'score_method': 'TEXT DEFAULT "HARDCODED"',
        'piotroski_f_score': 'REAL', 'altman_z_score': 'REAL',
        'beneish_m_score': 'REAL', 'forensic_last_updated': 'TEXT'
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
        'vp_poc': 'REAL',
        'vp_val': 'REAL',
        'vp_vah': 'REAL',
        'vp_entry_zone': 'REAL',
        'vp_exit_zone': 'REAL',
        'kc_z_score': 'REAL',
        'kc_entry_signal': 'INTEGER',
        'kc_exit_signal': 'INTEGER',
        'price_q10': 'REAL',
        'price_q90': 'REAL',
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
        'ai_hmm_state': 'INTEGER',
        'price_hmm_state': 'INTEGER',
        'price_hmm_label': 'TEXT',
        'price_hmm_prob': 'REAL',
        'market_stress_score': 'REAL',
        'market_stress_features': 'TEXT',
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

    # price_hmm_states (guard for pre-feature DBs)
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_hmm_states (
                date        TEXT PRIMARY KEY,
                state       INTEGER NOT NULL,
                label       TEXT NOT NULL,
                probability REAL NOT NULL
            )
        ''')
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to create price_hmm_states: %s", e)

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
                expire_date  DATE NOT NULL DEFAULT (date('now')),
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

    # intraday_monitors — add expire_date if missing (pre-feature DBs)
    cursor.execute("PRAGMA table_info(intraday_monitors)")
    _im_cols = [r['name'] for r in cursor.fetchall()]
    if 'expire_date' not in _im_cols:
        try:
            cursor.execute("ALTER TABLE intraday_monitors ADD COLUMN expire_date DATE")
            cursor.execute("UPDATE intraday_monitors SET expire_date = date_added WHERE expire_date IS NULL")
            logger.info("[MIGRATION] Added expire_date to intraday_monitors.")
        except Exception as e:
            logger.error("[MIGRATION ERROR] Failed adding expire_date to intraday_monitors: %s", e)

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

    # idx_qs_ticker_date exactly duplicates the quant_signals(ticker, date) PK index — drop it
    try:
        cursor.execute("DROP INDEX IF EXISTS idx_qs_ticker_date")
        logger.info("[MIGRATION] Dropped redundant index idx_qs_ticker_date (duplicates PK).")
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to drop idx_qs_ticker_date: %s", e)

    # macro_calendar: event_date is range-filtered in 6+ call sites and was unindexed
    try:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_macro_event_date
            ON macro_calendar(event_date)
        """)
        logger.info("[MIGRATION] Verified index idx_macro_event_date on macro_calendar(event_date).")
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to create idx_macro_event_date: %s", e)

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

    # DATA MIGRATION: nullify legacy raw CPIAUCSL index values stored in us_cpi_inflation.
    # Commit c1761b3 introduced YoY% conversion but lacked 12-month lookback for the first
    # ~12 months of the 730-day fetch window, leaving raw index values (~313-320) in those
    # rows instead of YoY% (~2-4%). CPI YoY% has never exceeded 20% in the modern era;
    # anything above 20 is a corrupt artefact. Rows nullified here are re-downloaded with
    # correct values on the next macro_data_engine run.
    try:
        cursor.execute("SELECT COUNT(*) FROM macro_indicators WHERE us_cpi_inflation > 20")
        bad_count = cursor.fetchone()[0]
        if bad_count > 0:
            logger.info("[MIGRATION] Nullifying %s corrupted us_cpi_inflation rows (raw index values > 20).", bad_count)
            cursor.execute("UPDATE macro_indicators SET us_cpi_inflation=NULL WHERE us_cpi_inflation > 20")
    except Exception as e:
        logger.error("[MIGRATION ERROR] CPI corruption cleanup: %s", e)

    try:
        conn.commit()
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to commit migration changes: %s", e)
        conn.rollback()
