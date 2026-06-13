# insider_engine.py
import logging
import os
import json
import pandas as pd
from datetime import datetime, timedelta, timezone
from database import get_connection
from config import PORTFOLIO_PATH, WATCHLIST_PATH, load_config
from yahoo_engine import yahoo_engine
from notification_engine import notify

logger = logging.getLogger(__name__)

def get_tickers_from_json(filepath: str, is_watchlist: bool = False) -> list:
    """Safely extracts tickers from either portfolio.json or watchlist.json."""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
            if is_watchlist:
                return data.get("watchlist", [])
            else:
                return [v.get('ticker') for v in data.values() if v.get('ticker')]
    except Exception:
        return []

def run_insider_alert():
    """Scrapes recent SEC Form 4 filings for massive insider buying and aligns with quant scores."""
    try:
        logger.info("Starting Insider Trading Alert Check...")

        config = load_config()
        insider_cfg = config.get("NOTIFICATIONS", {}).get("INSIDER_TRADING", {})

        enable_portfolio = insider_cfg.get("ENABLED_PORTFOLIO", False)
        enable_watchlist = insider_cfg.get("ENABLED_WATCHLIST", False)
        min_value = int(insider_cfg.get("MIN_VALUE", 50000))
        days_back = int(insider_cfg.get("DAYS_BACK", 7))

        if not enable_portfolio and not enable_watchlist:
            return True, "Insider checks skipped (Both toggles disabled)."

        target_tickers = set()
        if enable_portfolio:
            target_tickers.update(get_tickers_from_json(PORTFOLIO_PATH, False))
        if enable_watchlist:
            target_tickers.update(get_tickers_from_json(WATCHLIST_PATH, True))

        target_tickers = [t for t in target_tickers if t and not t.startswith('0P')]
        if not target_tickers:
            return True, "No valid equity tickers found to check."

        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            placeholders = ','.join('?' for _ in target_tickers)

            query = f"""
                SELECT ticker, company_name, composite_score, atr_stop_loss, current_price
                FROM stock_signals
                WHERE ticker IN ({placeholders})
            """
            cursor.execute(query, list(target_tickers))

            db_data = {}
            for row in cursor.fetchall():
                db_data[row['ticker']] = {
                    'company_name': row['company_name'],
                    'composite_score': row['composite_score'],
                    'atr_stop_loss': row['atr_stop_loss'],
                    'current_price': row['current_price']
                }

            cutoff_date = pd.to_datetime(datetime.now(timezone.utc) - timedelta(days=days_back), utc=True)
            alerts_sent = 0

            for ticker in target_tickers:
                try:
                    insider_df = yahoo_engine.get_insider_transactions(ticker)

                    if insider_df is None or not isinstance(insider_df, pd.DataFrame) or insider_df.empty:
                        continue

                    insider_df = insider_df.reset_index()

                    # yfinance column names vary across versions — try known names then fuzzy-match.
                    date_col = next((col for col in ['Start Date', 'Date', 'Transaction Date'] if col in insider_df.columns), None)
                    if not date_col:
                        date_col = next((c for c in insider_df.columns if 'date' in c.lower()), None)

                    if date_col:
                        insider_df['Parsed_Date'] = pd.to_datetime(insider_df[date_col], utc=True, errors='coerce')
                    else:
                        continue

                    col_action = next((col for col in ['Text', 'Transaction', 'Action'] if col in insider_df.columns), None)
                    if not col_action:
                        col_action = next((c for c in insider_df.columns if 'text' in c.lower() or 'trans' in c.lower() or 'action' in c.lower()), None)

                    if not col_action:
                        continue

                    val_col = next((col for col in ['Value'] if col in insider_df.columns), None)
                    if not val_col:
                        val_col = next((c for c in insider_df.columns if 'value' in c.lower()), None)

                    if val_col:
                        insider_df['Clean_Value'] = pd.to_numeric(
                            insider_df[val_col].astype(str).replace(r'[\$,]', '', regex=True), errors='coerce'
                        )
                    else:
                        continue

                    shares_col = next((col for col in ['Shares'] if col in insider_df.columns), None)
                    if not shares_col:
                        shares_col = next((c for c in insider_df.columns if 'share' in c.lower()), None)

                    if shares_col:
                        insider_df['Clean_Shares'] = pd.to_numeric(
                            insider_df[shares_col].astype(str).replace(r'[,]', '', regex=True), errors='coerce'
                        )
                    else:
                        insider_df['Clean_Shares'] = 0

                    recent_buys = insider_df[insider_df['Parsed_Date'] >= cutoff_date].copy()
                    if recent_buys.empty:
                        continue

                    recent_buys = recent_buys[recent_buys[col_action].astype(str).str.contains('Buy|Purchase|Acquisition|P -|P-', case=False, na=False)]
                    major_buys = recent_buys[recent_buys['Clean_Value'] >= min_value]

                    t_data = db_data.get(ticker, {})
                    comp_name = t_data.get('company_name', ticker)
                    score = t_data.get('composite_score')
                    atr_stop = t_data.get('atr_stop_loss')
                    curr_price = t_data.get('current_price')

                    is_bullish_trend = score is not None and score >= 60
                    is_buying_dip = curr_price is not None and atr_stop is not None and (atr_stop < curr_price <= atr_stop * 1.15)

                    for idx, row in major_buys.iterrows():
                        exec_name = row.get('Insider', 'Unknown Executive')
                        position = row.get('Position', 'Insider')
                        val_str = f"${row['Clean_Value']:,.2f}"
                        share_str = f"{row['Clean_Shares']:,.0f}" if row['Clean_Shares'] > 0 else "Unknown"
                        date_str = row['Parsed_Date'].strftime('%Y-%m-%d')

                        alignment_banner = ""
                        if is_bullish_trend or is_buying_dip:
                            alignment_banner = "\n\n🔥 **QUANTAMENTAL ALIGNMENT TRIGGERED** 🔥"
                            if is_bullish_trend:
                                alignment_banner += f"\n✅ **System Score:** {score}/100 (Strong Bullish Trend)"
                            if is_buying_dip:
                                alignment_banner += f"\n📉 **Institutional Pullback:** Price (${curr_price:.2f}) is pulling back to, but securely holding, the ATR Support Floor (${atr_stop:.2f}). Insider is defending the trend!"

                        msg = (
                            f"🚨 **INSIDER BUYING DETECTED** 🚨\n"
                            f"Stock: {comp_name} ({ticker})\n"
                            f"Executive: {exec_name} ({position})\n"
                            f"Action: Bought {share_str} shares\n"
                            f"Value: {val_str}\n"
                            f"Date: {date_str}"
                            f"{alignment_banner}"
                        )

                        notify("insider_alert", "Insider", msg, conn=conn)
                        alerts_sent += 1

                except Exception:
                    logger.exception("Error evaluating insider trades for %s.", ticker)

            return True, f"Insider check complete. Triggered {alerts_sent} alerts based on ${min_value:,.0f} limit."
        finally:
            if conn is not None:
                conn.close()

    except Exception:
        logger.exception("Fatal crash in run_insider_alert.")
        return False, "System Crash: see application log for details."
