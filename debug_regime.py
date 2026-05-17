import traceback
import numpy as np
import pandas as pd
import yfinance as yf
from database import get_connection

def run_diagnostics():
    print("="*60)
    print(" 🌍 MARKET REGIME DIAGNOSTICS ENGINE")
    print("="*60)

    # ---------------------------------------------------------
    # TEST 1: Database Connection & Schema
    # ---------------------------------------------------------
    print("\n[TEST 1] Checking Database Connection & Schema...")
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Ensure table exists
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_regimes (
                date TEXT PRIMARY KEY,
                vix_close REAL,
                spy_volatility REAL,
                turbulence_index REAL,
                regime_label TEXT
            )
        ''')
        
        # Check current row count
        cursor.execute("SELECT COUNT(*) as count FROM market_regimes")
        count = cursor.fetchone()['count']
        print(f"✅ DB Connection Successful. Current rows in `market_regimes`: {count}")
        
    except Exception as e:
        print(f"❌ DB ERROR: Failed to connect or read table.\n{traceback.format_exc()}")
        return
    finally:
        conn.close()

    # ---------------------------------------------------------
    # TEST 2: Yahoo Finance API Connectivity
    # ---------------------------------------------------------
    print("\n[TEST 2] Testing Yahoo Finance API for SPY and ^VIX...")
    try:
        tickers = ["SPY", "^VIX"]
        df = yf.download(tickers, period="1y", interval="1d", group_by='ticker', auto_adjust=True, progress=False)
        
        if df.empty:
            print("❌ YF ERROR: DataFrame returned completely empty. Yahoo Finance might be blocking your IP.")
            return
            
        if 'SPY' not in df.columns or '^VIX' not in df.columns:
            print(f"❌ YF ERROR: Missing required tickers in columns. Columns found: {list(df.columns)}")
            return
            
        spy_data = df['SPY'].dropna(subset=['Close'])
        vix_data = df['^VIX'].dropna(subset=['Close'])
        
        print(f"✅ YF API Successful.")
        print(f"   -> SPY Data Points: {len(spy_data)}")
        print(f"   -> VIX Data Points: {len(vix_data)}")
        
        if spy_data.empty or vix_data.empty:
            print("❌ YF ERROR: Download succeeded but Close prices are entirely empty/NaN.")
            return

    except Exception as e:
        print(f"❌ YF ERROR: Exception during yfinance download.\n{traceback.format_exc()}")
        return

    # ---------------------------------------------------------
    # TEST 3: Mathematical Processing
    # ---------------------------------------------------------
    print("\n[TEST 3] Testing Mathematical Regime Calculations...")
    try:
        spy_log_returns = np.log(spy_data['Close'] / spy_data['Close'].shift(1))
        spy_vol_21d = spy_log_returns.rolling(window=21).std() * np.sqrt(252) * 100.0

        latest_date = spy_data.index[-1].strftime('%Y-%m-%d')
        latest_vix = float(vix_data['Close'].iloc[-1])
        latest_spy_vol = float(spy_vol_21d.iloc[-1])
        
        if pd.isna(latest_spy_vol):
            print("❌ MATH ERROR: Not enough data points to calculate 21-day rolling volatility (Need > 21 days).")
            return
            
        turbulence_index = (latest_vix + latest_spy_vol) / 2.0
        
        if turbulence_index >= 30.0 or latest_vix >= 30.0:
            regime_label = 'Crash'
        elif turbulence_index >= 20.0 or latest_vix >= 20.0:
            regime_label = 'Volatile'
        else:
            regime_label = 'Normal'
            
        print(f"✅ Math Successful.")
        print(f"   -> Target Date:      {latest_date}")
        print(f"   -> VIX Close:        {latest_vix:.2f}")
        print(f"   -> SPY Volatility:   {latest_spy_vol:.2f}")
        print(f"   -> Turbulence Index: {turbulence_index:.2f}")
        print(f"   -> Regime Label:     {regime_label}")

    except Exception as e:
        print(f"❌ MATH ERROR: Exception during calculation.\n{traceback.format_exc()}")
        return

    # ---------------------------------------------------------
    # TEST 4: Database Write Test
    # ---------------------------------------------------------
    print("\n[TEST 4] Testing Database Insert...")
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO market_regimes 
            (date, vix_close, spy_volatility, turbulence_index, regime_label)
            VALUES (?, ?, ?, ?, ?)
        ''', (latest_date, round(latest_vix, 2), round(latest_spy_vol, 2), round(turbulence_index, 2), regime_label))
        
        conn.commit()
        
        # Verify it wrote
        cursor.execute("SELECT * FROM market_regimes WHERE date = ?", (latest_date,))
        inserted_row = cursor.fetchone()
        
        if inserted_row:
            print("✅ DB Write Successful. Data safely persisted to SQLite.")
            print(f"   -> Readback verification: {dict(inserted_row)}")
        else:
            print("❌ DB ERROR: Insert executed without error, but readback returned nothing.")
            
    except Exception as e:
        print(f"❌ DB WRITE ERROR: Failed to insert data into SQLite.\n{traceback.format_exc()}")
    finally:
        conn.close()

    print("\n" + "="*60)
    print(" DIAGNOSTICS COMPLETE")
    print("="*60 + "\n")

if __name__ == "__main__":
    # Activate your virtual environment first if necessary!
    run_diagnostics()