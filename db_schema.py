import json
import os
import logging

from database import get_connection
from config import BASE_CURRENCY, WATCHLIST_PATH
import time_engine
import learn_cards_seed

logger = logging.getLogger(__name__)

_EXCHANGE_HOURS_PATH = os.path.join(os.path.dirname(__file__), "data", "exchange_hours.json")

_DEFAULT_EXCHANGE_HOURS = {
    "NYSE":    {"open":"09:30","close":"16:00","tz":"America/New_York",     "currency":"USD","suffixes":[],"premarket_open":"04:00"},
    "LSE":     {"open":"08:00","close":"16:30","tz":"Europe/London",        "currency":"GBP","suffixes":[".L"],"quote_delay_minutes":15},
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
    "BOVESPA": {"open":"10:00","close":"18:00","tz":"America/Sao_Paulo",    "currency":"BRL","suffixes":[".SA"]},
    "BMV":     {"open":"08:30","close":"15:00","tz":"America/Mexico_City",  "currency":"MXN","suffixes":[".MX"]},
    "Euronext":{"open":"09:00","close":"17:30","tz":"Europe/Paris",         "currency":"EUR","suffixes":[".PA",".AS",".BR",".LS"]},
    "SIX":     {"open":"09:00","close":"17:30","tz":"Europe/Zurich",        "currency":"CHF","suffixes":[".SW"]},
    "MIL":     {"open":"09:00","close":"17:30","tz":"Europe/Rome",          "currency":"EUR","suffixes":[".MI"]},
    "BME":     {"open":"09:00","close":"17:30","tz":"Europe/Madrid",        "currency":"EUR","suffixes":[".MC"]},
    "OMXS":    {"open":"09:00","close":"17:30","tz":"Europe/Stockholm",     "currency":"SEK","suffixes":[".ST"]},
    "OMXH":    {"open":"10:00","close":"18:30","tz":"Europe/Helsinki",      "currency":"EUR","suffixes":[".HE"]},
    "OMXC":    {"open":"09:00","close":"17:00","tz":"Europe/Copenhagen",    "currency":"DKK","suffixes":[".CO"]},
    "OSE":     {"open":"09:00","close":"16:20","tz":"Europe/Oslo",          "currency":"NOK","suffixes":[".OL"]},
    "WBAG":    {"open":"09:00","close":"17:30","tz":"Europe/Vienna",        "currency":"EUR","suffixes":[".VI"]},
    "WSE":     {"open":"09:00","close":"17:00","tz":"Europe/Warsaw",        "currency":"PLN","suffixes":[".WA"]},
    "JSE":     {"open":"09:00","close":"17:00","tz":"Africa/Johannesburg",  "currency":"ZAR","suffixes":[".JO"]},
    "TASE":    {"open":"09:59","close":"17:15","tz":"Asia/Jerusalem",       "currency":"ILS","suffixes":[".TA"]},
    "Tadawul": {"open":"10:00","close":"15:00","tz":"Asia/Riyadh",          "currency":"SAR","suffixes":[".SR"]},
}


def _seed_exchange_hours_json() -> None:
    if not os.path.exists(_EXCHANGE_HOURS_PATH):
        try:
            os.makedirs(os.path.dirname(_EXCHANGE_HOURS_PATH), exist_ok=True)
            with open(_EXCHANGE_HOURS_PATH, "w", encoding="utf-8") as f:
                json.dump(_DEFAULT_EXCHANGE_HOURS, f, indent=2)
            logger.info("Created default exchange_hours.json at %s", _EXCHANGE_HOURS_PATH)
        except Exception as exc:
            logger.warning("Could not write exchange_hours.json: %s", exc)
        # time_engine caches its exchange registry at import time, which on a fresh install
        # happens before this function ever runs (main.py/conftest.py import chains reach
        # time_engine before calling init_db()) — without this, time_engine stays pinned to
        # its incomplete built-in fallback (NYSE/LSE/XETRA/TSE only) for the rest of the process.
        time_engine.reload_exchange_registry()
        return

    # Backfill exchanges/fields added to _DEFAULT_EXCHANGE_HOURS after this file was first
    # seeded on this install (e.g. LSE's quote_delay_minutes) — never overwrites a value
    # already present, so any operator edit to an existing field survives.
    try:
        with open(_EXCHANGE_HOURS_PATH, "r", encoding="utf-8") as f:
            current = json.load(f)
        changed = False
        for exchange, defaults in _DEFAULT_EXCHANGE_HOURS.items():
            existing = current.setdefault(exchange, {})
            for key, value in defaults.items():
                if key not in existing:
                    existing[key] = value
                    changed = True
        if changed:
            with open(_EXCHANGE_HOURS_PATH, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=2)
            logger.info("Backfilled new default fields into exchange_hours.json")
    except Exception as exc:
        logger.warning("Could not backfill exchange_hours.json: %s", exc)
    time_engine.reload_exchange_registry()


