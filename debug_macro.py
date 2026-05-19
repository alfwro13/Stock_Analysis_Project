# debug_macro.py
import os
import json
import sqlite3
import pandas as pd
import requests

def run_diagnostics():
    print("="*60)
    print(" 🔍 MACRO DATA DIAGNOSTICS")
    print("="*60)

    # 1. Check Config for FRED API Key
    config_path = "config.json"
    fred_key = None
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)
            fred_key = config.get("FRED_API_KEY", "")
            if fred_key:
                print(f"✅ FRED_API_KEY found in config.json (Length: {len(fred_key)})")
            else:
                print("❌ FRED_API_KEY is empty in config.json!")
    else:
        print("❌ config.json not found!")

    # 2. Test FRED API if key exists
    if fred_key:
        print("\nTesting FRED API connectivity...")
        test_url = f"https://api.stlouisfed.org/fred/series?series_id=WM2NS&api_key={fred_key}&file_type=json"
        try:
            res = requests.get(test_url, timeout=10)
            if res.status_code == 200:
                print("✅ FRED API Key is VALID and returning data.")
            else:
                print(f"❌ FRED API Key TEST FAILED! HTTP {res.status_code}: {res.text}")
        except Exception as e:
            print(f"❌ Error reaching FRED API: {e}")
    else:
        print("\n⚠️ Skipping FRED API test because key is missing.")
        print("👉 You must get a free API key from https://fred.stlouisfed.org/docs/api/api_key.html and save it in the dashboard Settings.")

    # 3. Check Database Contents
    print("\n" + "="*60)
    print(" 🗄️ DATABASE INSPECTION")
    print("="*60)
    
    db_path = "data/analysis.db"
    if not os.path.exists(db_path):
        print(f"❌ Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query("SELECT * FROM macro_indicators", conn)
        print(f"Total rows in macro_indicators table: {len(df)}\n")
        
        if not df.empty:
            print("--- Non-Null Data Count Per Column ---")
            print(df.notnull().sum().to_string())
            print("\n--- Latest 3 Rows in Database ---")
            print(df.tail(3).to_string())
        else:
            print("⚠️ The macro_indicators table is completely empty.")
            
    except Exception as e:
        print(f"❌ Database read error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run_diagnostics()