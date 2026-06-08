import math
import os
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Tuple

from database import get_connection
from regime_engine import get_latest_regime
from constants import (
    RSI_OVERSOLD, RSI_OVERBOUGHT, RSI_OVERBOUGHT_STRESSED,
    RSI_MOMENTUM_MIN,
    ML_CONFIDENCE_THRESHOLD,
    DEFENSIVE_SECTORS,
)

logger = logging.getLogger(__name__)

def _is_valid_numeric(v) -> bool:
    """Returns True only when v is a real, finite number — not None, NaN, or ±inf."""
    if v is None:
        return False
    try:
        f = float(v)
    except (ValueError, TypeError):
        return False
    return math.isfinite(f)

def get_oversold_reversals(data: List[Dict[str, Any]], regime_label: str) -> List[Dict[str, Any]]:
    """RSI<30 + positive MACD hist; in Crash/Volatile requires low-beta or defensive sector instead of >200D SMA."""
    results = []
    defensive_sectors = DEFENSIVE_SECTORS
    
    for row in data:
        rsi = row.get('rsi_14')
        macd_hist = row.get('macd_hist')
        sector = row.get('sector', 'Unknown')
        
        if _is_valid_numeric(rsi) and _is_valid_numeric(macd_hist):
            # MACD bullish cross not required — typically lags RSI recovery by sessions, making both near-impossible together.
            if rsi < RSI_OVERSOLD and macd_hist > 0:
                if regime_label in ['Crash', 'Volatile']:
                    # Safely extract beta — guard against None, non-numeric, and ±inf
                    beta_raw = row.get('beta')
                    try:
                        beta = float(beta_raw) if beta_raw is not None else 1.0
                        if not math.isfinite(beta):
                            beta = 1.0
                    except (ValueError, TypeError):
                        beta = 1.0
                        
                    is_defensive = sector in defensive_sectors
                    is_low_beta = beta < 0.8
                    
                    if is_defensive or is_low_beta:
                        results.append(row)
                else:
                    results.append(row)
    return results

def get_macd_bullish_crosses(data: List[Dict[str, Any]], regime_label: str) -> List[Dict[str, Any]]:
    """bullish_cross==True; in Crash/Volatile also requires price > SMA-200 to avoid false bear-market rallies."""
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
    """volume_surge==True + RSI in momentum band (50–70); in Crash/Volatile also requires price > SMA-200."""
    results = []
    for row in data:
        vol_surge = row.get('volume_surge')
        rsi = row.get('rsi_14')
        close_price = row.get('close_price', 0)
        sma_200 = row.get('sma_200', 0)
        
        if vol_surge in (1, True) and _is_valid_numeric(rsi):
            if RSI_MOMENTUM_MIN <= rsi <= RSI_OVERBOUGHT:
                if regime_label in ['Crash', 'Volatile']:
                    if sma_200 is not None and close_price > sma_200:
                        results.append(row)
                else:
                    results.append(row)
    return results

def get_overbought_warnings(data: List[Dict[str, Any]], regime_label: str) -> List[Dict[str, Any]]:
    """RSI > 70 (>65 in Crash/Volatile) AND negative MACD hist — distribution-risk flag."""
    results = []
    rsi_threshold = RSI_OVERBOUGHT_STRESSED if regime_label in ['Crash', 'Volatile'] else RSI_OVERBOUGHT
    
    for row in data:
        rsi = row.get('rsi_14')
        macd_hist = row.get('macd_hist')
        
        if _is_valid_numeric(rsi) and _is_valid_numeric(macd_hist):
            if rsi > rsi_threshold and macd_hist < 0:
                results.append(row)
    return results

