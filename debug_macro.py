# debug_macro.py
import os
import json
import sqlite3
import pandas as pd
import requests

def run_diagnostics():
    print("="*70)
    print(" 🔍 MACRO DATA & AI PIPELINE DIAGNOSTICS")
    print("="*70)

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

    # 3. Check Database Contents & AI Pipeline Readiness
    print("\n" + "="*70)
    print(" 🗄️ DATABASE & MODEL INSPECTION")
    print("="*70)
    
    db_path = "data/analysis.db"
    if not os.path.exists(db_path):
        print(f"❌ Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    try:
        # --- macro_indicators (Used for HMM Clustering) ---
        print("\n[TABLE: macro_indicators] -> Used for HMM Training")
        df_ind = pd.read_sql_query("SELECT * FROM macro_indicators", conn)
        print(f"Total historical indicator rows: {len(df_ind)}")
        if not df_ind.empty:
            valid_hmm = df_ind.dropna(subset=['us_m2', 'us_jobless_claims', 'us_high_yield_spread', 'us_yield_curve'])
            print(f"✅ Valid rows for HMM Training (needs > 50): {len(valid_hmm)}")
        else:
            print("⚠️ The macro_indicators table is empty.")

        # --- market_regimes (HMM Output Surface) ---
        print("\n[TABLE: market_regimes] -> HMM Output Tracker")
        df_mr = pd.read_sql_query("SELECT date, us_turbulence, us_regime_label, ai_hmm_state FROM market_regimes ORDER BY date DESC LIMIT 3", conn)
        if not df_mr.empty:
            print("Latest 3 Regime States:")
            print(df_mr.to_string(index=False))
        else:
            print("⚠️ market_regimes table is empty.")

        # --- macro_calendar (RF and XGBoost Targets & Inferences) ---
        print("\n[TABLE: macro_calendar] -> Event Volatility Engine")
        df_cal = pd.read_sql_query("SELECT * FROM macro_calendar", conn)
        print(f"Total events tracked: {len(df_cal)}")
        
        if not df_cal.empty:
            passed_events = df_cal[df_cal['is_event_passed'] == 1]
            upcoming_events = df_cal[df_cal['is_event_passed'] == 0]
            
            print(f"\n--- Ground Truth (Passed Events: {len(passed_events)}) ---")
            valid_rf = passed_events.dropna(subset=['forecast_val', 'previous_val', 'actual_val'])
            valid_xgb = passed_events.dropna(subset=['forecast_val', 'previous_val', 'post_event_spy_gap'])
            print(f"✅ Valid rows for Random Forest Training (needs Actuals, >10): {len(valid_rf)}")
            print(f"✅ Valid rows for XGBoost Training (needs SPY Gaps, >10): {len(valid_xgb)}")
            
            print(f"\n--- AI Inference (Upcoming Events: {len(upcoming_events)}) ---")
            inferred = upcoming_events.dropna(subset=['ai_consensus_miss_prob', 'ai_volatility_warning'])
            print(f"✅ Upcoming Events with AI Predictions successfully attached: {len(inferred)}")
            
            if not inferred.empty:
                print("\nLatest AI Predicted Upcoming Events:")
                display_cols = ['event_date', 'event_name', 'forecast_val', 'ai_consensus_miss_prob', 'ai_volatility_warning']
                print(inferred[display_cols].head(5).to_string(index=False))
        else:
            print("⚠️ macro_calendar table is empty.")
            
    except Exception as e:
        print(f"❌ Database read error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run_diagnostics()