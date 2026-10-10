from database import get_connection

_SIGNAL_COLUMNS = """
                   (SELECT ml_confidence_score FROM quant_signals
                    WHERE ticker = s.ticker AND ml_confidence_score IS NOT NULL
                    ORDER BY date DESC LIMIT 1) AS ml_confidence_score,
                   (SELECT var_95 FROM quant_signals
                    WHERE ticker = s.ticker AND var_95 IS NOT NULL
                    ORDER BY date DESC LIMIT 1) AS var_95,
                   (SELECT cvar_95 FROM quant_signals
                    WHERE ticker = s.ticker AND cvar_95 IS NOT NULL
                    ORDER BY date DESC LIMIT 1) AS cvar_95,
                   (SELECT sentiment_score FROM quant_signals
                    WHERE ticker = s.ticker AND sentiment_score IS NOT NULL
                    ORDER BY date DESC LIMIT 1) AS sentiment_score,
                   q.atr_pct,
                   q.close_price as quant_close_price,
                   q.vp_entry_zone,
                   q.vp_exit_zone,
                   q.macd, q.macd_signal, q.macd_hist, q.sma_200,
                   q.week52_pct, q.anomaly_score, q.vp_poc, q.vp_val, q.vp_vah,
                   q.kc_z_score, q.kc_entry_signal, q.kc_exit_signal,
                   q.price_q10, q.price_q90, q.mom_1m, q.mom_3m, q.mom_6m, q.mom_12m_skip1m,
                   q.rel_strength_5d, q.rel_strength_20d, q.hist_vol_20, q.volume,
                   ap.industry, mu.index_membership,
                   tmeta.market_cap,
                   xrisk.beta AS xray_beta, xrisk.annualized_vol AS xray_annualized_vol,
                   (SELECT dividend_yield_pct FROM xray_dividend_cache
                    WHERE ticker = s.ticker ORDER BY last_updated DESC LIMIT 1) AS xray_dividend_yield,
                   ev.edge_score AS earnings_edge_score, ev.implied_move_pct AS earnings_implied_move,
                   trap.phase as trap_phase,
                   (SELECT flag FROM bubble_radar_metrics
                    WHERE ticker = s.ticker ORDER BY scan_date DESC LIMIT 1) AS bubble_flag,
                   COALESCE(
                       cno.display_name,
                       NULLIF(ap.company_name, s.ticker),
                       NULLIF(mu.company_name, s.ticker),
                       s.company_name,
                       s.ticker
                   ) as resolved_company_name"""

_SIGNAL_JOINS = """
            FROM stock_signals s
            LEFT JOIN asset_profiles ap ON s.ticker = ap.ticker
            LEFT JOIN market_universe mu ON s.ticker = mu.ticker
            LEFT JOIN company_name_overrides cno ON s.ticker = cno.ticker
            LEFT JOIN ticker_metadata tmeta ON s.ticker = tmeta.ticker
            LEFT JOIN quant_signals q ON s.ticker = q.ticker
            AND q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = s.ticker)
            LEFT JOIN xray_risk_cache xrisk ON s.ticker = xrisk.ticker AND xrisk.benchmark = ?
            LEFT JOIN earnings_volatility ev ON s.ticker = ev.ticker
            LEFT JOIN trap_monitor_results trap ON s.ticker = trap.ticker"""


def _select_signal_rows(cursor, benchmark_symbol: str, tickers: list[str], extra_columns: str, extra_joins: str = ""):
    rows = []
    # Leave room for the benchmark parameter under SQLite's older 999-variable limit.
    for start in range(0, len(tickers), 900):
        batch = tickers[start:start + 900]
        placeholders = ",".join("?" for _ in batch)
        cursor.execute(f"""
            SELECT s.*, {_SIGNAL_COLUMNS},
                   {extra_columns}
            {_SIGNAL_JOINS}
            {extra_joins}
            WHERE s.ticker IN ({placeholders})
        """, (benchmark_symbol, *batch))
        rows.extend(cursor.fetchall())
    return rows


def _global_updated(cursor):
    cursor.execute("SELECT MAX(last_updated) as global_updated FROM stock_signals")
    value = cursor.fetchone()['global_updated']
    return value if value else "Awaiting initial update..."


def fetch_portfolio_signal_rows(benchmark_symbol: str, tickers: list[str]):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        db_rows = _select_signal_rows(
            cursor, benchmark_symbol, tickers,
            "rc.risk_tier AS heat_index_tier",
            "LEFT JOIN ticker_risk_contribution rc ON s.ticker = rc.ticker",
        )

        cursor.execute("SELECT * FROM macro_regimes ORDER BY date DESC LIMIT 1")
        macro_row = cursor.fetchone()
        macro_regime = dict(macro_row) if macro_row else None

        global_updated = _global_updated(cursor)
    finally:
        if conn:
            conn.close()

    return db_rows, macro_regime, global_updated


def fetch_watchlist_signal_rows(benchmark_symbol: str, tickers: list[str]):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        db_rows = _select_signal_rows(cursor, benchmark_symbol, tickers, "mu.is_freetrade")

        global_updated = _global_updated(cursor)
    finally:
        if conn:
            conn.close()

    return db_rows, global_updated
