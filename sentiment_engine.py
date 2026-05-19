# sentiment_engine.py
import os
import json
import time
import random
import logging
import threading
import pandas as pd
import requests
from fake_useragent import UserAgent
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Critical for headless Linux servers: Use 'Agg' backend so matplotlib doesn't crash
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# NLP Sentiment Analyzer (FinBERT via HuggingFace)
from transformers import pipeline

from nextcloud_talk import upload_file_webdav, share_file_to_talk, send_text_message
from config import (
    NEXTCLOUD_URL, BOT_USERNAME, APP_PASSWORD, CONVERSATION_TOKEN,
    load_config, HISTORICAL_DIR
)
from database import get_connection

# Configure robust module-level logging
logger = logging.getLogger(__name__)

# --- GLOBAL THREAD-SAFE STORAGE AND STATE MATRIX ---
_CACHE_LOCK = threading.Lock()
_IS_REFRESHING = False
_LAST_CACHE_TIME = 0.0

_MACRO_HTML_CACHE: Dict[str, str] = {
    "sentiment_html": "",
    "vix_spy_html": "",
    "yield_equity_html": "",
    "uk_yield_equity_html": "",
    "ftse_gbp_html": ""
}


# ==========================================================
# 1. MACRO SENTIMENT (FEAR & GREED INDEX)
# ==========================================================

def fetch_fear_greed_data(start_date_str: str) -> pd.DataFrame:
    base_url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/"
    ua = UserAgent()
    headers = {'User-Agent': ua.random}
    try:
        r = requests.get(base_url + start_date_str, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        fng_list = data['fear_and_greed_historical']['data']
        fng_df = pd.DataFrame(fng_list)
        fng_df['Date'] = pd.to_datetime(fng_df['x'], unit='ms').dt.date
        fng_df = fng_df.rename(columns={'y': 'Fear_Greed_Index'})
        fng_df = fng_df[['Date', 'Fear_Greed_Index']]
        fng_df.set_index('Date', inplace=True)
        return fng_df
    except Exception as e:
        logger.error(f"Fetching F&G data failed: {e}")
        return pd.DataFrame()


def fetch_stock_data(ticker: str, start_date: str) -> pd.DataFrame:
    stock_df = yf.download(
        tickers=ticker, start=start_date, progress=False, auto_adjust=True
    )
    if stock_df.empty:
        return pd.DataFrame()
    if isinstance(stock_df.columns, pd.MultiIndex):
        stock_df.columns = stock_df.columns.get_level_values(0)
    stock_df.reset_index(inplace=True)
    stock_df['Date'] = stock_df['Date'].dt.date
    stock_df.set_index('Date', inplace=True)
    if 'Close' not in stock_df.columns:
        return pd.DataFrame()
    return stock_df[['Close']].rename(columns={'Close': f'{ticker}_Close'})


def fetch_parquet_data(parquet_name: str, start_date: str) -> pd.DataFrame:
    """Reads heavily sanitized local baseline data directly from Parquet files."""
    path = HISTORICAL_DIR / parquet_name
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index).date
        df = df[df.index >= pd.to_datetime(start_date).date()]
        # Extract prefix (e.g., 'FTSE' from 'FTSE_BASELINE.parquet')
        prefix = parquet_name.split("_")[0]
        return df[['Close']].rename(columns={'Close': f'{prefix}_Close'})
    except Exception as e:
        logger.error(f"Failed to read parquet {parquet_name}: {e}")
        return pd.DataFrame()


def get_sentiment_data() -> Optional[pd.DataFrame]:
    today = datetime.now()
    start_date = (today - timedelta(days=365)).strftime('%Y-%m-%d')
    fng_data = fetch_fear_greed_data(start_date)
    spy_data = fetch_stock_data('SPY', start_date)
    if fng_data.empty or spy_data.empty:
        return None
    merged_df = spy_data.merge(fng_data, left_index=True, right_index=True, how='left')
    merged_df['Fear_Greed_Index'] = merged_df['Fear_Greed_Index'].ffill()
    merged_df.dropna(inplace=True)
    return merged_df