def get_longterm_entry_setups(data: List[Dict[str, Any]], regime_label: str) -> List[Dict[str, Any]]:
    """Price>SMA-200, score≥20, RSI 35–60 (55 ceiling in Crash/Volatile), ATR<2.5%, grade A/B — buy-and-hold entry filter."""
    results = []
    rsi_ceiling = 55 if regime_label in ['Crash', 'Volatile'] else 60

    for row in data:
        close_price    = row.get('close_price')
        sma_200        = row.get('sma_200')
        composite_score = row.get('composite_score')
        rsi            = row.get('rsi_14')
        atr_pct        = row.get('atr_pct')

        if not all(_is_valid_numeric(v) for v in [close_price, sma_200, composite_score, rsi, atr_pct]):
            continue
        if close_price <= sma_200:
            continue
        if composite_score < 20:
            continue
        if not (35 <= rsi <= rsi_ceiling):
            continue
        if atr_pct >= 0.025:
            continue
        if compute_quality_grade(row) not in ('A', 'B'):
            continue

        results.append(row)

    return results

def filter_ai_vetoes(setups: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Splits setups into (approved, vetoed) based on ML confidence threshold; missing confidence → vetoed."""
    approved = []
    vetoed = []
    for row in setups:
        ml_conf = row.get('ml_confidence_score')
        if ml_conf is None or ml_conf < ML_CONFIDENCE_THRESHOLD:
            vetoed.append(row)
        else:
            approved.append(row)
    return approved, vetoed

def filter_macro_vetoes(setups: List[Dict[str, Any]], threat_level: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Credit-spread circuit breaker + yield-correlation veto on high-multiple/high-debt stocks in RED/YELLOW regimes."""
    us_spread = 0.0
    uk_spread = 0.0
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT us_high_yield_spread, uk_corporate_spread FROM macro_indicators ORDER BY date DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            row_dict = dict(row)
            us_spread = float(row_dict.get('us_high_yield_spread') or 0.0)
            uk_spread = float(row_dict.get('uk_corporate_spread') or 0.0)
    except Exception as e:
        logger.error("Failed to fetch credit spreads for circuit breaker: %s", e)
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
        
        # Hard circuit breaker: credit spread >6.5% (US) / >3.0% (UK) overrides all other logic.
        if (is_us_asset and us_spread > 6.5) or (is_uk_asset and uk_spread > 3.0):
            vetoed.append(row)
            continue
            
        if threat_level not in ['RED', 'YELLOW']:
            approved.append(row)
            continue
            
        pe = row.get('trailing_pe')
        debt = row.get('debt_to_equity')
        corr = row.get('yield_correlation')

        is_high_multiple = (pe is not None and pe > 30) or (debt is not None and debt > 1.5)
        # Missing correlation treated as risk: no yield-correlation history → cannot assume rate-safety → veto.
        is_neg_corr = corr is None or corr <= -0.3
        
        if is_high_multiple and is_neg_corr:
            vetoed.append(row)
        else:
            approved.append(row)
            
    return approved, vetoed

def get_portfolio_deterioration_alerts(target_date: str) -> List[Dict[str, Any]]:
    """Returns portfolio holdings whose composite score dropped ≥15pts vs their most recent score ≥5 days ago."""
    try:
        from data_engine import DataEngine
        portfolio_tickers = DataEngine().get_all_tickers()
    except Exception:
        return []

    if not portfolio_tickers:
        return []

    lookup_cutoff = (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=5)).strftime('%Y-%m-%d')
    placeholders = ','.join('?' for _ in portfolio_tickers)

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            f"SELECT ticker, score, close_price FROM score_history WHERE ticker IN ({placeholders}) AND date = ?",
            (*portfolio_tickers, target_date),
        )
        today_scores = {r['ticker']: dict(r) for r in cursor.fetchall()}

        cursor.execute(
            f"""SELECT ticker, score, date FROM score_history
                WHERE ticker IN ({placeholders}) AND date <= ?
                GROUP BY ticker HAVING date = MAX(date)""",
            (*portfolio_tickers, lookup_cutoff),
        )
        past_scores = {r['ticker']: dict(r) for r in cursor.fetchall()}

        alerts = []
        for ticker, today in today_scores.items():
            past = past_scores.get(ticker)
            if past is None:
                continue
            drop = today['score'] - past['score']
            if drop <= -15:
                alerts.append({
                    'ticker': ticker,
                    'score_today': today['score'],
                    'score_past': past['score'],
                    'score_drop': drop,
                    'past_date': past['date'],
                    'close_price': today.get('close_price'),
                })
        alerts.sort(key=lambda r: r['score_drop'])
        return alerts

    except Exception as e:
        logger.warning("Portfolio deterioration check skipped (score_history may be empty): %s", e)
        return []
    finally:
        if conn:
            conn.close()

