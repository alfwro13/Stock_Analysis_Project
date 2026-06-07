"""Lunchtime Quant Briefing: mid-session news, UK snapshot, US pre-market, intraday alerts."""

import logging
import os
from datetime import datetime, timedelta, timezone

import time_engine
from database import get_connection
from morning_briefing import (
    fetch_portfolio_news,
    generate_uk_charts,
    _load_portfolio_tickers,
    _get_company_names,
    _get_pulse_rows,
    _render_news_section,
    _US_PULSE_TICKERS,
    _UK_PULSE_TICKERS,
    _US_DISPLAY_NAMES,
    _UK_DISPLAY_NAMES,
)
from quant_screener import fetch_upcoming_macro_events, _extract_numeric
from regime_engine import get_latest_regime

logger = logging.getLogger(__name__)


def _render_uk_midsession(
    pulse: dict,
    regime_data: dict,
    macro_regime: dict,
    charts: dict | None = None,
) -> str:
    uk_regime = regime_data.get("uk_regime_label", "Unknown") if regime_data else "Unknown"
    uk_turb = regime_data.get("uk_turbulence", 0.0) if regime_data else 0.0
    uk_threat = macro_regime.get("uk_threat_level", "GREEN")
    uk_vel_raw = macro_regime.get("uk_yield_velocity")
    uk_vel = float(uk_vel_raw) if uk_vel_raw is not None else 0.0
    us_regime = regime_data.get("us_regime_label", "Unknown") if regime_data else "Unknown"
    us_turb = regime_data.get("us_turbulence", 0.0) if regime_data else 0.0
    us_threat = macro_regime.get("us_threat_level", "GREEN")
    us_vel_raw = macro_regime.get("us_yield_velocity")
    us_vel = float(us_vel_raw) if us_vel_raw is not None else 0.0

    regime_icon = {"Normal": "🟢", "Volatile": "🟡", "Crash": "🔴"}
    threat_icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}

    out = "## 🌍 UK Mid-Session\n"
    out += (
        f"**UK Regime:** {regime_icon.get(uk_regime, '⚪')} {uk_regime} "
        f"*(Turbulence: {uk_turb:.1f}%)* | "
        f"**Yield Threat:** {threat_icon.get(uk_threat, '⚪')} {uk_threat} "
        f"*(Velocity: {uk_vel:+.2f}%)*\n\n"
    )

    for sym in _UK_PULSE_TICKERS:
        row = pulse.get(sym)
        display = _UK_DISPLAY_NAMES.get(sym, sym)
        if not row:
            out += f"**{display}:** —  "
            continue
        price = row.get("price")
        chg = row.get("change_pct")
        if sym == "GBPUSD=X":
            price_str = f"{price:.4f}" if price is not None else "—"
        elif sym in ("UK10YG",):
            price_str = f"{price:.2f}%" if price is not None else "—"
        else:
            price_str = f"{price:,.0f}" if price is not None else "—"
        chg_str = f"({chg:+.2f}%)" if chg is not None else ""
        out += f"**{display}:** {price_str} {chg_str}  "

    out += "\n\n"

    out += "## 🌐 Global Regime Context\n"
    out += (
        f"**US:** {regime_icon.get(us_regime, '⚪')} {us_regime} "
        f"*(Turbulence: {us_turb:.1f}%)* | "
        f"**Yield:** {threat_icon.get(us_threat, '⚪')} {us_threat} "
        f"*(Velocity: {us_vel:+.2f}%)*\n"
    )
    out += (
        f"**UK:** {regime_icon.get(uk_regime, '⚪')} {uk_regime} "
        f"*(Turbulence: {uk_turb:.1f}%)* | "
        f"**Yield:** {threat_icon.get(uk_threat, '⚪')} {uk_threat} "
        f"*(Velocity: {uk_vel:+.2f}%)*\n\n"
    )

    combined_worst = (
        "Crash" if "Crash" in (us_regime, uk_regime)
        else "Volatile" if "Volatile" in (us_regime, uk_regime)
        else "Normal"
    )
    if combined_worst == "Crash":
        out += "> ⚠️ **Crash conditions active.** Elevated risk across both regions.\n\n"
    elif combined_worst == "Volatile":
        out += "> ⚡ **Volatile conditions.** Apply tighter risk controls.\n\n"
    else:
        out += "> Standard conditions. No systemic warnings active.\n\n"

    if charts:
        if "ftse" in charts:
            out += f"![FTSE 100]({charts['ftse']})\n"
        if "gilt" in charts:
            out += f"![UK 10Y Gilt]({charts['gilt']})\n"
        if "gbpusd" in charts:
            out += f"![GBP/USD]({charts['gbpusd']})\n"
        out += "\n"

    return out


def _render_us_premarket(pulse: dict) -> str:
    out = "## 📈 US Pre-Market\n"
    out += "*US markets open at approx 14:30 UK*\n\n"
    out += "| Asset | Price | Change |\n"
    out += "|-------|-------|--------|\n"

    for sym in _US_PULSE_TICKERS:
        row = pulse.get(sym)
        display = _US_DISPLAY_NAMES.get(sym, sym)
        if not row:
            out += f"| {display} | — | — |\n"
            continue
        price = row.get("price")
        chg = row.get("change_pct")
        price_str = f"{price:,.2f}" if price is not None else "—"
        if sym in ("^TNX",):
            price_str = f"{price:.2f}%" if price is not None else "—"
        chg_str = f"{chg:+.2f}%" if chg is not None else "—"
        out += f"| {display} | {price_str} | {chg_str} |\n"

    out += "\n"
    return out