def generate_sentiment_figure() -> Optional[go.Figure]:
    merged_df = get_sentiment_data()
    if merged_df is None:
        return None

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(
        x=merged_df.index, y=merged_df['SPY_Close'], name="S&P 500",
        line=dict(color='#4da6ff', width=2)), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=merged_df.index, y=merged_df['Fear_Greed_Index'], name="F&G Index",
        line=dict(color='#ff4d4d', dash='dot', width=2)), secondary_y=True)

    levels = {25: 'Fear (25)', 50: 'Neutral (50)', 75: 'Greed (75)'}
    for level, text in levels.items():
        fig.add_hline(
            y=level, line_dash="dash", line_color="#555", secondary_y=True,
            annotation_text=text, annotation_position="top right",
            annotation_font_color="#aaa"
        )

    min_spy = merged_df['SPY_Close'].min() * 0.98
    max_spy = merged_df['SPY_Close'].max() * 1.02

    fig.update_layout(
        title=dict(text="Fear & Greed vs S&P 500 (1 Year)", x=0.5, xanchor='center'),
        template="plotly_dark",
        height=450,
        margin=dict(l=40, r=40, t=60, b=40),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_yaxes(title_text="S&P 500 Price ($)", range=[min_spy, max_spy], secondary_y=False)
    fig.update_yaxes(title_text="Fear & Greed Index (0-100)", range=[0, 100], secondary_y=True)
    return fig


# ==========================================================
# 2. MARKET REGIME (VIX vs SPY)
# ==========================================================

def get_vix_spy_data() -> Optional[pd.DataFrame]:
    try:
        tickers = ["SPY", "^VIX"]
        df = yf.download(
            tickers, period="1y", interval="1d", group_by='ticker',
            auto_adjust=True, progress=False
        )
        
        if df.empty or 'SPY' not in df.columns or '^VIX' not in df.columns:
            logger.error("Failed to fetch SPY or VIX data from Yahoo Finance.")
            return None

        spy_data = df['SPY'].dropna(subset=['Close']).rename(columns={'Close': 'SPY_Close'})
        vix_data = df['^VIX'].dropna(subset=['Close']).rename(columns={'Close': 'VIX_Close'})
        
        if spy_data.empty or vix_data.empty:
            logger.error("Incomplete data received for SPY or VIX.")
            return None

        merged_df = spy_data[['SPY_Close']].merge(
            vix_data[['VIX_Close']], left_index=True, right_index=True, how='inner'
        )
        return merged_df
        
    except Exception as e:
        logger.error(f"Fatal error fetching VIX vs SPY data: {e}")
        return None


def generate_vix_spy_figure() -> Optional[go.Figure]:
    merged_df = get_vix_spy_data()
    if merged_df is None:
        return None

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(
        x=merged_df.index, y=merged_df['SPY_Close'], name="S&P 500",
        line=dict(color='#4da6ff', width=2)), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=merged_df.index, y=merged_df['VIX_Close'], name="VIX",
        line=dict(color='#ffaa00', dash='dot', width=2)), secondary_y=True)

    fig.add_hline(
        y=20, line_dash="dash", line_color="#ffaa00", secondary_y=True,
        annotation_text="Volatile (20)", annotation_position="top right",
        annotation_font_color="#ffaa00"
    )
    fig.add_hline(
        y=30, line_dash="dash", line_color="#ff4d4d", secondary_y=True,
        annotation_text="Crash (30)", annotation_position="top right",
        annotation_font_color="#ff4d4d"
    )

    min_spy = merged_df['SPY_Close'].min() * 0.98
    max_spy = merged_df['SPY_Close'].max() * 1.02

    fig.update_layout(
        title=dict(text="S&P 500 vs VIX (1 Year)", x=0.5, xanchor='center'),
        template="plotly_dark",
        height=450,
        margin=dict(l=40, r=40, t=60, b=40),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    
    fig.update_yaxes(title_text="S&P 500 Price ($)", range=[min_spy, max_spy], secondary_y=False)
    max_vix = max(50, merged_df['VIX_Close'].max() * 1.1)
    fig.update_yaxes(title_text="VIX Level", range=[0, max_vix], secondary_y=True)
    return fig


# ==========================================================
# 3. INTERMARKET COST OF CAPITAL (YIELDS VS EQUITIES)
# ==========================================================

def get_yield_equity_html() -> str:
    """Renders the Cost of Capital baseline chart comparing Treasury yields against Equities."""
    _check_and_trigger_async_refresh()
    if _MACRO_HTML_CACHE.get("yield_equity_html"):
        return _MACRO_HTML_CACHE["yield_equity_html"]
    
    today = datetime.now()
    start_date = (today - timedelta(days=365)).strftime('%Y-%m-%d')
    spy_data = fetch_stock_data('SPY', start_date)
    tyx_data = fetch_stock_data('^TYX', start_date)
    
    if spy_data.empty or tyx_data.empty:
        return "<p>Error loading US Cost of Capital data.</p>"
    merged_df = spy_data.merge(tyx_data, left_index=True, right_index=True, how='inner')

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(
        x=merged_df.index, y=merged_df['SPY_Close'], name="S&P 500",
        line=dict(color='#4da6ff', width=2)), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=merged_df.index, y=merged_df['^TYX_Close'], name="30Y Treasury Yield",
        line=dict(color='#ff4d4d', dash='dot', width=2)), secondary_y=True)

    fig.update_layout(
        title=dict(text="US Cost of Capital: 30Y Treasury Yield vs S&P 500 (1 Year)", x=0.5, xanchor='center'),
        template="plotly_dark", height=450, margin=dict(l=40, r=40, t=60, b=40),
        hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_yaxes(title_text="S&P 500 Price ($)", secondary_y=False)
    fig.update_yaxes(title_text="30Y Yield (%)", secondary_y=True)
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})


