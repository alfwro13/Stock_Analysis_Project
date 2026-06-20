"""Generates the Morning Quant Briefing: overnight portfolio news, US futures, UK pre-open context, and quant screener signals."""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from yahoo_engine import yahoo_engine

import time_engine
from config import PORTFOLIO_PATH, HISTORICAL_DIR
from database import get_connection
from quant_screener import fetch_latest_signals, generate_markdown_briefing
from regime_engine import get_latest_regime
from utils import normalize_ticker

logger = logging.getLogger(__name__)

_US_PULSE_TICKERS = ["^GSPC", "^NDX", "^TNX", "DX-Y.NYB", "BZ=F"]
_UK_PULSE_TICKERS = ["^FTSE", "^FTMC", "GBPUSD=X", "UK10YG"]

_US_DISPLAY_NAMES = {
    "^GSPC":    "S&P 500",
    "^NDX":     "Nasdaq 100",
    "^TNX":     "US 10Y Yield",
    "DX-Y.NYB": "Dollar Index",
    "BZ=F":     "Brent Crude",
}
_UK_DISPLAY_NAMES = {
    "^FTSE":    "FTSE 100",
    "^FTMC":    "FTSE 250",
    "GBPUSD=X": "GBP/USD",
    "UK10YG":   "UK 10Y Gilt",
}


def _load_portfolio_tickers() -> list[str]:
    try:
        with open(PORTFOLIO_PATH, "r") as f:
            data = json.load(f)
        return [normalize_ticker(v["ticker"]) for v in data.values() if "ticker" in v]
    except Exception:
        logger.warning("Could not load portfolio tickers for morning briefing.")
        return []


def _get_company_names(tickers: list[str]) -> dict[str, str]:
    if not tickers:
        return {}
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in tickers)
        cursor.execute(
            f"SELECT ticker, company_name FROM stock_signals WHERE ticker IN ({placeholders})",
            tickers,
        )
        result = {row["ticker"]: (row["company_name"] or row["ticker"]) for row in cursor.fetchall()}
    except Exception:
        result = {}
    finally:
        if conn:
            conn.close()
    return {t: result.get(t, t) for t in tickers}


def _get_pulse_rows(tickers: list[str]) -> dict[str, dict]:
    if not tickers:
        return {}
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in tickers)
        cursor.execute(
            f"SELECT ticker, name, price, change_pct, is_positive "
            f"FROM market_pulse_cache WHERE ticker IN ({placeholders})",
            tickers,
        )
        return {row["ticker"]: dict(row) for row in cursor.fetchall()}
    except Exception:
        return {}
    finally:
        if conn:
            conn.close()


def _format_age(pub_time: datetime) -> str:
    now = datetime.now(timezone.utc)
    delta_secs = (now - pub_time).total_seconds()
    if delta_secs < 3600:
        return f"{int(delta_secs / 60)}min ago"
    return f"{delta_secs / 3600:.0f}h ago"


