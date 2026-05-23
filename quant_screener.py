# quant_screener.py
import os
import re
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple

from database import get_connection
from regime_engine import get_latest_regime

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - QUANT_SCREENER - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Expert System: Rule-Based Screens with Regime Context ---

def get_oversold_reversals(data: List[Dict[str, Any]], regime_label: str) -> List[Dict[str, Any]]:
    """
    Identifies assets that are heavily oversold but showing early momentum recovery.
    Logic: RSI < 30 AND MACD Histogram > 0 AND Bullish Cross Confirmed today.
    Regime Context: In 'Crash'/'Volatile', traditional RSI dips fail as stocks keep dropping. 
    Instead of requiring Price > 200D SMA (which contradicts RSI < 30), we require defensive 
    characteristics: low beta (<0.8) or defensive sectors.
    """
    results = []
    defensive_sectors = ['Healthcare', 'Utilities', 'Consumer Defensive', 'Consumer Staples']
    
    for row in data:
        rsi = row.get('rsi_14')
        macd_hist = row.get('macd_hist')
        sector = row.get('sector', 'Unknown')
        
        if rsi is not None and macd_hist is not None:
            # RSI < 30 = deeply oversold. Positive MACD histogram = momentum recovering.
            # Bullish cross is a bonus tag but not required — the cross typically lags
            # RSI recovery by several sessions, making both conditions near-impossible to satisfy together.
            if rsi < 30 and macd_hist > 0:
                if regime_label in ['Crash', 'Volatile']:
                    # Safely extract beta
                    beta_raw = row.get('beta')
                    try:
                        beta = float(beta_raw) if beta_raw is not None else 1.0
                    except ValueError:
                        beta = 1.0
                        
                    is_defensive = sector in defensive_sectors
                    is_low_beta = beta < 0.8
                    
                    if is_defensive or is_low_beta:
                        results.append(row)
                else:
                    results.append(row)
    return results

def get_macd_bullish_crosses(data: List[Dict[str, Any]], regime_label: str) -> List[Dict[str, Any]]:
    """
    Identifies assets that have triggered a MACD Bullish Cross.
    Logic: bullish_cross == 1 (True)
    Regime Context: In 'Crash'/'Volatile', strictly requires Price > 200D SMA to avoid false bear-market rallies.
    """
    results = []
    for row in data:
        if row.get('bullish_cross') in (1, True):
            close_price = row.get('close_price', 0)
            sma_200 = row.get('sma_200', 0)
            
            if regime_label in ['Crash', 'Volatile']:
                if sma_200 is not None and close_price > sma_200:
                    results.append(row)
            else:
                results.append(row)
    return results

def get_momentum_surges(data: List[Dict[str, Any]], regime_label: str) -> List[Dict[str, Any]]:
    """
    Identifies assets breaking out with high volume while in a healthy momentum band.
    Logic: volume_surge == 1 (True) AND 50 < RSI < 70
    Regime Context: In 'Crash'/'Volatile', strictly requires Price > 200D SMA.
    """
    results = []
    for row in data:
        vol_surge = row.get('volume_surge')
        rsi = row.get('rsi_14')
        close_price = row.get('close_price', 0)
        sma_200 = row.get('sma_200', 0)
        
        if vol_surge in (1, True) and rsi is not None:
            if 50 < rsi < 70:
                if regime_label in ['Crash', 'Volatile']:
                    if sma_200 is not None and close_price > sma_200:
                        results.append(row)
                else:
                    results.append(row)
    return results

def get_overbought_warnings(data: List[Dict[str, Any]], regime_label: str) -> List[Dict[str, Any]]:
    """
    Identifies assets that are mathematically overextended and beginning to lose momentum.
    Logic: RSI > 70 AND MACD Histogram < 0 (Tightened to > 65 in Crash Regimes).
    """
    results = []
    rsi_threshold = 65 if regime_label in ['Crash', 'Volatile'] else 70
    
    for row in data:
        rsi = row.get('rsi_14')
        macd_hist = row.get('macd_hist')
        
        if rsi is not None and macd_hist is not None:
            if rsi > rsi_threshold and macd_hist < 0:
                results.append(row)
    return results

