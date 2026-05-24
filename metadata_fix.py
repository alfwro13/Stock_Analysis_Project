import yfinance as yf
import sqlite3
from config import DB_PATH

# These are the tickers scoring near-zero due to Unknown sector
tickers_to_fix = ['SWDA.L', 'GSPX.L', 'L100.L', 'VEA', 'ES=F']

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

for ticker in tickers_to_fix:
    try:
        info   = yf.Ticker(ticker).info
        sector = info.get('sector', None)
        
        # ETFs and futures don't have a 'sector' field in yfinance.
        # Assign a meaningful category manually.
        if sector is None:
            name = info.get('longName', '').lower()
            if any(x in name for x in ['s&p', 'msci', 'ftse', 'index', 'tracker', 'world']):
                sector = 'Broad Market ETF'
            elif 'es=f' in ticker.lower():
                sector = 'Futures'
            else:
                sector = 'ETF'
        
        cursor.execute("""
            UPDATE ticker_metadata SET sector = ? WHERE ticker = ?
        """, (sector, ticker))
        print(f"{ticker}: set sector to '{sector}'")
    except Exception as e:
        print(f"{ticker}: failed — {e}")

conn.commit()
conn.close()