def get_uk_yield_equity_html() -> str:
    """Renders the Cost of Capital baseline chart comparing UK 10Y Gilt yields against FTSE 100."""
    _check_and_trigger_async_refresh()
    if _MACRO_HTML_CACHE.get("uk_yield_equity_html"):
        return _MACRO_HTML_CACHE["uk_yield_equity_html"]
    
    today = datetime.now()
    start_date = (today - timedelta(days=365)).strftime('%Y-%m-%d')
    ftse_data = fetch_parquet_data('FTSE_BASELINE.parquet', start_date)
    gilt_data = fetch_parquet_data('UK_GILT_BASELINE.parquet', start_date)
    
    if ftse_data.empty or gilt_data.empty:
        return "<p>Error loading UK Cost of Capital data. Ensure Gilt Data Service has run.</p>"
        
    merged_df = ftse_data.merge(gilt_data, left_index=True, right_index=True, how='inner')

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(
        x=merged_df.index, y=merged_df['FTSE_Close'], name="FTSE 100",
        line=dict(color='#4da6ff', width=2)), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=merged_df.index, y=merged_df['UK_Close'], name="10Y Gilt Yield",
        line=dict(color='#ff4d4d', dash='dot', width=2)), secondary_y=True)

    fig.update_layout(
        title=dict(text="UK Cost of Capital: 10Y Gilt Yield vs FTSE 100 (1 Year)", x=0.5, xanchor='center'),
        template="plotly_dark", height=450, margin=dict(l=40, r=40, t=60, b=40),
        hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_yaxes(title_text="FTSE 100 Points", secondary_y=False)
    fig.update_yaxes(title_text="10Y Gilt Yield (%)", secondary_y=True)
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})