def fetch_latest_signals(target_date: str) -> List[Dict[str, Any]]:
    """Fetches quant_signals for target_date joined with stock_signals/earnings_volatility for veto-filter fields."""
    logger.info("Fetching overnight quant signals for date: %s", target_date)

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # COALESCE: earnings_volatility provides next_earnings_date when stock_signals lacks it.
        cursor.execute('''
            SELECT q.*,
                   s.trailing_pe, s.debt_to_equity, s.yield_correlation,
                   s.country, s.currency, s.sector, s.beta,
                   s.roe, s.peg_ratio,
                   COALESCE(s.next_earnings_date, ev.next_earnings_date) AS next_earnings_date
            FROM quant_signals q
            LEFT JOIN stock_signals s ON q.ticker = s.ticker
            LEFT JOIN earnings_volatility ev ON q.ticker = ev.ticker
            WHERE q.date = ?
        ''', (target_date,))

        rows = cursor.fetchall()
        data = [dict(row) for row in rows]

        logger.info("Successfully retrieved %d records from the database.", len(data))
        return data

    except Exception as e:
        logger.error("Failed to fetch quant signals: %s", e)
        return []
    finally:
        if conn:
            conn.close()

def fetch_upcoming_macro_events(target_date: str) -> List[Dict[str, Any]]:
    """Fetches macro_calendar rows within the 48-hour window starting at target_date."""
    logger.info("Fetching upcoming Tier-1 macro events for 48-hour window starting: %s", target_date)

    conn = None
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
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error("Failed to fetch upcoming macro events: %s", e)
        return []
    finally:
        if conn:
            conn.close()

def _format_longterm_setup_list(data: List[Dict[str, Any]], target_date: str = '') -> str:
    """Formats long-term entry setups with composite score and key metrics front and centre."""
    if not data:
        return "*No setups passed all long-term entry criteria today.*\n\n"

    # Sort strongest score first
    sorted_data = sorted(data, key=lambda r: r.get('composite_score', 0), reverse=True)
    output = ""
    for row in sorted_data:
        ticker  = row.get('ticker', 'N/A')
        price   = f"${row.get('close_price', 0):.2f}"
        score   = row.get('composite_score', 'N/A')
        signal  = row.get('overall_signal', '')
        rsi     = f"{row.get('rsi_14', 0):.1f}" if _is_valid_numeric(row.get('rsi_14')) else "N/A"
        atr_pct = row.get('atr_pct')
        atr_str = f"{atr_pct * 100:.1f}%" if _is_valid_numeric(atr_pct) else "N/A"
        grade   = compute_quality_grade(row)
        w52     = f"{row.get('week52_pct') * 100:.0f}%" if _is_valid_numeric(row.get('week52_pct')) else "N/A"

        earnings_tag = ""
        if target_date:
            days = _get_earnings_days(row, target_date)
            if days is not None and days <= 14:
                earnings_tag = f" | ⚠️ Earnings {days}d"

        output += f"🎯 **{ticker}** ({price}) | **Score:** {score} | **Qual:** {grade} | **52W:** {w52}{earnings_tag}\n"
        output += f"&nbsp;&nbsp;&nbsp;↳ *RSI:* {rsi} | *ATR:* {atr_str} | *Signal:* {signal}\n\n"

    return output

