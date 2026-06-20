import os
import sqlite3
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
import numpy as np
import xgboost as xgb
from hmmlearn import hmm
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.preprocessing import StandardScaler

from constants import (
    MACRO_CAL_MIN_TRAIN_ROWS,
    MACRO_CV_N_SPLITS,
    MACRO_HMM_TRAIN_FRAC,
    MACRO_HMM_MIN_TRAIN_ROWS,
    MACRO_HMM_N_ITER,
    MACRO_HMM_N_STATES,
    MACRO_RF_MAX_DEPTH,
    MACRO_RF_N_ESTIMATORS,
    MACRO_SEVERE_VOL_THRESHOLD,
    MACRO_VIX_DEFAULT,
    MACRO_XGB_LEARNING_RATE,
    MACRO_XGB_MAX_DEPTH,
    MACRO_XGB_N_ESTIMATORS,
)
from database import get_connection

logger = logging.getLogger(__name__)


class MacroAIEngine:
    def __init__(self) -> None:
        self.conn = get_connection()

        # Production Models
        self.hmm_model: Optional[hmm.GaussianHMM] = None
        self.hmm_scaler = StandardScaler()
        self.hmm_state_order: Optional[np.ndarray] = None  # canonical sort: 0=expansion, 2=recession
        self.rf_model: Optional[RandomForestClassifier] = None
        self.xgb_model: Optional[xgb.XGBRegressor] = None

    def close(self) -> None:
        self.conn.close()

    def _log_training_score(self, model_name: str, n_samples: int, cv_mean: float, cv_std: Optional[float], metric: str) -> None:
        try:
            self.conn.execute(
                """
                INSERT INTO model_training_log
                    (model_name, trained_at, n_samples, cv_score_mean, cv_score_std, score_metric)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (model_name, datetime.now(timezone.utc).isoformat(), n_samples, cv_mean, cv_std, metric),
            )
            self.conn.commit()
        except Exception:
            logger.exception(f"Failed to persist training score for {model_name}.")

    def _remap_hmm_states(self, raw_states: np.ndarray) -> np.ndarray:
        """Remap arbitrary HMM state indices to a canonical ordering stable across retrains.

        States are sorted ascending by mean us_high_yield_spread (feature index 2 in the
        scaled feature vector), so canonical index 0 always means lowest credit stress
        (expansion) and 2 always means highest stress (recession/crash).
        Falls back to identity if hmm_state_order is not yet set.
        """
        if self.hmm_state_order is None:
            return raw_states
        inv = np.empty(len(self.hmm_state_order), dtype=int)
        inv[self.hmm_state_order] = np.arange(len(self.hmm_state_order))
        return inv[raw_states]

    def _extract_numeric(self, val_str: Any) -> float:
        """Extract a float from formatted strings (e.g. '5.0%', '-1.2K', '1.2e3', '1,234.5')."""
        if pd.isna(val_str) or not str(val_str).strip():
            return np.nan
        try:
            # Strip thousands separators before matching so '1,234.5' → '1234.5'
            s = str(val_str).replace(',', '')
            # Match a valid float including scientific notation; re.search ignores surrounding
            # decoration ('%', '$', 'K', etc.) without stripping characters from the number itself
            m = re.search(r'-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?', s)
            return float(m.group()) if m else np.nan
        except Exception:
            return np.nan

    def train_regime_clustering(self) -> None:
        """HMM regime clustering on structural macro indicators (liquidity, labor, credit, yield curve)."""
        logger.info("Training Time-Series Regime Clustering model (HMM)...")
        try:
            query = """
                SELECT date, us_m2, us_jobless_claims, us_high_yield_spread, us_yield_curve 
                FROM macro_indicators 
                ORDER BY date ASC
            """
            df = pd.read_sql_query(query, self.conn)
            df = df.dropna()
            
            if len(df) < MACRO_HMM_MIN_TRAIN_ROWS:
                logger.warning("Insufficient structural data to train HMM Clustering. Need more weekly runs. Skipping.")
                return
            
            # Scale features for the Gaussian emission distributions
            X = self.hmm_scaler.fit_transform(df[['us_m2', 'us_jobless_claims', 'us_high_yield_spread', 'us_yield_curve']])

            split = max(1, int(len(X) * MACRO_HMM_TRAIN_FRAC))
            if split < len(X):
                eval_hmm = hmm.GaussianHMM(n_components=MACRO_HMM_N_STATES, covariance_type="full", n_iter=MACRO_HMM_N_ITER, random_state=42)
                eval_hmm.fit(X[:split])
                ll_per_sample = eval_hmm.score(X[split:]) / len(X[split:])
                logger.info(f"HMM held-out log-likelihood/sample: {ll_per_sample:.4f} (n_train={split}, n_test={len(X)-split}; scores are noisy below ~200 samples)")
                self._log_training_score('hmm_regime', len(X), ll_per_sample, None, 'log_likelihood_per_sample')

            self.hmm_model = hmm.GaussianHMM(
                n_components=MACRO_HMM_N_STATES,
                covariance_type="full",
                n_iter=MACRO_HMM_N_ITER,
                random_state=42
            )
            self.hmm_model.fit(X)

            # argsort on means_[:,2] (us_high_yield_spread) gives a stable canonical ordering: 0=expansion, 2=recession
            self.hmm_state_order = np.argsort(self.hmm_model.means_[:, 2])
            logger.info(f"HMM state canonical order (raw->canonical): {dict(enumerate(self.hmm_state_order))}")

            logger.info("Successfully trained Hidden Markov Model for Regime Clustering.")
        except Exception:
            logger.exception("Failed to train Regime Clustering (HMM).")

    def train_consensus_miss_probability(self) -> None:
        """Random Forest classifier predicting whether actual macro releases exceed Wall Street forecasts."""
        logger.info("Training Consensus Miss Probability model (Random Forest)...")
        try:
            query = "SELECT forecast_val, previous_val, actual_val FROM macro_calendar WHERE is_event_passed = 1"
            df = pd.read_sql_query(query, self.conn)
            
            df['forecast_num'] = df['forecast_val'].apply(self._extract_numeric)
            df['previous_num'] = df['previous_val'].apply(self._extract_numeric)
            df['actual_num'] = df['actual_val'].apply(self._extract_numeric)
            
            df = df.dropna(subset=['forecast_num', 'previous_num', 'actual_num'])
            
            if len(df) < MACRO_CAL_MIN_TRAIN_ROWS:
                logger.warning("Insufficient verified ground-truth data to train Consensus Miss model. Skipping.")
                return

            X = df[['forecast_num', 'previous_num']].values
            # Target classification: 1 if Actual > Forecast, else 0
            y = (df['actual_num'] > df['forecast_num']).astype(int).values
            
            # CV score before final fit (accuracy is robust to class imbalance in sparse folds)
            tscv = TimeSeriesSplit(n_splits=MACRO_CV_N_SPLITS)
            cv_scores = cross_val_score(
                RandomForestClassifier(n_estimators=MACRO_RF_N_ESTIMATORS, max_depth=MACRO_RF_MAX_DEPTH, random_state=42),
                X, y, cv=tscv, scoring='accuracy',
            )
            logger.info(f"RF Consensus Miss CV accuracy: {cv_scores.mean():.3f} ± {cv_scores.std():.3f} (n={len(X)}; scores are noisy below ~100 samples)")
            self._log_training_score('rf_consensus_miss', len(X), float(cv_scores.mean()), float(cv_scores.std()), 'accuracy')

            # Restrict depth to prevent overfitting on sparse early data
            self.rf_model = RandomForestClassifier(n_estimators=MACRO_RF_N_ESTIMATORS, max_depth=MACRO_RF_MAX_DEPTH, random_state=42)
            self.rf_model.fit(X, y)

            logger.info(f"Successfully trained Random Forest Consensus Miss model on {len(X)} historical events.")
        except Exception:
            logger.exception("Failed to train Consensus Miss Probability.")

    def train_volatility_magnitude(self) -> None:
        """Stacked XGBoost regressor predicting SPY gap magnitude from event data, VIX, HMM state, and RF probability."""
        logger.info("Training Volatility Magnitude model (XGBoost) with Model Stacking Architecture...")
        try:
            query = """
                SELECT c.event_id, c.event_date, c.forecast_val, c.previous_val, c.actual_val, c.post_event_spy_gap, r.vix_close 
                FROM macro_calendar c
                LEFT JOIN market_regimes r ON date(c.event_date) = r.date
                WHERE c.is_event_passed = 1 AND c.post_event_spy_gap IS NOT NULL AND c.post_event_spy_gap >= 0
            """
            df_cal = pd.read_sql_query(query, self.conn)
            
            if df_cal.empty:
                logger.warning("No verified calendar rows found for XGBoost training. Skipping.")
                return
                
            df_cal['forecast_num'] = df_cal['forecast_val'].apply(self._extract_numeric)
            df_cal['previous_num'] = df_cal['previous_val'].apply(self._extract_numeric)

            # Assert VIX join quality before filling nulls.
            # A 0% match rate means date(event_date) and market_regimes.date formats diverge —
            # the whole model would silently train on VIX=20.0 constant.
            vix_matched = int(df_cal['vix_close'].notna().sum())
            vix_total = len(df_cal)
            vix_match_rate = vix_matched / vix_total if vix_total else 0.0
            if vix_match_rate == 0.0:
                logger.error(
                    f"XGBoost training: 0% VIX join match ({vix_matched}/{vix_total} rows). "
                    "date(event_date) and market_regimes.date formats likely diverge. "
                    "Entire model will train on constant VIX=20.0 — investigate date storage format."
                )
            elif vix_match_rate < 0.5:
                logger.warning(
                    f"XGBoost training: low VIX join match rate {vix_match_rate:.1%} "
                    f"({vix_matched}/{vix_total} rows) — remaining rows fall back to VIX=20.0."
                )
            else:
                logger.info(f"XGBoost training: VIX join match rate {vix_match_rate:.1%} ({vix_matched}/{vix_total} rows).")

            df_cal['vix_close'] = df_cal['vix_close'].fillna(MACRO_VIX_DEFAULT)
            
            df_cal = df_cal.dropna(subset=['forecast_num', 'previous_num', 'post_event_spy_gap'])
            
            if len(df_cal) < MACRO_CAL_MIN_TRAIN_ROWS:
                logger.warning("Insufficient verified SPY gap data to train Volatility Magnitude model. Skipping.")
                return

            hmm_state_map = {}
            if self.hmm_model is not None:
                try:
                    q_ind = "SELECT date, us_m2, us_jobless_claims, us_high_yield_spread, us_yield_curve FROM macro_indicators ORDER BY date ASC"
                    df_ind = pd.read_sql_query(q_ind, self.conn)
                    df_ind_clean = df_ind.dropna()
                    if not df_ind_clean.empty:
                        X_ind = self.hmm_scaler.transform(df_ind_clean[['us_m2', 'us_jobless_claims', 'us_high_yield_spread', 'us_yield_curve']])
                        predicted_states = self._remap_hmm_states(self.hmm_model.predict(X_ind))
                        for d, s in zip(df_ind_clean['date'], predicted_states):
                            hmm_state_map[d] = int(s)
                except Exception as ex:
                    logger.error(f"Failed to compile historical HMM feature states for stacking: {ex}")

            # log1p-transform: post_event_spy_gap is right-skewed + non-negative; log-space symmetrises MSE; inverted with expm1 at inference
            y = np.log1p(df_cal['post_event_spy_gap'].values)

            # HMM states: vectorised date-map lookup, fallback 0 for unmatched dates
            evt_dates = pd.to_datetime(df_cal['event_date']).dt.strftime('%Y-%m-%d')
            hmm_states = np.array([hmm_state_map.get(d, 0) for d in evt_dates])

            # RF probabilities: one batched call instead of one call per row
            rf_probs = np.full(len(df_cal), 0.5)
            if self.rf_model is not None:
                try:
                    rf_probs = self.rf_model.predict_proba(
                        df_cal[['forecast_num', 'previous_num']].values
                    )[:, 1]
                except Exception as ex:
                    logger.warning(f"Batched RF predict_proba failed, using 0.5 fallback: {ex}")

            X = np.column_stack([
                df_cal['forecast_num'].values,
                df_cal['previous_num'].values,
                df_cal['vix_close'].values,
                hmm_states,
                rf_probs,
            ])

            tscv = TimeSeriesSplit(n_splits=MACRO_CV_N_SPLITS)
            cv_scores = cross_val_score(
                xgb.XGBRegressor(n_estimators=MACRO_XGB_N_ESTIMATORS, max_depth=MACRO_XGB_MAX_DEPTH, learning_rate=MACRO_XGB_LEARNING_RATE, objective='reg:squarederror', random_state=42),
                X, y, cv=tscv, scoring='neg_root_mean_squared_error',
            )
            rmse_scores = -cv_scores
            logger.info(f"XGBoost Volatility CV RMSE (log1p-space): {rmse_scores.mean():.4f} ± {rmse_scores.std():.4f} (n={len(X)}; scores are noisy below ~100 samples)")
            self._log_training_score('xgb_volatility', len(X), float(rmse_scores.mean()), float(rmse_scores.std()), 'rmse_log1p')

            self.xgb_model = xgb.XGBRegressor(
                n_estimators=MACRO_XGB_N_ESTIMATORS,
                max_depth=MACRO_XGB_MAX_DEPTH,
                learning_rate=MACRO_XGB_LEARNING_RATE,
                objective='reg:squarederror',
                random_state=42
            )
            self.xgb_model.fit(X, y)

            logger.info(f"Successfully trained Stacking XGBoost Volatility model on {len(X)} historical events.")
        except Exception:
            logger.exception("Failed to train Volatility Magnitude model.")

    def run_macro_inference(self, target_date: str) -> None:
        """Run live stacked inference for upcoming 48h macro events; writes ai_volatility_warning and ai_consensus_miss_prob."""
        logger.info(f"Running Stacked Macro AI Inference for 48H window starting: {target_date}")
        try:
            cursor = self.conn.cursor()
            
            cursor.execute("SELECT vix_close FROM market_regimes ORDER BY date DESC LIMIT 1")
            vix_row = cursor.fetchone()
            current_vix = float(vix_row['vix_close']) if vix_row and vix_row['vix_close'] else MACRO_VIX_DEFAULT

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
                        X_ind_live = self.hmm_scaler.transform([[
                            float(ind_row['us_m2']),
                            float(ind_row['us_jobless_claims']),
                            float(ind_row['us_high_yield_spread']),
                            float(ind_row['us_yield_curve'])
                        ]])
                        current_hmm_state = int(self._remap_hmm_states(self.hmm_model.predict(X_ind_live))[0])
                        
                        cursor.execute("""
                            UPDATE market_regimes 
                            SET ai_hmm_state = ? 
                            WHERE date = (SELECT MAX(date) FROM market_regimes)
                        """, (current_hmm_state,))
                except Exception:
                    logger.exception("Failed to calculate current live HMM hidden macro regime state.")

            # VIX and HMM state are market-wide; the same values apply to every event in the 48h window
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

                rf_consensus_miss_prob = 0.5
                if self.rf_model is not None:
                    try:
                        rf_consensus_miss_prob = float(self.rf_model.predict_proba([[f_val, p_val]])[0][1])
                    except Exception as ex:
                        logger.debug(f"Failed to infer consensus miss probability for event {event['event_id']}: {ex}")

                # clamp before expm1 (not after): correct inversion of log1p; log1p(0)=0, so clamp at 0
                X_infer = np.array([[f_val, p_val, current_vix, current_hmm_state, rf_consensus_miss_prob]])
                predicted_gap = float(np.expm1(max(0.0, float(self.xgb_model.predict(X_infer)[0]))))
                
                cursor.execute("""
                    UPDATE macro_calendar 
                    SET ai_volatility_warning = ?, 
                        ai_consensus_miss_prob = ? 
                    WHERE event_id = ?
                """, (predicted_gap, rf_consensus_miss_prob, event['event_id']))
                updates_count += 1
                
                if predicted_gap > MACRO_SEVERE_VOL_THRESHOLD:
                    logger.warning(f"🚨 SEVERE VOLATILITY PREDICTED: Event ID {event['event_id']} (Miss Prob: {rf_consensus_miss_prob:.2%}) — Absolute Volatility Shock Magnitude: ±{predicted_gap:.2f}% (direction unknown)")

            self.conn.commit()
            logger.info(f"Successfully processed {updates_count} events for Stacked AI Volatility and Surprise Warnings.")

        except Exception:
            self.conn.rollback()
            logger.exception("Failed to run stacked macro inference.")