def get_ftse_gbp_html() -> str:
    """Renders the currency impact chart comparing GBP/USD against FTSE 100."""
    _check_and_trigger_async_refresh()
    if _MACRO_HTML_CACHE.get("ftse_gbp_html"):
        return _MACRO_HTML_CACHE["ftse_gbp_html"]
    
    today = datetime.now()
    start_date = (today - timedelta(days=365)).strftime('%Y-%m-%d')
    ftse_data = fetch_parquet_data('FTSE_BASELINE.parquet', start_date)
    gbp_data = fetch_parquet_data('GBPUSD_BASELINE.parquet', start_date)
    
    if ftse_data.empty or gbp_data.empty:
        return "<p>Error loading GBP/USD Data.</p>"
        
    merged_df = ftse_data.merge(gbp_data, left_index=True, right_index=True, how='inner')

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(
        x=merged_df.index, y=merged_df['FTSE_Close'], name="FTSE 100",
        line=dict(color='#4da6ff', width=2)), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=merged_df.index, y=merged_df['GBPUSD_Close'], name="GBP/USD",
        line=dict(color='#00ffcc', dash='dot', width=2)), secondary_y=True)

    fig.update_layout(
        title=dict(text="Currency Impact: GBP/USD vs FTSE 100 (1 Year)", x=0.5, xanchor='center'),
        template="plotly_dark", height=450, margin=dict(l=40, r=40, t=60, b=40),
        hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_yaxes(title_text="FTSE 100 Points", secondary_y=False)
    fig.update_yaxes(title_text="GBP/USD Rate", secondary_y=True)
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})


def get_sentiment_html() -> str:
    """Renders the Fear and Greed Index overlay frame."""
    _check_and_trigger_async_refresh()
    if _MACRO_HTML_CACHE.get("sentiment_html"):
        return _MACRO_HTML_CACHE["sentiment_html"]
    
    fig = generate_sentiment_figure()
    if not fig:
        return "<p>Error loading sentiment data. Please try again later.</p>"
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})


def get_vix_spy_html() -> str:
    """Renders the standard VIX versus SPY regime volatility matrix."""
    _check_and_trigger_async_refresh()
    if _MACRO_HTML_CACHE.get("vix_spy_html"):
        return _MACRO_HTML_CACHE["vix_spy_html"]
        
    fig = generate_vix_spy_figure()
    if not fig:
        return "<p>Error loading VIX data. Please try again later.</p>"
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})


# ==========================================================
# 4. UNIFIED CACHE MANAGEMENT & BACKGROUND PROCESSING WORKER
# ==========================================================

def _check_and_trigger_async_refresh() -> None:
    """Evaluates the cache baseline age and shifts heavy processing out-of-band to a dedicated thread."""
    global _LAST_CACHE_TIME, _IS_REFRESHING
    
    config_data = load_config()
    refresh_rate: int = config_data.get("UI_PREFERENCES", {}).get("REFRESH_RATE", 60)
    current_time: float = time.time()
    
    if (current_time - _LAST_CACHE_TIME) > refresh_rate:
        with _CACHE_LOCK:
            if not _IS_REFRESHING:
                _IS_REFRESHING = True
                logger.info("Macro Sentiment visual cache is stale. Spawning background refresh worker thread...")
                threading.Thread(target=_async_chart_cruncher_worker, daemon=True).start()


