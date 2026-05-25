import sqlite3
import pandas as pd
from config import DB_PATH

def record_baseline():
    print(f"Connecting to database at {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    
    # Changed ml_confidence_score to ml_confidence to match your stock_signals schema
    query = """
    SELECT ticker, ml_confidence, composite_score, overall_signal
    FROM stock_signals
    WHERE ml_confidence IS NOT NULL
    ORDER BY ml_confidence DESC
    LIMIT 30;
    """
    
    try:
        # Execute query and load into a pandas DataFrame
        df = pd.read_sql_query(query, conn)
        
        if df.empty:
            print("No records found with an ML confidence score.")
            return

        # Print the results nicely to the console
        print("\n=== TOP 30 BASELINE ML CONFIDENCE SCORES (BEFORE FIX) ===")
        print(df.to_string(index=False))
        
        # Save the results to a CSV file spreadsheet
        output_file = "baseline_scores_before_fix.csv"
        df.to_csv(output_file, index=False)
        print(f"\n✅ Results successfully saved to spreadsheet: {output_file}")
        
    except Exception as e:
        print(f"❌ Error executing query: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    record_baseline()