# Seed rows for market_ticker_registry — the single source of truth for every index/commodity/FX
# ticker tracked by the Markets page and Market Pulse widget (replaces the old hardcoded
# market_pulse.INDEX_TICKERS dict). Columns:
# (ticker, display_name, region, asset_type, exchange, currency, future_ticker,
#  future_display_name, invert_color, is_pulse_tile, pulse_sort_order, is_pulse_mobile,
#  sort_order, context_blurb, baseline_parquet)
_MARKET_TICKER_REGISTRY_SEED = [
    # The first 10 rows (today's INDEX_TICKERS) carry is_pulse_tile=1 so a fresh init_db()
    # reproduces today's exact Market Pulse tile set with no behavior change until the user edits it.
    ("^FTSE", "UK FTSE 100", "Europe", "Index", "LSE", "GBP", None, None, 0, 1, 0, 1, 1,
     "The FTSE 100 tracks the 100 largest companies on the London Stock Exchange. Heavily weighted to mining, energy, and banks; often moves inversely to GBP strength.",
     "FTSE_BASELINE.parquet"),
    ("^FTMC", "UK FTSE 250", "Europe", "Index", "LSE", "GBP", None, None, 0, 1, 1, 1, 2,
     "The FTSE 250 tracks mid-cap UK companies (ranks 101–350 on LSE). More domestically driven than the FTSE 100 — a purer barometer of UK economic health.",
     None),
    ("GBPUSD=X", "GBP/USD", "Commodities_FX", "FX", None, "USD", None, None, 0, 1, 2, 1, 0,
     "GBP/USD exchange rate. Weakness boosts FTSE 100 exporters' translated earnings; strength signals UK economic confidence and tighter BoE policy expectations.",
     "GBPUSD_BASELINE.parquet"),
    ("BZ=F", "Brent Crude", "Commodities_FX", "Commodity", None, "USD", None, None, 1, 1, 3, 1, 4,
     "Brent Crude Oil futures — the global benchmark for oil pricing. Elevated prices raise input costs across the economy and pressure rate-sensitive equities.",
     None),
    ("UK10YG", "UK 10Y Gilt", "Europe", "Rate", None, "GBP", None, None, 1, 1, 4, 0, 3,
     "The UK 10-Year Gilt Yield reflects sovereign borrowing costs and BoE monetary policy expectations. Rising yields compress equity multiples and increase corporate financing costs.",
     "UK_GILT_BASELINE.parquet"),
    ("^GSPC", "US S&P 500", "US", "Index", "NYSE", "USD", "ES=F", "S&P 500 Futures", 0, 1, 5, 1, 0,
     "The S&P 500 tracks 500 large-cap US equities — the primary benchmark for US equity market health and the foundation of most global asset allocation frameworks.",
     "SP500_BASELINE.parquet"),
    ("^NDX", "US Nasdaq 100", "US", "Index", "NYSE", "USD", "NQ=F", "Nasdaq 100 Futures", 0, 1, 6, 1, 1,
     "The Nasdaq 100 tracks the 100 largest non-financial companies on Nasdaq. Tech-heavy and highly sensitive to real interest rate expectations and liquidity conditions.",
     None),
    ("^TYX", "US 30Y Yield", "US", "Rate", None, "USD", None, None, 1, 1, 7, 0, 5,
     "The US 30-Year Treasury Yield gauges long-term US borrowing costs and inflation expectations. Directly impacts mortgage rates and long-duration equity discount rates.",
     "TYX_BASELINE.parquet"),
    ("^TNX", "US 10Y Yield", "US", "Rate", None, "USD", None, None, 1, 1, 8, 1, 6,
     "The US 10-Year Treasury Yield is the global risk-free rate benchmark. Rising yields tighten financial conditions, compress equity multiples, and strengthen the US Dollar.",
     "TNX_BASELINE.parquet"),
    ("DX-Y.NYB", "US Dollar Index", "Commodities_FX", "FX", None, "USD", None, None, 1, 1, 9, 1, 5,
     "The US Dollar Index (DXY) measures USD strength vs a basket of major currencies. A rising DXY tightens global dollar liquidity and pressures commodities and EM assets.",
     "DXY_BASELINE.parquet"),

    ("GC=F", "Gold", "Commodities_FX", "Commodity", None, "USD", None, None, 0, 0, 0, 1, 0,
     "Safe-haven flow and real-yield proxy.", None),
    ("SI=F", "Silver", "Commodities_FX", "Commodity", None, "USD", None, None, 0, 0, 0, 1, 1,
     "High-beta precious metal with meaningful industrial demand.", None),
    ("HG=F", "Copper", "Commodities_FX", "Commodity", None, "USD", None, None, 0, 0, 0, 1, 2,
     "\"Dr. Copper\" — a leading indicator for global economic expansion.", None),
    ("CL=F", "WTI Crude", "Commodities_FX", "Commodity", None, "USD", None, None, 1, 0, 0, 1, 3,
     "US energy benchmark; feeds domestic demand and supply-chain cost signals.", None),

    ("^N225", "Nikkei 225", "Asia", "Index", "TSE", "JPY", "NIY=F", "Nikkei 225 Futures", 0, 0, 0, 1, 0,
     "Japan's headline equity index.", None),
    ("^HSI", "Hang Seng Index", "Asia", "Index", "HKEX", "HKD", None, None, 0, 0, 0, 1, 1,
     "Hong Kong's benchmark index, a proxy for Greater China risk appetite.", None),
    ("000001.SS", "Shanghai Composite", "Asia", "Index", "SSE", "CNY", None, None, 0, 0, 0, 1, 2,
     "Mainland China's headline equity index.", None),
    ("^AXJO", "S&P/ASX 200", "Asia", "Index", "ASX", "AUD", None, None, 0, 0, 0, 1, 3,
     "Australia's benchmark equity index.", None),

    ("^STOXX50E", "Euro Stoxx 50", "Europe", "Index", "Euronext", "EUR", None, None, 0, 0, 0, 1, 0,
     "Eurozone blue-chip benchmark spanning the bloc's largest companies.", None),
    ("^GDAXI", "DAX", "Europe", "Index", "XETRA", "EUR", None, None, 0, 0, 0, 1, 2,
     "Germany's headline equity index.", None),
    ("^FCHI", "CAC 40", "Europe", "Index", "Euronext", "EUR", None, None, 0, 0, 0, 1, 3,
     "France's headline equity index.", None),
    ("EURUSD=X", "EUR/USD", "Commodities_FX", "FX", None, "USD", None, None, 0, 0, 0, 1, 2,
     "Euro/Dollar exchange rate — a risk proxy for eurozone assets.", None),

    ("^DJI", "Dow Jones Industrial", "US", "Index", "NYSE", "USD", "YM=F", "Dow Jones Futures", 0, 0, 0, 1, 2,
     "Price-weighted index of 30 large US industrial/blue-chip companies.", None),
    ("^RUT", "Russell 2000", "US", "Index", "NYSE", "USD", "RTY=F", "Russell 2000 Futures", 0, 0, 0, 1, 3,
     "Small-cap US equity benchmark.", None),
    ("^VIX", "CBOE Volatility Index", "US", "Volatility", "NYSE", "USD", None, None, 1, 0, 0, 1, 4,
     "The market's expected 30-day volatility, derived from S&P 500 options pricing.", None),
]