def _async_chart_cruncher_worker() -> None:
    """Isolated thread runner dedicated to handling external network I/O loops safely."""
    global _LAST_CACHE_TIME, _IS_REFRESHING
    try:
        logger.info("Background cruncher started compiling Plotly HTML fragments...")
        
        # 1. Re-render Fear and Greed Matrix
        fig_sentiment = generate_sentiment_figure()
        html_sentiment = fig_sentiment.to_html(
            full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False}
        ) if fig_sentiment else ""
        
        # 2. Re-render VIX Volatility Matrix
        fig_vix = generate_vix_spy_figure()
        html_vix = fig_vix.to_html(
            full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False}
        ) if fig_vix else ""
        
        # 3. Re-render US Yield Compression
        today = datetime.now()
        start_date = (today - timedelta(days=365)).strftime('%Y-%m-%d')
        spy_df = fetch_stock_data('SPY', start_date)
        tyx_df = fetch_stock_data('^TYX', start_date)
        
        html_yield_equity = ""
        if not spy_df.empty and not tyx_df.empty:
            m_df = spy_df.merge(tyx_df, left_index=True, right_index=True, how='inner')
            fig_yield_equity = make_subplots(specs=[[{"secondary_y": True}]])
            fig_yield_equity.add_trace(go.Scatter(
                x=m_df.index, y=m_df['SPY_Close'], name="S&P 500",
                line=dict(color='#4da6ff', width=2)), secondary_y=False)
            fig_yield_equity.add_trace(go.Scatter(
                x=m_df.index, y=m_df['^TYX_Close'], name="30Y Treasury Yield",
                line=dict(color='#ff4d4d', dash='dot', width=2)), secondary_y=True)
            fig_yield_equity.update_layout(
                title=dict(text="US Cost of Capital: 30Y Treasury Yield vs S&P 500 (1 Year)", x=0.5, xanchor='center'),
                template="plotly_dark", height=450, margin=dict(l=40, r=40, t=60, b=40),
                hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            fig_yield_equity.update_yaxes(title_text="S&P 500 Price ($)", secondary_y=False)
            fig_yield_equity.update_yaxes(title_text="30Y Yield (%)", secondary_y=True)
            html_yield_equity = fig_yield_equity.to_html(
                full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False}
            )

        # 4. Re-render UK Yield Compression
        ftse_data = fetch_parquet_data('FTSE_BASELINE.parquet', start_date)
        gilt_data = fetch_parquet_data('UK_GILT_BASELINE.parquet', start_date)
        
        html_uk_yield_equity = ""
        if not ftse_data.empty and not gilt_data.empty:
            m_df_uk = ftse_data.merge(gilt_data, left_index=True, right_index=True, how='inner')
            fig_uk_yield = make_subplots(specs=[[{"secondary_y": True}]])
            fig_uk_yield.add_trace(go.Scatter(
                x=m_df_uk.index, y=m_df_uk['FTSE_Close'], name="FTSE 100",
                line=dict(color='#4da6ff', width=2)), secondary_y=False)
            fig_uk_yield.add_trace(go.Scatter(
                x=m_df_uk.index, y=m_df_uk['UK_Close'], name="10Y Gilt Yield",
                line=dict(color='#ff4d4d', dash='dot', width=2)), secondary_y=True)
            fig_uk_yield.update_layout(
                title=dict(text="UK Cost of Capital: 10Y Gilt Yield vs FTSE 100 (1 Year)", x=0.5, xanchor='center'),
                template="plotly_dark", height=450, margin=dict(l=40, r=40, t=60, b=40),
                hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            fig_uk_yield.update_yaxes(title_text="FTSE 100 Points", secondary_y=False)
            fig_uk_yield.update_yaxes(title_text="10Y Gilt Yield (%)", secondary_y=True)
            html_uk_yield_equity = fig_uk_yield.to_html(
                full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False}
            )

        # 5. Re-render UK Currency Impact
        gbp_data = fetch_parquet_data('GBPUSD_BASELINE.parquet', start_date)
        html_ftse_gbp = ""
        if not ftse_data.empty and not gbp_data.empty:
            m_df_gbp = ftse_data.merge(gbp_data, left_index=True, right_index=True, how='inner')
            fig_gbp = make_subplots(specs=[[{"secondary_y": True}]])
            fig_gbp.add_trace(go.Scatter(
                x=m_df_gbp.index, y=m_df_gbp['FTSE_Close'], name="FTSE 100",
                line=dict(color='#4da6ff', width=2)), secondary_y=False)
            fig_gbp.add_trace(go.Scatter(
                x=m_df_gbp.index, y=m_df_gbp['GBPUSD_Close'], name="GBP/USD",
                line=dict(color='#00ffcc', dash='dot', width=2)), secondary_y=True)
            fig_gbp.update_layout(
                title=dict(text="Currency Impact: GBP/USD vs FTSE 100 (1 Year)", x=0.5, xanchor='center'),
                template="plotly_dark", height=450, margin=dict(l=40, r=40, t=60, b=40),
                hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            fig_gbp.update_yaxes(title_text="FTSE 100 Points", secondary_y=False)
            fig_gbp.update_yaxes(title_text="GBP/USD Rate", secondary_y=True)
            html_ftse_gbp = fig_gbp.to_html(
                full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False}
            )

        # Atomic update under synchronization fence
        with _CACHE_LOCK:
            if html_sentiment: _MACRO_HTML_CACHE["sentiment_html"] = html_sentiment
            if html_vix: _MACRO_HTML_CACHE["vix_spy_html"] = html_vix
            if html_yield_equity: _MACRO_HTML_CACHE["yield_equity_html"] = html_yield_equity
            if html_uk_yield_equity: _MACRO_HTML_CACHE["uk_yield_equity_html"] = html_uk_yield_equity
            if html_ftse_gbp: _MACRO_HTML_CACHE["ftse_gbp_html"] = html_ftse_gbp
            _LAST_CACHE_TIME = time.time()
            
        logger.info("Visual macro caches synchronized successfully.")
    except Exception as ex:
        logger.error(f"Background visual cruncher encountered a processing error: {ex}")
    finally:
        _IS_REFRESHING = False