def fetch_portfolio_news(
    tickers: list[str],
    since_dt: datetime,
    max_per_ticker: int = 3,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch Yahoo Finance news per ticker; since_dt must be UTC-aware; returns max_per_ticker items newest-first."""
    result: dict[str, list[dict]] = {}

    for ticker in tickers:
        try:
            raw_news = yahoo_engine.get_news(ticker) or []
            parsed: list[tuple[datetime, dict]] = []

            for item in raw_news:
                content = item.get("content", item)
                title = content.get("title", "")
                if not title:
                    continue

                summary = (
                    content.get("summary", "")
                    or content.get("description", "")
                    or ""
                )
                publisher = content.get("publisher", "")
                if not publisher and isinstance(content.get("provider"), dict):
                    publisher = content["provider"].get("displayName", "")

                pub_time_raw = (
                    content.get("pubDate")
                    or content.get("providerPublishTime")
                    or item.get("providerPublishTime", 0)
                )
                try:
                    if isinstance(pub_time_raw, str):
                        pub_time = pd.to_datetime(pub_time_raw, utc=True).to_pydatetime()
                    else:
                        pub_time = datetime.fromtimestamp(
                            float(pub_time_raw), tz=timezone.utc
                        )

                    if pub_time >= since_dt:
                        parsed.append((
                            pub_time,
                            {
                                "title": title,
                                "summary": summary[:250].strip() if summary else "",
                                "publisher": publisher or "Source",
                                "age_str": _format_age(pub_time),
                            },
                        ))
                except Exception:
                    continue

            parsed.sort(key=lambda x: x[0], reverse=True)
            result[ticker] = [item for _, item in parsed[:max_per_ticker]]

        except Exception as e:
            logger.warning("News fetch failed for %s: %s", ticker, e)
            result[ticker] = []

        time.sleep(0.3)

    return result


def _render_news_section(
    tickers: list[str],
    news_data: dict[str, list[dict]],
    company_names: dict[str, str],
    window_desc: str,
) -> str:
    out = f"## 📰 Overnight News — Your Holdings\n"
    out += f"*{window_desc} — portfolio holdings only*\n\n"

    tickers_with_news = [t for t in tickers if news_data.get(t)]
    tickers_without = [t for t in tickers if not news_data.get(t)]

    if not tickers_with_news and tickers_without:
        out += "*No recent news found for any of your holdings in this time window.*\n\n"
        return out

    for ticker in tickers_with_news:
        name = company_names.get(ticker, ticker)
        out += f"**{ticker}** — {name}\n"
        for item in news_data[ticker]:
            out += f"- *{item['publisher']} ({item['age_str']}):* {item['title']}\n"
            if item["summary"]:
                out += f"  > {item['summary']}{'…' if len(item['summary']) >= 250 else ''}\n"
        out += "\n"

    if tickers_without:
        out += f"*No overnight news found for: {', '.join(tickers_without)}*\n\n"

    return out


def _render_us_futures(pulse: dict[str, dict]) -> str:
    out = "## 📈 US Futures & Macro\n\n"
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
        if sym in ("^TNX", "UK10YG"):
            price_str = f"{price:.2f}%" if price is not None else "—"
        chg_str = f"{chg:+.2f}%" if chg is not None else "—"
        out += f"| {display} | {price_str} | {chg_str} |\n"

    out += "\n"
    return out


def generate_uk_charts(target_date: str) -> dict[str, str]:
    """Generate FTSE/Gilt/GBPUSD PNG snapshots into static/briefing_charts/; returns {key: /static/... URL}."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    charts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "briefing_charts")
    os.makedirs(charts_dir, exist_ok=True)

    BG = "#0d1117"
    GRID = "#21262d"
    LABEL = "#9ca3af"

    def _style(ax, fig):
        fig.patch.set_facecolor(BG)
        ax.set_facecolor(BG)
        ax.tick_params(colors=LABEL, labelsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=0, ha="center")
        for spine in ax.spines.values():
            spine.set_color(GRID)
        ax.grid(True, color=GRID, linestyle="--", linewidth=0.5, alpha=0.7)
        ax.title.set_color("white")

    result: dict[str, str] = {}

    # 1. FTSE 100
    try:
        df = yahoo_engine.get_price_history(["^FTSE"], period="14d", interval="1d").get("^FTSE", pd.DataFrame())
        if not df.empty and len(df) >= 3:
            fig, ax = plt.subplots(figsize=(8, 3))
            _style(ax, fig)
            ax.plot(df.index, df["Close"], color="#4da6ff", linewidth=2)
            ax.fill_between(df.index, df["Close"], df["Close"].min() * 0.998, alpha=0.12, color="#4da6ff")
            ax.set_title("FTSE 100 — 10 Day", fontsize=10, pad=6)
            ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
            fig.tight_layout(pad=1.2)
            path = os.path.join(charts_dir, f"ftse_{target_date}.png")
            fig.savefig(path, dpi=110, bbox_inches="tight", facecolor=BG)
            plt.close(fig)
            result["ftse"] = f"/static/briefing_charts/ftse_{target_date}.png"
            logger.info("FTSE chart saved to %s", path)
    except Exception as e:
        logger.warning("FTSE chart generation failed: %s", e)

    # 2. UK 10Y Gilt (from parquet baseline maintained by the Gilt Data Service)
    try:
        gilt_path = HISTORICAL_DIR / "UK_GILT_BASELINE.parquet"
        if gilt_path.exists():
            df_gilt = pd.read_parquet(gilt_path).tail(14)
            if not df_gilt.empty and len(df_gilt) >= 3:
                fig, ax = plt.subplots(figsize=(8, 3))
                _style(ax, fig)
                ax.plot(df_gilt.index, df_gilt["Close"], color="#ff6b6b", linewidth=2)
                ax.fill_between(df_gilt.index, df_gilt["Close"], df_gilt["Close"].min() * 0.998, alpha=0.12, color="#ff6b6b")
                ax.set_title("UK 10Y Gilt Yield — 10 Day", fontsize=10, pad=6)
                ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.2f}%"))
                fig.tight_layout(pad=1.2)
                path = os.path.join(charts_dir, f"gilt_{target_date}.png")
                fig.savefig(path, dpi=110, bbox_inches="tight", facecolor=BG)
                plt.close(fig)
                result["gilt"] = f"/static/briefing_charts/gilt_{target_date}.png"
                logger.info("Gilt chart saved to %s", path)
    except Exception as e:
        logger.warning("Gilt chart generation failed: %s", e)

    # 3. GBP/USD
    try:
        df = yahoo_engine.get_price_history(["GBPUSD=X"], period="14d", interval="1d").get("GBPUSD=X", pd.DataFrame())
        if not df.empty and len(df) >= 3:
            fig, ax = plt.subplots(figsize=(8, 3))
            _style(ax, fig)
            ax.plot(df.index, df["Close"], color="#00d2a0", linewidth=2)
            ax.fill_between(df.index, df["Close"], df["Close"].min() * 0.998, alpha=0.12, color="#00d2a0")
            ax.set_title("GBP/USD — 10 Day", fontsize=10, pad=6)
            ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.4f}"))
            fig.tight_layout(pad=1.2)
            path = os.path.join(charts_dir, f"gbpusd_{target_date}.png")
            fig.savefig(path, dpi=110, bbox_inches="tight", facecolor=BG)
            plt.close(fig)
            result["gbpusd"] = f"/static/briefing_charts/gbpusd_{target_date}.png"
            logger.info("GBP/USD chart saved to %s", path)
    except Exception as e:
        logger.warning("GBP/USD chart generation failed: %s", e)

    return result