def _seed_market_ticker_registry(cursor) -> None:
    for row in _MARKET_TICKER_REGISTRY_SEED:
        cursor.execute('''
            INSERT OR IGNORE INTO market_ticker_registry (
                ticker, display_name, region, asset_type, exchange, currency,
                future_ticker, future_display_name, invert_color, is_pulse_tile,
                pulse_sort_order, is_pulse_mobile, sort_order, context_blurb, baseline_parquet
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', row)


def _seed_learn_cards(cursor) -> None:
    level_order = {section_id: i + 1 for i, (section_id, _) in enumerate(learn_cards_seed.LEVELS)}
    seeded_keys = []
    for card in learn_cards_seed.CARDS:
        seeded_keys.append(card["term_key"])
        cursor.execute('''
            INSERT INTO learn_cards (
                term_key, section_id, level_order, term_title, question, answer, distractors,
                explanation, candle_html
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(term_key) DO UPDATE SET
                section_id=excluded.section_id, level_order=excluded.level_order,
                term_title=excluded.term_title, question=excluded.question,
                answer=excluded.answer, distractors=excluded.distractors,
                explanation=excluded.explanation, candle_html=excluded.candle_html,
                updated_at=datetime('now')
        ''', (
            card["term_key"], card["section_id"], level_order[card["section_id"]],
            card["term_title"], card["question"], card["answer"], json.dumps(card["distractors"]),
            card["explanation"], card.get("candle_html")
        ))
    if seeded_keys:
        placeholders = ",".join("?" for _ in seeded_keys)
        cursor.execute(f"DELETE FROM learn_cards WHERE term_key NOT IN ({placeholders})", seeded_keys)
        cursor.execute(f"DELETE FROM learn_term_state WHERE term_key NOT IN ({placeholders})", seeded_keys)


DEFAULT_PENSION_BENCHMARK_TICKERS = [
    ("URTH", "MSCI World Index"),
    ("VWRL.L", "FTSE All-World Index"),
]


def _seed_pension_benchmark_defaults(cursor) -> None:
    cursor.execute(
        "SELECT id FROM accounts WHERE account_type = 'Pension' AND deleted_at IS NULL "
        "AND id NOT IN (SELECT DISTINCT account_id FROM account_benchmark_tickers)"
    )
    for row in cursor.fetchall():
        for sort_order, (ticker, display_name) in enumerate(DEFAULT_PENSION_BENCHMARK_TICKERS):
            cursor.execute(
                "INSERT OR IGNORE INTO account_benchmark_tickers (account_id, ticker, display_name, sort_order) "
                "VALUES (?, ?, ?, ?)",
                (row["id"], ticker, display_name, sort_order)
            )


def _ensure_watchlist_account() -> None:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM accounts WHERE account_type = 'Watchlist' AND deleted_at IS NULL LIMIT 1")
        if cursor.fetchone() is None:
            cursor.execute(
                "INSERT INTO accounts (name, currency, initial_cash, account_type) VALUES (?, ?, 0, 'Watchlist')",
                ("Watchlist", BASE_CURRENCY)
            )
            conn.commit()
            logger.info("Created default Watchlist account.")
    except Exception as e:
        logger.error("Failed to ensure Watchlist account exists: %s", e)
    finally:
        if conn:
            conn.close()


def _import_legacy_watchlist_json() -> None:
    """One-time import: watchlist_items replaces watchlist.json as the watchlist's source of truth."""
    if not os.path.exists(WATCHLIST_PATH):
        return
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM watchlist_items")
        if cursor.fetchone()[0] != 0:
            return
        with open(WATCHLIST_PATH) as f:
            tickers = json.load(f).get("watchlist", [])
        cursor.execute("SELECT id FROM accounts WHERE account_type = 'Watchlist' AND deleted_at IS NULL LIMIT 1")
        wl_row = cursor.fetchone()
        if not wl_row or not tickers:
            return
        logger.info("[MIGRATION] Importing %s tickers from watchlist.json into watchlist_items...", len(tickers))
        for ticker in tickers:
            cursor.execute(
                "SELECT company_name, currency, quote_type FROM stock_signals WHERE ticker = ?", (ticker,)
            )
            cached = cursor.fetchone()
            company_name = cached["company_name"] if cached else None
            currency = cached["currency"] if cached else None
            quote_type = cached["quote_type"] if cached else None
            exchange = time_engine.ticker_exchange(ticker, currency) if currency else None
            cursor.execute(
                """INSERT OR IGNORE INTO watchlist_items
                       (account_id, ticker, company_name, currency, quote_type, exchange)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (wl_row["id"], ticker, company_name, currency, quote_type, exchange)
            )
        conn.commit()
    except Exception as e:
        logger.error("[MIGRATION ERROR] watchlist.json import failed: %s", e)
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
                holdings_updated_at TEXT,

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
                last_updated REAL,
                market_state TEXT
            )
        ''')

        # Single source of truth for every index/commodity/FX ticker tracked anywhere in the
        # app (Markets page + Market Pulse) — see AGENTS.md central-engine rule for ticker data.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_ticker_registry (
                ticker               TEXT PRIMARY KEY,
                display_name         TEXT NOT NULL,
                region               TEXT NOT NULL,
                asset_type           TEXT NOT NULL,
                exchange             TEXT,
                currency             TEXT NOT NULL DEFAULT 'USD',
                future_ticker        TEXT,
                future_display_name  TEXT,
                invert_color         INTEGER NOT NULL DEFAULT 0,
                is_pulse_tile        INTEGER NOT NULL DEFAULT 0,
                pulse_sort_order     INTEGER NOT NULL DEFAULT 0,
                is_pulse_mobile      INTEGER NOT NULL DEFAULT 1,
                sort_order           INTEGER NOT NULL DEFAULT 0,
                enabled              INTEGER NOT NULL DEFAULT 1,
                context_blurb        TEXT,
                baseline_parquet     TEXT,
                created_at           TEXT DEFAULT (datetime('now')),
                updated_at           TEXT DEFAULT (datetime('now'))
            )
        ''')

        # Today's-session intraday points per ticker, feeding the Markets page mini sparkline.
        # Full replace on each fetch cycle (see market_pulse.fetch_and_save_pulse) — rows are
        # left untouched when the market is closed so the last session's line persists.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_pulse_sparkline (
                ticker TEXT NOT NULL,
                ts     REAL NOT NULL,
                price  REAL NOT NULL,
                PRIMARY KEY (ticker, ts)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_market_pulse_sparkline_ticker ON market_pulse_sparkline(ticker)')

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
            CREATE TABLE IF NOT EXISTS treasury_auction_results (
                cusip          TEXT NOT NULL,
                maturity_label TEXT NOT NULL,
                auction_date   TEXT NOT NULL,
                high_yield     REAL,
                bid_to_cover   REAL,
                tail_bp        REAL,
                direct_pct     REAL,
                indirect_pct   REAL,
                dealer_pct     REAL,
                offering_amt   REAL,
                alert_fired    INTEGER DEFAULT 0,
                PRIMARY KEY (cusip, auction_date)
            )
        ''')

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_treasury_auction_date "
            "ON treasury_auction_results(auction_date)"
        )

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

        # Orphaned (2026-06-29) — superseded by xray_returns_cache below, which stores per-ticker
        # series so a weighted return series can be derived for any account scope, not just a
        # Ghostfolio-only global one. Table kept (not dropped) per this codebase's convention for
        # superseded cache tables (see smgb_predictions); no code reads or writes it anymore.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS xray_portfolio_returns_cache (
                benchmark TEXT PRIMARY KEY,
                last_updated TEXT NOT NULL,
                dates_json TEXT NOT NULL,
                returns_json TEXT NOT NULL,
                benchmark_returns_json TEXT NOT NULL
            )
        ''')

        # Per-ticker daily return series (same data already fetched for beta/vol/correlation in
        # XRayRiskComputer.compute_and_cache) so assemble_xray_report can derive a weighted
        # portfolio return series for ANY account scope at request time, with no live yfinance
        # call and no Ghostfolio dependency.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS xray_returns_cache (
                ticker TEXT NOT NULL,
                benchmark TEXT NOT NULL,
                last_updated TEXT NOT NULL,
                dates_json TEXT NOT NULL,
                returns_json TEXT NOT NULL,
                PRIMARY KEY (ticker, benchmark)
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
                bias_corrected_price       REAL,
                bias_corrected_change_pct  REAL,
                blended_price              REAL,
                blended_change_pct         REAL,
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
            CREATE TABLE IF NOT EXISTS pairs_spread_results (
                pair_key      TEXT PRIMARY KEY,
                scope         TEXT NOT NULL DEFAULT 'portfolio_watchlist',
                ticker_a      TEXT NOT NULL,
                ticker_b      TEXT NOT NULL,
                currency      TEXT,
                correlation   REAL,
                zscore        REAL,
                spread_mean   REAL,
                spread_std    REAL,
                last_spread   REAL,
                direction     TEXT,
                scan_ts       TEXT NOT NULL
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

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS yahoo_api_stats (
                date                  TEXT PRIMARY KEY,
                total_calls           INTEGER NOT NULL DEFAULT 0,
                ipv4_calls            INTEGER NOT NULL DEFAULT 0,
                ipv6_calls            INTEGER NOT NULL DEFAULT 0,
                rate_limit_429        INTEGER NOT NULL DEFAULT 0,
                other_errors          INTEGER NOT NULL DEFAULT 0,
                yfinance_logged_errors INTEGER NOT NULL DEFAULT 0
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS yahoo_api_call_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                call_time        TEXT NOT NULL,
                date             TEXT NOT NULL,
                interface        TEXT NOT NULL,
                status           TEXT NOT NULL,
                job_id           TEXT,
                action_context   TEXT,
                yf_logged_errors INTEGER NOT NULL DEFAULT 0
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_yahoo_api_call_log_date ON yahoo_api_call_log(date)')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                currency        TEXT NOT NULL,
                initial_cash    REAL NOT NULL DEFAULT 0,
                note            TEXT,
                opened_date     TEXT,
                account_type    TEXT NOT NULL DEFAULT 'Trading',
                scraper_url      TEXT,
                scraper_selector TEXT,
                scraper_headers  TEXT NOT NULL DEFAULT '{}',
                scrape_time      TEXT NOT NULL DEFAULT '02:00',
                scraper_enabled  INTEGER NOT NULL DEFAULT 0,
                pension_start_date TEXT,
                opening_balance_units REAL,
                opening_balance_txn_id INTEGER,
                pension_ticker_label TEXT,
                autotopup_enabled INTEGER NOT NULL DEFAULT 0,
                autotopup_amount REAL,
                autotopup_frequency TEXT,
                autotopup_day_of_month INTEGER,
                autotopup_day_of_week INTEGER,
                autotopup_notes TEXT,
                benchmark_cpi_target_pct REAL NOT NULL DEFAULT 4.0,
                deleted_at      TEXT DEFAULT NULL,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_transactions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                txn_type        TEXT NOT NULL,
                ticker          TEXT,
                isin            TEXT,
                company_name    TEXT,
                currency        TEXT,
                txn_date        TEXT NOT NULL,
                quantity        REAL,
                unit_price      REAL,
                fee             REAL NOT NULL DEFAULT 0,
                exchange_rate   REAL,
                fee_currency    TEXT,
                fee_exchange_rate REAL,
                notes           TEXT,
                update_cash     INTEGER NOT NULL DEFAULT 1,
                price_in_pence  INTEGER NOT NULL DEFAULT 0,
                ghostfolio_ref  TEXT,
                linked_txn_id   INTEGER,
                is_adjustment   INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_value_history (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id        INTEGER NOT NULL,
                snapshot_date     TEXT NOT NULL,
                total_value       REAL,
                cash_value        REAL,
                equity_value      REAL,
                net_contributions REAL,
                UNIQUE(account_id, snapshot_date)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_price_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL,
                price_date  TEXT NOT NULL,
                price       REAL NOT NULL,
                source      TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                UNIQUE(account_id, price_date)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_benchmark_tickers (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id    INTEGER NOT NULL,
                ticker        TEXT NOT NULL,
                display_name  TEXT NOT NULL,
                sort_order    INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now')),
                UNIQUE(account_id, ticker)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_value_history_currency (
                account_id            INTEGER NOT NULL,
                snapshot_date         TEXT NOT NULL,
                currency              TEXT NOT NULL,
                equity_value_native   REAL NOT NULL,
                equity_value_base     REAL NOT NULL,
                fx_rate               REAL NOT NULL,
                PRIMARY KEY (account_id, snapshot_date, currency)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_performance_cache (
                account_id      INTEGER PRIMARY KEY,
                total_value     REAL,
                equity_value    REAL,
                cash_balance    REAL,
                unrealized_pnl  REAL,
                return_1d       REAL,
                return_1w       REAL,
                return_1m       REAL,
                return_3m       REAL,
                return_6m       REAL,
                return_1y       REAL,
                mwrr            REAL,
                realized_pnl    REAL,
                dividend_income REAL,
                interest_income REAL,
                last_updated    REAL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_autotopup_pending (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                scheduled_date  TEXT NOT NULL,
                expected_amount REAL NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                confirmed_amount REAL,
                confirmed_date  TEXT,
                txn_id          INTEGER,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backup_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at      TEXT NOT NULL,
                finished_at     TEXT,
                trigger_type    TEXT NOT NULL,
                location_type   TEXT NOT NULL,
                destination     TEXT,
                components      TEXT,
                filename        TEXT,
                size_bytes      INTEGER,
                status          TEXT NOT NULL,
                error_message   TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS watchlist_items (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id    INTEGER NOT NULL,
                ticker        TEXT NOT NULL,
                company_name  TEXT,
                currency      TEXT,
                quote_type    TEXT,
                exchange      TEXT,
                added_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(account_id, ticker)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS holding_price_limits (
                account_id  INTEGER NOT NULL,
                ticker      TEXT NOT NULL,
                low_limit   REAL,
                high_limit  REAL,
                updated_at  TEXT,
                PRIMARY KEY (account_id, ticker)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS treasury_bills (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                buy_txn_id      INTEGER NOT NULL,
                ticker          TEXT NOT NULL UNIQUE,
                face_value      REAL NOT NULL,
                purchase_price  REAL NOT NULL,
                indicative_ytm  REAL,
                ytm_confirmed   INTEGER NOT NULL DEFAULT 0,
                purchase_date   TEXT NOT NULL,
                maturity_date   TEXT NOT NULL,
                auto_reinvest   INTEGER NOT NULL DEFAULT 0,
                status          TEXT NOT NULL DEFAULT 'Open',
                maturity_txn_id INTEGER,
                notes           TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_treasury_bills_account ON treasury_bills(account_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_treasury_bills_status_maturity ON treasury_bills(status, maturity_date)')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS learn_cards (
                term_key     TEXT PRIMARY KEY,
                section_id   TEXT NOT NULL,
                level_order  INTEGER NOT NULL,
                term_title   TEXT NOT NULL,
                question     TEXT NOT NULL,
                answer       TEXT NOT NULL,
                distractors  TEXT NOT NULL,
                explanation  TEXT NOT NULL DEFAULT '',
                candle_html  TEXT,
                updated_at   TEXT DEFAULT (datetime('now'))
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS learn_term_state (
                term_key         TEXT PRIMARY KEY,
                box              INTEGER NOT NULL DEFAULT 0,
                due_at           TEXT,
                correct_streak   INTEGER NOT NULL DEFAULT 0,
                lapses           INTEGER NOT NULL DEFAULT 0,
                total_reviews    INTEGER NOT NULL DEFAULT 0,
                last_result      TEXT,
                last_reviewed_at TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_learn_state_due ON learn_term_state(due_at)')

        conn.commit()

        migrate_db(conn, cursor)
        _seed_exchange_hours_json()
        _seed_market_ticker_registry(cursor)
        _seed_learn_cards(cursor)
        conn.commit()
        _ensure_watchlist_account()
        _import_legacy_watchlist_json()

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

    cursor.execute("PRAGMA table_info(market_pulse_cache)")
    existing_pulse_columns = [info['name'] for info in cursor.fetchall()]
    if 'market_state' not in existing_pulse_columns:
        try:
            logger.info("[MIGRATION] Adding column: market_state to market_pulse_cache...")
            cursor.execute("ALTER TABLE market_pulse_cache ADD COLUMN market_state TEXT")
        except Exception as e:
            logger.error("[MIGRATION ERROR] Failed on market_pulse_cache: %s", e)

    cursor.execute("PRAGMA table_info(accounts)")
    existing_account_columns = [info['name'] for info in cursor.fetchall()]
    if 'opened_date' not in existing_account_columns:
        try:
            logger.info("[MIGRATION] Adding column: opened_date to accounts...")
            cursor.execute("ALTER TABLE accounts ADD COLUMN opened_date TEXT")
        except Exception as e:
            logger.error("[MIGRATION ERROR] Failed on accounts: %s", e)

    if 'account_type' not in existing_account_columns:
        try:
            logger.info("[MIGRATION] Adding column: account_type to accounts...")
            cursor.execute("ALTER TABLE accounts ADD COLUMN account_type TEXT NOT NULL DEFAULT 'Trading'")
        except Exception as e:
            logger.error("[MIGRATION ERROR] Failed on accounts: %s", e)

    for col, ddl in (
        ('scraper_url', "ALTER TABLE accounts ADD COLUMN scraper_url TEXT"),
        ('scraper_selector', "ALTER TABLE accounts ADD COLUMN scraper_selector TEXT"),
        ('scraper_headers', "ALTER TABLE accounts ADD COLUMN scraper_headers TEXT NOT NULL DEFAULT '{}'"),
        ('scrape_time', "ALTER TABLE accounts ADD COLUMN scrape_time TEXT NOT NULL DEFAULT '02:00'"),
        ('scraper_enabled', "ALTER TABLE accounts ADD COLUMN scraper_enabled INTEGER NOT NULL DEFAULT 0"),
        ('pension_start_date', "ALTER TABLE accounts ADD COLUMN pension_start_date TEXT"),
        ('opening_balance_units', "ALTER TABLE accounts ADD COLUMN opening_balance_units REAL"),
        ('opening_balance_txn_id', "ALTER TABLE accounts ADD COLUMN opening_balance_txn_id INTEGER"),
        ('pension_ticker_label', "ALTER TABLE accounts ADD COLUMN pension_ticker_label TEXT"),
        ('autotopup_enabled', "ALTER TABLE accounts ADD COLUMN autotopup_enabled INTEGER NOT NULL DEFAULT 0"),
        ('autotopup_amount', "ALTER TABLE accounts ADD COLUMN autotopup_amount REAL"),
        ('autotopup_frequency', "ALTER TABLE accounts ADD COLUMN autotopup_frequency TEXT"),
        ('autotopup_day_of_month', "ALTER TABLE accounts ADD COLUMN autotopup_day_of_month INTEGER"),
        ('autotopup_day_of_week', "ALTER TABLE accounts ADD COLUMN autotopup_day_of_week INTEGER"),
        ('autotopup_notes', "ALTER TABLE accounts ADD COLUMN autotopup_notes TEXT"),
        ('benchmark_cpi_target_pct', "ALTER TABLE accounts ADD COLUMN benchmark_cpi_target_pct REAL NOT NULL DEFAULT 4.0"),
    ):
        if col not in existing_account_columns:
            try:
                logger.info("[MIGRATION] Adding column: %s to accounts...", col)
                cursor.execute(ddl)
            except Exception as e:
                logger.error("[MIGRATION ERROR] Failed on accounts: %s", e)

    cursor.execute("PRAGMA table_info(account_transactions)")
    existing_account_txn_columns = [info['name'] for info in cursor.fetchall()]
    if 'linked_txn_id' not in existing_account_txn_columns:
        try:
            logger.info("[MIGRATION] Adding column: linked_txn_id to account_transactions...")
            cursor.execute("ALTER TABLE account_transactions ADD COLUMN linked_txn_id INTEGER")
        except Exception as e:
            logger.error("[MIGRATION ERROR] Failed on account_transactions: %s", e)
    if 'isin' not in existing_account_txn_columns:
        try:
            logger.info("[MIGRATION] Adding column: isin to account_transactions...")
            cursor.execute("ALTER TABLE account_transactions ADD COLUMN isin TEXT")
        except Exception as e:
            logger.error("[MIGRATION ERROR] Failed on account_transactions: %s", e)
    if 'is_adjustment' not in existing_account_txn_columns:
        try:
            logger.info("[MIGRATION] Adding column: is_adjustment to account_transactions...")
            cursor.execute("ALTER TABLE account_transactions ADD COLUMN is_adjustment INTEGER NOT NULL DEFAULT 0")
        except Exception as e:
            logger.error("[MIGRATION ERROR] Failed on account_transactions: %s", e)
    if 'fee_currency' not in existing_account_txn_columns:
        try:
            logger.info("[MIGRATION] Adding column: fee_currency to account_transactions...")
            cursor.execute("ALTER TABLE account_transactions ADD COLUMN fee_currency TEXT")
        except Exception as e:
            logger.error("[MIGRATION ERROR] Failed on account_transactions: %s", e)
    if 'fee_exchange_rate' not in existing_account_txn_columns:
        try:
            logger.info("[MIGRATION] Adding column: fee_exchange_rate to account_transactions...")
            cursor.execute("ALTER TABLE account_transactions ADD COLUMN fee_exchange_rate REAL")
        except Exception as e:
            logger.error("[MIGRATION ERROR] Failed on account_transactions: %s", e)

    cursor.execute("PRAGMA table_info(account_value_history)")
    existing_account_value_columns = [info['name'] for info in cursor.fetchall()]
    if 'net_contributions' not in existing_account_value_columns:
        try:
            logger.info("[MIGRATION] Adding column: net_contributions to account_value_history...")
            cursor.execute("ALTER TABLE account_value_history ADD COLUMN net_contributions REAL")
        except Exception as e:
            logger.error("[MIGRATION ERROR] Failed on account_value_history: %s", e)

    cursor.execute("PRAGMA table_info(account_performance_cache)")
    existing_performance_cache_columns = [info['name'] for info in cursor.fetchall()]
    for col, ddl in (
        ('realized_pnl', "ALTER TABLE account_performance_cache ADD COLUMN realized_pnl REAL"),
        ('dividend_income', "ALTER TABLE account_performance_cache ADD COLUMN dividend_income REAL"),
        ('interest_income', "ALTER TABLE account_performance_cache ADD COLUMN interest_income REAL"),
    ):
        if col not in existing_performance_cache_columns:
            try:
                logger.info("[MIGRATION] Adding column: %s to account_performance_cache...", col)
                cursor.execute(ddl)
            except Exception as e:
                logger.error("[MIGRATION ERROR] Failed on account_performance_cache: %s", e)

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
        'holdings_updated_at': 'TEXT',
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS yahoo_api_stats (
                date                   TEXT PRIMARY KEY,
                total_calls            INTEGER NOT NULL DEFAULT 0,
                ipv4_calls             INTEGER NOT NULL DEFAULT 0,
                ipv6_calls             INTEGER NOT NULL DEFAULT 0,
                rate_limit_429         INTEGER NOT NULL DEFAULT 0,
                other_errors           INTEGER NOT NULL DEFAULT 0,
                yfinance_logged_errors INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
    except Exception as e:
        logger.debug("yahoo_api_stats migration: %s", e)

    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS yahoo_api_call_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                call_time        TEXT NOT NULL,
                date             TEXT NOT NULL,
                interface        TEXT NOT NULL,
                status           TEXT NOT NULL,
                job_id           TEXT,
                action_context   TEXT,
                yf_logged_errors INTEGER NOT NULL DEFAULT 0
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_yahoo_api_call_log_date ON yahoo_api_call_log(date)")
        conn.commit()
    except Exception as e:
        logger.debug("yahoo_api_call_log migration: %s", e)

    try:
        cursor.execute("PRAGMA table_info(yahoo_api_stats)")
        existing_yahoo_stats_columns = [info['name'] for info in cursor.fetchall()]
        if 'yfinance_logged_errors' not in existing_yahoo_stats_columns:
            logger.info("[MIGRATION] Adding column: yfinance_logged_errors to yahoo_api_stats...")
            cursor.execute("ALTER TABLE yahoo_api_stats ADD COLUMN yfinance_logged_errors INTEGER NOT NULL DEFAULT 0")
            conn.commit()
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to add yfinance_logged_errors to yahoo_api_stats: %s", e)

    try:
        cursor.execute("PRAGMA table_info(yahoo_api_call_log)")
        existing_yahoo_call_log_columns = [info['name'] for info in cursor.fetchall()]
        if 'yf_logged_errors' not in existing_yahoo_call_log_columns:
            logger.info("[MIGRATION] Adding column: yf_logged_errors to yahoo_api_call_log...")
            cursor.execute("ALTER TABLE yahoo_api_call_log ADD COLUMN yf_logged_errors INTEGER NOT NULL DEFAULT 0")
            conn.commit()
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to add yf_logged_errors to yahoo_api_call_log: %s", e)

    # account_value_history_currency (guard for pre-feature DBs)
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_value_history_currency (
                account_id            INTEGER NOT NULL,
                snapshot_date         TEXT NOT NULL,
                currency              TEXT NOT NULL,
                equity_value_native   REAL NOT NULL,
                equity_value_base     REAL NOT NULL,
                fx_rate               REAL NOT NULL,
                PRIMARY KEY (account_id, snapshot_date, currency)
            )
        ''')
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to create account_value_history_currency: %s", e)

    # holding_price_limits (guard for pre-feature DBs)
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS holding_price_limits (
                account_id  INTEGER NOT NULL,
                ticker      TEXT NOT NULL,
                low_limit   REAL,
                high_limit  REAL,
                updated_at  TEXT,
                PRIMARY KEY (account_id, ticker)
            )
        ''')
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to create holding_price_limits: %s", e)

    # treasury_bills (guard for pre-feature DBs)
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS treasury_bills (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                buy_txn_id      INTEGER NOT NULL,
                ticker          TEXT NOT NULL UNIQUE,
                face_value      REAL NOT NULL,
                purchase_price  REAL NOT NULL,
                indicative_ytm  REAL,
                ytm_confirmed   INTEGER NOT NULL DEFAULT 0,
                purchase_date   TEXT NOT NULL,
                maturity_date   TEXT NOT NULL,
                auto_reinvest   INTEGER NOT NULL DEFAULT 0,
                status          TEXT NOT NULL DEFAULT 'Open',
                maturity_txn_id INTEGER,
                notes           TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_treasury_bills_account ON treasury_bills(account_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_treasury_bills_status_maturity ON treasury_bills(status, maturity_date)')
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to create treasury_bills: %s", e)

    try:
        cursor.execute("PRAGMA table_info(treasury_bills)")
        existing_tbill_columns = [info['name'] for info in cursor.fetchall()]
        if 'indicative_ytm' not in existing_tbill_columns:
            logger.info("[MIGRATION] Adding column: indicative_ytm to treasury_bills...")
            cursor.execute("ALTER TABLE treasury_bills ADD COLUMN indicative_ytm REAL")
        if 'ytm_confirmed' not in existing_tbill_columns:
            logger.info("[MIGRATION] Adding column: ytm_confirmed to treasury_bills...")
            cursor.execute("ALTER TABLE treasury_bills ADD COLUMN ytm_confirmed INTEGER NOT NULL DEFAULT 0")
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to add indicative_ytm/ytm_confirmed to treasury_bills: %s", e)

    # account_benchmark_tickers (guard for pre-feature DBs)
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_benchmark_tickers (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id    INTEGER NOT NULL,
                ticker        TEXT NOT NULL,
                display_name  TEXT NOT NULL,
                sort_order    INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now')),
                UNIQUE(account_id, ticker)
            )
        ''')
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to create account_benchmark_tickers: %s", e)

    try:
        _seed_pension_benchmark_defaults(cursor)
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to seed pension benchmark defaults: %s", e)

    cursor.execute("PRAGMA table_info(etf_predictor_predictions)")
    existing_etf_prediction_columns = [info['name'] for info in cursor.fetchall()]
    for col, ddl in (
        ('bias_corrected_price', "ALTER TABLE etf_predictor_predictions ADD COLUMN bias_corrected_price REAL"),
        ('bias_corrected_change_pct', "ALTER TABLE etf_predictor_predictions ADD COLUMN bias_corrected_change_pct REAL"),
        ('blended_price', "ALTER TABLE etf_predictor_predictions ADD COLUMN blended_price REAL"),
        ('blended_change_pct', "ALTER TABLE etf_predictor_predictions ADD COLUMN blended_change_pct REAL"),
    ):
        if col not in existing_etf_prediction_columns:
            try:
                logger.info("[MIGRATION] Adding column: %s to etf_predictor_predictions...", col)
                cursor.execute(ddl)
            except Exception as e:
                logger.error("[MIGRATION ERROR] Failed on etf_predictor_predictions: %s", e)

    cursor.execute("PRAGMA table_info(learn_cards)")
    existing_learn_card_columns = [info['name'] for info in cursor.fetchall()]
    for col, ddl in (
        ('explanation', "ALTER TABLE learn_cards ADD COLUMN explanation TEXT NOT NULL DEFAULT ''"),
        ('candle_html', "ALTER TABLE learn_cards ADD COLUMN candle_html TEXT"),
    ):
        if col not in existing_learn_card_columns:
            try:
                logger.info("[MIGRATION] Adding column: %s to learn_cards...", col)
                cursor.execute(ddl)
            except Exception as e:
                logger.error("[MIGRATION ERROR] Failed on learn_cards: %s", e)

    try:
        cursor.execute("PRAGMA table_info(pairs_spread_results)")
        existing_pairs_spread_columns = [info['name'] for info in cursor.fetchall()]
        if 'scope' not in existing_pairs_spread_columns:
            logger.info("[MIGRATION] Adding column: scope to pairs_spread_results...")
            cursor.execute("ALTER TABLE pairs_spread_results ADD COLUMN scope TEXT NOT NULL DEFAULT 'portfolio_watchlist'")
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to add scope to pairs_spread_results: %s", e)

    try:
        conn.commit()
    except Exception as e:
        logger.error("[MIGRATION ERROR] Failed to commit migration changes: %s", e)
        conn.rollback()
