# market_pulse.py
import yfinance as yf
import pandas as pd


# Dictionary mapping Yahoo Finance tickers to our clean UI display names
INDEX_TICKERS = {
    "^GSPC": "S&P 500",
    "ES=F": "S&P 500 Futures",
    "^NDX": "Nasdaq 100",
    "NQ=F": "Nasdaq 100 Futures",
    "^FTSE": "FTSE 100",
    "^FTMC": "FTSE 250",
    "^KS11": "KOSPI"
}


def get_market_pulse(asset_tickers=None) -> dict:
    """
    Fetches the latest live prices and daily percentage changes
    for both global market indexes and dynamically requested portfolio assets.
    Returns structured dict segregating 'indexes' and 'assets'.
    """
    if asset_tickers is None:
        asset_tickers = []
        
    # Deduplicate and merge indices with dynamically requested user assets
    requested_assets = [t for t in asset_tickers if t not in INDEX_TICKERS]
    all_tickers = list(INDEX_TICKERS.keys()) + requested_assets
    
    results = {"indexes": [], "assets": []}
    
    if not all_tickers:
        return results

    try:
        # 1. Fetch official daily closes (ignores pre/post market noise) to secure the true anchor point
        df_daily = yf.download(all_tickers, period="5d", interval="1d", group_by='ticker', progress=False)
        
        # 2. Fetch the absolute latest live tick (includes US pre-market trading)
        df_live = yf.download(all_tickers, period="1d", interval="1m", prepost=True, group_by='ticker', progress=False)
        
        for ticker in all_tickers:
            try:
                # Handle MultiIndex extraction safely based on batch size
                if len(all_tickers) > 1:
                    if ticker not in df_daily.columns.get_level_values(0) or ticker not in df_live.columns.get_level_values(0):
                        continue
                    t_daily = df_daily[ticker].copy()
                    t_live = df_live[ticker].copy()
                else:
                    t_daily = df_daily.copy()
                    t_live = df_live.copy()
                    
                t_daily.dropna(subset=['Close'], inplace=True)
                t_live.dropna(subset=['Close'], inplace=True)
                
                if t_daily.empty or t_live.empty:
                    continue

                # The absolute latest tick (capturing pre-market, regular, or post-market)
                current_price = t_live['Close'].iloc[-1]
                
                # Determining the correct previous close
                # If the daily data's latest row belongs to today, the previous close is the row before it.
                last_daily_date = t_daily.index[-1].date()
                live_date = t_live.index[-1].date()
                
                if last_daily_date >= live_date and len(t_daily) >= 2:
                    prev_close = t_daily['Close'].iloc[-2]
                else:
                    prev_close = t_daily['Close'].iloc[-1]
                    
                change_pts = current_price - prev_close
                change_pct = (change_pts / prev_close) * 100.0 if prev_close else 0.0

                data_obj = {
                    "ticker": ticker,
                    "price": f"{current_price:,.2f}",
                    "change_pts": f"{change_pts:,.2f}",
                    "change_pct": f"{change_pct:,.2f}",
                    "is_positive": bool(change_pts >= 0)
                }

                if ticker in INDEX_TICKERS:
                    data_obj["name"] = INDEX_TICKERS[ticker]
                    results["indexes"].append(data_obj)
                else:
                    results["assets"].append(data_obj)

            except Exception as e:
                print(f"[MARKET PULSE] Error processing {ticker}: {e}")
                
    except Exception as e:
        print(f"[MARKET PULSE] Batch download failed: {e}")

    return results
