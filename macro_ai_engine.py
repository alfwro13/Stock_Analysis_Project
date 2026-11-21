import os
import sqlite3
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

import pandas as pd
import numpy as np
import xgboost as xgb
from hmmlearn import hmm
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.preprocessing import StandardScaler

from config import BASE_DIR
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
from db.database import get_connection
from model_compatibility_engine import dump_sklearn_artifact, load_sklearn_artifact, remove_sklearn_artifact

logger = logging.getLogger(__name__)

MODELS_DIR = BASE_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
MACRO_HMM_PATH = MODELS_DIR / "macro_hmm.joblib"
MACRO_RF_FILE = "macro_rf.joblib"
MACRO_XGB_FILE = "macro_xgb.joblib"
MACRO_RF_PATH = MODELS_DIR / MACRO_RF_FILE
MACRO_XGB_PATH = MODELS_DIR / MACRO_XGB_FILE


def discard_persisted_models(models_dir: Path, files: Tuple[str, ...] = (MACRO_RF_FILE, MACRO_XGB_FILE)) -> None:
    """Delete the persisted consensus-miss and volatility models (or the given files) so no stale estimator keeps scoring events."""
    for name in files:
        remove_sklearn_artifact(models_dir / name)


class MacroAIEngine:
    def __init__(self) -> None:
        self.conn = get_connection()

        # Production Models
        self.hmm_model: Optional[hmm.GaussianHMM] = None
        self.hmm_scaler = StandardScaler()
        self.hmm_state_order: Optional[np.ndarray] = None  # canonical sort: 0=expansion, 2=recession
        self.rf_model: Optional[RandomForestClassifier] = None
        self.xgb_model: Optional[xgb.XGBRegressor] = None

        self._load_persisted_models()

    def close(self) -> None:
        self.conn.close()

    def _load_persisted_models(self) -> None:
        try:
            if MACRO_HMM_PATH.exists():
                bundle = load_sklearn_artifact(MACRO_HMM_PATH, "macro_model_training_job")
                self.hmm_model = bundle["model"]
                self.hmm_scaler = bundle["scaler"]
                self.hmm_state_order = bundle["state_order"]
        except Exception:
            logger.exception("Failed to load persisted HMM regime model from %s.", MACRO_HMM_PATH)

        try:
            if MACRO_RF_PATH.exists():
                self.rf_model = load_sklearn_artifact(MACRO_RF_PATH, "macro_model_training_job")
        except Exception:
            logger.exception("Failed to load persisted RF consensus-miss model from %s.", MACRO_RF_PATH)

        try:
            if MACRO_XGB_PATH.exists():
                self.xgb_model = load_sklearn_artifact(MACRO_XGB_PATH, "macro_model_training_job")
        except Exception:
            logger.exception("Failed to load persisted XGBoost volatility model from %s.", MACRO_XGB_PATH)

    @staticmethod
    def _expected_change(forecast: Any, previous: Any) -> np.ndarray:
        """Forecast minus previous as a share of the larger magnitude (0 when both are 0), so percent releases, payrolls in persons and policy rates share one scale."""
        forecast = np.asarray(forecast, dtype=float)
        previous = np.asarray(previous, dtype=float)
        scale = np.maximum(np.abs(forecast), np.abs(previous))
        return np.divide(forecast - previous, scale, out=np.zeros_like(scale), where=scale > 0)

    def _consensus_miss_probability(self, forecast: Any, previous: Any) -> np.ndarray:
        return self.rf_model.predict_proba(self._expected_change(forecast, previous).reshape(-1, 1))[:, 1]

    @staticmethod
    def _volatility_features(expected_change: Any, vix: Any, hmm_state: Any, rf_prob: Any) -> np.ndarray:
        return np.column_stack([expected_change, vix, hmm_state, rf_prob])

    def _discard_persisted_model(self, attribute: str, path: Path) -> None:
        setattr(self, attribute, None)
        remove_sklearn_artifact(path)

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
            logger.exception("Failed to persist training score for %s.", model_name)

    def _remap_hmm_states(self, raw_states: np.ndarray) -> np.ndarray:
        """Remap raw HMM indices to canonical order by ascending us_high_yield_spread mean (0=expansion, 2=recession/crash); identity fallback if hmm_state_order unset."""
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
            # Strip thousands separators first; re.search then ignores surrounding decoration ('%', '$', 'K') without mangling the number
            s = str(val_str).replace(',', '')
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
                logger.info("HMM held-out log-likelihood/sample: %.4f (n_train=%d, n_test=%d; scores are noisy below ~200 samples)", ll_per_sample, split, len(X) - split)
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
            logger.info("HMM state canonical order (raw->canonical): %s", dict(enumerate(self.hmm_state_order)))

            dump_sklearn_artifact({"model": self.hmm_model, "scaler": self.hmm_scaler, "state_order": self.hmm_state_order}, MACRO_HMM_PATH)

            logger.info("Successfully trained Hidden Markov Model for Regime Clustering.")
        except Exception:
            logger.exception("Failed to train Regime Clustering (HMM).")

    def train_consensus_miss_probability(self) -> None:
        """Random Forest classifier predicting whether actual macro releases exceed Wall Street forecasts."""
        logger.info("Training Consensus Miss Probability model (Random Forest)...")
        try:
            query = "SELECT forecast_val, previous_val, actual_val FROM macro_calendar WHERE is_event_passed = 1 ORDER BY event_date"
            df = pd.read_sql_query(query, self.conn)
            
            df['forecast_num'] = df['forecast_val'].apply(self._extract_numeric)
            df['previous_num'] = df['previous_val'].apply(self._extract_numeric)
            df['actual_num'] = df['actual_val'].apply(self._extract_numeric)
            
            df = df.dropna(subset=['forecast_num', 'previous_num', 'actual_num'])
            
            # Target classification: 1 if Actual > Forecast, else 0
            y = (df['actual_num'] > df['forecast_num']).astype(int).values

            if len(df) < MACRO_CAL_MIN_TRAIN_ROWS or len(set(y)) < 2:
                logger.warning("Insufficient verified ground-truth data to train Consensus Miss model (%d rows, %d outcome classes). Skipping and discarding the persisted model.", len(df), len(set(y)))
                self._discard_persisted_model("rf_model", MACRO_RF_PATH)
                return

            X = self._expected_change(df['forecast_num'], df['previous_num']).reshape(-1, 1)
            
            # CV score before final fit (accuracy is robust to class imbalance in sparse folds)
            tscv = TimeSeriesSplit(n_splits=MACRO_CV_N_SPLITS)
            cv_scores = cross_val_score(
                RandomForestClassifier(n_estimators=MACRO_RF_N_ESTIMATORS, max_depth=MACRO_RF_MAX_DEPTH, random_state=42),
                X, y, cv=tscv, scoring='accuracy',
            )
            logger.info("RF Consensus Miss CV accuracy: %.3f ± %.3f (n=%d; scores are noisy below ~100 samples)", cv_scores.mean(), cv_scores.std(), len(X))
            self._log_training_score('rf_consensus_miss', len(X), float(cv_scores.mean()), float(cv_scores.std()), 'accuracy')

            # Restrict depth to prevent overfitting on sparse early data
            self.rf_model = RandomForestClassifier(n_estimators=MACRO_RF_N_ESTIMATORS, max_depth=MACRO_RF_MAX_DEPTH, random_state=42)
            self.rf_model.fit(X, y)
            dump_sklearn_artifact(self.rf_model, MACRO_RF_PATH)

            logger.info("Successfully trained Random Forest Consensus Miss model on %d historical events.", len(X))
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
                ORDER BY c.event_date
            """
            df_cal = pd.read_sql_query(query, self.conn)
            
            if df_cal.empty:
                logger.warning("No verified calendar rows found for XGBoost training. Skipping and discarding the persisted model.")
                self._discard_persisted_model("xgb_model", MACRO_XGB_PATH)
                return
                
            df_cal['forecast_num'] = df_cal['forecast_val'].apply(self._extract_numeric)
            df_cal['previous_num'] = df_cal['previous_val'].apply(self._extract_numeric)

            # 0% VIX join match means date(event_date)/market_regimes.date formats diverge — model silently trains on VIX=20.0 constant.
            vix_matched = int(df_cal['vix_close'].notna().sum())
            vix_total = len(df_cal)
            vix_match_rate = vix_matched / vix_total if vix_total else 0.0
            if vix_match_rate == 0.0:
                logger.error(
                    "XGBoost training: 0%% VIX join match (%d/%d rows). "
                    "date(event_date) and market_regimes.date formats likely diverge. "
                    "Entire model will train on constant VIX=20.0 — investigate date storage format.",
                    vix_matched, vix_total,
                )
            elif vix_match_rate < 0.5:
                logger.warning(
                    "XGBoost training: low VIX join match rate %.1f%% (%d/%d rows) — remaining rows fall back to VIX=20.0.",
                    vix_match_rate * 100, vix_matched, vix_total,
                )
            else:
                logger.info("XGBoost training: VIX join match rate %.1f%% (%d/%d rows).", vix_match_rate * 100, vix_matched, vix_total)

            df_cal['vix_close'] = df_cal['vix_close'].fillna(MACRO_VIX_DEFAULT)
            
            df_cal = df_cal.dropna(subset=['forecast_num', 'previous_num', 'post_event_spy_gap'])
            
            if len(df_cal) < MACRO_CAL_MIN_TRAIN_ROWS:
                logger.warning("Insufficient verified SPY gap data to train Volatility Magnitude model. Skipping and discarding the persisted model.")
                self._discard_persisted_model("xgb_model", MACRO_XGB_PATH)
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
                    logger.error("Failed to compile historical HMM feature states for stacking: %s", ex)

            # log1p-transform: post_event_spy_gap is right-skewed + non-negative; log-space symmetrises MSE; inverted with expm1 at inference
            y = np.log1p(df_cal['post_event_spy_gap'].values)

            # HMM states: vectorised date-map lookup, fallback 0 for unmatched dates
            evt_dates = pd.to_datetime(df_cal['event_date']).dt.strftime('%Y-%m-%d')
            hmm_states = np.array([hmm_state_map.get(d, 0) for d in evt_dates])

            # RF probabilities: one batched call instead of one call per row
            rf_probs = np.full(len(df_cal), 0.5)
            if self.rf_model is not None:
                try:
                    rf_probs = self._consensus_miss_probability(df_cal['forecast_num'], df_cal['previous_num'])
                except Exception as ex:
                    logger.warning("Batched RF predict_proba failed, using 0.5 fallback: %s", ex)

            X = self._volatility_features(
                self._expected_change(df_cal['forecast_num'], df_cal['previous_num']),
                df_cal['vix_close'].values,
                hmm_states,
                rf_probs,
            )

            tscv = TimeSeriesSplit(n_splits=MACRO_CV_N_SPLITS)
            cv_scores = cross_val_score(
                xgb.XGBRegressor(n_estimators=MACRO_XGB_N_ESTIMATORS, max_depth=MACRO_XGB_MAX_DEPTH, learning_rate=MACRO_XGB_LEARNING_RATE, objective='reg:squarederror', random_state=42),
                X, y, cv=tscv, scoring='neg_root_mean_squared_error',
            )
            rmse_scores = -cv_scores
            logger.info("XGBoost Volatility CV RMSE (log1p-space): %.4f ± %.4f (n=%d; scores are noisy below ~100 samples)", rmse_scores.mean(), rmse_scores.std(), len(X))
            self._log_training_score('xgb_volatility', len(X), float(rmse_scores.mean()), float(rmse_scores.std()), 'rmse_log1p')

            self.xgb_model = xgb.XGBRegressor(
                n_estimators=MACRO_XGB_N_ESTIMATORS,
                max_depth=MACRO_XGB_MAX_DEPTH,
                learning_rate=MACRO_XGB_LEARNING_RATE,
                objective='reg:squarederror',
                random_state=42
            )
            self.xgb_model.fit(X, y)
            dump_sklearn_artifact(self.xgb_model, MACRO_XGB_PATH)

            logger.info("Successfully trained Stacking XGBoost Volatility model on %d historical events.", len(X))
        except Exception:
            logger.exception("Failed to train Volatility Magnitude model.")

    def run_macro_inference(self, target_date: str) -> None:
        """Run live stacked inference for upcoming 48h macro events; writes ai_volatility_warning and ai_consensus_miss_prob."""
        logger.info("Running Stacked Macro AI Inference for 48H window starting: %s", target_date)
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

            if self.rf_model is None:
                logger.warning("Consensus Miss model is untrained. Bypassing stacked inference.")
                return

            updates_count = 0
            for event in events:
                f_val = self._extract_numeric(event['forecast_val'])
                p_val = self._extract_numeric(event['previous_val'])

                if pd.isna(f_val) or pd.isna(p_val):
                    continue

                rf_consensus_miss_prob = float(self._consensus_miss_probability([f_val], [p_val])[0])

                # clamp before expm1 (not after): correct inversion of log1p; log1p(0)=0, so clamp at 0
                X_infer = self._volatility_features(self._expected_change([f_val], [p_val]), [current_vix], [current_hmm_state], [rf_consensus_miss_prob])
                predicted_gap = float(np.expm1(max(0.0, float(self.xgb_model.predict(X_infer)[0]))))
                
                cursor.execute("""
                    UPDATE macro_calendar 
                    SET ai_volatility_warning = ?, 
                        ai_consensus_miss_prob = ? 
                    WHERE event_id = ?
                """, (predicted_gap, rf_consensus_miss_prob, event['event_id']))
                updates_count += 1
                
                if predicted_gap > MACRO_SEVERE_VOL_THRESHOLD:
                    logger.warning("🚨 SEVERE VOLATILITY PREDICTED: Event ID %s (Miss Prob: %.2f%%) — Absolute Volatility Shock Magnitude: ±%.2f%% (direction unknown)", event['event_id'], rf_consensus_miss_prob * 100, predicted_gap)

            self.conn.commit()
            logger.info("Successfully processed %d events for Stacked AI Volatility and Surprise Warnings.", updates_count)

        except Exception:
            self.conn.rollback()
            logger.exception("Failed to run stacked macro inference.")