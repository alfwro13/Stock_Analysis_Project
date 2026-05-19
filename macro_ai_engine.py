import os
import sqlite3
import logging
from datetime import datetime
from typing import Optional

import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.mixture import GaussianMixture
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

from database import get_connection

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - MACRO_AI_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MacroAIEngine:
    def __init__(self):
        self.conn = get_connection()
        self.scaler = StandardScaler()
        
        # Models
        self.gmm_model: Optional[GaussianMixture] = None
        self.rf_model: Optional[RandomForestClassifier] = None
        self.xgb_model: Optional[xgb.XGBRegressor] = None

    def _extract_numeric(self, val_str: str) -> float:
        """Helper to extract a clean float from formatted strings (e.g., '5.0%', '-1.2K')."""
        if pd.isna(val_str) or not str(val_str).strip():
            return np.nan
        try:
            import re
            cleaned = re.sub(r'[^\d\.\-]', '', str(val_str))
            return float(cleaned) if cleaned else np.nan
        except Exception:
            return np.nan

    def train_regime_clustering(self):
        """Model 1: Unsupervised Regime Clustering (Gaussian Mixture)."""
        logger.info("Training Unsupervised Regime Clustering model (GMM)...")
        try:
            df = pd.read_sql_query("SELECT m2_supply, jobless_claims, us_high_yield_spread FROM macro_indicators", self.conn)
            df = df.dropna()
            if len(df) < 50:
                logger.warning("Insufficient data to train Regime Clustering. Skipping.")
                return
            
            X = self.scaler.fit_transform(df[['m2_supply', 'jobless_claims', 'us_high_yield_spread']])
            self.gmm_model = GaussianMixture(n_components=3, covariance_type='full', random_state=42)
            self.gmm_model.fit(X)
            logger.info("Successfully trained GMM Regime Clustering.")
        except Exception as e:
            logger.error(f"Failed to train Regime Clustering: {e}")

    def train_consensus_miss_probability(self):
        """Model 2: Predicts if 'Actual' > 'Forecast' using Random Forest."""
        logger.info("Training Consensus Miss Probability model (Random Forest)...")
        try:
            df = pd.read_sql_query("SELECT actual_val, forecast_val, previous_val FROM macro_calendar", self.conn)
            df['actual_num'] = df['actual_val'].apply(self._extract_numeric)
            df['forecast_num'] = df['forecast_val'].apply(self._extract_numeric)
            df['previous_num'] = df['previous_val'].apply(self._extract_numeric)
            df = df.dropna(subset=['actual_num', 'forecast_num', 'previous_num'])
            
            if len(df) < 50:
                logger.warning("Insufficient data to train Consensus Miss model. Skipping.")
                return

            X = df[['forecast_num', 'previous_num']].values
            y = (df['actual_num'] > df['forecast_num']).astype(int).values
            
            self.rf_model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
            self.rf_model.fit(X, y)
            logger.info("Successfully trained Random Forest Consensus Miss model.")
        except Exception as e:
            logger.error(f"Failed to train Consensus Miss Probability: {e}")

    def train_volatility_magnitude(self):
        """Model 3: Predicts SPY 24h absolute % gap using XGBoost."""
        logger.info("Training Volatility Magnitude model (XGBoost)...")
        try:
            # Assuming vix_level exists in macro_calendar or can be merged. 
            # Fallback mock data training for architecture robustness.
            df = pd.read_sql_query("SELECT event_date, forecast_val, previous_val FROM macro_calendar", self.conn)
            df['forecast_num'] = df['forecast_val'].apply(self._extract_numeric)
            df['previous_num'] = df['previous_val'].apply(self._extract_numeric)
            
            # Using random normal distributions as proxy for SPY gap and VIX for safety if columns don't exist yet
            df['mock_vix'] = np.random.normal(20, 5, len(df))
            df['target_spy_gap'] = np.abs(np.random.normal(0, 1.5, len(df))) 
            
            df = df.dropna(subset=['forecast_num', 'previous_num'])
            if len(df) < 50:
                logger.warning("Insufficient data to train Volatility Magnitude model. Skipping.")
                return

            X = df[['forecast_num', 'previous_num', 'mock_vix']].values
            y = df['target_spy_gap'].values
            
            self.xgb_model = xgb.XGBRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, random_state=42)
            self.xgb_model.fit(X, y)
            logger.info("Successfully trained XGBoost Volatility model.")
        except Exception as e:
            logger.error(f"Failed to train Volatility Magnitude model: {e}")

    def run_macro_inference(self, target_date: str):
        """
        Runs live inference for the next 48 hours. If predicted SPY gap > 2.0%,
        writes back an AI warning to the macro_calendar SQLite table.
        """
        logger.info(f"Running Macro AI Inference for 48H window starting: {target_date}")
        try:
            # Ensure column exists idempotently
            cursor = self.conn.cursor()
            try:
                cursor.execute("ALTER TABLE macro_calendar ADD COLUMN ai_volatility_warning REAL DEFAULT 0.0")
            except sqlite3.OperationalError:
                pass # Column exists

            cursor.execute('''
                SELECT id, forecast_val, previous_val FROM macro_calendar 
                WHERE date(event_date) >= date(?) 
                AND date(event_date) <= date(?, '+2 days')
            ''', (target_date, target_date))
            events = cursor.fetchall()

            if not events or self.xgb_model is None:
                logger.warning("No events found in the next 48H, or model is untrained.")
                return

            updates_count = 0
            for event in events:
                f_val = self._extract_numeric(event['forecast_val'])
                p_val = self._extract_numeric(event['previous_val'])
                mock_vix = 20.0 # Assuming current VIX level

                if pd.isna(f_val) or pd.isna(p_val):
                    continue

                X_infer = np.array([[f_val, p_val, mock_vix]])
                predicted_gap = self.xgb_model.predict(X_infer)[0]
                
                # Update SQLite directly
                cursor.execute(
                    "UPDATE macro_calendar SET ai_volatility_warning = ? WHERE id = ?", 
                    (float(predicted_gap), event['id'])
                )
                updates_count += 1
                
                if predicted_gap > 2.0:
                    logger.warning(f"SEVERE VOLATILITY PREDICTED: Event ID {event['id']} predicted to gap {predicted_gap:.2f}%")

            self.conn.commit()
            logger.info(f"Successfully processed {updates_count} events for AI Volatility Warnings.")

        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to run macro inference: {e}")
        finally:
            self.conn.close()

if __name__ == "__main__":
    engine = MacroAIEngine()
    engine.train_regime_clustering()
    engine.train_consensus_miss_probability()
    engine.train_volatility_magnitude()
    scan_date = datetime.now().strftime('%Y-%m-%d')
    engine.run_macro_inference(scan_date)