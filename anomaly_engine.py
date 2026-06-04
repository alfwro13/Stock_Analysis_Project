# anomaly_engine.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import ta
from sklearn.ensemble import IsolationForest

from config import ANOMALY_MODELS_DIR
from utils import clamp_beta

logger = logging.getLogger(__name__)

_N_ESTIMATORS = 100
_CONTAMINATION = 0.05
_MIN_ROWS = 50


class AnomalyEngine:
    """
    Per-ticker Isolation Forest anomaly detection.

    Training: call train_all() nightly after market close. Each ticker gets its own
    IsolationForest fit on ~1 year of daily OHLCV history. The fitted model plus the
    training-set score range are persisted to ANOMALY_MODELS_DIR/{ticker}.joblib.

    Scoring: call score() during the intraday scan. Loads the cached model, builds a
    1-row feature vector from live data, and returns a float in [0, 1] where 0 = normal
    and 1 = maximally anomalous. Returns None when no trained model is available.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.models_dir = ANOMALY_MODELS_DIR

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train_all(self, tickers: list[str], parquet_dir: Path) -> None:
        """Train one IsolationForest per ticker and persist to disk."""
        trained, skipped = 0, 0
        for ticker in tickers:
            path = parquet_dir / f"{ticker}.parquet"
            if not path.exists():
                logger.warning("Skipping anomaly training for %s: no Parquet at %s", ticker, path)
                skipped += 1
                continue
            try:
                df = pd.read_parquet(path)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                self.train_one(ticker, df)
                trained += 1
            except Exception:
                logger.error("Anomaly training failed for %s", ticker, exc_info=True)
                skipped += 1
        logger.info("Anomaly training complete: %d trained, %d skipped.", trained, skipped)

    def train_one(self, ticker: str, df_hist: pd.DataFrame, beta: float | None = None) -> None:
        """
        Compute a 6-feature matrix from df_hist, fit IsolationForest, and save the
        model plus the training-score min/max needed for normalisation to disk.
        """
        df = df_hist.copy()

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        required = {'Open', 'High', 'Low', 'Close', 'Volume'}
        if not required.issubset(df.columns):
            logger.warning("Skipping %s: missing required OHLCV columns.", ticker)
            return

        df = df[list(required)].copy()

        # Feature 1: volume_ratio — current day vol vs 20-day rolling mean
        df['vol_ma20'] = df['Volume'].rolling(20).mean()
        df['volume_ratio'] = df['Volume'] / df['vol_ma20'].replace(0, np.nan)

        # Feature 2: rsi_14 (Wilder smoothing via ta, matches indicators.py convention)
        df['rsi_14'] = ta.momentum.RSIIndicator(close=df['Close'], window=14).rsi()

        # Feature 3: daily_return_pct
        df['daily_return_pct'] = df['Close'].pct_change() * 100

        # Feature 4: sma50_dist_pct
        sma50 = ta.trend.SMAIndicator(close=df['Close'], window=50).sma_indicator()
        df['sma50_dist_pct'] = ((df['Close'] - sma50) / sma50.replace(0, np.nan)) * 100

        # Feature 5: hist_vol_20 — annualised log-return std, matches ai_prediction_engine.py
        log_ret = np.log(df['Close'] / df['Close'].shift(1))
        df['hist_vol_20'] = log_ret.rolling(20).std() * np.sqrt(252)

        # Feature 6: beta (scalar broadcast)
        if beta is None:
            beta = 1.0
        df['beta'] = clamp_beta(beta)

        feature_cols = ['volume_ratio', 'rsi_14', 'daily_return_pct',
                        'sma50_dist_pct', 'hist_vol_20', 'beta']
        df = df[feature_cols].dropna()

        if len(df) < _MIN_ROWS:
            logger.info("Skipping %s: only %d clean rows after NaN-drop (need %d).", ticker, len(df), _MIN_ROWS)
            return

        X = df.values
        model = IsolationForest(
            n_estimators=_N_ESTIMATORS,
            contamination=_CONTAMINATION,
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X)

        raw_scores = model.decision_function(X)
        score_min = float(raw_scores.min())
        score_max = float(raw_scores.max())

        if score_max == score_min:
            logger.warning("Degenerate score range for %s — skipping save.", ticker)
            return

        out_path = self.models_dir / f"{ticker}.joblib"
        joblib.dump({'model': model, 'score_min': score_min, 'score_max': score_max}, out_path)
        logger.debug("Saved anomaly model for %s (%d rows) → %s", ticker, len(df), out_path)

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score(self, ticker: str, feature_vector: list[float]) -> float | None:
        """
        Score a live feature vector against the pre-trained model.

        Returns a float in [0.0, 1.0] where 1.0 = maximally anomalous, or None if
        no trained model exists for this ticker.

        feature_vector order must match train_one():
            [volume_ratio, rsi_14, daily_return_pct, sma50_dist_pct, hist_vol_20, beta]
        """
        model_path = self.models_dir / f"{ticker}.joblib"
        if not model_path.exists():
            logger.debug("No anomaly model for %s — skipping score.", ticker)
            return None

        try:
            payload = joblib.load(model_path)
            model: IsolationForest = payload['model']
            score_min: float = payload['score_min']
            score_max: float = payload['score_max']

            if score_max == score_min:
                return None

            X = np.array(feature_vector, dtype=float).reshape(1, -1)
            raw = float(model.decision_function(X)[0])

            # Flip: anomalies (negative raw) become high anomaly_score
            anomaly_score = 1.0 - (raw - score_min) / (score_max - score_min)
            return max(0.0, min(1.0, anomaly_score))

        except Exception:
            logger.error("Anomaly scoring failed for %s", ticker, exc_info=True)
            return None
