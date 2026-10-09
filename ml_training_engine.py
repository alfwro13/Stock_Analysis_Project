import logging
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

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


class _Split(NamedTuple):
    X_train: pd.DataFrame
    y_train: pd.Series
    X_calib: pd.DataFrame
    y_calib: pd.Series
    X_test: pd.DataFrame
    y_test: pd.Series
    train_dates: pd.Series


def _search_iterations() -> Optional[int]:
    """Randomized-search budget for the RAM available now; None (after notifying) when training must not start."""
    avail_gb = psutil.virtual_memory().available / (1024 ** 3)
    abort_gb = 0.75
    throttle_gb = 1.5
    if avail_gb < abort_gb:
        msg = (
            f"ML Training aborted: only {avail_gb:.1f} GB RAM available "
            f"(minimum {abort_gb} GB required). Free memory and retry."
        )
        logger.error(msg)
        log_notification("Error", msg)
        return None
    n_iter = 4 if avail_gb < throttle_gb else 10
    if avail_gb < throttle_gb:
        logger.warning(
            'Low memory (%s GB available) — throttling search to %s iterations.', format(avail_gb, '.1f'), n_iter
        )
    return n_iter


def _log_remaining_fundamental_nulls(df: pd.DataFrame) -> None:
    for col in FUNDAMENTAL_FEATURES:
        remaining_nulls = df[col].isna().sum()
        if remaining_nulls > 0:
            logger.warning(
                '  %s: %s NULLs remain after imputation (dates with zero equity coverage — will be dropped by dropna).',
                col,
                remaining_nulls,
            )


def _save_feature_stats(df: pd.DataFrame) -> None:
    """Written for the inference coverage guard and the train-vs-serve drift diagnostic in update_daily_ml_predictions."""
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


def _add_classifier_target(df: pd.DataFrame) -> pd.DataFrame:
    """Entry proxy is close[T+1], exit close[T+PREDICTION_HORIZON_DAYS]; a 10-day horizon has a better signal-to-noise ratio than 5."""
    df['next_close']   = df.groupby('ticker')['close_price'].shift(-1)
    df['future_close'] = df.groupby('ticker')['close_price'].shift(-PREDICTION_HORIZON_DAYS)
    df.dropna(subset=['next_close', 'future_close'], inplace=True)

    df['target'] = (
        (df['future_close'] - df['next_close']) / df['next_close'] > PREDICTION_RETURN_THRESHOLD
    ).astype(int)
    return df


def _log_class_distribution(df: pd.DataFrame) -> None:
    pos = (df['target'] == 1).sum()
    neg = (df['target'] == 0).sum()
    logger.info(
        'Class distribution — Positive (1): %s (%s) | Negative (0): %s (%s)',
        format(pos, ','),
        format(pos / len(df), '.1%'),
        format(neg, ','),
        format(neg / len(df), '.1%'),
    )


def _temporal_split(df: pd.DataFrame) -> _Split:
    """60/20/20 Train/Calib/Test by date, so calibration and the final score only use rows unseen during tuning."""
    df.sort_values('date', inplace=True)
    df.reset_index(drop=True, inplace=True)

    df = df[FEATURE_COLS + ['date', 'target']]
    X_full = df[FEATURE_COLS]
    y_full = df['target']

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

    split = _Split(
        X_train=X_full.iloc[train_idx], y_train=y_full.iloc[train_idx],
        X_calib=X_full.iloc[calib_idx], y_calib=y_full.iloc[calib_idx],
        X_test=X_full.iloc[test_idx],   y_test=y_full.iloc[test_idx],
        train_dates=date_series.iloc[train_idx].reset_index(drop=True),
    )

    logger.info(
        'Temporal split — Train: %s rows (%s → %s)  |  Calib: %s rows (%s → %s)  |  Test: %s rows (%s → %s)',
        format(len(split.X_train), ','),
        unique_dates[0],
        unique_dates[train_end - PREDICTION_HORIZON_DAYS - 1],
        format(len(split.X_calib), ','),
        unique_dates[train_end],
        unique_dates[calib_end - PREDICTION_HORIZON_DAYS - 1],
        format(len(split.X_test), ','),
        unique_dates[calib_end],
        unique_dates[-1],
    )
    return split


def _walk_forward_splits(train_dates: pd.Series) -> List[Tuple[List[int], List[int]]]:
    logger.info("Constructing Strict 5-Fold Walk-Forward Splits on training region...")

    train_unique_dates = np.sort(train_dates.unique())

    cv_splits = []
    for tr_date_idx, te_date_idx in TimeSeriesSplit(n_splits=5).split(train_unique_dates):
        if len(tr_date_idx) > PREDICTION_HORIZON_DAYS:
            tr_dates = set(train_unique_dates[tr_date_idx[:-PREDICTION_HORIZON_DAYS]])
            te_dates = set(train_unique_dates[te_date_idx])
            tr_idx   = train_dates.index[train_dates.isin(tr_dates)].tolist()
            te_idx   = train_dates.index[train_dates.isin(te_dates)].tolist()
            if tr_idx and te_idx:
                cv_splits.append((tr_idx, te_idx))
    return cv_splits


