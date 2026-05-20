# test_ml_pipeline.py
import logging
import sqlite3
import pandas as pd
from pathlib import Path
from config import BASE_DIR
from ai_prediction_engine import (
    run_historical_backfill,
    train_global_ml_model,
    update_daily_ml_predictions,
    get_target_tickers
)

# Configure console logging to see the Walk-Forward results immediately
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

logger = logging.getLogger(__name__)

def verify_database_state() -> None:
    """
    Connects to the SQLite database to verify the schema updates and data ingestion.
    """
    db_path = BASE_DIR / "data" / "analysis.db" 
    if not db_path.exists():
        logger.warning(f"Database not found at {db_path}. Assuming handled by get_connection().")
        return

    try:
        conn = sqlite3.connect(db_path)
        
        metadata_df = pd.read_sql_query("SELECT * FROM ticker_metadata LIMIT 5", conn)
        logger.info(f"--- Ticker Metadata Sample ---\n{metadata_df}")
        
        inference_df = pd.read_sql_query("""
            SELECT ticker, date, close_price, ml_confidence_score 
            FROM quant_signals 
            WHERE ml_confidence_score IS NOT NULL 
            LIMIT 5
        """, conn)
        logger.info(f"--- ML Inference Sample ---\n{inference_df}")
        
        conn.close()
    except Exception as e:
        logger.error(f"Database verification failed: {e}")

def main():
    logger.info("=== STARTING QUANT PIPELINE E2E TEST ===")
    
    # STEP 1: Test ETL & Contextual Metadata Injection
    logger.info("--- STEP 1: Running Historical Backfill & Metadata Sync ---")
    run_historical_backfill()
    
    # STEP 2: Test Walk-Forward Validation & Model Training
    logger.info("--- STEP 2: Running Walk-Forward ML Training ---")
    train_global_ml_model()
    
    # STEP 3: Test Dynamic Inference
    logger.info("--- STEP 3: Running Daily ML Inference ---")
    # Grab a small sample of tickers to test inference
    sample_tickers = get_target_tickers()[:10] 
    update_daily_ml_predictions(sample_tickers)
    
    # STEP 4: Verification
    logger.info("--- STEP 4: Verifying SQLite Database State ---")
    verify_database_state()
    
    logger.info("=== TEST COMPLETE ===")

if __name__ == "__main__":
    main()