def _render_uk_preopen(
    pulse: dict[str, dict],
    regime_data: dict,
    macro_regime: dict,
    charts: dict[str, str] | None = None,
) -> str:
    uk_regime = regime_data.get("uk_regime_label", "Unknown") if regime_data else "Unknown"
    uk_turb = regime_data.get("uk_turbulence", 0.0) if regime_data else 0.0
    uk_threat = macro_regime.get("uk_threat_level", "GREEN")
    uk_vel_raw = macro_regime.get("uk_yield_velocity")
    uk_vel = float(uk_vel_raw) if uk_vel_raw is not None else 0.0

    out = "## 🇬🇧 UK Pre-Open Snapshot\n"

    regime_icon = {"Normal": "🟢", "Volatile": "🟡", "Crash": "🔴"}.get(uk_regime, "⚪")
    threat_icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(uk_threat, "⚪")

    out += f"**Regime:** {regime_icon} {uk_regime} *(Turbulence: {uk_turb:.1f}%)* | "
    out += f"**Yield Threat:** {threat_icon} {uk_threat} *(Velocity: {uk_vel:+.2f}%)*\n\n"

    for sym in _UK_PULSE_TICKERS:
        row = pulse.get(sym)
        display = _UK_DISPLAY_NAMES.get(sym, sym)
        if not row:
            out += f"**{display}:** — | "
            continue
        price = row.get("price")
        chg = row.get("change_pct")
        if sym == "GBPUSD=X":
            price_str = f"{price:.4f}" if price is not None else "—"
        elif sym in ("UK10YG", "^TNX"):
            price_str = f"{price:.2f}%" if price is not None else "—"
        else:
            price_str = f"{price:,.0f}" if price is not None else "—"
        chg_str = f"({chg:+.2f}%)" if chg is not None else ""
        out += f"**{display}:** {price_str} {chg_str}  "

    out += "\n\n"

    if uk_regime == "Crash":
        out += "> ⚠️ UK markets are in **Crash regime**. Elevated volatility expected at open.\n\n"
    elif uk_regime == "Volatile":
        out += "> ⚡ UK markets are in **Volatile regime**. Exercise caution at open.\n\n"
    else:
        out += "> Market conditions are normal. Standard risk parameters apply.\n\n"

    if charts:
        if "ftse" in charts:
            out += f"![FTSE 100]({charts['ftse']})\n"
        if "gilt" in charts:
            out += f"![UK 10Y Gilt]({charts['gilt']})\n"
        if "gbpusd" in charts:
            out += f"![GBP/USD]({charts['gbpusd']})\n"
        out += "\n"

    return out