def _search_hyperparameters(X_train, y_train, cv_splits, n_iter: int):
    """Randomized search on the Train region only; returns the best (RF, XGB) estimators."""
    # scale_pos_weight derived from train set only to prevent test leakage
    neg_count_train        = (y_train == 0).sum()
    pos_count_train        = (y_train == 1).sum()
    scale_pos_weight_train = (
        neg_count_train / pos_count_train if pos_count_train > 0 else 1.0
    )

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
        n_iter=n_iter, cv=cv_splits, scoring='average_precision',
        random_state=42, n_jobs=1
    )
    xgb_search = RandomizedSearchCV(
        estimator=xgb_base, param_distributions=xgb_param_dist,
        n_iter=n_iter, cv=cv_splits, scoring='average_precision',
        random_state=42, n_jobs=1
    )

    rf_search.fit(X_train, y_train)
    xgb_search.fit(X_train, y_train)

    logger.info('Optimal RF Params Found:  %s', rf_search.best_params_)
    logger.info('Optimal XGB Params Found: %s', xgb_search.best_params_)

    # CV score on train region only — informational, not the headline metric
    train_cv_pr_auc = (rf_search.best_score_ + xgb_search.best_score_) / 2.0
    logger.info('Train-region CV Avg-Precision: %s', format(train_cv_pr_auc, '.4f'))

    return rf_search.best_estimator_, xgb_search.best_estimator_


def _fit_calibrated(estimator, X_calib, y_calib) -> CalibratedClassifierCV:
    """FrozenEstimator marks the estimator as already fitted, so isotonic regression is fitted on the Calib rows only."""
    calibrated = CalibratedClassifierCV(estimator=FrozenEstimator(estimator), method='isotonic')
    calibrated.fit(X_calib, y_calib)
    return calibrated


def _holdout_pr_auc(best_rf, best_xgb, split: _Split) -> Tuple[float, float]:
    """The only honest PR-AUC: the Test region was never touched during tuning or calibration."""
    logger.info("Calibrating on held-out calibration region (never seen during tuning)...")

    calibrated_rf  = _fit_calibrated(best_rf,  split.X_calib, split.y_calib)
    calibrated_xgb = _fit_calibrated(best_xgb, split.X_calib, split.y_calib)

    ensemble_probs_test = (
        calibrated_rf.predict_proba(split.X_test)[:, 1] +
        calibrated_xgb.predict_proba(split.X_test)[:, 1]
    ) / 2.0
    true_oos_pr_auc  = average_precision_score(split.y_test, ensemble_probs_test)
    true_oos_baseline = float(split.y_test.mean())
    logger.info(
        '✅ True OOS PR-AUC (clean holdout): %s  (random baseline = %s)',
        format(true_oos_pr_auc, '.4f'),
        format(true_oos_baseline, '.4f'),
    )
    return true_oos_pr_auc, true_oos_baseline


def _refit_production_ensemble(final_rf, final_xgb, X_prod, y_prod, X_calib, y_calib) -> VotingClassifier:
    """Hyperparameters are fixed; refit on Train + Calib for more signal, then recalibrate on Calib again."""
    final_rf.fit(X_prod, y_prod)
    final_xgb.fit(X_prod, y_prod)

    final_calibrated_rf  = _fit_calibrated(final_rf,  X_calib, y_calib)
    final_calibrated_xgb = _fit_calibrated(final_xgb, X_calib, y_calib)

    production_ensemble = VotingClassifier(
        estimators=[('rf', final_calibrated_rf), ('xgb', final_calibrated_xgb)],
        voting='soft'
    )
    # Estimators are already fitted — manually mark VotingClassifier as fitted
    production_ensemble.estimators_ = [final_calibrated_rf, final_calibrated_xgb]
    production_ensemble.classes_    = np.array([0, 1])
    # n_features_in_ is auto-derived from estimators_ in sklearn 1.6+
    return production_ensemble


def train_global_ml_model() -> None:
    """Fundamentals are joined and preprocessed for drift diagnostics only; FEATURE_COLS excludes them (see ml_features.py)."""
    logger.info("Initiating Global ML Model Training pipeline with Hyperparameter Optimization...")

    yahoo_engine.prune_expired()
    n_iter = _search_iterations()
    if n_iter is None:
        return

    log_notification("Info", "Global ML Model Training pipeline initiated.")

    try:
        df = _load_training_history()

        if df.empty:
            logger.warning("No data found in DB. Aborting ML training.")
            return

        logger.info('Extracting features from %s historical records...', len(df))

        df = build_model_features(df)
        _log_remaining_fundamental_nulls(df)
        _save_feature_stats(df)

        df.dropna(subset=FEATURE_COLS, inplace=True)
        df = _add_classifier_target(df)

        if len(df) < 1000:
            logger.warning('Insufficient training samples (%s).', len(df))
            return

        _log_class_distribution(df)

        split = _temporal_split(df)
        del df

        cv_splits = _walk_forward_splits(split.train_dates)
        best_rf, best_xgb = _search_hyperparameters(split.X_train, split.y_train, cv_splits, n_iter)
        del cv_splits

        true_oos_pr_auc, true_oos_baseline = _holdout_pr_auc(best_rf, best_xgb, split)

        logger.info("Refitting production model on Train + Calib region (80% of data)...")

        X_prod = pd.concat([split.X_train, split.X_calib])
        y_prod = pd.concat([split.y_train, split.y_calib])
        X_calib, y_calib = split.X_calib, split.y_calib

        final_rf = clone(best_rf)
        final_xgb = clone(best_xgb)
        del best_rf, best_xgb, split
        production_ensemble = _refit_production_ensemble(final_rf, final_xgb, X_prod, y_prod, X_calib, y_calib)

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
