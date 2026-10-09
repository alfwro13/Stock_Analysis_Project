import logging
from typing import Any, Dict

import joblib
import numpy as np
import pandas as pd
import psutil
from xgboost import XGBClassifier, XGBRegressor
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.metrics import average_precision_score
from sklearn.base import clone
from sklearn.frozen import FrozenEstimator
from sklearn.model_selection import TimeSeriesSplit, RandomizedSearchCV
from sklearn.calibration import CalibratedClassifierCV

from yahoo_engine import yahoo_engine
from model_compatibility_engine import dump_sklearn_artifact
from database import get_connection, log_notification
from constants import PREDICTION_HORIZON_DAYS, PREDICTION_RETURN_THRESHOLD
from ml_features import (
    MODEL_PATH, FEATURE_STATS_PATH, QUANTILE_Q10_PATH, QUANTILE_Q90_PATH, FEATURE_COLS,
    CONTINUOUS_FEATURES, FUNDAMENTAL_FEATURES, TRAINING_HISTORY_QUERY, build_model_features,
)

logger = logging.getLogger(__name__)

# GUI name: "Global Model Training (Walk-Forward)". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.


def _load_training_history() -> pd.DataFrame:
    """Bound SQLite row-object allocations without sampling or changing the training population."""
    conn = None
    try:
        conn = get_connection()
        chunks = list(pd.read_sql_query(TRAINING_HISTORY_QUERY, conn, chunksize=10_000))
        df = pd.concat(chunks, ignore_index=True)
        del chunks
    finally:
        if conn:
            conn.close()
    logger.info(
        "Training history loaded: %d rows, %.1f MiB table, %.1f MiB process RSS, %.1f MiB available",
        len(df), df.memory_usage(deep=True).sum() / 1024**2,
        psutil.Process().memory_info().rss / 1024**2,
        psutil.virtual_memory().available / 1024**2,
    )
    return df


