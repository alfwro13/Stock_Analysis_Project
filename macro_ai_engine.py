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
        Model 3: XGBoost Regressor with Model Stacking.
        Consumes Event Data + Historical VIX + Upstream Stacking Features (HMM state and RF probability)
        to predict the exact percentage magnitude of the SPY gap following a macroeconomic release.
        """
        logger.info("Training Volatility Magnitude model (XGBoost) with Model Stacking Architecture...")
        try:
            # Join the specific event dates with the market regime table to capture historical VIX context
            query = """
                SELECT c.event_id, c.event_date, c.forecast_val, c.previous_val, c.actual_val, c.post_event_spy_gap, r.vix_close 
                FROM macro_calendar c
                LEFT JOIN market_regimes r ON date(c.event_date) = r.date
                WHERE c.is_event_passed = 1 AND c.post_event_spy_gap IS NOT NULL
            """
            df_cal = pd.read_sql_query(query, self.conn)
            
            if df_cal.empty:
                logger.warning("No verified calendar rows found for XGBoost training. Skipping.")
                return
                
            df_cal['forecast_num'] = df_cal['forecast_val'].apply(self._extract_numeric)
            df_cal['previous_num'] = df_cal['previous_val'].apply(self._extract_numeric)
            df_cal['vix_close'] = df_cal['vix_close'].fillna(20.0)
            
            df_cal = df_cal.dropna(subset=['forecast_num', 'previous_num', 'post_event_spy_gap'])
            
            if len(df_cal) < 10:
                logger.warning("Insufficient verified SPY gap data to train Volatility Magnitude model. Skipping.")
                return

            # --- Stacking Feature 1: Compile historical HMM hidden state map ---
            hmm_state_map = {}
            if self.hmm_model is not None:
                try:
                    q_ind = "SELECT date, us_m2, us_jobless_claims, us_high_yield_spread, us_yield_curve FROM macro_indicators ORDER BY date ASC"
                    df_ind = pd.read_sql_query(q_ind, self.conn)
                    df_ind_clean = df_ind.dropna()
                    if not df_ind_clean.empty:
                        X_ind = self.scaler.transform(df_ind_clean[['us_m2', 'us_jobless_claims', 'us_high_yield_spread', 'us_yield_curve']])
                        predicted_states = self.hmm_model.predict(X_ind)
                        for d, s in zip(df_ind_clean['date'], predicted_states):
                            hmm_state_map[d] = int(s)
                except Exception as ex:
                    logger.error(f"Failed to compile historical HMM feature states for stacking: {ex}")

            # --- Stacking Feature 2: Assemble unified multidimensional feature matrix ---
            X_list = []
            y = df_cal['post_event_spy_gap'].values
            
            for _, row in df_cal.iterrows():
                f_num = row['forecast_num']
                p_num = row['previous_num']
                vix = row['vix_close']
                
                # Fetch HMM state for the specific event date (fallback to 0 if missing)
                evt_date_str = pd.to_datetime(row['event_date']).strftime('%Y-%m-%d')
                hmm_state = hmm_state_map.get(evt_date_str, 0)
                
                # Fetch RF consensus miss probability (fallback to 0.5 if model or inputs missing)
                rf_prob = 0.5
                if self.rf_model is not None:
                    try:
                        rf_prob = float(self.rf_model.predict_proba([[f_num, p_num]])[0][1])
                    except Exception:
                        pass
                        
                X_list.append([f_num, p_num, vix, hmm_state, rf_prob])
                
            X = np.array(X_list)
            
            # Tuning constraints for stacked volatility modeling (expanded max_depth to handle additional dimensions)
            self.xgb_model = xgb.XGBRegressor(
                n_estimators=100, 
                max_depth=4, 
                learning_rate=0.05, 
                objective='reg:squarederror',
                random_state=42
            )
            self.xgb_model.fit(X, y)
            
            logger.info(f"Successfully trained Stacking XGBoost Volatility model on {len(X)} historical events.")
        except Exception as e:
            logger.error(f"Failed to train Volatility Magnitude model: {e}")

    def run_macro_inference(self, target_date: str) -> None:
        """
        Runs live inference for upcoming events in the next 48 hours.
        Leverages the HMM regime model and Random Forest surprise model as standalone predictors,
        persists their independent insights to SQLite tables for the UI, and feeds them into
        the final stacked XGBoost model for precision volatility forecasting.
        """
        logger.info(f"Running Stacked Macro AI Inference for 48H window starting: {target_date}")
        try:
            cursor = self.conn.cursor()
            
            # 1. Fetch current VIX context
            cursor.execute("SELECT vix_close FROM market_regimes ORDER BY date DESC LIMIT 1")
            vix_row = cursor.fetchone()
            current_vix = float(vix_row['vix_close']) if vix_row and vix_row['vix_close'] else 20.0

            # 2. Compute current live HMM Hidden Macro State
            current_hmm_state = 0
            if self.hmm_model is not None:
                try:
                    cursor.execute("""
                        SELECT us_m2, us_jobless_claims, us_high_yield_spread, us_yield_curve 
                        FROM macro_indicators 
                        ORDER BY date DESC LIMIT 1
                    """)
                    ind_row = cursor.fetchone()
                    if ind_row:
                        X_ind_live = self.scaler.transform([[
                            float(ind_row['us_m2']),
                            float(ind_row['us_jobless_claims']),
                            float(ind_row['us_high_yield_spread']),
                            float(ind_row['us_yield_curve'])
                        ]])
                        current_hmm_state = int(self.hmm_model.predict(X_ind_live)[0])
                        
                        # Surface the standalone HMM prediction to the UI via market_regimes table update
                        cursor.execute("""
                            UPDATE market_regimes 
                            SET ai_hmm_state = ? 
                            WHERE date = (SELECT MAX(date) FROM market_regimes)
                        """, (current_hmm_state,))
                except Exception as ex:
                    logger.error(f"Failed to calculate current live HMM hidden macro regime state: {ex}")

            # 3. Fetch upcoming events to forecast
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
                logger.warning("XGBoost Volatility model is untrained. Bypassing stacked inference.")
                return

            updates_count = 0
            for event in events:
                f_val = self._extract_numeric(event['forecast_val'])
                p_val = self._extract_numeric(event['previous_val'])

                if pd.isna(f_val) or pd.isna(p_val):
                    continue

                # Compute Standalone Surprise Probability using the Random Forest model
                rf_consensus_miss_prob = 0.5
                if self.rf_model is not None:
                    try:
                        rf_consensus_miss_prob = float(self.rf_model.predict_proba([[f_val, p_val]])[0][1])
                    except Exception as ex:
                        logger.debug(f"Failed to infer consensus miss probability for event {event['event_id']}: {ex}")

                # Assemble the complete stacked feature vector matching training schema
                X_infer = np.array([[f_val, p_val, current_vix, current_hmm_state, rf_consensus_miss_prob]])
                predicted_gap = self.xgb_model.predict(X_infer)[0]
                predicted_gap = max(0.0, float(predicted_gap))
                
                # Surface BOTH the final volatility warning AND the independent surprise probability to the database
                cursor.execute("""
                    UPDATE macro_calendar 
                    SET ai_volatility_warning = ?, 
                        ai_consensus_miss_prob = ? 
                    WHERE event_id = ?
                """, (predicted_gap, rf_consensus_miss_prob, event['event_id']))
                updates_count += 1
                
                if predicted_gap > 2.0:
                    logger.warning(f"🚨 SEVERE VOLATILITY PREDICTED: Event ID {event['event_id']} (Miss Prob: {rf_consensus_miss_prob:.2%}) — Absolute Volatility Shock Magnitude: ±{predicted_gap:.2f}% (direction unknown)")

            self.conn.commit()
            logger.info(f"Successfully processed {updates_count} events for Stacked AI Volatility and Surprise Warnings.")

        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to run stacked macro inference: {e}")
        finally:
            self.conn.close()