# ==========================================================
# 5. NEXTCLOUD ALERTS & MICRO SENTIMENT (FINBERT NLP)
# ==========================================================

def run_nextcloud_alert() -> tuple:
    logger.info("Starting Market Sentiment Pipeline...")
    try:
        merged_df = get_sentiment_data()
        if merged_df is None:
            return False, "Failed to fetch data."

        time_stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        file_name = f"Fear_vs_Greed_{time_stamp}.png"
        local_path = file_name
        remote_path = f"StockAlerts/{file_name}"

        try:
            fig, ax1 = plt.subplots(figsize=(12, 6))
            color = 'tab:blue'
            ax1.set_xlabel('Date')
            ax1.set_ylabel('S&P 500 Adjusted Close Price', color=color)
            ax1.plot(merged_df.index, merged_df['SPY_Close'], color=color, label='S&P 500 Price')
            ax1.tick_params(axis='y', labelcolor=color)
            ax1.grid(True)
            
            min_spy = merged_df['SPY_Close'].min() * 0.98
            max_spy = merged_df['SPY_Close'].max() * 1.02
            ax1.set_ylim(min_spy, max_spy)
            
            ax2 = ax1.twinx()
            color = 'tab:red'
            ax2.set_ylabel('Fear & Greed Index (0-100)', color=color)
            ax2.plot(merged_df.index, merged_df['Fear_Greed_Index'], color=color, linestyle='--', alpha=0.6, label='F&G Index')
            ax2.tick_params(axis='y', labelcolor=color)
            ax2.set_ylim(0, 100)
            ax2.set_yticks([0, 25, 50, 75, 100])
            
            sentiment_levels = {0: 'Extreme Fear', 25: 'Fear', 50: 'Neutral', 75: 'Greed', 100: 'Extreme Greed'}
            for y_level, label in sentiment_levels.items():
                ax2.axhline(y=y_level, color='gray', linestyle=':', alpha=0.4, linewidth=1)
                ax2.text(merged_df.index[-1], y_level, f'— {label}', color='black', fontsize=9, ha='right', va='center')
            
            plt.title('SPY Price vs. Fear & Greed Index')
            lines_1, labels_1 = ax1.get_legend_handles_labels()
            lines_2, labels_2 = ax2.get_legend_handles_labels()
            ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc='upper left')
            
            plt.savefig(local_path, dpi=300, bbox_inches='tight')
            plt.close(fig)
        except Exception as e:
            return False, f"Matplotlib Render Error: {str(e)}"
            
        upload_success = upload_file_webdav(local_path, remote_path, NEXTCLOUD_URL, BOT_USERNAME, APP_PASSWORD, print)
        if not upload_success:
            return False, "WebDAV Upload Failed. Check credentials or folder path."
            
        report_message = "📊 *Fear & Greed Index overlayed with S&P 500 for comparison*"
        share_success = share_file_to_talk(remote_path, CONVERSATION_TOKEN, NEXTCLOUD_URL, BOT_USERNAME, APP_PASSWORD, print)
        if share_success:
            report_message += "\n\n🟢 File successfully shared."
        else:
            report_message += "\n\n❌ WARNING: File sharing failed. Talk Token may be invalid."

        api_endpoint = f"{NEXTCLOUD_URL}/ocs/v2.php/apps/spreed/api/v1/chat/{CONVERSATION_TOKEN}"
        resp = requests.post(
            api_endpoint,
            headers={"OCS-APIRequest": "true", "Content-Type": "application/json"},
            data=json.dumps({"message": report_message}),
            auth=(BOT_USERNAME, APP_PASSWORD),
            timeout=15
        )
        
        if resp.status_code in [200, 201]:
            if share_success:
                return True, "Alert successfully generated, uploaded, and shared to Talk."
            else:
                return False, "File upload succeeded, but Talk Share failed. Check Conversation Token."
        else:
            return False, f"Failed to send final text message. HTTP {resp.status_code}"

    except Exception as e:
        return False, f"Unexpected System Error: {str(e)}"
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)


