import sqlite3
import random
from datetime import datetime, timedelta
import uuid

def seed_calendar():
    conn = sqlite3.connect("data/analysis.db")
    cursor = conn.cursor()
    
    print("🌱 Seeding historical Macro Calendar events for AI Training...")
    
    records = []
    base_date = datetime.now() - timedelta(days=180)
    
    for i in range(50):
        event_date = (base_date + timedelta(days=i*3)).strftime("%Y-%m-%d 14:00:00")
        currency = random.choice(['USD', 'GBP'])
        impact = 'High'
        event_name = "Fed Interest Rate Decision" if currency == 'USD' else "BoE Official Bank Rate"
        
        # Realistic forecast vs previous values (e.g., 5.25%)
        prev_val = round(random.uniform(4.0, 5.5), 2)
        forecast_val = prev_val + random.choice([0.0, 0.25, -0.25])
        
        # --- THE FIX: Inject Synthetic Ground Truth Targets for the AI Models ---
        # 1. Random actual value simulating either hitting, missing, or beating the forecast
        actual_val = forecast_val + random.choice([0.0, 0.25, -0.25])
        
        # 2. Simulate the SPY gap percentage. Larger divergence = larger gap
        if actual_val != forecast_val:
            post_event_spy_gap = round(random.uniform(0.5, 3.5), 2)  # Shock gap
        else:
            post_event_spy_gap = round(random.uniform(0.0, 0.8), 2)  # Normal chop
        
        records.append((
            str(uuid.uuid4()), event_date, currency, impact, event_name,
            forecast_val, prev_val, actual_val, post_event_spy_gap, 1, 1
        ))
        
    cursor.executemany('''
        INSERT OR REPLACE INTO macro_calendar (
            event_id, event_date, currency, impact, event_name, 
            forecast_val, previous_val, actual_val, post_event_spy_gap, is_event_passed, alert_dispatched
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', records)
    
    conn.commit()
    conn.close()
    print(f"✅ Successfully seeded {len(records)} historical events with AI training targets.")

if __name__ == "__main__":
    seed_calendar()