def train_global_ml_model() -> None:
    """
    Builds an 18-feature ensemble model predicting returns exceeding
    PREDICTION_RETURN_THRESHOLD over PREDICTION_HORIZON_DAYS trading days,
    using Anchored Walk-Forward Validation with Temporal Embargos.

    FEATURE SET (18 features — see FEATURE REGISTRY block for history):
        Technical:         rsi_14, macd_pct, macd_signal_pct, macd_hist_pct,
                           volume_surge, bullish_cross, dist_sma_50, dist_sma_200,
                           sector_code, dollar_vol_log
        Momentum:          mom_1m, mom_3m, mom_6m, mom_12m_skip1m
        Volatility:        atr_pct, hist_vol_20
        Relative Strength: rel_strength_5d, rel_strength_20d

    Fundamental columns are still joined and preprocessed for feature drift
    diagnostics but are excluded from FEATURE_COLS and do not influence
    predictions. See the A/B test note in the FEATURE REGISTRY block of ml_features.py.
    """
    logger.info("Initiating Global ML Model Training pipeline with Hyperparameter Optimization...")

    yahoo_engine.prune_expired()
    _mem = psutil.virtual_memory()
    _avail_gb = _mem.available / (1024 ** 3)
    _ABORT_GB = 0.75
    _THROTTLE_GB = 1.5
    if _avail_gb < _ABORT_GB:
        msg = (
            f"ML Training aborted: only {_avail_gb:.1f} GB RAM available "
            f"(minimum {_ABORT_GB} GB required). Free memory and retry."
        )
        logger.error(msg)
        log_notification("Error", msg)
        return
    _n_iter = 4 if _avail_gb < _THROTTLE_GB else 10
    if _avail_gb < _THROTTLE_GB:
        logger.warning(
            'Low memory (%s GB available) — throttling search to %s iterations.', format(_avail_gb, '.1f'), _n_iter
        )

    log_notification("Info", "Global ML Model Training pipeline initiated.")

    try:
        df = _load_training_history()

        if df.empty:
            logger.warning("No data found in DB. Aborting ML training.")
            return

        logger.info('Extracting features from %s historical records...', len(df))

        df = build_model_features(df)

        # Log null counts after imputation for diagnostics
        for col in FUNDAMENTAL_FEATURES:
            remaining_nulls = df[col].isna().sum()
            if remaining_nulls > 0:
                logger.warning(
                    '  %s: %s NULLs remain after imputation (dates with zero equity coverage — will be dropped by dropna).',
                    col,
                    remaining_nulls,
                )

        # ── Save training population statistics ───────────────────────────────
        # Consumed by the inference coverage guard (Change A) and the
        # train-vs-serve drift diagnostic (Change B) in update_daily_ml_predictions.
        logger.info("Computing and saving training population statistics...")
        train_universe_size = int(df.groupby('date')['ticker'].size().median())
        feature_stats: Dict[str, Any] = {
            '_meta': {'train_universe_size': train_universe_size},
            'features': {},
        }
        for col in CONTINUOUS_FEATURES:
            col_data = df[col].dropna()
            feature_stats['features'][col] = {
                'mean': float(col_data.mean()),
                'std':  float(col_data.std())
            }
            logger.info(
                "  Feature '%s': mean=%s, std=%s  (n=%s)",
                col,
                format(feature_stats['features'][col]['mean'], '.4f'),
                format(feature_stats['features'][col]['std'], '.4f'),
                format(len(col_data), ','),
            )
        logger.info('  Training universe size (median tickers/date): %s', format(train_universe_size, ','))
        joblib.dump(feature_stats, FEATURE_STATS_PATH)
        logger.info('✅ Feature statistics saved to %s', FEATURE_STATS_PATH)

        df.dropna(subset=FEATURE_COLS, inplace=True)

        # ── Target Construction ───────────────────────────────────────────────
        # AFTER — 10-day horizon
        # Signal-to-noise improves at 10 days vs 5 days: technical momentum
        # has more time to play out before mean-reversion noise dominates.
        # Entry proxy remains close[T+1]. Exit moves to close[T+10].
        df['next_close']   = df.groupby('ticker')['close_price'].shift(-1)
        df['future_close'] = df.groupby('ticker')['close_price'].shift(-PREDICTION_HORIZON_DAYS)
        df.dropna(subset=['next_close', 'future_close'], inplace=True)

        df['target'] = (
            (df['future_close'] - df['next_close']) / df['next_close'] > PREDICTION_RETURN_THRESHOLD
        ).astype(int)

        if len(df) < 1000:
            logger.warning('Insufficient training samples (%s).', len(df))
            return

        pos = (df['target'] == 1).sum()
        neg = (df['target'] == 0).sum()
        logger.info(
            'Class distribution — Positive (1): %s (%s) | Negative (0): %s (%s)',
            format(pos, ','),
            format(pos / len(df), '.1%'),
            format(neg, ','),
            format(neg / len(df), '.1%'),
        )

        df.sort_values('date', inplace=True)
        df.reset_index(drop=True, inplace=True)

        df = df[FEATURE_COLS + ['date', 'target']]
        X_full = df[FEATURE_COLS]
        y_full = df['target']

        # ── Three-Way Temporal Split ──────────────────────────────────────────
        # Fixes BUG-04: calibration data leakage.
        #
        # The old approach used the same cv_splits for both hyperparameter
        # search and calibration. The hyperparameters were selected to maximise
        # PR-AUC on those exact folds, so the calibration was fitting isotonic
        # regression on optimistically biased out-of-fold predictions.
        #
        # The correct approach uses three non-overlapping temporal regions:
        #   Train  (60%): hyperparameter search via walk-forward CV
        #   Calib  (20%): isotonic calibration — never seen during tuning
        #   Test   (20%): true OOS evaluation — never touched during training
        #
        # The production model is then refitted on Train + Calib (80%) with
        # the best hyperparameters found on Train only, and calibrated again
        # on Calib only. The Test set is used solely for honest reporting.
        unique_dates = np.sort(df['date'].unique())
        date_series  = df['date'].reset_index(drop=True)
        n_dates      = len(unique_dates)

        train_end = int(n_dates * 0.60)
        calib_end = int(n_dates * 0.80)

        # Purge the last PREDICTION_HORIZON_DAYS dates from each partition so
        # their forward labels cannot bleed into the next region — same embargo
        # concept applied to the outer split that already exists inside CV folds.
        train_dates = set(unique_dates[:train_end - PREDICTION_HORIZON_DAYS])
        calib_dates = set(unique_dates[train_end:calib_end - PREDICTION_HORIZON_DAYS])
        test_dates  = set(unique_dates[calib_end:])

        train_idx = date_series.index[date_series.isin(train_dates)].tolist()
        calib_idx = date_series.index[date_series.isin(calib_dates)].tolist()
        test_idx  = date_series.index[date_series.isin(test_dates)].tolist()

        X_train = X_full.iloc[train_idx]
        y_train = y_full.iloc[train_idx]
        X_calib = X_full.iloc[calib_idx]
        y_calib = y_full.iloc[calib_idx]
        X_test  = X_full.iloc[test_idx]
        y_test  = y_full.iloc[test_idx]

        logger.info(
            'Temporal split — Train: %s rows (%s → %s)  |  Calib: %s rows (%s → %s)  |  Test: %s rows (%s → %s)',
            format(len(X_train), ','),
            unique_dates[0],
            unique_dates[train_end - PREDICTION_HORIZON_DAYS - 1],
            format(len(X_calib), ','),
            unique_dates[train_end],
            unique_dates[calib_end - PREDICTION_HORIZON_DAYS - 1],
            format(len(X_test), ','),
            unique_dates[calib_end],
            unique_dates[-1],
        )

        # scale_pos_weight derived from train set only to prevent test leakage
        neg_count_train        = (y_train == 0).sum()
        pos_count_train        = (y_train == 1).sum()
        scale_pos_weight_train = (
            neg_count_train / pos_count_train if pos_count_train > 0 else 1.0
        )
        # ── Walk-Forward CV Splits (Train region only) ────────────────────────
        logger.info("Constructing Strict 5-Fold Walk-Forward Splits on training region...")

        train_date_series = date_series.iloc[train_idx].reset_index(drop=True)
        train_unique_dates = np.sort(train_date_series.unique())
        del df, X_full, y_full, col_data

        cv_splits_train = []
        for tr_date_idx, te_date_idx in TimeSeriesSplit(n_splits=5).split(train_unique_dates):
            if len(tr_date_idx) > PREDICTION_HORIZON_DAYS:
                tr_dates = set(train_unique_dates[tr_date_idx[:-PREDICTION_HORIZON_DAYS]])
                te_dates = set(train_unique_dates[te_date_idx])
                tr_idx   = train_date_series.index[train_date_series.isin(tr_dates)].tolist()
                te_idx   = train_date_series.index[train_date_series.isin(te_dates)].tolist()
                if tr_idx and te_idx:
                    cv_splits_train.append((tr_idx, te_idx))

        # ── Hyperparameter Search (Train region only) ─────────────────────────
        rf_base = RandomForestClassifier(
            class_weight='balanced', random_state=42, n_jobs=1
        )
        xgb_base = XGBClassifier(
            scale_pos_weight=scale_pos_weight_train,
            random_state=42, n_jobs=1, eval_metric='logloss'
        )

        rf_param_dist = {
            'n_estimators':     [100, 150, 200, 250],
            'max_depth':        [4, 6, 8, 10],
            'min_samples_leaf': [1, 5, 10]
        }
        xgb_param_dist = {
            'n_estimators':    [100, 150, 200],
            'max_depth':       [3, 5, 7],
            'learning_rate':   [0.01, 0.05, 0.1],
            'subsample':       [0.7, 0.9, 1.0],
            'colsample_bytree': [0.7, 0.9, 1.0]
        }

        logger.info("Executing Randomized Search on training region only...")

        rf_search = RandomizedSearchCV(
            estimator=rf_base, param_distributions=rf_param_dist,
            n_iter=_n_iter, cv=cv_splits_train, scoring='average_precision',
            random_state=42, n_jobs=1
        )
        xgb_search = RandomizedSearchCV(
            estimator=xgb_base, param_distributions=xgb_param_dist,
            n_iter=_n_iter, cv=cv_splits_train, scoring='average_precision',
            random_state=42, n_jobs=1
        )

        rf_search.fit(X_train, y_train)
        xgb_search.fit(X_train, y_train)

        best_rf  = rf_search.best_estimator_
        best_xgb = xgb_search.best_estimator_

        logger.info('Optimal RF Params Found:  %s', rf_search.best_params_)
        logger.info('Optimal XGB Params Found: %s', xgb_search.best_params_)

        # CV score on train region only — informational, not the headline metric
        train_cv_pr_auc = (rf_search.best_score_ + xgb_search.best_score_) / 2.0
        logger.info('Train-region CV Avg-Precision: %s', format(train_cv_pr_auc, '.4f'))

        # ── Calibration on held-out Calib region ─────────────────────────────
        # cv='prefit' tells sklearn the estimator is already fitted.
        # Isotonic regression is fitted on Calib predictions only —
        # these rows were never seen during hyperparameter search.
        logger.info("Calibrating on held-out calibration region (never seen during tuning)...")

        calibrated_rf  = CalibratedClassifierCV(
            estimator=FrozenEstimator(best_rf),  method='isotonic'
        )
        calibrated_xgb = CalibratedClassifierCV(
            estimator=FrozenEstimator(best_xgb), method='isotonic'
        )
        calibrated_rf.fit(X_calib,  y_calib)
        calibrated_xgb.fit(X_calib, y_calib)

        # ── True OOS Evaluation on Test region ───────────────────────────────
        # Test region was never touched during training or calibration.
        # This is the only honest PR-AUC number.
        ensemble_probs_test = (
            calibrated_rf.predict_proba(X_test)[:, 1] +
            calibrated_xgb.predict_proba(X_test)[:, 1]
        ) / 2.0
        true_oos_pr_auc  = average_precision_score(y_test, ensemble_probs_test)
        true_oos_baseline = float(y_test.mean())
        logger.info(
            '✅ True OOS PR-AUC (clean holdout): %s  (random baseline = %s)',
            format(true_oos_pr_auc, '.4f'),
            format(true_oos_baseline, '.4f'),
        )

        # ── Production Model: refit on Train + Calib (80% of data) ───────────
        # Best hyperparameters are fixed. We now refit on more data (Train +
        # Calib) to give the production model as much signal as possible,
        # then recalibrate on Calib again (cv='prefit').
        logger.info("Refitting production model on Train + Calib region (80% of data)...")

        X_prod = pd.concat([X_train, X_calib])
        y_prod = pd.concat([y_train, y_calib])

        final_rf = clone(best_rf)
        final_xgb = clone(best_xgb)
        del rf_search, xgb_search, best_rf, best_xgb, calibrated_rf, calibrated_xgb
        del X_train, y_train, X_test, y_test, cv_splits_train
        final_rf.fit(X_prod, y_prod)
        final_xgb.fit(X_prod, y_prod)

        final_calibrated_rf  = CalibratedClassifierCV(
            estimator=FrozenEstimator(final_rf),  method='isotonic'
        )
        final_calibrated_xgb = CalibratedClassifierCV(
            estimator=FrozenEstimator(final_xgb), method='isotonic'
        )
        final_calibrated_rf.fit(X_calib,  y_calib)
        final_calibrated_xgb.fit(X_calib, y_calib)

        production_ensemble = VotingClassifier(
            estimators=[('rf', final_calibrated_rf), ('xgb', final_calibrated_xgb)],
            voting='soft'
        )
        # Estimators are already fitted — manually mark VotingClassifier as fitted
        production_ensemble.estimators_ = [final_calibrated_rf, final_calibrated_xgb]
        production_ensemble.classes_    = np.array([0, 1])
        # n_features_in_ is auto-derived from estimators_ in sklearn 1.6+

        dump_sklearn_artifact(production_ensemble, MODEL_PATH)
        logger.info('✅ Production ML Ensemble saved to %s', MODEL_PATH)
        log_notification(
            "Success",
            f"ML Model trained — True OOS PR-AUC: {true_oos_pr_auc:.2%}  "
            f"(baseline: {true_oos_baseline:.2%})."
        )

    except Exception as e:
        logger.error('Fatal error during ML training: %s', e)
        log_notification("Error", f"ML Model Training failed: {str(e)}")