def _render_intraday_alerts(since_dt: datetime) -> str:
    out = "## ⚡ Intraday Alerts Since Morning Briefing\n"
    try:
        conn = get_connection()
        cursor = conn.cursor()
        # alert_state.last_fired_utc is an ISO string; filter alerts from today after since_dt
        since_str = since_dt.strftime("%Y-%m-%dT%H:%M:%S")
        cursor.execute(
            "SELECT engine, ticker, last_fired_utc FROM alert_state "
            "WHERE last_fired_utc >= ? ORDER BY last_fired_utc DESC",
            (since_str,),
        )
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            out += "*No crash or anomaly alerts triggered since this morning's briefing.*\n\n"
        else:
            for row in rows:
                out += f"⚠️ **{row['ticker']}** — {row['engine']} alert fired at {row['last_fired_utc']} UTC\n"
            out += "\n"
    except Exception as e:
        logger.warning("Could not query intraday alerts: %s", e)
        out += "*Alert history unavailable.*\n\n"

    return out


def _render_macro_events(target_date: str) -> str:
    macro_events = fetch_upcoming_macro_events(target_date)

    out = "## 📅 Macro Events — Next 48 Hours\n"
    if not macro_events:
        out += "*No Tier-1 macroeconomic events scheduled for USD or GBP in the next 48 hours.*\n\n"
        return out

    for ev in macro_events:
        ev_date = ev.get("event_date", "N/A")
        ev_name = ev.get("event_name", "Unknown Event")
        currency = ev.get("currency", "USD")
        prev_val = ev.get("previous_val", "N/A")
        fcst_val = ev.get("forecast_val", "N/A")
        ai_warning = ev.get("ai_volatility_warning", 0.0)

        is_divergent = False
        if prev_val != "N/A" and fcst_val != "N/A":
            try:
                prev_num = _extract_numeric(str(prev_val))
                fcst_num = _extract_numeric(str(fcst_val))
                if prev_num is not None and fcst_num is not None and abs(prev_num - fcst_num) > 0.0001:
                    is_divergent = True
                elif prev_val != fcst_val:
                    is_divergent = True
            except Exception:
                if prev_val != fcst_val:
                    is_divergent = True

        flag = "⚠️ " if is_divergent else ""
        if ai_warning and float(ai_warning) > 2.0:
            flag = "🚨 [AI VOLATILITY WARNING] "

        out += f"🔹 **{ev_date}** | [{currency}] {ev_name}\n"
        out += f"&nbsp;&nbsp;&nbsp;↳ {flag}*Forecast:* {fcst_val} | *Previous:* {prev_val}\n\n"

    return out


def generate_lunchtime_briefing(target_date: str) -> str:
    generated_at = time_engine.now_local().strftime("%H:%M")

    logger.info("Generating lunchtime briefing for %s", target_date)

    # --- Data loading ---
    tickers = _load_portfolio_tickers()
    company_names = _get_company_names(tickers)
    pulse = _get_pulse_rows(_US_PULSE_TICKERS + _UK_PULSE_TICKERS)
    regime_data = get_latest_regime()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM macro_regimes ORDER BY date DESC LIMIT 1")
    macro_row = cursor.fetchone()
    conn.close()
    macro_regime = dict(macro_row) if macro_row else {}

    # Morning session news window: ~5 hours back (07:15 UK to 12:00 UK)
    since_dt = datetime.now(timezone.utc) - timedelta(hours=5)
    morning_start_str = (time_engine.now_local() - timedelta(hours=5)).strftime("%H:%M")
    window_desc = f"News published since ~{morning_start_str} UK (morning session)"

    news_data: dict = {}
    if tickers:
        logger.info("Fetching morning session news for %d portfolio tickers...", len(tickers))
        # fetch_portfolio_news expects naive UTC for its internal pub_time comparisons
        news_data = fetch_portfolio_news(tickers, since_dt.replace(tzinfo=None))

    # --- UK charts (regenerated with live mid-session data) ---
    logger.info("Generating UK market chart snapshots for lunchtime briefing...")
    charts = generate_uk_charts(target_date)

    # --- Assemble report ---
    report = f"# 🕛 Quant Lunch Briefing — {target_date}\n"
    report += f"**Generated:** {generated_at} UK | Morning session update\n\n"
    report += "---\n\n"

    if tickers:
        section = _render_news_section(tickers, news_data, company_names, window_desc)
        # Retitle the news section for the lunchtime context
        section = section.replace(
            "## 📰 Overnight News — Your Holdings",
            "## 📰 Morning News — Your Holdings",
        )
        report += section
        report += "---\n\n"

    report += _render_uk_midsession(pulse, regime_data, macro_regime, charts=charts)
    report += "---\n\n"

    report += _render_us_premarket(pulse)
    report += "---\n\n"

    report += _render_intraday_alerts(since_dt)
    report += "---\n\n"

    report += _render_macro_events(target_date)

    report += "---\n"
    report += "*Generated automatically by the Quantamental Python Engine.*"

    # --- Save to disk ---
    reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    file_path = os.path.join(reports_dir, f"lunch_briefing_{target_date}.md")
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(report)
        logger.info("Lunchtime briefing saved to %s", file_path)
    except Exception as e:
        logger.error("Failed to write lunchtime briefing to disk: %s", e)

    return report
