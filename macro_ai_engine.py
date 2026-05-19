# macro_ai_engine.py
import os
import sqlite3
import logging
import re
from datetime import datetime
from typing import Optional

import pandas as pd
import numpy as np
import xgboost as xgb
from hmmlearn import hmm
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
    def __init__(self) -> None:
        self.conn = get_connection()
        self.scaler = StandardScaler()
        
        # Production Models
        self.hmm_model: Optional[hmm.GaussianHMM] = None
        self.rf_model: Optional[RandomForestClassifier] = None
        self.xgb_model: Optional[xgb.XGBRegressor] = None

    def _extract_numeric(self, val_str: str) -> float:
        """Helper to extract a clean float from formatted strings (e.g., '5.0%', '-1.2K')."""
        if pd.isna(val_str) or not str(val_str).strip():
            return np.nan
        try:
            cleaned = re.sub(r'[^\d\.\-]', '', str(val_str))
            return float(cleaned) if cleaned else np.nan
        except Exception:
            return np.nan

    def train_regime_clustering(self) -> None:
        """
        Model 1: Time-Series Regime Detection (Hidden Markov Model).
        Consumes deep structural macro factors (Liquidity, Labor, Credit, Yield Curve)
        to calculate transition probabilities and hidden market states.
        """
        logger.info("Training Time-Series Regime Clustering model (HMM)...")
        try:
            # Fetch structural macro data, including the new Yield Curve inversion tracker
            query = """
                SELECT date, us_m2, us_jobless_claims, us_high_yield_spread, us_yield_curve 
                FROM macro_indicators 
                ORDER BY date ASC
            """
            df = pd.read_sql_query(query, self.conn)
            df = df.dropna()
            
            if len(df) < 50:
                logger.warning("Insufficient structural data to train HMM Clustering. Need more weekly runs. Skipping.")
                return
            
            # Scale features for the Gaussian emission distributions
            X = self.scaler.fit_transform(df[['us_m2', 'us_jobless_claims', 'us_high_yield_spread', 'us_yield_curve']])
            
            # Initialize a 3-State Hidden Markov Model (e.g., Expansion, Choppy/Stagflation, Recession/Crash)
            self.hmm_model = hmm.GaussianHMM(
                n_components=3, 
                covariance_type="full", 
                n_iter=100, 
                random_state=42
            )
            self.hmm_model.fit(X)
            
            logger.info("Successfully trained Hidden Markov Model for Regime Clustering.")
        except Exception as e:
            logger.error(f"Failed to train Regime Clustering (HMM): {e}")

    def train_consensus_miss_probability(self) -> None:
        """
        Model 2: Random Forest Classifier.
        Predicts whether the 'Actual' release will be mathematically greater than the 'Forecast'
        based on historical tracking of how often Wall Street is wrong.
        """
        logger.info("Training Consensus Miss Probability model (Random Forest)...")
        try:
            query = "SELECT forecast_val, previous_val, actual_val FROM macro_calendar WHERE is_event_passed = 1"
            df = pd.read_sql_query(query, self.conn)
            
            df['forecast_num'] = df['forecast_val'].apply(self._extract_numeric)
            df['previous_num'] = df['previous_val'].apply(self._extract_numeric)
            df['actual_num'] = df['actual_val'].apply(self._extract_numeric)
            
            # Drop rows where we lack ground truth
            df = df.dropna(subset=['forecast_num', 'previous_num', 'actual_num'])
            
            if len(df) < 10:
                logger.warning("Insufficient verified ground-truth data to train Consensus Miss model. Skipping.")
                return

            X = df[['forecast_num', 'previous_num']].values
            # Target classification: 1 if Actual > Forecast, else 0
            y = (df['actual_num'] > df['forecast_num']).astype(int).values
            
            # Restrict depth to prevent overfitting on sparse early data
            self.rf_model = RandomForestClassifier(n_estimators=100, max_depth=4, random_state=42)
            self.rf_model.fit(X, y)
            
            logger.info(f"Successfully trained Random Forest Consensus Miss model on {len(X)} historical events.")
        except Exception as e:
            logger.error(f"Failed to train Consensus Miss Probability: {e}")

    def train_volatility_magnitude(self) -> None:
        """
        Model 3: XGBoost Regressor.
        Consumes Event Data + Historical VIX to predict the exact percentage magnitude 
        of the SPY gap following a macroeconomic release.
        """
        logger.info("Training Volatility Magnitude model (XGBoost)...")
        try:
            # Join the specific event dates with the market regime table to capture historical VIX context
            query = """
                SELECT c.forecast_val, c.previous_val, c.post_event_spy_gap, r.vix_close 
                FROM macro_calendar c
                LEFT JOIN market_regimes r ON date(c.event_date) = r.date
                WHERE c.is_event_passed = 1 AND c.post_event_spy_gap IS NOT NULL
            """
            df = pd.read_sql_query(query, self.conn)
            
            df['forecast_num'] = df['forecast_val'].apply(self._extract_numeric)
            df['previous_num'] = df['previous_val'].apply(self._extract_numeric)
            
            # If historical VIX isn't available for an old event, default to baseline normal (20.0)
            df['vix_close'] = df['vix_close'].fillna(20.0)
            
            df = df.dropna(subset=['forecast_num', 'previous_num', 'post_event_spy_gap'])
            
            if len(df) < 10:
                logger.warning("Insufficient verified SPY gap data to train Volatility Magnitude model. Skipping.")
                return

            X = df[['forecast_num', 'previous_num', 'vix_close']].values
            y = df['post_event_spy_gap'].values
            
            # Tuning constraints for volatility (smooth learning rate, shallow depth)
            self.xgb_model = xgb.XGBRegressor(
                n_estimators=100, 
                max_depth=3, 
                learning_rate=0.05, 
                objective='reg:squarederror',
                random_state=42
            )
            self.xgb_model.fit(X, y)
            
            logger.info(f"Successfully trained XGBoost Volatility model on {len(X)} verified historical SPY gaps.")
        except Exception as e:
            logger.error(f"Failed to train Volatility Magnitude model: {e}")

    def run_macro_inference(self, target_date: str) -> None:
        """
        Runs live inference for upcoming events in the next 48 hours.
        If the predicted SPY gap is extreme (> 2.0%), writes an AI warning 
        to the macro_calendar SQLite table for downstream defense systems to intercept.
        """
        logger.info(f"Running Macro AI Inference for 48H window starting: {target_date}")
        try:
            cursor = self.conn.cursor()
            
            # Fetch the most recent VIX closing value to act as the baseline volatility context
            cursor.execute("SELECT vix_close FROM market_regimes ORDER BY date DESC LIMIT 1")
            vix_row = cursor.fetchone()
            current_vix = float(vix_row['vix_close']) if vix_row and vix_row['vix_close'] else 20.0

            # Fetch upcoming events
            cursor.execute('''
                SELECT event_id, forecast_val, previous_val FROM macro_calendar 
                WHERE date(event_date) >= date(?) 
                AND date(event_date) <= date(?, '+2 days')
                AND is_event_passed = 0
            ''', (target_date, target_date))
            events = cursor.fetchall()

            if not events:
                logger.info("No upcoming Tier-1 events in the next 48H to run inference on.")
                return
                
            if self.xgb_model is None:
                logger.warning("XGBoost Volatility model is untrained. Awaiting more historical event data to accumulate. Bypassing inference.")
                return

            updates_count = 0
            for event in events:
                f_val = self._extract_numeric(event['forecast_val'])
                p_val = self._extract_numeric(event['previous_val'])

                # If the event lacks numerical forecasts (e.g., "OPEC Meetings" or Speeches), skip inference
                if pd.isna(f_val) or pd.isna(p_val):
                    continue

                # Run XGBoost Inference
                X_infer = np.array([[f_val, p_val, current_vix]])
                predicted_gap = self.xgb_model.predict(X_infer)[0]
                
                # Enforce absolute floor to prevent negative volatility logic errors
                predicted_gap = max(0.0, float(predicted_gap))
                
                # Update SQLite directly
                cursor.execute(
                    "UPDATE macro_calendar SET ai_volatility_warning = ? WHERE event_id = ?", 
                    (predicted_gap, event['event_id'])
                )
                updates_count += 1
                
                if predicted_gap > 2.0:
                    logger.warning(f"🚨 SEVERE VOLATILITY PREDICTED: Event ID {event['event_id']} predicted to gap SPY by {predicted_gap:.2f}%")
                else:
                    logger.debug(f"Event ID {event['event_id']} gap predicted at normal threshold: {predicted_gap:.2f}%")

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