def _format_mobile_markdown_list(data: List[Dict[str, Any]], target_date: str = '') -> str:
    """Formats signal rows into a mobile-friendly Markdown list with quality grade and earnings risk flag."""
    if not data:
        return "*No assets met the criteria for this screen today.*\n\n"

    output = ""
    for row in data:
        ticker      = row.get('ticker', 'N/A')
        price       = f"${row.get('close_price', 0):.2f}"
        rsi         = f"{row.get('rsi_14', 0):.1f}" if row.get('rsi_14') is not None else "N/A"
        macd_hist   = f"{row.get('macd_hist', 0):.3f}" if row.get('macd_hist') is not None else "N/A"
        vol_surge   = "Yes" if row.get('volume_surge') in (1, True) else "No"
        bullish_cross = "Yes" if row.get('bullish_cross') in (1, True) else "No"
        ml_conf     = f"{row.get('ml_confidence_score'):.1f}%" if row.get('ml_confidence_score') is not None else "N/A"
        grade       = compute_quality_grade(row)
        w52         = f"{row.get('week52_pct') * 100:.0f}%" if _is_valid_numeric(row.get('week52_pct')) else "N/A"

        earnings_tag = ""
        if target_date:
            days = _get_earnings_days(row, target_date)
            if days is not None and days <= 14:
                earnings_tag = f" | ⚠️ **Earnings in {days}d**"

        output += f"🔹 **{ticker}** ({price}) | **RSI:** {rsi} | **Qual:** {grade} | **52W:** {w52} | **ML:** {ml_conf}{earnings_tag}\n"
        output += f"&nbsp;&nbsp;&nbsp;↳ *MACD:* {macd_hist} | *Vol Surge:* {vol_surge} | *Cross:* {bullish_cross}\n\n"

    return output

def compute_quality_grade(row: Dict[str, Any]) -> str:
    """A/B/C/D grade from ROE, debt/equity, PE/PEG: D=loss-making or over-leveraged, A=high-quality compounder."""
    roe  = row.get('roe')
    debt = row.get('debt_to_equity')
    pe   = row.get('trailing_pe')
    peg  = row.get('peg_ratio')

    # D: loss-making or dangerously leveraged
    if (roe is not None and roe < 0) or (debt is not None and debt > 2.0):
        return 'D'

    # A: high-quality compounder
    a_roe  = roe is not None and roe > 15
    a_debt = debt is None or debt < 0.5
    a_val  = (pe is not None and pe < 25) or (peg is not None and peg < 1.5)
    if a_roe and a_debt and a_val:
        return 'A'

    # B: solid business
    b_roe  = roe is not None and roe > 10
    b_debt = debt is None or debt < 1.0
    b_val  = pe is None or pe < 35
    if b_roe and b_debt and b_val:
        return 'B'

    return 'C'

def _get_earnings_days(row: Dict[str, Any], target_date: str) -> int | None:
    """Returns days until next earnings, or None if unknown or already passed."""
    raw = row.get('next_earnings_date')
    if not raw or raw == 'Unknown':
        return None
    try:
        earnings_dt = datetime.strptime(raw[:10], '%Y-%m-%d').date()
        today_dt = datetime.strptime(target_date, '%Y-%m-%d').date()
        delta = (earnings_dt - today_dt).days
        return delta if delta >= 0 else None
    except (ValueError, TypeError):
        return None

def _extract_numeric(val_str: str) -> float:
    """Extracts the first number (including optional leading minus) from a string."""
    if not val_str:
        return None
    try:
        match = re.search(r'-?[\d]*\.?[\d]+', str(val_str))
        return float(match.group()) if match else None
    except Exception:
        return None

