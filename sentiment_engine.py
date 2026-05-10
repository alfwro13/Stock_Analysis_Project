# sentiment_engine.py
import os
import json
import pandas as pd
import requests
from fake_useragent import UserAgent
from datetime import datetime, timedelta
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from nextcloud_talk import upload_file_webdav, share_file_to_talk
from config import NEXTCLOUD_URL, BOT_USERNAME, APP_PASSWORD, CONVERSATION_TOKEN

def fetch_fear_greed_data(start_date_str):
    """Fetches historical Fear & Greed Index data from CNN API."""
    BASE_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/"
    ua = UserAgent()
    headers = {'User-Agent': ua.random}
    
    try:
        r = requests.get(BASE_URL + start_date_str, headers=headers)
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
        print(f"[ERROR] Fetching F&G data: {e}")
        return pd.DataFrame()

def fetch_stock_data(ticker, start_date):
    """Fetches historical stock data from Yahoo Finance."""
    stock_df = yf.download(tickers=ticker, start=start_date, progress=False, auto_adjust=True)
    if stock_df.empty: return pd.DataFrame()

    if isinstance(stock_df.columns, pd.MultiIndex):
        stock_df.columns = stock_df.columns.get_level_values(0)

    stock_df.reset_index(inplace=True)
    stock_df['Date'] = stock_df['Date'].dt.date
    stock_df.set_index('Date', inplace=True)
    
    if 'Close' not in stock_df.columns: return pd.DataFrame()
    return stock_df[['Close']].rename(columns={'Close': f'{ticker}_Close'})

def generate_sentiment_figure():
    """Generates the dual-axis Plotly figure for Market Sentiment."""
    today = datetime.now()
    start_date = (today - timedelta(days=365)).strftime('%Y-%m-%d')
    
    fng_data = fetch_fear_greed_data(start_date)
    spy_data = fetch_stock_data('SPY', start_date)
    
    if fng_data.empty or spy_data.empty:
        return None

    merged_df = spy_data.merge(fng_data, left_index=True, right_index=True, how='left')
    merged_df['Fear_Greed_Index'] = merged_df['Fear_Greed_Index'].ffill()
    merged_df.dropna(inplace=True)

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # S&P 500 Price Line (Blue)
    fig.add_trace(
        go.Scatter(x=merged_df.index, y=merged_df['SPY_Close'], name="S&P 500", line=dict(color='#4da6ff', width=2)),
        secondary_y=False,
    )

    # Fear & Greed Line (Red Dashed)
    fig.add_trace(
        go.Scatter(x=merged_df.index, y=merged_df['Fear_Greed_Index'], name="F&G Index", line=dict(color='#ff4d4d', dash='dot', width=2)),
        secondary_y=True,
    )

    # Add Sentiment Floor Levels (Similar to your Matplotlib logic)
    levels = {25: 'Fear (25)', 50: 'Neutral (50)', 75: 'Greed (75)'}
    for level, text in levels.items():
        fig.add_hline(y=level, line_dash="dash", line_color="#555", secondary_y=True, 
                      annotation_text=text, annotation_position="top right", annotation_font_color="#aaa")

    fig.update_layout(
        title="Fear & Greed vs S&P 500 (1 Year)",
        template="plotly_dark",
        height=600,
        margin=dict(l=40, r=40, t=60, b=40),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )

    fig.update_yaxes(title_text="S&P 500 Price ($)", secondary_y=False)
    fig.update_yaxes(title_text="Fear & Greed Index (0-100)", range=[0, 100], secondary_y=True)
    
    return fig

def get_sentiment_html():
    """Returns the raw HTML of the Plotly figure for the Web Dashboard."""
    fig = generate_sentiment_figure()
    if not fig:
        return "<p>Error loading sentiment data. Please try again later.</p>"
    return fig.to_html(full_html=False, include_plotlyjs='cdn')

def run_nextcloud_alert():
    """Background task: Generates the plot, saves to PNG, uploads, and sends to Nextcloud Talk."""
    print("[SCHEDULER] Executing Market Sentiment Notification...")
    
    fig = generate_sentiment_figure()
    if not fig:
        print("[ERROR] Failed to generate figure for alert.")
        return

    # Generate Temp File
    file_name = f"Fear_vs_Greed_{datetime.now().strftime('%Y-%m-%d')}.png"
    local_path = file_name
    remote_path = f"StockAlerts/{file_name}"

    try:
        # kaleido writes the high-res PNG locally
        fig.write_image(local_path, width=1200, height=600, scale=2)
        
        # Upload via Nextcloud
        upload_success = upload_file_webdav(local_path, remote_path, NEXTCLOUD_URL, BOT_USERNAME, APP_PASSWORD, print)
        
        report_message = "📊 *Fear & Greed Index overlayed with S&P 500 for comparison*"
        
        if upload_success:
            share_success = share_file_to_talk(remote_path, CONVERSATION_TOKEN, NEXTCLOUD_URL, BOT_USERNAME, APP_PASSWORD, print)
            report_message += "\n\n🟢 File successfully shared." if share_success else "\n\n❌ WARNING: File sharing failed."
        else:
            report_message += "\n\n❌ FATAL ERROR: File upload failed."

        # Send textual follow-up notification via Talk API
        api_endpoint = f"{NEXTCLOUD_URL}/ocs/v2.php/apps/spreed/api/v1/chat/{CONVERSATION_TOKEN}"
        requests.post(
            api_endpoint, 
            headers={"OCS-APIRequest": "true", "Content-Type": "application/json"}, 
            data=json.dumps({"message": report_message}), 
            auth=(BOT_USERNAME, APP_PASSWORD)
        )
        print("[SUCCESS] Market Sentiment notification complete.")

    finally:
        # Clean up the local PNG file to prevent disk clutter
        if os.path.exists(local_path):
            os.remove(local_path)