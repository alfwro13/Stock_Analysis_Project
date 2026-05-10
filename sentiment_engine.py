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
        # Added strict 15-second timeout to prevent infinite hanging
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

    # Add Sentiment Floor Levels
    levels = {25: 'Fear (25)', 50: 'Neutral (50)', 75: 'Greed (75)'}
    for level, text in levels.items():
        fig.add_hline(y=level, line_dash="dash", line_color="#555", secondary_y=True, 
                      annotation_text=text, annotation_position="top right", annotation_font_color="#aaa")

    # Calculate dynamic Y-Axis for S&P 500 to prevent flatlining
    min_spy = merged_df['SPY_Close'].min() * 0.95
    max_spy = merged_df['SPY_Close'].max() * 1.05

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

def get_sentiment_html():
    """Returns the raw HTML of the Plotly figure for the Web Dashboard."""
    fig = generate_sentiment_figure()
    if not fig:
        return "<p>Error loading sentiment data. Please try again later.</p>"
    return fig.to_html(full_html=False, include_plotlyjs='cdn')

def run_nextcloud_alert():
    """Background task: Generates the plot, saves to PNG, uploads, and sends to Nextcloud Talk."""
    print("\n[DEBUG] 1/5 - Starting Market Sentiment Pipeline...")
    try:
        fig = generate_sentiment_figure()
        if not fig:
            print("[DEBUG] FAILED at Step 1: Data fetch error.")
            return False, "Failed to generate figure (Data fetch error)."

        file_name = f"Fear_vs_Greed_{datetime.now().strftime('%Y-%m-%d')}.png"
        local_path = file_name
        remote_path = f"StockAlerts/{file_name}"

        print(f"[DEBUG] 2/5 - Data fetched successfully. Rendering PNG to {local_path} via Kaleido...")
        try:
            fig.write_image(local_path, width=1200, height=600, scale=2)
        except Exception as e:
            print(f"[DEBUG] FAILED at Step 2: Kaleido Render Error - {e}")
            return False, f"Kaleido Image Render Error: {str(e)}"
            
        print(f"[DEBUG] 3/5 - PNG Rendered. Uploading via WebDAV to {remote_path}...")
        upload_success = upload_file_webdav(local_path, remote_path, NEXTCLOUD_URL, BOT_USERNAME, APP_PASSWORD, print)
        if not upload_success:
            print("[DEBUG] FAILED at Step 3: WebDAV Upload.")
            return False, "WebDAV Upload Failed. Check credentials or folder path."
            
        report_message = "📊 *Fear & Greed Index overlayed with S&P 500 for comparison*"
        
        print("[DEBUG] 4/5 - WebDAV Success. Sharing to Nextcloud Talk...")
        share_success = share_file_to_talk(remote_path, CONVERSATION_TOKEN, NEXTCLOUD_URL, BOT_USERNAME, APP_PASSWORD, print)
        if share_success:
            report_message += "\n\n🟢 File successfully shared."
        else:
            report_message += "\n\n❌ WARNING: File sharing failed. Talk Token may be invalid."

        print("[DEBUG] 5/5 - Sending final text message summary...")
        api_endpoint = f"{NEXTCLOUD_URL}/ocs/v2.php/apps/spreed/api/v1/chat/{CONVERSATION_TOKEN}"
        resp = requests.post(
            api_endpoint, 
            headers={"OCS-APIRequest": "true", "Content-Type": "application/json"}, 
            data=json.dumps({"message": report_message}), 
            auth=(BOT_USERNAME, APP_PASSWORD),
            timeout=15 # Added strict timeout
        )
        
        if resp.status_code in [200, 201]:
            print("[DEBUG] PIPELINE COMPLETE. Success!")
            return True, "Alert successfully generated, uploaded, and shared to Talk."
        else:
            print(f"[DEBUG] FAILED at Step 5: Text Message HTTP {resp.status_code}")
            return False, f"Failed to send final text message. HTTP {resp.status_code}"

    except Exception as e:
        print(f"[DEBUG] UNEXPECTED SYSTEM ERROR: {e}")
        return False, f"Unexpected System Error: {str(e)}"
    finally:
        # Clean up the local PNG file
        if os.path.exists(local_path):
            os.remove(local_path)
            print("[DEBUG] Cleaned up temporary PNG file.")