def generate_markdown_briefing(target_date: str, data: List[Dict[str, Any]]) -> str:
    """Runs all screener rules under market/macro regime context, builds the Markdown briefing, and saves to disk."""
    logger.info("Applying contextual screening rules and generating Markdown briefing...")

    regime_data = get_latest_regime()
    us_regime = regime_data.get('us_regime_label', 'Normal') if regime_data else 'Normal'
    uk_regime = regime_data.get('uk_regime_label', 'Normal') if regime_data else 'Normal'
    us_turb = regime_data.get('us_turbulence', 0.0) if regime_data else 0.0
    uk_turb = regime_data.get('uk_turbulence', 0.0) if regime_data else 0.0
    
    # Global worst-case for circuit breaker logic
    if us_regime == 'Crash' or uk_regime == 'Crash':
        regime_label = 'Crash'
    elif us_regime == 'Volatile' or uk_regime == 'Volatile':
        regime_label = 'Volatile'
    else:
        regime_label = 'Normal'

    conn = None
    try:
        conn = get_connection()
        macro_row = conn.execute("SELECT * FROM macro_regimes ORDER BY date DESC LIMIT 1").fetchone()
    except Exception:
        macro_row = None
    finally:
        if conn:
            conn.close()
    macro_regime = dict(macro_row) if macro_row else {}

    us_threat = macro_regime.get('us_threat_level', 'GREEN')
    uk_threat = macro_regime.get('uk_threat_level', 'GREEN')
    us_vel_raw = macro_regime.get('us_yield_velocity')
    uk_vel_raw = macro_regime.get('uk_yield_velocity')
    
    us_vel = float(us_vel_raw) if us_vel_raw is not None else 0.0
    uk_vel = float(uk_vel_raw) if uk_vel_raw is not None else 0.0

    if us_threat == 'RED' or uk_threat == 'RED':
        threat_level = 'RED'
    elif us_threat == 'YELLOW' or uk_threat == 'YELLOW':
        threat_level = 'YELLOW'
    else:
        threat_level = 'GREEN'

    macro_events = fetch_upcoming_macro_events(target_date)

    raw_oversold = get_oversold_reversals(data, regime_label)
    raw_macd_crosses = get_macd_bullish_crosses(data, regime_label)
    raw_surges = get_momentum_surges(data, regime_label)
    warnings = get_overbought_warnings(data, regime_label)
    longterm_setups = get_longterm_entry_setups(data, regime_label)
    deterioration_alerts = get_portfolio_deterioration_alerts(target_date)

    approved_1, ml_veto_1 = filter_ai_vetoes(raw_oversold)
    oversold, macro_veto_1 = filter_macro_vetoes(approved_1, threat_level)

    approved_2, ml_veto_2 = filter_ai_vetoes(raw_macd_crosses)
    macd_crosses, macro_veto_2 = filter_macro_vetoes(approved_2, threat_level)

    approved_3, ml_veto_3 = filter_ai_vetoes(raw_surges)
    surges, macro_veto_3 = filter_macro_vetoes(approved_3, threat_level)

    ml_vetoed_dict = {row['ticker']: row for row in ml_veto_1 + ml_veto_2 + ml_veto_3}
    ml_vetoed = list(ml_vetoed_dict.values())

    macro_vetoed_dict = {row['ticker']: row for row in macro_veto_1 + macro_veto_2 + macro_veto_3}
    macro_vetoed = list(macro_vetoed_dict.values())

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
            
            is_divergent = False
            if prev_val != 'N/A' and fcst_val != 'N/A':
                prev_num = _extract_numeric(prev_val)
                fcst_num = _extract_numeric(fcst_val)
                if prev_num is not None and fcst_num is not None and abs(prev_num - fcst_num) > 0.0001:
                    is_divergent = True
                elif prev_val != fcst_val:  # fallback to string comparison
                    is_divergent = True
            
            flag = "⚠️ " if is_divergent else ""
            if ai_warning > 2.0:
                flag = "🚨 [AI VOLATILITY WARNING] "

            report += f"🔹 **{ev_date}** | [{currency}] {ev_name}\n"
            report += f"&nbsp;&nbsp;&nbsp;↳ {flag}*Forecast:* {fcst_val} | *Previous:* {prev_val}\n\n"
    
    report += "## Executive Summary\n"
    report += f"Automated overnight screening executed against {len(data)} tracked equities. "
    report += "Identifies high-probability statistical anomalies, momentum shifts, and risk-management triggers based on institutional-grade technical parameters.\n\n"
    
    report += "## 🎯 Long-Term Entry Setups\n"
    report += "*Quality A/B-grade stocks in a confirmed uptrend (above 200D MA) that have pulled back into a healthy RSI zone (35–60). Filtered for low volatility and a composite score ≥ 20. Purpose-built for buy-and-hold entry timing.*\n\n"
    report += _format_longterm_setup_list(longterm_setups, target_date)

    report += "## 📉 Oversold Reversals\n"
    report += "*Aggressively sold off (RSI < 30) but demonstrating early quantitative signs of momentum recovery (Positive MACD Histogram). High-conviction, deep-value entry opportunities.*\n\n"
    report += _format_mobile_markdown_list(oversold, target_date)

    report += "## 📈 MACD Bullish Crosses\n"
    report += "*Triggered a Bullish MACD Crossover, indicating a mathematical momentum reversal to the upside and underlying institutional accumulation.*\n\n"
    report += _format_mobile_markdown_list(macd_crosses, target_date)

    report += "## 🚀 Momentum & Volume Surges\n"
    report += "*Explosive buying volume (Volume > 1.5x 20-Day SMA) operating in a healthy momentum band (RSI 50-70). Strong institutional backing without immediate overbought exhaustion risk.*\n\n"
    report += _format_mobile_markdown_list(surges, target_date)

    if macro_vetoed:
        report += "## 🏛️ Macro/Liquidity Vetoed Setups\n"
        report += "*These equities triggered algorithmic buy signals but were VETOED by the Intermarket Engine due to surging global interest rates or failing systemic credit spreads. Avoid entry.*\n\n"
        report += _format_mobile_markdown_list(macro_vetoed, target_date)

    if ml_vetoed:
        report += "## 🤖 AI Vetoed Setups (Divergence Warnings)\n"
        report += "*These equities triggered algorithmic buy signals, but the Machine Learning Ensemble predicts a high probability of failure (Confidence < 40%). Proceed with extreme caution.*\n\n"
        report += _format_mobile_markdown_list(ml_vetoed, target_date)

    report += "## 🚨 Overbought Warnings (Distribution Risk)\n"
    report += "*Risk Management Alert: Mathematically overextended and beginning to flash negative momentum divergence (Negative MACD Histogram). Trim positions or tighten stop-losses, as algorithmic mean-reversion is highly probable.*\n\n"
    report += _format_mobile_markdown_list(warnings, target_date)

    if deterioration_alerts:
        report += "## 📉 Portfolio Deterioration Alerts\n"
        report += "*Holdings where the composite score has dropped 15+ points over the past 5 days. The technical or fundamental thesis may be weakening — review before adding or holding.*\n\n"
        for alert in deterioration_alerts:
            ticker = alert['ticker']
            today_score = alert['score_today']
            past_score  = alert['score_past']
            drop        = alert['score_drop']
            past_date   = alert['past_date']
            price_str   = f"${alert['close_price']:.2f}" if _is_valid_numeric(alert.get('close_price')) else "N/A"
            report += f"⚠️ **{ticker}** ({price_str}) | **Score now:** {today_score} | **Score {past_date}:** {past_score} | **Drop:** {drop:+d} pts\n\n"

    report += "---\n"
    report += "*Generated automatically by the Quantamental Python Engine.*"
    
    reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
    os.makedirs(reports_dir, exist_ok=True)
    file_path = os.path.join(reports_dir, f"quant_briefing_{target_date}.md")
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(report)
        logger.info("Morning Briefing successfully saved to: %s", file_path)
    except Exception as e:
        logger.error("Failed to write report to disk: %s", e)
        
    return report

if __name__ == "__main__":
    scan_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    raw_signals = fetch_latest_signals(scan_date)

    if not raw_signals:
        logger.warning("No data found for %s. Ensure the quant_engine ran successfully overnight.", scan_date)
    else:
        markdown_output = generate_markdown_briefing(scan_date, raw_signals)
        print("\n" + "="*60 + "\n")
        print(markdown_output)
        print("\n" + "="*60 + "\n")