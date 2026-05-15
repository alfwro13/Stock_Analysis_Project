# sentiment_engine.py
import os
import json
import time
import random
import logging
import pandas as pd
import requests
from fake_useragent import UserAgent
from datetime import datetime, timedelta
from typing import List

import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Critical for headless Linux servers: Use 'Agg' backend so matplotlib doesn't crash
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# NLP Sentiment Analyzer
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from nextcloud_talk import upload_file_webdav, share_file_to_talk
from config import NEXTCLOUD_URL, BOT_USERNAME, APP_PASSWORD, CONVERSATION_TOKEN
from database import get_connection

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - SENTIMENT_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ==========================================================
# 1. MACRO SENTIMENT (FEAR & GREED INDEX)
# ==========================================================

def fetch_fear_greed_data(start_date_str: str) -> pd.DataFrame:
    BASE_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/"
    ua = UserAgent()
    headers = {'User-Agent': ua.random}
    try:
        r = requests.get(BASE_URL + start_date_str, headers=headers, timeout=15)
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
    stock_df = yf.download(tickers=ticker, start=start_date, progress=False, auto_adjust=True)
    if stock_df.empty: return pd.DataFrame()
    if isinstance(stock_df.columns, pd.MultiIndex):
        stock_df.columns = stock_df.columns.get_level_values(0)
    stock_df.reset_index(inplace=True)
    stock_df['Date'] = stock_df['Date'].dt.date
    stock_df.set_index('Date', inplace=True)
    if 'Close' not in stock_df.columns: return pd.DataFrame()
    return stock_df[['Close']].rename(columns={'Close': f'{ticker}_Close'})

def get_sentiment_data() -> pd.DataFrame:
    today = datetime.now()
    start_date = (today - timedelta(days=365)).strftime('%Y-%m-%d')
    fng_data = fetch_fear_greed_data(start_date)
    spy_data = fetch_stock_data('SPY', start_date)
    if fng_data.empty or spy_data.empty: return None
    merged_df = spy_data.merge(fng_data, left_index=True, right_index=True, how='left')
    merged_df['Fear_Greed_Index'] = merged_df['Fear_Greed_Index'].ffill()
    merged_df.dropna(inplace=True)
    return merged_df

def generate_sentiment_figure():
    merged_df = get_sentiment_data()
    if merged_df is None: return None

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['SPY_Close'], name="S&P 500", line=dict(color='#4da6ff', width=2)), secondary_y=False)
    fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['Fear_Greed_Index'], name="F&G Index", line=dict(color='#ff4d4d', dash='dot', width=2)), secondary_y=True)

    levels = {25: 'Fear (25)', 50: 'Neutral (50)', 75: 'Greed (75)'}
    for level, text in levels.items():
        fig.add_hline(y=level, line_dash="dash", line_color="#555", secondary_y=True, annotation_text=text, annotation_position="top right", annotation_font_color="#aaa")

    min_spy = merged_df['SPY_Close'].min() * 0.98
    max_spy = merged_df['SPY_Close'].max() * 1.02

    fig.update_layout(
        title="Fear & Greed vs S&P 500 (1 Year)",
        template="plotly_dark",
        height=600,
        margin=dict(l=40, r=40, t=60, b=40),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_yaxes(title_text="S&P 500 Price ($)", range=[min_spy, max_spy], secondary_y=False)
    fig.update_yaxes(title_text="Fear & Greed Index (0-100)", range=[0, 100], secondary_y=True)
    return fig

def get_sentiment_html() -> str:
    fig = generate_sentiment_figure()
    if not fig: return "<p>Error loading sentiment data. Please try again later.</p>"
    
    clean_config = {
        'responsive': True,
        'displaylogo': False
    }
    
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config=clean_config)

# ==========================================================
# 2. MARKET REGIME (VIX vs SPY)
# ==========================================================

def get_vix_spy_data() -> pd.DataFrame:
    try:
        tickers = ["SPY", "^VIX"]
        df = yf.download(tickers, period="1y", interval="1d", group_by='ticker', auto_adjust=True, progress=False)
        
        if df.empty or 'SPY' not in df.columns or '^VIX' not in df.columns:
            logger.error("Failed to fetch SPY or VIX data from Yahoo Finance.")
            return None

        spy_data = df['SPY'].dropna(subset=['Close']).rename(columns={'Close': 'SPY_Close'})
        vix_data = df['^VIX'].dropna(subset=['Close']).rename(columns={'Close': 'VIX_Close'})
        
        if spy_data.empty or vix_data.empty:
            logger.error("Incomplete data received for SPY or VIX.")
            return None

        merged_df = spy_data[['SPY_Close']].merge(vix_data[['VIX_Close']], left_index=True, right_index=True, how='inner')
        return merged_df
        
    except Exception as e:
        logger.error(f"Fatal error fetching VIX vs SPY data: {e}")
        return None