def fetch_and_score_news(ticker: str, analyzer) -> float:
    """
    Fetches the latest 15 news headlines for a ticker via Yahoo Finance.
    Scores the text utilizing FinBERT NLP and returns the normalized compound average.
    """
    try:
        stock = yf.Ticker(ticker)
        news = stock.news
        
        if not news or not isinstance(news, list):
            return 0.0
            
        scores = []
        for item in news[:15]:
            # Defensively un-nest the Yahoo Finance payload
            content = item.get('content', item)
            
            title = content.get('title', '')
            summary = content.get('summary', '')
            
            # Publisher could be under 'publisher', 'provider', or nested inside provider
            publisher = content.get('publisher', '')
            if not publisher and isinstance(content.get('provider'), dict):
                publisher = content['provider'].get('displayName', '')
                
            text_to_analyze = f"{title}. {summary}. {publisher}"
            
            # Skip if the combined string is entirely empty
            if not text_to_analyze.strip(". "):
                continue
                
            # FinBERT output is typically [{'label': 'positive', 'score': 0.85}]
            try:
                # We truncate to 512 characters to avoid exceeding BERT's maximum token limit
                result = analyzer(text_to_analyze[:512])[0]
                label = result['label'].lower()
                prob = result['score']
                
                if label == 'positive':
                    compound = prob
                elif label == 'negative':
                    compound = -prob
                else:
                    compound = 0.0
                    
                scores.append(compound)
            except Exception as e:
                logger.debug(f"FinBERT failed to score string for {ticker}: {e}")
                continue
            
        if not scores:
            return 0.0
            
        return sum(scores) / len(scores)
        
    except Exception as e:
        logger.debug(f"Failed to fetch/score news for {ticker}: {e}")
        return 0.0


def update_all_sentiment(tickers: List[str]) -> None:
    """
    Loops through the target list, fetches the FinBERT sentiment score, 
    and updates the latest record in the quant_signals database table.
    """
    macro_tickers = ["^GSPC", "^NDX", "^FTSE", "^FTMC", "GBPUSD=X", "DX-Y.NYB"]
    combined_tickers = list(set((tickers if tickers else []) + macro_tickers))

    if not combined_tickers:
        logger.warning("Ticker list is empty. Aborting FinBERT sentiment scan.")
        return

    logger.info(f"Initiating FinBERT NLP Sentiment Scan for {len(combined_tickers)} assets. Loading model into memory...")
    
    # Initialize the HuggingFace pipeline with FinBERT
    # Note: On first run, this will download the model weights (~400MB)
    try:
        analyzer = pipeline("sentiment-analysis", model="ProsusAI/finbert")
    except Exception as e:
        logger.error(f"Failed to initialize HuggingFace FinBERT pipeline: {e}")
        return
    
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("ALTER TABLE quant_signals ADD COLUMN sentiment_score REAL")
        conn.commit()
        logger.info("Database Schema Migrated: Added 'sentiment_score' to quant_signals.")
    except Exception:
        pass # Column already exists

    for i, ticker in enumerate(combined_tickers):
        try:
            score = fetch_and_score_news(ticker, analyzer)
            
            cursor.execute("""
                UPDATE quant_signals 
                SET sentiment_score = ? 
                WHERE ticker = ? AND date = (SELECT MAX(date) FROM quant_signals WHERE ticker = ?)
            """, (score, ticker, ticker))
            
            # INSERT macro tickers / new assets if the UPDATE didn't hit any existing rows
            if cursor.rowcount == 0:
                today_str = datetime.now().strftime('%Y-%m-%d')
                cursor.execute("""
                    INSERT INTO quant_signals (ticker, date, sentiment_score)
                    VALUES (?, ?, ?)
                """, (ticker, today_str, score))
            
            conn.commit()
            logger.info(f"[{ticker}] Processed Sentiment: {score:+.3f}")
            
        except Exception as e:
            logger.error(f"Failed to process sentiment for {ticker}: {e}")
            conn.rollback()
        finally:
            time.sleep(random.uniform(0.5, 1.5))
            
    conn.close()
    logger.info("FinBERT NLP Analysis completed successfully.")


