# ai_prediction_engine.py
import time
import logging
import sqlite3
import pandas as pd
import numpy as np
import joblib
import yfinance as yf
import ta
from pathlib import Path
from typing import List, Tuple
from xgboost import XGBClassifier
from sklearn.ensemble import RandomForestClassifier, VotingClassifier

from config import BASE_DIR
from database import get_connection
from data_engine import DataEngine

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

# Hardcoded list of High Quality Blue Chips to supplement the dataset
BLUE_CHIPS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "BRK-B", "LLY", "TSLA", 
    "AVGO", "JPM", "UNH", "V", "XOM", "MA", "JNJ", "PG", "HD", "COST", "MRK", 
    "ABBV", "CVX", "CRM", "AMD", "BAC", "PEP", "KO", "LIN", "TMO", "WMT", "MCD", 
    "DIS", "CSCO", "ACN", "ABT", "INTU", "QCOM", "IBM", "CAT", "VZ", "AMGN", 
    "TXN", "NOW", "PFE", "COP", "BA", "SPY", "QQQ", "DIA", "IWM"
]

def log_notification(message_type: str, message_text: str) -> None:
    """Helper function to log scan progress to the system notification center."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)",
            (message_type, message_text)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to log notification: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def get_target_tickers() -> List[str]:
    """
    Combines the user's existing portfolio/watchlist tickers with a curated list
    of Blue Chips. Deduplicates and limits the final payload to 250 tickers.
    """
    logger.info("Extracting user portfolio and watchlist tickers...")
    try:
        engine = DataEngine()
        user_tickers = engine.get_all_tickers()
    except Exception as e:
        logger.error(f"Failed to fetch user tickers from DataEngine: {e}")
        user_tickers = []

    # Combine, deduplicate, and sort for determinism
    combined_set = set(user_tickers).union(set(BLUE_CHIPS))
    
    # Filter out mutual funds (0P...) or known bad tickers if any slipped through
    cleaned_list = [t for t in combined_set if t and not t.startswith("0P")]
    
    # Limit to maximum 250 tickers
    final_tickers = sorted(cleaned_list)[:250]
    
    logger.info(f"Targeting {len(final_tickers)} unique tickers for historical backfill.")
    return final_tickers

def run_historical_backfill() -> None:
    """
    Downloads 2 years of daily data per ticker, calculates vectorized 
    technical indicators, and executes bulk INSERT OR IGNORE operations into SQLite.
    """
    tickers = get_target_tickers()
    if not tickers:
        logger.warning("No tickers found to backfill. Aborting.")
        return

    log_notification("Info", f"ML Historical Backfill initiated for {len(tickers)} assets.")
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        total_inserted = 0
        total_tickers = len(tickers)
        
        for i, ticker in enumerate(tickers):
            logger.info(f"[{i+1}/{total_tickers}] Processing 2y historical data for {ticker}...")
            
            try:
                df = yf.download(ticker, period="2y", interval="1d", progress=False, auto_adjust=True)
                
                if df.empty:
                    continue

                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                df.dropna(subset=['Close', 'Volume'], inplace=True)
                
                if len(df) < 200:
                    continue

                # Vector-calculate technical indicators via 'ta' library
                df['rsi_14'] = ta.momentum.RSIIndicator(close=df['Close'], window=14).rsi()
                
                macd_indicator = ta.trend.MACD(close=df['Close'])
                df['macd'] = macd_indicator.macd()
                df['macd_signal'] = macd_indicator.macd_signal()
                df['macd_hist'] = macd_indicator.macd_diff()
                
                df['sma_50'] = ta.trend.SMAIndicator(close=df['Close'], window=50).sma_indicator()
                df['sma_200'] = ta.trend.SMAIndicator(close=df['Close'], window=200).sma_indicator()
                df['vol_sma_20'] = df['Volume'].rolling(window=20).mean()

                # Vectorize proxy logic for boolean triggers
                df['volume_surge'] = (df['Volume'] > (df['vol_sma_20'] * 1.5)).astype(int)
                df['bullish_cross'] = ((df['macd'] > df['macd_signal']) & (df['macd'].shift(1) <= df['macd_signal'].shift(1))).astype(int)

                df.dropna(inplace=True)
                if df.empty:
                    continue

                records: List[Tuple] = []
                for index, row in df.iterrows():
                    date_str = index.strftime('%Y-%m-%d')
                    records.append((
                        ticker, date_str, float(row['Close']), int(row['Volume']),
                        float(row['rsi_14']), float(row['macd']), float(row['macd_signal']),
                        float(row['macd_hist']), float(row['sma_50']), float(row['sma_200']),
                        int(row['volume_surge']), int(row['bullish_cross'])
                    ))

                query = """
                    INSERT OR IGNORE INTO quant_signals 
                    (ticker, date, close_price, volume, rsi_14, macd, macd_signal, macd_hist, sma_50, sma_200, volume_surge, bullish_cross)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                cursor.executemany(query, records)
                conn.commit()
                
                inserted = cursor.rowcount
                total_inserted += inserted

            except Exception as e:
                logger.error(f"Error processing ticker {ticker}: {e}")
                conn.rollback()
            finally:
                time.sleep(0.5)

            processed = i + 1
            if total_tickers >= 2 and processed == total_tickers // 2:
                log_notification("Info", f"ML Historical Backfill is 50% complete ({processed}/{total_tickers}).")

        logger.info(f"--- BACKFILL COMPLETE. Injected {total_inserted} new historical rows. ---")
        log_notification("Success", f"ML Historical Backfill completed successfully. Injected {total_inserted:,} data points.")

    except Exception as e:
        logger.error(f"Fatal error during historical backfill execution: {e}")
        log_notification("Error", f"ML Historical Backfill failed: {str(e)}")
    finally:
        conn.close()

def train_global_ml_model() -> None:
    """
    Connects to the local SQLite DB, builds technical features and targets,
    and trains a global ensemble model predicting >3% returns over 5 days.
    """
    logger.info("Initiating Global ML Model Training pipeline...")
    log_notification("Info", "Global ML Model Training pipeline initiated.")
    
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
            log_notification("Error", f"Insufficient training samples ({len(X)}). Backfill required.")
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
        log_notification("Success", f"Global ML Model successfully trained and persisted. Samples evaluated: {len(X):,}")

    except Exception as e:
        logger.error(f"Fatal error during ML model training: {e}")
        log_notification("Error", f"ML Model Training failed: {str(e)}")

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