def train_quantile_models() -> None:
    """
    Trains two XGBoost quantile regressors on the same 18-feature pipeline
    as the binary classifier, with a continuous 10-day return target.

    Q10 (quantile_alpha=0.10): pessimistic floor — 10th-percentile return.
    Q90 (quantile_alpha=0.90): optimistic ceiling — 90th-percentile return.

    Inference converts returns to prices:
        price_qN = close_price * (1 + qN_predicted_return)

    Models are saved to models/quantile_q10.joblib and quantile_q90.joblib.
    """
    logger.info("Initiating Quantile Regression model training (Q10 / Q90)...")

    yahoo_engine.prune_expired()
    _mem = psutil.virtual_memory()
    _avail_gb = _mem.available / (1024 ** 3)
    if _avail_gb < 0.75:
        msg = (
            f"Quantile training aborted: only {_avail_gb:.1f} GB RAM available "
            "(minimum 0.75 GB required)."
        )
        logger.error(msg)
        log_notification("Error", msg)
        return

    try:
        df = _load_training_history()

        if df.empty:
            logger.warning("No data found for quantile training. Aborting.")
            return

        df = build_model_features(df)
        df.dropna(subset=FEATURE_COLS, inplace=True)

        # Continuous return target (not binary)
        df['next_close']   = df.groupby('ticker')['close_price'].shift(-1)
        df['future_close'] = df.groupby('ticker')['close_price'].shift(-PREDICTION_HORIZON_DAYS)
        df.dropna(subset=['next_close', 'future_close'], inplace=True)
        df['return_target'] = (
            (df['future_close'] - df['next_close']) / df['next_close']
        ).clip(-1.0, 1.0)

        if len(df) < 1000:
            logger.warning('Insufficient training samples (%s) for quantile training.', len(df))
            return

        df.sort_values('date', inplace=True)
        df.reset_index(drop=True, inplace=True)

        df = df[FEATURE_COLS + ['date', 'return_target']]
        X_full = df[FEATURE_COLS]
        y_full = df['return_target']

        # 80/20 temporal split with embargo — no calibration step for regression
        unique_dates = np.sort(df['date'].unique())
        date_series  = df['date'].reset_index(drop=True)
        n_dates      = len(unique_dates)
        train_end    = int(n_dates * 0.80)
        train_dates  = set(unique_dates[:train_end - PREDICTION_HORIZON_DAYS])
        test_dates   = set(unique_dates[train_end:])

        train_idx = date_series.index[date_series.isin(train_dates)].tolist()
        test_idx  = date_series.index[date_series.isin(test_dates)].tolist()

        X_train = X_full.iloc[train_idx]
        y_train = y_full.iloc[train_idx]
        X_test  = X_full.iloc[test_idx]
        y_test  = y_full.iloc[test_idx]

        logger.info(
            'Quantile temporal split — Train: %s rows | Test: %s rows', format(len(X_train), ','), format(len(X_test), ',')
        )

        _xgb_params = dict(
            n_estimators=200, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            random_state=42, n_jobs=1,
        )

        q10 = XGBRegressor(objective='reg:quantileerror', quantile_alpha=0.10, **_xgb_params)
        q90 = XGBRegressor(objective='reg:quantileerror', quantile_alpha=0.90, **_xgb_params)
        q10.fit(X_train, y_train)
        q90.fit(X_train, y_train)

        q10_preds = q10.predict(X_test)
        q90_preds = q90.predict(X_test)
        crossing_pct = float(np.mean(q10_preds >= q90_preds))
        logger.info(
            'Test-set quantile diagnostics — Q10 median: %s, Q90 median: %s, crossing rate: %s',
            format(float(np.median(q10_preds)), '.4f'),
            format(float(np.median(q90_preds)), '.4f'),
            format(crossing_pct, '.2%'),
        )

        del q10, q90, X_train, y_train, X_test, y_test, df

        # Refit on full dataset with fixed hyperparameters
        final_q10 = XGBRegressor(objective='reg:quantileerror', quantile_alpha=0.10, **_xgb_params)
        final_q90 = XGBRegressor(objective='reg:quantileerror', quantile_alpha=0.90, **_xgb_params)
        final_q10.fit(X_full, y_full)
        final_q90.fit(X_full, y_full)

        dump_sklearn_artifact(final_q10, QUANTILE_Q10_PATH)
        dump_sklearn_artifact(final_q90, QUANTILE_Q90_PATH)
        logger.info(
            '✅ Quantile models saved — Q10: %s, Q90: %s', QUANTILE_Q10_PATH, QUANTILE_Q90_PATH
        )
        log_notification(
            "Success",
            f"Quantile Regression models trained — Q10/Q90 price bands ready. "
            f"Test crossing rate: {crossing_pct:.2%}."
        )

    except Exception as e:
        logger.error('Fatal error during quantile training: %s', e)
        log_notification("Error", f"Quantile Regression Training failed: {str(e)}")
