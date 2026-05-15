import logging
import sqlite3
import pandas as pd
import numpy as np
import joblib
from pathlib import Path
from typing import List
from xgboost import XGBClassifier
from sklearn.ensemble import RandomForestClassifier, VotingClassifier

from config import BASE_DIR
from database import get_connection

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - AI_PREDICTION_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
MODELS_DIR = BASE_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
MODEL_PATH = MODELS_DIR / "ml_ensemble.joblib"

# The exact feature space expected by the model
FEATURE_COLS = [
    'rsi_14', 'macd', 'macd_signal', 'macd_hist', 
    'volume_surge', 'bullish_cross', 'dist_sma_50', 'dist_sma_200'
]


def train_global_ml_model() -> None:
    """
    Connects to the local SQLite DB, builds technical features and targets,
    and trains a global ensemble model predicting >3% returns over 5 days.
    """
    logger.info("Initiating Global ML Model Training pipeline...")
    
    try:
        conn = get_connection()
        
        # 1. Fetch all historical quantitative data
        query = """
            SELECT ticker, date, close_price, rsi_14, macd, macd_signal, macd_hist, 
                   sma_50, sma_200, volume_surge, bullish_cross 
            FROM quant_signals 
            ORDER BY ticker, date ASC
        """
        df = pd.read_sql_query(query, conn)
        conn.close()

        if df.empty:
            logger.warning("No quantitative data found in DB. Aborting ML training.")
            return

        # 2. Feature Engineering
        logger.info(f"Extracting features from {len(df)} historical records...")
        df['dist_sma_50'] = (df['close_price'] - df['sma_50']) / df['sma_50']
        df['dist_sma_200'] = (df['close_price'] - df['sma_200']) / df['sma_200']
        
        # Coerce boolean/int proxy columns to strict integers
        df['volume_surge'] = df['volume_surge'].fillna(0).astype(int)
        df['bullish_cross'] = df['bullish_cross'].fillna(0).astype(int)

        # Replace infinite division errors and drop missing data safely
        df.replace([np.inf, -np.inf], np.nan, inplace=True)
        df.dropna(subset=FEATURE_COLS, inplace=True)

        # 3. Target Variable Creation (Shift -5 days per ticker)
        df['future_close'] = df.groupby('ticker')['close_price'].shift(-5)
        df.dropna(subset=['future_close'], inplace=True)
        
        # Classification Target: 1 if return > 3%, else 0
        df['target'] = ((df['future_close'] - df['close_price']) / df['close_price'] > 0.03).astype(int)

        X = df[FEATURE_COLS]
        y = df['target']

        if len(X) < 1000:
            logger.warning(f"Insufficient training samples ({len(X)}). Need more historical data.")
            return

        # 4. Train the Ensemble Model
        logger.info(f"Training Soft-Voting Ensemble on {len(X)} samples with {y.sum()} positive classes...")
        
        rf = RandomForestClassifier(
            n_estimators=150, 
            max_depth=6, 
            random_state=42, 
            n_jobs=-1
        )
        xgb = XGBClassifier(
            n_estimators=150, 
            max_depth=6, 
            learning_rate=0.05, 
            random_state=42, 
            n_jobs=-1, 
            use_label_encoder=False, 
            eval_metric='logloss'
        )

        ensemble = VotingClassifier(estimators=[('rf', rf), ('xgb', xgb)], voting='soft')
        ensemble.fit(X, y)

        # 5. Persist to Disk
        joblib.dump(ensemble, MODEL_PATH)
        logger.info(f"✅ ML Ensemble successfully trained and saved to {MODEL_PATH}")

    except Exception as e:
        logger.error(f"Fatal error during ML model training: {e}")


def update_daily_ml_predictions(tickers: List[str]) -> None:
    """
    Loads the trained model, fetches the latest raw row for each ticker,
    calculates inference features, and updates the database with confidence scores.
    """
    if not tickers:
        logger.warning("Empty ticker list provided for ML inference. Skipping.")
        return

    if not MODEL_PATH.exists():
        logger.warning(f"Model file {MODEL_PATH} not found. Awaiting weekend training cycle.")
        return

    logger.info(f"Initiating ML Inference for {len(tickers)} assets...")
    
    try:
        model = joblib.load(MODEL_PATH)
        conn = get_connection()
        
        # Fetch the most recent quantitative row for the target assets
        placeholders = ','.join('?' for _ in tickers)
        query = f"""
            SELECT ticker, date, close_price, rsi_14, macd, macd_signal, macd_hist, 
                   sma_50, sma_200, volume_surge, bullish_cross 
            FROM quant_signals 
            WHERE ticker IN ({placeholders})
            AND date = (SELECT MAX(date) FROM quant_signals qs WHERE qs.ticker = quant_signals.ticker)
        """
        df = pd.read_sql_query(query, conn, params=tickers)
        
        if df.empty:
            logger.warning("No recent data found to run inference on.")
            conn.close()
            return

        # Feature Engineering (Same logic as training)
        df['dist_sma_50'] = (df['close_price'] - df['sma_50']) / df['sma_50']
        df['dist_sma_200'] = (df['close_price'] - df['sma_200']) / df['sma_200']
        df['volume_surge'] = df['volume_surge'].fillna(0).astype(int)
        df['bullish_cross'] = df['bullish_cross'].fillna(0).astype(int)
        
        df.replace([np.inf, -np.inf], np.nan, inplace=True)
        
        update_payloads = []
        for _, row in df.iterrows():
            # If standard indicators are missing, skip prediction safely
            if pd.isna(row[FEATURE_COLS]).any():
                continue
                
            # Isolate the exact feature array order expected by the model
            X_infer = pd.DataFrame([row[FEATURE_COLS]])
            
            # Predict Probability [Class 0, Class 1] -> Extract Class 1
            prob = model.predict_proba(X_infer)[0][1]
            ml_confidence_score = round(prob * 100.0, 2)
            
            update_payloads.append((ml_confidence_score, row['ticker'], row['date']))

        if update_payloads:
            cursor = conn.cursor()
            update_query = """
                UPDATE quant_signals 
                SET ml_confidence_score = ? 
                WHERE ticker = ? AND date = ?
            """
            cursor.executemany(update_query, update_payloads)
            conn.commit()
            logger.info(f"✅ Executed ML predictions for {len(update_payloads)} assets.")
        
    except Exception as e:
        logger.error(f"Fatal error during ML inference: {e}")
    finally:
        if 'conn' in locals():
            conn.close()