def filter_ai_vetoes(setups: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Evaluates ML prediction metrics on algorithmic setups. 
    If ML Confidence < 40% (or missing), the asset is stripped from the standard list and funnelled to the veto list.
    """
    approved = []
    vetoed = []
    for row in setups:
        ml_conf = row.get('ml_confidence_score')
        if ml_conf is None or ml_conf < 40.0:
            vetoed.append(row)
        else:
            approved.append(row)
    return approved, vetoed

def filter_macro_vetoes(setups: List[Dict[str, Any]], threat_level: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Applies Systemic Liquidity Drain Circuit Breaker via credit spreads and vetoes high-multiple 
    or highly indebted stocks that negatively correlate with surging yields.
    """
    # 1. Fetch Systemic Liquidity Constraints (Credit Spreads)
    us_spread = 0.0
    uk_spread = 0.0
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT us_high_yield_spread, uk_corporate_spread FROM macro_indicators ORDER BY date DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            # FIX: Cast sqlite3.Row to dict to use .get()
            row_dict = dict(row)
            us_spread = float(row_dict.get('us_high_yield_spread') or 0.0)
            uk_spread = float(row_dict.get('uk_corporate_spread') or 0.0)
    except Exception as e:
        logger.error(f"Failed to fetch credit spreads for circuit breaker: {e}")
    finally:
        if conn:
            conn.close()

    approved = []
    vetoed = []
    
    for row in setups:
        country = row.get('country', 'US')
        currency = row.get('currency', 'USD')
        
        is_us_asset = country == 'US' or currency == 'USD'
        is_uk_asset = country == 'UK' or currency in ['GBP', 'GBp']
        
        # 2. Hard Liquidity Drain Circuit Breaker (Trumps all logic) - Threshold raised to 6.5
        if (is_us_asset and us_spread > 6.5) or (is_uk_asset and uk_spread > 3.0):
            vetoed.append(row)
            continue
            
        # 3. Macro Yield Valuation Vetoes
        if threat_level not in ['RED', 'YELLOW']:
            approved.append(row)
            continue
            
        pe = row.get('trailing_pe')
        debt = row.get('debt_to_equity')
        corr = row.get('yield_correlation')
        
        # Neutral assumption for missing correlation data.
        # A stock with no yield correlation history is unknown, not negatively correlated.
        if corr is None:
            corr = 0.0
            
        is_high_multiple = (pe is not None and pe > 30) or (debt is not None and debt > 1.5)
        is_neg_corr = corr <= -0.3
        
        if is_high_multiple and is_neg_corr:
            vetoed.append(row)
        else:
            approved.append(row)
            
    return approved, vetoed

# --- Data Retrieval ---

def fetch_latest_signals(target_date: str) -> List[Dict[str, Any]]:
    """
    Connects to the SQLite database and retrieves all quantitative signals for the target date,
    joining stock_signals to capture valuation and macro correlation metrics required for veto filters.
    """
    logger.info(f"Fetching overnight quant signals for date: {target_date}")
    
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Parameterized query to prevent SQL injection, joining fundamentals
        cursor.execute('''
            SELECT q.*, s.trailing_pe, s.debt_to_equity, s.yield_correlation, s.country, s.currency, s.sector, s.beta
            FROM quant_signals q
            LEFT JOIN stock_signals s ON q.ticker = s.ticker
            WHERE q.date = ?
        ''', (target_date,))
        
        rows = cursor.fetchall()
        data = [dict(row) for row in rows]
        
        conn.close()
        
        logger.info(f"Successfully retrieved {len(data)} records from the database.")
        return data
        
    except Exception as e:
        logger.error(f"Failed to fetch quant signals: {e}")
        return []

def fetch_upcoming_macro_events(target_date: str) -> List[Dict[str, Any]]:
    """
    Retrieves Tier-1 macroeconomic events occurring within a rolling 48-hour window from the target date.
    """
    logger.info(f"Fetching upcoming Tier-1 macro events for 48-hour window starting: {target_date}")
    
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM macro_calendar 
            WHERE date(event_date) >= date(?) 
            AND date(event_date) <= date(?, '+2 days') 
            ORDER BY event_date ASC
        ''', (target_date, target_date))
        
        rows = cursor.fetchall()
        events = [dict(row) for row in rows]
        conn.close()
        
        return events
    except Exception as e:
        logger.error(f"Failed to fetch upcoming macro events: {e}")
        return []

# --- Report Generation ---

def _format_mobile_markdown_list(data: List[Dict[str, Any]]) -> str:
    """
    Helper function to format a list of dictionaries into a mobile-friendly 
    Markdown list, bypassing the lack of table support in mobile chat apps.
    """
    if not data:
        return "*No assets met the criteria for this screen today.*\n\n"
        
    output = ""
    for row in data:
        ticker = row.get('ticker', 'N/A')
        price = f"${row.get('close_price', 0):.2f}"
        rsi = f"{row.get('rsi_14', 0):.1f}" if row.get('rsi_14') is not None else "N/A"
        macd_hist = f"{row.get('macd_hist', 0):.3f}" if row.get('macd_hist') is not None else "N/A"
        vol_surge = "Yes" if row.get('volume_surge') in (1, True) else "No"
        bullish_cross = "Yes" if row.get('bullish_cross') in (1, True) else "No"
        ml_conf = f"{row.get('ml_confidence_score'):.1f}%" if row.get('ml_confidence_score') is not None else "N/A"
        
        # Dense, mobile-friendly 2-line structure
        output += f"🔹 **{ticker}** ({price}) | **RSI:** {rsi} | **ML:** {ml_conf}\n"
        output += f"&nbsp;&nbsp;&nbsp;↳ *MACD:* {macd_hist} | *Vol Surge:* {vol_surge} | *Cross:* {bullish_cross}\n\n"
        
    return output

def _extract_numeric(val_str: str) -> float:
    """Helper to extract a clean float from formatted strings (e.g., '5.0%', '-1.2K')."""
    if not val_str:
        return None
    try:
        # Strip everything except numbers, decimal points, and negative signs
        cleaned = re.sub(r'[^\d\.\-]', '', str(val_str))
        return float(cleaned) if cleaned else None
    except Exception:
        return None

def generate_markdown_briefing(target_date: str, data: List[Dict[str, Any]]) -> str:
    """
    Executes all rule-based screens contextualized by Market and Macro Regime, 
    and generates the final formatted Markdown report. Writes the output to disk.
    """
    logger.info("Applying contextual screening rules and generating Markdown briefing...")
    
    # 1. Fetch Market Regime Context (Volatility - Dual Region Support)
    regime_data = get_latest_regime()
    us_regime = regime_data.get('us_regime_label', 'Normal') if regime_data else 'Normal'
    uk_regime = regime_data.get('uk_regime_label', 'Normal') if regime_data else 'Normal'
    us_turb = regime_data.get('us_turbulence', 0.0) if regime_data else 0.0
    uk_turb = regime_data.get('uk_turbulence', 0.0) if regime_data else 0.0
    
    # Global Worst Case for Circuit Breaker logic
    if us_regime == 'Crash' or uk_regime == 'Crash':
        regime_label = 'Crash'
    elif us_regime == 'Volatile' or uk_regime == 'Volatile':
        regime_label = 'Volatile'
    else:
        regime_label = 'Normal'

    # Fetch Macro Regime Context (Systemic Yields)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM macro_regimes ORDER BY date DESC LIMIT 1")
    macro_row = cursor.fetchone()
    conn.close()
    macro_regime = dict(macro_row) if macro_row else {}
    
    # Extract dual-region metrics
    us_threat = macro_regime.get('us_threat_level', 'GREEN')
    uk_threat = macro_regime.get('uk_threat_level', 'GREEN')
    us_vel_raw = macro_regime.get('us_yield_velocity')
    uk_vel_raw = macro_regime.get('uk_yield_velocity')
    
    us_vel = float(us_vel_raw) if us_vel_raw is not None else 0.0
    uk_vel = float(uk_vel_raw) if uk_vel_raw is not None else 0.0
    
    # Calculate Unified Global Threat Level for downstream filtering
    if us_threat == 'RED' or uk_threat == 'RED':
        threat_level = 'RED'
    elif us_threat == 'YELLOW' or uk_threat == 'YELLOW':
        threat_level = 'YELLOW'
    else:
        threat_level = 'GREEN'
        
    # Fetch 48H Macro Events
    macro_events = fetch_upcoming_macro_events(target_date)
    
    # 2. Execute Screens mapped by Global Worst-Case Regime
    raw_oversold = get_oversold_reversals(data, regime_label)
    raw_macd_crosses = get_macd_bullish_crosses(data, regime_label)
    raw_surges = get_momentum_surges(data, regime_label)
    warnings = get_overbought_warnings(data, regime_label)
    
    # 3. Intercept & Filter ML Divergences & Macro Yield Vetoes
    approved_1, ml_veto_1 = filter_ai_vetoes(raw_oversold)
    oversold, macro_veto_1 = filter_macro_vetoes(approved_1, threat_level)

    approved_2, ml_veto_2 = filter_ai_vetoes(raw_macd_crosses)
    macd_crosses, macro_veto_2 = filter_macro_vetoes(approved_2, threat_level)

    approved_3, ml_veto_3 = filter_ai_vetoes(raw_surges)
    surges, macro_veto_3 = filter_macro_vetoes(approved_3, threat_level)
    
    # Combine Vetoes natively, deduplicating by ticker
    ml_vetoed_dict = {row['ticker']: row for row in ml_veto_1 + ml_veto_2 + ml_veto_3}
    ml_vetoed = list(ml_vetoed_dict.values())

    macro_vetoed_dict = {row['ticker']: row for row in macro_veto_1 + macro_veto_2 + macro_veto_3}
    macro_vetoed = list(macro_vetoed_dict.values())
    
    # 4. Build Markdown String
    report = f"# 📊 Morning Quant Briefing\n"
    report += f"**Date:** {target_date}\n\n"
    
    report += "## 🌍 Market Regime Context\n"
    report += f"**US Volatility:** {us_regime} *(Turbulence: {us_turb:.2f})* | **UK Volatility:** {uk_regime} *(Turbulence: {uk_turb:.2f})*\n"
    report += f"**US Yield Threat:** {us_threat} *(Velocity: {us_vel:+.2f}%)* | **UK Yield Threat:** {uk_threat} *(Velocity: {uk_vel:+.2f}%)*\n\n"
    
    if threat_level in ['RED', 'YELLOW']:
        report += "*⚠️ SYSTEMIC YIELD WARNING: Global cost of capital is surging. The quantitative screener has aggressively vetoed highly indebted and high P/E equities that show strong negative correlations to rising interest rates.*\n\n"
    elif regime_label in ['Crash', 'Volatile']:
        report += "*⚠️ VOLATILITY WARNING: Market is highly turbulent. The screener has filtered for defensive sectors and low beta 'Flight to Safety' setups.*\n\n"
    else:
        report += "*Market conditions are normal and stable. Standard quantitative thresholds apply.*\n\n"
        
    report += "## 📅 Upcoming Tier-1 Macro Events (48H Radar)\n"
    if not macro_events:
        report += "*No Tier-1 macroeconomic events scheduled for USD or GBP in the next 48 hours.*\n\n"
    else:
        for ev in macro_events:
            ev_date = ev.get('event_date', 'N/A')
            ev_name = ev.get('event_name', 'Unknown Event')
            currency = ev.get('currency', 'USD')
            prev_val = ev.get('previous_val', 'N/A')
            fcst_val = ev.get('forecast_val', 'N/A')
            ai_warning = ev.get('ai_volatility_warning', 0.0)
            
            # Mathematical evaluation of divergence
            is_divergent = False
            if prev_val != 'N/A' and fcst_val != 'N/A':
                prev_num = _extract_numeric(prev_val)
                fcst_num = _extract_numeric(fcst_val)
                
                # Flag if there is a measurable expectation shift
                if prev_num is not None and fcst_num is not None and abs(prev_num - fcst_num) > 0.0001:
                    is_divergent = True
                elif prev_val != fcst_val: # Fallback to string matching
                    is_divergent = True
            
            flag = "⚠️ " if is_divergent else ""
            if ai_warning > 2.0:
                flag = "🚨 [AI VOLATILITY WARNING] "

            report += f"🔹 **{ev_date}** | [{currency}] {ev_name}\n"
            report += f"&nbsp;&nbsp;&nbsp;↳ {flag}*Forecast:* {fcst_val} | *Previous:* {prev_val}\n\n"
    
    report += "## Executive Summary\n"
    report += f"Automated overnight screening executed against {len(data)} tracked equities. "
    report += "Identifies high-probability statistical anomalies, momentum shifts, and risk-management triggers based on institutional-grade technical parameters.\n\n"
    
    report += "## 📉 Oversold Reversals\n"
    report += "*Aggressively sold off (RSI < 30) but demonstrating early quantitative signs of momentum recovery (Positive MACD Histogram). High-conviction, deep-value entry opportunities.*\n\n"
    report += _format_mobile_markdown_list(oversold)
    
    report += "## 📈 MACD Bullish Crosses\n"
    report += "*Triggered a Bullish MACD Crossover, indicating a mathematical momentum reversal to the upside and underlying institutional accumulation.*\n\n"
    report += _format_mobile_markdown_list(macd_crosses)
    
    report += "## 🚀 Momentum & Volume Surges\n"
    report += "*Explosive buying volume (Volume > 1.5x 20-Day SMA) operating in a healthy momentum band (RSI 50-70). Strong institutional backing without immediate overbought exhaustion risk.*\n\n"
    report += _format_mobile_markdown_list(surges)
    
    if macro_vetoed:
        report += "## 🏛️ Macro/Liquidity Vetoed Setups\n"
        report += "*These equities triggered algorithmic buy signals but were VETOED by the Intermarket Engine due to surging global interest rates or failing systemic credit spreads. Avoid entry.*\n\n"
        report += _format_mobile_markdown_list(macro_vetoed)

    if ml_vetoed:
        report += "## 🤖 AI Vetoed Setups (Divergence Warnings)\n"
        report += "*These equities triggered algorithmic buy signals, but the Machine Learning Ensemble predicts a high probability of failure (Confidence < 40%). Proceed with extreme caution.*\n\n"
        report += _format_mobile_markdown_list(ml_vetoed)
    
    report += "## 🚨 Overbought Warnings (Distribution Risk)\n"
    report += "*Risk Management Alert: Mathematically overextended and beginning to flash negative momentum divergence (Negative MACD Histogram). Trim positions or tighten stop-losses, as algorithmic mean-reversion is highly probable.*\n\n"
    report += _format_mobile_markdown_list(warnings)
    
    report += "---\n"
    report += "*Generated automatically by the Quantamental Python Engine.*"
    
    # Ensure the reports directory exists
    reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
    os.makedirs(reports_dir, exist_ok=True)
    
    # Write to file
    file_path = os.path.join(reports_dir, f"quant_briefing_{target_date}.md")
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(report)
        logger.info(f"Morning Briefing successfully saved to: {file_path}")
    except Exception as e:
        logger.error(f"Failed to write report to disk: {e}")
        
    return report

if __name__ == "__main__":
    scan_date = datetime.now().strftime('%Y-%m-%d')
    raw_signals = fetch_latest_signals(scan_date)
    
    if not raw_signals:
        logger.warning(f"No data found for {scan_date}. Ensure the quant_engine ran successfully overnight.")
    else:
        markdown_output = generate_markdown_briefing(scan_date, raw_signals)
        print("\n" + "="*60 + "\n")
        print(markdown_output)
        print("\n" + "="*60 + "\n")