def run_central_bank_nlp_alert(event_name: str, currency: str) -> bool:
    """
    Specifically targets Tier-1 Central Bank events (FOMC, BoE).
    Parses immediate media reactions to determine if the monetary policy tone 
    is mathematically 'Hawkish' (Bearish for equities) or 'Dovish' (Bullish for equities).
    Dispatches a specialized alert to Nextcloud Talk.
    """
    logger.info(f"Intercepting Central Bank Event for NLP Analysis: {event_name}")
    config = load_config()
    
    # 1. Initialize FinBERT 
    try:
        analyzer = pipeline("sentiment-analysis", model="ProsusAI/finbert")
    except Exception as e:
        logger.error(f"Failed to load FinBERT for Central Bank NLP: {e}")
        return False

    # 2. Determine target entity for proxy news scraping
    if currency == "USD":
        target_entity = "Federal Reserve"
        ticker_proxy = "^TNX" # 10Y Treasury news usually captures Fed statements fastest
    elif currency == "GBP":
        target_entity = "Bank of England"
        ticker_proxy = "^FTSE"
    else:
        logger.warning(f"Unsupported currency {currency} for Central Bank NLP.")
        return False

    # 3. Fetch latest headlines
    try:
        stock = yf.Ticker(ticker_proxy)
        news = stock.news
        if not news:
            return False
            
        scores = []
        parsed_headlines = []
        
        for item in news[:20]:
            content = item.get('content', item)
            title = content.get('title', '')
            summary = content.get('summary', '')
            
            # Ensure the news is actually talking about the central bank or rates
            text_to_analyze = f"{title}. {summary}"
            if "rate" not in text_to_analyze.lower() and "inflation" not in text_to_analyze.lower() and target_entity.lower() not in text_to_analyze.lower():
                continue
                
            # Score with FinBERT
            result = analyzer(text_to_analyze[:512])[0]
            label = result['label'].lower()
            prob = result['score']
            
            if label == 'positive':
                compound = prob
            elif label == 'negative':
                compound = -prob
            else:
                compound = 0.0
                
            scores.append(compound)
            parsed_headlines.append(title)
            
        if not scores:
            logger.info("No relevant Central Bank headlines found in the immediate fetch window.")
            return False
            
        avg_score = sum(scores) / len(scores)
        
        # 4. Map General Sentiment to Monetary Policy Tone
        if avg_score > 0.15:
            tone = "🦅 HAWKISH (Restrictive)"
            equity_impact = "Bearish for Equities"
        elif avg_score < -0.15:
            tone = "🕊️ DOVISH (Accommodative)"
            equity_impact = "Bullish for Equities"
        else:
            tone = "⚖️ NEUTRAL"
            equity_impact = "Market pricing unchanged"
            
        # 5. Dispatch Alert
        msg = (
            f"🏛️ **CENTRAL BANK NLP ANALYSIS** 🏛️\n\n"
            f"**Event:** {event_name} ({currency})\n"
            f"**Calculated Tone:** {tone}\n"
            f"**Expected Equity Impact:** {equity_impact}\n\n"
            f"**Analyzed FinBERT Score:** {avg_score:+.3f}\n"
            f"*Top Headline Parsed:* {parsed_headlines[0]}"
        )
        
        send_text_message(msg, config)
        
        # Log to DB
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)", ("Macro NLP", msg))
        conn.commit()
        conn.close()
        
        logger.info(f"Central Bank NLP successfully dispatched: {tone}")
        return True
        
    except Exception as e:
        logger.error(f"Central Bank NLP analysis failed: {e}")
        return False