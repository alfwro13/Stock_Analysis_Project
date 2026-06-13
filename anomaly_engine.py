# anomaly_engine.py
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import ta
from sklearn.ensemble import IsolationForest

from config import ANOMALY_MODELS_DIR
from database import get_connection
from utils import clamp_beta

logger = logging.getLogger(__name__)

# GUI name: "Isolation Forest Anomaly Detection". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

_N_ESTIMATORS = 100
_CONTAMINATION = 0.05
_MIN_ROWS = 50
_RSI_WINDOW = 14
_SMA_WINDOW = 50
_VOL_WINDOW = 20
_TRADING_DAYS = 252

_FEATURE_COLS = ['volume_ratio', 'rsi_14', 'daily_return_pct',
                 'sma50_dist_pct', 'hist_vol_20', 'beta']
_REQUIRED_PAYLOAD_KEYS = {'model', 'score_min', 'score_max'}


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse a MultiIndex column header to the first level, return df unchanged otherwise."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


class AnomalyEngine:
    """
    Per-ticker Isolation Forest anomaly detection.

    Training: call train_all() nightly after market close. Each ticker gets its own
    IsolationForest fit on ~1 year of daily OHLCV history. The fitted model plus the
    training-set score range are persisted to ANOMALY_MODELS_DIR/{ticker}.joblib.

    Scoring: call score() during the intraday scan. Loads the cached model, builds a
    1-row feature vector from live data, and returns a dict with the anomaly score
    (float in [0, 1]) and the labeled feature values used. Returns None when no
    trained model is available.
    """

    def __init__(self) -> None:
        self.models_dir = ANOMALY_MODELS_DIR
        self._model_cache: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_features(self, df: pd.DataFrame, beta: float) -> pd.DataFrame:
        """Compute the 6 Isolation Forest feature columns from an OHLCV DataFrame."""
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        vol_ma = df['Volume'].rolling(_VOL_WINDOW).mean()
        df['volume_ratio'] = df['Volume'] / vol_ma.replace(0, np.nan)
        df['rsi_14'] = ta.momentum.RSIIndicator(close=df['Close'], window=_RSI_WINDOW).rsi()
        df['daily_return_pct'] = df['Close'].pct_change() * 100
        sma = ta.trend.SMAIndicator(close=df['Close'], window=_SMA_WINDOW).sma_indicator()
        df['sma50_dist_pct'] = ((df['Close'] - sma) / sma.replace(0, np.nan)) * 100
        log_ret = np.log(df['Close'] / df['Close'].shift(1))
        df['hist_vol_20'] = log_ret.rolling(_VOL_WINDOW).std() * np.sqrt(_TRADING_DAYS)
        df['beta'] = beta
        return df[_FEATURE_COLS]

    @staticmethod
    def _validate_payload(payload: dict, ticker: str) -> None:
        missing = _REQUIRED_PAYLOAD_KEYS - set(payload)
        if missing:
            raise ValueError(f"Anomaly payload for {ticker} is missing keys: {missing}")

    def _load_model(self, ticker: str, model_path: Path) -> dict:
        """Return a validated payload dict from cache or disk."""
        if ticker not in self._model_cache:
            payload = joblib.load(model_path)
            self._validate_payload(payload, ticker)
            self._model_cache[ticker] = payload
        return self._model_cache[ticker]

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
                df = _flatten_columns(pd.read_parquet(path))
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
        df = _flatten_columns(df_hist.copy())

        required = {'Open', 'High', 'Low', 'Close', 'Volume'}
        if not required.issubset(df.columns):
            logger.warning("Skipping %s: missing required OHLCV columns.", ticker)
            return

        feature_df = self._build_features(df, clamp_beta(beta) if beta is not None else 1.0).dropna()

        if len(feature_df) < _MIN_ROWS:
            logger.info(
                "Skipping %s: only %d clean rows after NaN-drop (need %d).",
                ticker, len(feature_df), _MIN_ROWS,
            )
            return

        X = feature_df.values
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
        payload = {
            'model': model,
            'score_min': score_min,
            'score_max': score_max,
            'trained_at': datetime.now(timezone.utc).isoformat(),
        }
        joblib.dump(payload, out_path)
        self._model_cache[ticker] = payload  # keep cache current
        logger.debug("Saved anomaly model for %s (%d rows) → %s", ticker, len(feature_df), out_path)

    # ------------------------------------------------------------------
    # Historical backfill
    # ------------------------------------------------------------------

    def backfill_all(self, tickers: list[str], parquet_dir: Path) -> None:
        """
        Score every existing quant_signals row for tickers that have a trained model
        and write the result back to anomaly_score. Designed to run immediately after
        train_all so the stock detail chart has data without waiting for a live scan.
        """
        if not tickers:
            return
        conn = get_connection()
        try:
            placeholders = ','.join('?' for _ in tickers)
            beta_rows = conn.execute(
                f"SELECT ticker, beta FROM stock_signals WHERE ticker IN ({placeholders})",
                tickers,
            ).fetchall()
            beta_map = {r['ticker']: clamp_beta(r['beta']) for r in beta_rows}

            scored, skipped = 0, 0
            for ticker in tickers:
                model_path = self.models_dir / f"{ticker}.joblib"
                if not model_path.exists():
                    skipped += 1
                    continue
                parquet_path = parquet_dir / f"{ticker}.parquet"
                if not parquet_path.exists():
                    skipped += 1
                    continue
                try:
                    df = _flatten_columns(pd.read_parquet(parquet_path))
                    beta = beta_map.get(ticker, 1.0)
                    rows_written = self._backfill_ticker(ticker, df, beta, conn, model_path)
                    scored += rows_written
                except Exception:
                    logger.error("Backfill failed for %s", ticker, exc_info=True)
                    skipped += 1

            conn.commit()
            logger.info(
                "Anomaly backfill complete: %d scores written, %d tickers skipped.",
                scored, skipped,
            )
        finally:
            conn.close()

    def _backfill_ticker(
        self, ticker: str, df_hist: pd.DataFrame, beta: float, conn, model_path: Path
    ) -> int:
        """
        Compute feature vectors for all rows in df_hist, score them, and bulk-UPDATE
        quant_signals via executemany. Returns the number of rows updated.
        """
        df = _flatten_columns(df_hist.copy())

        required = {'Open', 'High', 'Low', 'Close', 'Volume'}
        if not required.issubset(df.columns):
            return 0

        feature_df = self._build_features(df, beta).dropna()
        if feature_df.empty:
            return 0

        payload = self._load_model(ticker, model_path)
        score_min: float = payload['score_min']
        score_max: float = payload['score_max']
        if score_max == score_min:
            return 0

        X = feature_df.values
        raw_scores = payload['model'].decision_function(X)
        anomaly_scores = np.clip(
            1.0 - (raw_scores - score_min) / (score_max - score_min), 0.0, 1.0
        )

        rows = [
            (float(s), ticker, pd.Timestamp(idx).strftime('%Y-%m-%d'))
            for idx, s in zip(feature_df.index, anomaly_scores)
        ]
        cursor = conn.executemany(
            "UPDATE quant_signals SET anomaly_score = ? WHERE ticker = ? AND date = ?",
            rows,
        )
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score(self, ticker: str, feature_vector: list[float]) -> dict | None:
        """
        Score a live feature vector against the pre-trained model.

        Returns {'score': float, 'features': dict[str, float]} where score is in [0.0, 1.0]
        (1.0 = maximally anomalous), or None if no trained model exists for this ticker.

        feature_vector order must match train_one():
            [volume_ratio, rsi_14, daily_return_pct, sma50_dist_pct, hist_vol_20, beta]
        """
        model_path = self.models_dir / f"{ticker}.joblib"
        if not model_path.exists():
            logger.debug("No anomaly model for %s — skipping score.", ticker)
            return None

        try:
            payload = self._load_model(ticker, model_path)
            score_min: float = payload['score_min']
            score_max: float = payload['score_max']

            if score_max == score_min:
                logger.warning("Degenerate score range in model for %s — returning None.", ticker)
                return None

            X = np.array(feature_vector, dtype=float).reshape(1, -1)
            raw = float(payload['model'].decision_function(X)[0])
            anomaly_score = max(0.0, min(1.0, 1.0 - (raw - score_min) / (score_max - score_min)))

            return {
                'score': anomaly_score,
                'features': dict(zip(_FEATURE_COLS, feature_vector)),
            }

        except Exception:
            logger.error("Anomaly scoring failed for %s", ticker, exc_info=True)
            return None
