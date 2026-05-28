import sqlite3
import requests
import time

print("Flushing macro_indicators table to allow historical backfill...")
conn = sqlite3.connect("data/analysis.db")
conn.execute("DELETE FROM macro_indicators;")
conn.commit()
conn.close()

print("Triggering background Macro Pipeline rebuild...")
try:
    requests.post("http://127.0.0.1:8090/api/macro/run-pipeline", timeout=5)
    print("Rebuild initiated! Please wait ~30 seconds for the FRED/BoE data to download.")
except Exception as e:
    print(f"Failed to trigger API. Is the server running? Error: {e}")