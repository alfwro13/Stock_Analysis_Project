"""
quant_screener.py

Elite-level rule-based screening system for the Quantamental Dashboard.
Queries overnight mathematical signal data from the local SQLite database and 
generates a deterministic Morning Quant Briefing in Markdown format.
"""

import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any

from database import get_connection

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - QUANT_SCREENER - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Expert System: Rule-Based Screens ---

def get_oversold_reversals(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Identifies assets that are heavily oversold but showing early momentum recovery.
    Logic: RSI < 30 AND MACD Histogram > 0
    """
    results = []
    for row in data:
        rsi = row.get('rsi_14')
        macd_hist = row.get('macd_hist')
        
        if rsi is not None and macd_hist is not None:
            if rsi < 30 and macd_hist > 0:
                results.append(row)
    return results

def get_golden_crosses(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Identifies assets that have triggered a bullish MACD crossover.
    Logic: bullish_cross == 1 (True)
    """
    return [row for row in data if row.get('bullish_cross') in (1, True)]

def get_momentum_surges(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Identifies assets breaking out with high volume while in a healthy momentum band.
    Logic: volume_surge == 1 (True) AND 50 < RSI < 70
    """
    results = []
    for row in data:
        vol_surge = row.get('volume_surge')
        rsi = row.get('rsi_14')
        
        if vol_surge in (1, True) and rsi is not None:
            if 50 < rsi < 70:
                results.append(row)
    return results

def get_overbought_warnings(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Identifies assets that are mathematically overextended and beginning to lose momentum.
    Logic: RSI > 70 AND MACD Histogram < 0
    """
    results = []
    for row in data:
        rsi = row.get('rsi_14')
        macd_hist = row.get('macd_hist')
        
        if rsi is not None and macd_hist is not None:
            if rsi > 70 and macd_hist < 0:
                results.append(row)
    return results


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
        
    table = "| Ticker | Close Price | RSI (14) | MACD Hist | Vol Surge | Bullish Cross |\n"
    table += "|--------|-------------|----------|-----------|-----------|---------------|\n"
    
    for row in data:
        ticker = row.get('ticker', 'N/A')
        price = f"${row.get('close_price', 0):.2f}"
        rsi = f"{row.get('rsi_14', 0):.1f}" if row.get('rsi_14') is not None else "N/A"
        macd_hist = f"{row.get('macd_hist', 0):.3f}" if row.get('macd_hist') is not None else "N/A"
        vol_surge = "Yes" if row.get('volume_surge') in (1, True) else "No"
        bullish_cross = "Yes" if row.get('bullish_cross') in (1, True) else "No"
        
        table += f"| **{ticker}** | {price} | {rsi} | {macd_hist} | {vol_surge} | {bullish_cross} |\n"
        
    return table

def generate_markdown_briefing(target_date: str, data: List[Dict[str, Any]]) -> str:
    """
    Executes all rule-based screens and generates the final formatted Markdown report.
    Writes the output to a local 'reports' directory.
    """
    logger.info("Applying screening rules and generating Markdown briefing...")
    
    # Execute Screens
    oversold = get_oversold_reversals(data)
    golden_crosses = get_golden_crosses(data)
    surges = get_momentum_surges(data)
    warnings = get_overbought_warnings(data)
    
    # Build Markdown String
    report = f"# 📈 Morning Quant Briefing\n"
    report += f"**Date:** {target_date}\n\n"
    
    report += "## Executive Summary\n"
    report += f"Automated overnight screening executed against {len(data)} tracked equities. "
    report += "The following report identifies high-probability statistical anomalies, momentum shifts, and risk-management triggers based on institutional-grade technical parameters.\n\n"
    
    report += "## 🌅 Oversold Reversals\n"
    report += "*The following equities have been aggressively sold off (RSI < 30) but are demonstrating early quantitative signs of momentum recovery (Positive MACD Histogram). These present high-conviction, deep-value entry opportunities with asymmetric risk/reward profiles.*\n\n"
    report += _format_markdown_table(oversold) + "\n"
    
    report += "## ⚡ Golden MACD Crosses\n"
    report += "*The following equities have triggered a Golden MACD Cross, indicating a potential mathematical momentum reversal to the upside and underlying institutional accumulation.*\n\n"
    report += _format_markdown_table(golden_crosses) + "\n"
    
    report += "## 🔥 Momentum & Volume Surges\n"
    report += "*These assets are currently experiencing explosive buying volume (Volume > 1.5x 20-Day SMA) while operating in a healthy momentum band (RSI between 50 and 70). This signifies strong institutional backing without immediate overbought exhaustion risk.*\n\n"
    report += _format_markdown_table(surges) + "\n"
    
    report += "## 🚨 Overbought Warnings (Distribution Risk)\n"
    report += "*Risk Management Alert: The following assets are severely overextended (RSI > 70) and are beginning to flash negative momentum divergence (Negative MACD Histogram). Trim positions or tighten stop-losses, as algorithmic mean-reversion is highly probable.*\n\n"
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
    # Determine the target date. Typically, an overnight scan looks at the previous trading day's close.
    # For testing, we default to the current system date, but it can be overridden.
    scan_date = datetime.now().strftime('%Y-%m-%d')
    
    # Extract data
    raw_signals = fetch_latest_signals(scan_date)
    
    if not raw_signals:
        logger.warning(f"No data found for {scan_date}. Ensure the quant_engine ran successfully overnight.")
    else:
        # Generate Report
        markdown_output = generate_markdown_briefing(scan_date, raw_signals)
        print("\n" + "="*60 + "\n")
        print(markdown_output)
        print("\n" + "="*60 + "\n")