def generate_vix_spy_figure():
    merged_df = get_vix_spy_data()
    if merged_df is None: return None

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['SPY_Close'], name="S&P 500", line=dict(color='#4da6ff', width=2)), secondary_y=False)
    fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['VIX_Close'], name="VIX", line=dict(color='#ffaa00', dash='dot', width=2)), secondary_y=True)

    # Annotate key Regime transition lines
    fig.add_hline(y=20, line_dash="dash", line_color="#ffaa00", secondary_y=True, annotation_text="Volatile (20)", annotation_position="top right", annotation_font_color="#ffaa00")
    fig.add_hline(y=30, line_dash="dash", line_color="#ff4d4d", secondary_y=True, annotation_text="Crash (30)", annotation_position="top right", annotation_font_color="#ff4d4d")

    min_spy = merged_df['SPY_Close'].min() * 0.98
    max_spy = merged_df['SPY_Close'].max() * 1.02

    fig.update_layout(
        title="S&P 500 vs VIX (1 Year)",
        template="plotly_dark",
        height=600,
        margin=dict(l=40, r=40, t=60, b=40),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    
    fig.update_yaxes(title_text="S&P 500 Price ($)", range=[min_spy, max_spy], secondary_y=False)
    
    # Scale secondary axis to handle extreme VIX blowouts without crushing the chart
    max_vix = max(50, merged_df['VIX_Close'].max() * 1.1)
    fig.update_yaxes(title_text="VIX Level", range=[0, max_vix], secondary_y=True)
    
    return fig

def get_vix_spy_html() -> str:
    fig = generate_vix_spy_figure()
    if not fig: return "<p>Error loading VIX data. Please try again later.</p>"
    
    clean_config = {
        'responsive': True,
        'displaylogo': False
    }
    
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config=clean_config)


# ==========================================================
# 3. NEXTCLOUD ALERTS (PNG PUSH)
# ==========================================================

def run_nextcloud_alert():
    logger.info("Starting Market Sentiment Pipeline...")
    try:
        merged_df = get_sentiment_data()
        if merged_df is None: return False, "Failed to fetch data."

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
        if not upload_success: return False, "WebDAV Upload Failed. Check credentials or folder path."
            
        report_message = "📊 *Fear & Greed Index overlayed with S&P 500 for comparison*"
        share_success = share_file_to_talk(remote_path, CONVERSATION_TOKEN, NEXTCLOUD_URL, BOT_USERNAME, APP_PASSWORD, print)
        if share_success:
            report_message += "\n\n🟢 File successfully shared."
        else:
            report_message += "\n\n❌ WARNING: File sharing failed. Talk Token may be invalid."

        api_endpoint = f"{NEXTCLOUD_URL}/ocs/v2.php/apps/spreed/api/v1/chat/{CONVERSATION_TOKEN}"
        resp = requests.post(api_endpoint, headers={"OCS-APIRequest": "true", "Content-Type": "application/json"}, data=json.dumps({"message": report_message}), auth=(BOT_USERNAME, APP_PASSWORD), timeout=15)
        
        if resp.status_code in [200, 201]:
            if share_success: return True, "Alert successfully generated, uploaded, and shared to Talk."
            else: return False, "File upload succeeded, but Talk Share failed. Check Conversation Token."
        else:
            return False, f"Failed to send final text message. HTTP {resp.status_code}"

    except Exception as e:
        return False, f"Unexpected System Error: {str(e)}"
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)


# ==========================================================
# 4. MICRO SENTIMENT (VADER NLP ON NEWS HEADLINES)
# ==========================================================

def fetch_and_score_news(ticker: str, analyzer: SentimentIntensityAnalyzer) -> float:
    """
    Fetches the latest 15 news headlines for a ticker via Yahoo Finance.
    Scores the text utilizing VADER NLP and returns the normalized compound average.
    """
    try:
        stock = yf.Ticker(ticker)
        news = stock.news
        
        if not news or not isinstance(news, list):
            return 0.0
            
        scores = []
        for item in news[:15]:
            title = item.get('title', '')
            summary = item.get('summary', '')
            publisher = item.get('publisher', '')
            
            text_to_analyze = f"{title}. {summary}. {publisher}"
            
            if not text_to_analyze.strip(". "):
                continue
                
            score_dict = analyzer.polarity_scores(text_to_analyze)
            scores.append(score_dict['compound'])
            
        if not scores:
            return 0.0
            
        return sum(scores) / len(scores)
        
    except Exception as e:
        logger.debug(f"Failed to fetch/score news for {ticker}: {e}")
        return 0.0

def update_all_sentiment(tickers: List[str]) -> None:
    """
    Loops through the target list, fetches the VADER sentiment score, 
    and updates the latest record in the quant_signals database table.
    """
    if not tickers:
        logger.warning("Ticker list is empty. Aborting VADER sentiment scan.")
        return

    logger.info(f"Initiating Zero-LLM VADER Sentiment Scan for {len(tickers)} assets...")
    
    analyzer = SentimentIntensityAnalyzer()
    
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("ALTER TABLE quant_signals ADD COLUMN sentiment_score REAL")
        conn.commit()
        logger.info("Database Schema Migrated: Added 'sentiment_score' to quant_signals.")
    except Exception:
        pass # Column already exists

    for i, ticker in enumerate(tickers):
        try:
            score = fetch_and_score_news(ticker, analyzer)
            
            cursor.execute("""
                UPDATE quant_signals 
                SET sentiment_score = ? 
                WHERE ticker = ? AND date = (SELECT MAX(date) FROM quant_signals WHERE ticker = ?)
            """, (score, ticker, ticker))
            
            conn.commit()
            logger.info(f"[{ticker}] Processed Sentiment: {score:+.3f}")
            
        except Exception as e:
            logger.error(f"Failed to process sentiment for {ticker}: {e}")
            conn.rollback()
        finally:
            time.sleep(random.uniform(0.5, 1.5))
            
    conn.close()
    logger.info("VADER Local Sentiment Analysis completed successfully.")