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
from quant_signals import QuantEngine

# Configure console logging to see the Walk-Forward results immediately
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

logger = logging.getLogger(__name__)

def test_shap_scoring(ticker: str) -> None:
    """
    Tests the integration of the SHAP TreeExplainer in the QuantEngine.
    """
    logger.info(f"Running QuantEngine.analyze_ticker() for {ticker} to test SHAP scoring...")
    
    engine = QuantEngine()
    engine.analyze_ticker(ticker)
    engine.close()

def verify_database_state(test_ticker: str) -> None:
    """
    Connects to the SQLite database to verify the schema updates and data ingestion.
    """
    db_path = BASE_DIR / "data" / "analysis.db" 
    if not db_path.exists():
        logger.warning(f"Database not found at {db_path}. Assuming handled by get_connection().")
        return

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        # 1. Verify Metadata
        metadata_df = pd.read_sql_query("SELECT * FROM ticker_metadata LIMIT 5", conn)
        logger.info(f"--- Ticker Metadata Sample ---\n{metadata_df}")
        
        # 2. Verify Inference
        inference_df = pd.read_sql_query("""
            SELECT ticker, date, close_price, ml_confidence_score 
            FROM quant_signals 
            WHERE ml_confidence_score IS NOT NULL 
            LIMIT 5
        """, conn)
        logger.info(f"--- ML Inference Sample ---\n{inference_df}")
        
        # 3. Verify SHAP Engine applied correctly
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ticker, composite_score, score_method, ml_confidence 
            FROM stock_signals 
            WHERE ticker = ?
        """, (test_ticker,))
        row = cursor.fetchone()
        
        if row:
            logger.info(f"--- SHAP Scoring Verification for {test_ticker} ---")
            logger.info(f"Score Method: {row['score_method']}")
            logger.info(f"Final Score:  {row['composite_score']}")
            logger.info(f"ML Conf:      {row['ml_confidence']}")
            
            if row['score_method'] == 'SHAP':
                logger.info("✅ SUCCESS: The QuantEngine successfully used SHAP dynamic weights!")
            else:
                logger.warning("❌ WARNING: The QuantEngine fell back to HARDCODED weights. SHAP failed to load or calculate.")
        else:
            logger.warning(f"No record found in stock_signals for {test_ticker}.")
        
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
    
    # STEP 4: Test SHAP Scoring Integration
    logger.info("--- STEP 4: Testing SHAP Integration ---")
    target_test_ticker = sample_tickers[0] if sample_tickers else "SPY"
    test_shap_scoring(target_test_ticker)
    
    # STEP 5: Verification
    logger.info("--- STEP 5: Verifying SQLite Database State ---")
    verify_database_state(target_test_ticker)
    
    logger.info("=== TEST COMPLETE ===")

if __name__ == "__main__":
    main()