def generate_morning_briefing(target_date: str) -> str:
    """Assemble the morning briefing markdown (news → futures → UK pre-open → quant signals) and write to reports/."""
    generated_at = time_engine.now_local().strftime("%H:%M")

    logger.info("Generating morning briefing for %s", target_date)

    tickers = _load_portfolio_tickers()
    company_names = _get_company_names(tickers)
    pulse = _get_pulse_rows(_US_PULSE_TICKERS + _UK_PULSE_TICKERS)
    regime_data = get_latest_regime()

    conn = None
    macro_regime: dict = {}
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM macro_regimes ORDER BY date DESC LIMIT 1")
        macro_row = cursor.fetchone()
        macro_regime = dict(macro_row) if macro_row else {}
    except Exception:
        pass
    finally:
        if conn:
            conn.close()

    # ~12 hours back captures aftermarket + overnight
    since_dt = datetime.now(timezone.utc) - timedelta(hours=12)
    window_desc = "Aftermarket + overnight news (approx 21:00 UK yesterday — now)"

    news_data: dict[str, list[dict]] = {}
    if tickers:
        logger.info("Fetching overnight news for %d portfolio tickers...", len(tickers))
        news_data = fetch_portfolio_news(tickers, since_dt)

    logger.info("Generating UK market chart snapshots...")
    charts = generate_uk_charts(target_date)

    signals = fetch_latest_signals(target_date)
    if not signals:
        yesterday = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        signals = fetch_latest_signals(yesterday)

    report = f"# 🌅 Quant Morning Briefing — {target_date}\n"
    report += f"**Generated:** {generated_at} UK (pre-open) | Overnight & aftermarket coverage\n\n"
    report += "---\n\n"

    if tickers:
        report += _render_news_section(tickers, news_data, company_names, window_desc)
        report += "---\n\n"

    report += _render_us_futures(pulse)
    report += "---\n\n"

    report += _render_uk_preopen(pulse, regime_data, macro_regime, charts=charts)
    report += "---\n\n"

    # Strip the auto-generated quant screener title to avoid a duplicate header in the briefing.
    if signals:
        quant_md = generate_markdown_briefing(target_date, signals)
        # Drop first two lines ("# 📊 Morning Quant Briefing\n**Date:** ...\n\n")
        lines = quant_md.split("\n")
        skip = 0
        for i, line in enumerate(lines):
            if line.startswith("# ") or line.startswith("**Date:**"):
                skip = i + 1
            elif skip and line.strip() == "":
                skip = i + 1
            else:
                if i > skip:
                    break
        report += "\n".join(lines[skip:])
    else:
        report += "*⚠️ No quant signals available for today — overnight scan may not have run yet.*\n"

    reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    file_path = os.path.join(reports_dir, f"morning_briefing_{target_date}.md")
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(report)
        logger.info("Morning briefing saved to %s", file_path)
    except Exception as e:
        logger.error("Failed to write morning briefing to disk: %s", e)

    return report
