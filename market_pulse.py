# market_pulse.py
import yfinance as yf
import pandas as pd


# Dictionary mapping Yahoo Finance tickers to our clean UI display names
INDEX_TICKERS = {
    "^GSPC": "S&P 500",
    "^NDX": "Nasdaq 100",
    "^FTSE": "FTSE 100",
    "^FTMC": "FTSE 250",
    "^KS11": "KOSPI"
}


def get_market_pulse() -> list:
    """
    Fetches the latest live prices and daily percentage changes
    for the configured global market indexes using batch requests.
    """
    tickers_list = list(INDEX_TICKERS.keys())
    results = []

    try:
        # Fetching 5 days of 1-minute data ensures we can robustly extract
        # the previous day's closing price across different global timezones,
        # while securing the absolute latest live intraday tick.
        df = yf.download(
            tickers_list, 
            period="5d", 
            interval="1m", 
            group_by='ticker', 
            progress=False
        )
        
        for ticker, name in INDEX_TICKERS.items():
            try:
                # Handle MultiIndex extraction safely
                if len(tickers_list) > 1:
                    if ticker not in df.columns.get_level_values(0):
                        continue
                    ticker_df = df[ticker].copy()
                else:
                    ticker_df = df.copy()
                    
                ticker_df.dropna(subset=['Close'], inplace=True)
                
                if ticker_df.empty:
                    continue

                # Resample 1-minute data to a Daily frequency to isolate true daily closing prices
                daily_closes = ticker_df['Close'].resample('D').last().dropna()
                
                if len(daily_closes) < 2:
                    continue

                # The absolute latest 1-minute tick
                current_price = ticker_df['Close'].iloc[-1]
                
                # The final 1-minute tick from the previous trading day
                prev_close = daily_closes.iloc[-2]

                change_pts = current_price - prev_close
                change_pct = (change_pts / prev_close) * 100.0

                results.append({
                    "name": name,
                    "ticker": ticker,
                    "price": f"{current_price:,.2f}",
                    "change_pts": f"{change_pts:,.2f}",
                    "change_pct": f"{change_pct:,.2f}",
                    # EXPLICITLY CAST TO NATIVE PYTHON BOOL TO FIX JSON SERIALIZATION ERROR
                    "is_positive": bool(change_pts >= 0) 
                })
            except Exception as e:
                print(f"[MARKET PULSE] Error processing {ticker}: {e}")
                
    except Exception as e:
        print(f"[MARKET PULSE] Batch download failed: {e}")

    return results
