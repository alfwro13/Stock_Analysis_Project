"""
quant_screener.py

Elite-level rule-based screening system for the Quantamental Dashboard.
Queries overnight mathematical signal data from the local SQLite database,
applies Turbulence-Aware Market Regime contextual filtering, and generates 
a deterministic Morning Quant Briefing in Markdown format.
"""

import os
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
    Logic: RSI < 30 AND MACD Histogram > 0
    Regime Context: In 'Crash'/'Volatile', strict Flight to Safety requires Price > 200D SMA.
    """
    results = []
    for row in data:
        rsi = row.get('rsi_14')
        macd_hist = row.get('macd_hist')
        close_price = row.get('close_price', 0)
        sma_200 = row.get('sma_200', 0)
        
        if rsi is not None and macd_hist is not None:
            if rsi < 30 and macd_hist > 0:
                # Flight to Safety filter during turbulent regimes
                if regime_label in ['Crash', 'Volatile']:
                    if sma_200 is not None and close_price > sma_200:
                        results.append(row)
                else:
                    results.append(row)
    return results

def get_golden_crosses(data: List[Dict[str, Any]], regime_label: str) -> List[Dict[str, Any]]:
    """
    Identifies assets that have triggered a bullish MACD crossover.
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
    If ML Confidence < 40%, the asset is stripped from the standard list and funnelled to the veto list.
    """
    approved = []
    vetoed = []
    for row in setups:
        ml_conf = row.get('ml_confidence_score')
        if ml_conf is not None and ml_conf < 40.0:
            vetoed.append(row)
        else:
            approved.append(row)
    return approved, vetoed

# --- Data Retrieval ---

def fetch_latest_signals(target_date: str) -> List[Dict[str, Any]]:
    """
    Connects to the SQLite database and retrieves all quantitative signals for the target date.
    Returns a list of dictionaries for clean downstream processing.
    """
    logger.info(f"Fetching overnight quant signals for date: {target_date}")
    
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Parameterized query to prevent SQL injection
        cursor.execute(
            "SELECT * FROM quant_signals WHERE date = ?",
            (target_date,)
        )
        
        # Convert sqlite3.Row objects to standard Python dictionaries
        rows = cursor.fetchall()
        data = [dict(row) for row in rows]
        
        conn.close()
        
        logger.info(f"Successfully retrieved {len(data)} records from the database.")
        return data
        
    except Exception as e:
        logger.error(f"Failed to fetch quant signals: {e}")
        return []


# --- Report Generation ---

def _format_markdown_table(data: List[Dict[str, Any]]) -> str:
    """Helper function to format a list of dictionaries into a Markdown table."""
    if not data:
        return "*No assets met the criteria for this screen today.*\n"
        
    table = "| Ticker | Close Price | RSI (14) | MACD Hist | Vol Surge | Bullish Cross | ML Conf (%) |\n"
    table += "|--------|-------------|----------|-----------|-----------|---------------|-------------|\n"
    
    for row in data:
        ticker = row.get('ticker', 'N/A')
        price = f"${row.get('close_price', 0):.2f}"
        rsi = f"{row.get('rsi_14', 0):.1f}" if row.get('rsi_14') is not None else "N/A"
        macd_hist = f"{row.get('macd_hist', 0):.3f}" if row.get('macd_hist') is not None else "N/A"
        vol_surge = "Yes" if row.get('volume_surge') in (1, True) else "No"
        bullish_cross = "Yes" if row.get('bullish_cross') in (1, True) else "No"
        ml_conf = f"{row.get('ml_confidence_score'):.1f}%" if row.get('ml_confidence_score') is not None else "N/A"
        
        table += f"| **{ticker}** | {price} | {rsi} | {macd_hist} | {vol_surge} | {bullish_cross} | {ml_conf} |\n"
        
    return table

def generate_markdown_briefing(target_date: str, data: List[Dict[str, Any]]) -> str:
    """
    Executes all rule-based screens contextualized by Market Regime, and generates 
    the final formatted Markdown report. Writes the output to a local 'reports' directory.
    """
    logger.info("Applying contextual screening rules and generating Markdown briefing...")
    
    # 1. Fetch Market Regime Context
    regime_data = get_latest_regime()
    regime_label = regime_data.get('regime_label', 'Normal') if regime_data else 'Normal'
    turbulence_idx = regime_data.get('turbulence_index', 0.0) if regime_data else 0.0
    
    # 2. Execute Screens mapped by Regime
    raw_oversold = get_oversold_reversals(data, regime_label)
    raw_golden_crosses = get_golden_crosses(data, regime_label)
    raw_surges = get_momentum_surges(data, regime_label)
    
    # We do not veto warnings (bearish flags), only bullish ones.
    warnings = get_overbought_warnings(data, regime_label)
    
    # 3. Intercept & Filter ML Divergences
    oversold, veto_1 = filter_ai_vetoes(raw_oversold)
    golden_crosses, veto_2 = filter_ai_vetoes(raw_golden_crosses)
    surges, veto_3 = filter_ai_vetoes(raw_surges)
    
    # Combine all vetoed setups, deduping by ticker
    vetoed_dict = {}
    for row in veto_1 + veto_2 + veto_3:
        vetoed_dict[row['ticker']] = row
    ai_vetoed = list(vetoed_dict.values())
    
    # 4. Build Markdown String
    report = f"# 📊 Morning Quant Briefing\n"
    report += f"**Date:** {target_date}\n\n"
    
    report += "## 🌍 Market Regime Context\n"
    report += f"**Current Classification:** {regime_label} *(Turbulence Index: {turbulence_idx:.2f})*\n"
    if regime_label in ['Crash', 'Volatile']:
        report += "*⚠️ Market is highly turbulent. The quantitative screener has aggressively filtered for 'Flight to Safety' setups. Only assets displaying structural strength above their 200-day moving average are included in bullish setups.*\n\n"
    else:
        report += "*Market conditions are currently normal and stable. Standard quantitative thresholds apply.*\n\n"
    
    report += "## Executive Summary\n"
    report += f"Automated overnight screening executed against {len(data)} tracked equities. "
    report += "The following report identifies high-probability statistical anomalies, momentum shifts, and risk-management triggers based on institutional-grade technical parameters.\n\n"
    
    report += "## 📉 Oversold Reversals\n"
    report += "*The following equities have been aggressively sold off (RSI < 30) but are demonstrating early quantitative signs of momentum recovery (Positive MACD Histogram). These present high-conviction, deep-value entry opportunities with asymmetric risk/reward profiles.*\n\n"
    report += _format_markdown_table(oversold) + "\n"
    
    report += "## 📈 Golden MACD Crosses\n"
    report += "*The following equities have triggered a Golden MACD Cross, indicating a potential mathematical momentum reversal to the upside and underlying institutional accumulation.*\n\n"
    report += _format_markdown_table(golden_crosses) + "\n"
    
    report += "## 🚀 Momentum & Volume Surges\n"
    report += "*These assets are currently experiencing explosive buying volume (Volume > 1.5x 20-Day SMA) while operating in a healthy momentum band (RSI between 50 and 70). This signifies strong institutional backing without immediate overbought exhaustion risk.*\n\n"
    report += _format_markdown_table(surges) + "\n"
    
    report += "## 🚨 AI Vetoed Setups (Divergence Warnings)\n"
    report += "*The following equities triggered strong mathematical buy signals, but the Machine Learning Ensemble predicts a high probability of failure (Confidence < 40%). Proceed with extreme caution.*\n\n"
    report += _format_markdown_table(ai_vetoed) + "\n"
    
    report += "## 🚨 Overbought Warnings (Distribution Risk)\n"
    report += "*Risk Management Alert: The following assets are mathematically overextended and are beginning to flash negative momentum divergence (Negative MACD Histogram). Trim positions or tighten stop-losses, as algorithmic mean-reversion is highly probable.*\n\n"
    report += _format_markdown_table(warnings) + "\n"
    
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