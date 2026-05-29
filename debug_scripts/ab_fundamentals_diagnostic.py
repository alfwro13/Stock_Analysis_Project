#!/usr/bin/env python3
"""
debug_scripts/ab_fundamentals_diagnostic.py
============================================
DIAGNOSTIC — Audit item 2a: Quantify fundamental feature lookahead leakage.

Trains the ML ensemble TWICE on identical data and identical temporal splits:
  Arm A  — all 24 features (FULL_FEATURES)
  Arm B  — 18 features with the 6 fundamental z-scores removed (NO_FUND_FEATURES)

Both arms use the exact same SQL, feature engineering, winsorization/imputation,
cross-sectional z-scoring, temporal split (60 / 20 / 20 with PREDICTION_HORIZON_DAYS
embargo), walk-forward CV, hyperparameter search, and calibration that the
production pipeline uses.  The only input that differs is the feature list.

KNOWN CAVEAT: PR-AUC has run-to-run variance from the randomised hyperparameter
search.  A single run delta is noisy.  Pass --repeats N (e.g. 5) to run both
arms N times with different random seeds and report mean ± std of the delta —
this materially strengthens any conclusion.

SAFE BY DESIGN:
  - Never writes to MODEL_PATH or FEATURE_STATS_PATH.
  - Never calls yfinance — operates entirely on data already in the DB.
  - Never modifies any production table or file.
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import average_precision_score
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from xgboost import XGBClassifier

# ── Project root on sys.path ──────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Import helpers and constants from production modules so we share exactly
# the same winsorization, z-scoring, and sector-mapping logic.
from ai_prediction_engine import (  # noqa: E402
    CONTINUOUS_FEATURES,
    FEATURE_COLS,
    FUNDAMENTAL_FEATURES,
    SECTOR_MAP,
    _winsorize_and_impute_fundamentals,
    cross_sectional_zscore,
)
from constants import PREDICTION_HORIZON_DAYS, PREDICTION_RETURN_THRESHOLD  # noqa: E402
from database import get_connection  # noqa: E402

# ── Module logger (diagnostics go to logger; final block goes to stdout) ──────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ── Feature sets ──────────────────────────────────────────────────────────────
# The six fundamental z-scored features that carry a documented point-in-time
# lookahead bias (stock_signals stores only the latest snapshot, so today's
# fundamentals are joined to historical rows).
FUNDAMENTAL_Z_COLS: List[str] = [
    'trailing_pe_z',
    'price_to_book_z',
    'profit_margin_z',
    'roe_z',
    'revenue_growth_z',
    'debt_to_equity_z',
]

FULL_FEATURES: List[str] = list(FEATURE_COLS)
NO_FUND_FEATURES: List[str] = [c for c in FEATURE_COLS if c not in FUNDAMENTAL_Z_COLS]


# ── Data loading & feature engineering ───────────────────────────────────────

def _load_training_dataframe() -> Optional[pd.DataFrame]:
    """
    Loads the full training dataset from the database and applies all feature
    engineering, fundamental winsorization/imputation, cross-sectional
    z-scoring, and forward-return target construction — identical to the
    production training pipeline in train_global_ml_model().

    The raw (pre-z) columns are preserved alongside the *_z columns so that
    callers can inspect distributions if needed.

    Returns:
        Fully-prepared DataFrame sorted by date with index reset, or None
        when the database has insufficient data.
    """
    logger.info("Loading training data from database...")
    conn = get_connection()

    # Identical query to train_global_ml_model — LEFT JOINs ensure we get
    # NULL fundamentals for ETFs/futures rather than dropping them.
    query = """
        SELECT qs.ticker, qs.date, qs.close_price, qs.volume,
               qs.rsi_14, qs.macd, qs.macd_signal, qs.macd_hist,
               qs.sma_50, qs.sma_200, qs.volume_surge, qs.bullish_cross,
               qs.mom_1m, qs.mom_3m, qs.mom_6m, qs.mom_12m_skip1m,
               qs.atr_pct, qs.hist_vol_20,
               qs.rel_strength_5d, qs.rel_strength_20d,
               ss.trailing_pe, ss.price_to_book, ss.profit_margin,
               ss.roe, ss.revenue_growth, ss.debt_to_equity,
               tm.sector
        FROM quant_signals qs
        LEFT JOIN stock_signals  ss ON qs.ticker = ss.ticker
        LEFT JOIN ticker_metadata tm ON qs.ticker = tm.ticker
        WHERE qs.mom_1m           IS NOT NULL
          AND qs.mom_12m_skip1m   IS NOT NULL
          AND qs.atr_pct          IS NOT NULL
          AND qs.hist_vol_20      IS NOT NULL
          AND qs.rel_strength_5d  IS NOT NULL
          AND qs.rel_strength_20d IS NOT NULL
        ORDER BY qs.date ASC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()

    if df.empty:
        logger.error("No data found in DB. Run the historical backfill first.")
        return None

    logger.info(f"Loaded {len(df):,} rows from DB.")

    # ── Feature engineering (identical to production) ─────────────────────
    df['dist_sma_50']  = (df['close_price'] - df['sma_50'])  / df['sma_50']
    df['dist_sma_200'] = (df['close_price'] - df['sma_200']) / df['sma_200']

    df['macd_pct']        = df['macd']        / df['close_price']
    df['macd_signal_pct'] = df['macd_signal'] / df['close_price']
    df['macd_hist_pct']   = df['macd_hist']   / df['close_price']

    df['volume_surge']  = df['volume_surge'].fillna(0).astype(int)
    df['bullish_cross'] = df['bullish_cross'].fillna(0).astype(int)

    df['sector_code']    = df['sector'].map(SECTOR_MAP).fillna(99).astype(int)
    df['dollar_vol_log'] = np.log1p(df['close_price'] * df['volume'])

    # ── Fundamental winsorization + cross-sectional median imputation ──────
    logger.info("Winsorizing and imputing fundamental features...")
    df = _winsorize_and_impute_fundamentals(df)

    for col in FUNDAMENTAL_FEATURES:
        n_null = int(df[col].isna().sum())
        if n_null > 0:
            logger.warning(
                f"  {col}: {n_null} NULLs remain after imputation "
                "(dates with zero equity coverage — dropped by dropna below)."
            )

    df.replace([np.inf, -np.inf], np.nan, inplace=True)

    # ── Cross-sectional Z-scoring (per date, same as production) ─────────
    logger.info("Applying cross-sectional Z-scoring...")
    for col in CONTINUOUS_FEATURES:
        df[f'{col}_z'] = df.groupby('date')[col].transform(cross_sectional_zscore)

    # Drop on the full 24-feature set so BOTH arms operate on the same rows.
    # Using a smaller dropna here would change the population for Arm B.
    df.dropna(subset=FULL_FEATURES, inplace=True)

    # ── Target construction ───────────────────────────────────────────────
    # forward return = (close[T+10] - close[T+1]) / close[T+1]
    df['next_close']   = df.groupby('ticker')['close_price'].shift(-1)
    df['future_close'] = df.groupby('ticker')['close_price'].shift(-PREDICTION_HORIZON_DAYS)
    df.dropna(subset=['next_close', 'future_close'], inplace=True)

    df['target'] = (
        (df['future_close'] - df['next_close']) / df['next_close']
        > PREDICTION_RETURN_THRESHOLD
    ).astype(int)

    if len(df) < 1000:
        logger.error(f"Insufficient training samples after engineering: {len(df):,}.")
        return None

    pos = int((df['target'] == 1).sum())
    logger.info(
        f"Dataset ready: {len(df):,} rows | "
        f"positive rate: {pos / len(df):.1%} | "
        f"tickers: {df['ticker'].nunique():,} | "
        f"dates: {df['date'].min()} → {df['date'].max()}"
    )

    df.sort_values('date', inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# ── Core training + evaluation (no persistence) ───────────────────────────────

def _train_and_evaluate(
    df: pd.DataFrame,
    feature_cols: List[str],
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Runs the full train / calibrate / evaluate pipeline for a given feature
    subset.  Never writes any files or fires any notifications.

    The temporal split, embargo, CV fold construction, hyperparameter search
    grid, and calibration method are identical to the production pipeline so
    that differences in PR-AUC are attributable to the feature set only.

    Note: hyperparameter search is re-run per arm because the optimal
    parameters legitimately differ by feature count — this is correct
    behaviour, not a source of confounding.

    Args:
        df: Output of _load_training_dataframe().  Must contain 'date',
            'target', and all columns in feature_cols.
        feature_cols: Z-scored feature columns to use for this arm.
        seed: Random seed for RandomForestClassifier, XGBClassifier, and
              both RandomizedSearchCV calls.

    Returns:
        Dict with keys: true_oos_pr_auc, baseline, n_train, n_calib,
        n_test, n_features.
    """
    n_feats = len(feature_cols)
    logger.info(f"[{n_feats} features | seed={seed}] Starting pipeline...")

    X_full = df[feature_cols]
    y_full = df['target']

    # ── Three-way temporal split (60 / 20 / 20) ───────────────────────────
    unique_dates = np.sort(df['date'].unique())
    date_series  = df['date'].reset_index(drop=True)
    n_dates      = len(unique_dates)

    train_end = int(n_dates * 0.60)
    calib_end = int(n_dates * 0.80)

    # Purge the last PREDICTION_HORIZON_DAYS dates from each partition so
    # their forward labels cannot bleed into the next region.
    train_dates = set(unique_dates[:train_end - PREDICTION_HORIZON_DAYS])
    calib_dates = set(unique_dates[train_end:calib_end - PREDICTION_HORIZON_DAYS])
    test_dates  = set(unique_dates[calib_end:])

    train_idx = date_series.index[date_series.isin(train_dates)].tolist()
    calib_idx = date_series.index[date_series.isin(calib_dates)].tolist()
    test_idx  = date_series.index[date_series.isin(test_dates)].tolist()

    X_train, y_train = X_full.iloc[train_idx], y_full.iloc[train_idx]
    X_calib, y_calib = X_full.iloc[calib_idx], y_full.iloc[calib_idx]
    X_test,  y_test  = X_full.iloc[test_idx],  y_full.iloc[test_idx]

    logger.info(
        f"[{n_feats} feats] Temporal split — "
        f"train: {len(X_train):,}  calib: {len(X_calib):,}  test: {len(X_test):,}"
    )

    neg_count_train = int((y_train == 0).sum())
    pos_count_train = int((y_train == 1).sum())
    scale_pos_weight_train: float = (
        neg_count_train / pos_count_train if pos_count_train > 0 else 1.0
    )

    # ── Walk-forward CV splits on train region ────────────────────────────
    train_unique_dates = np.sort(df.iloc[train_idx]['date'].unique())
    train_date_series  = df.iloc[train_idx]['date'].reset_index(drop=True)

    cv_splits_train: List[Tuple[List[int], List[int]]] = []
    for tr_date_idx, te_date_idx in TimeSeriesSplit(n_splits=5).split(train_unique_dates):
        if len(tr_date_idx) > PREDICTION_HORIZON_DAYS:
            tr_dates_set = set(train_unique_dates[tr_date_idx[:-PREDICTION_HORIZON_DAYS]])
            te_dates_set = set(train_unique_dates[te_date_idx])
            tr_idx_cv = train_date_series.index[
                train_date_series.isin(tr_dates_set)
            ].tolist()
            te_idx_cv = train_date_series.index[
                train_date_series.isin(te_dates_set)
            ].tolist()
            if tr_idx_cv and te_idx_cv:
                cv_splits_train.append((tr_idx_cv, te_idx_cv))

    # ── Hyperparameter search (identical grid to production) ──────────────
    # n_jobs=1: loky parallel backend fails on Python 3.14 in this environment.
    # Single-threaded is slower but reliable for a diagnostic script.
    rf_base = RandomForestClassifier(
        class_weight='balanced', random_state=seed, n_jobs=1
    )
    xgb_base = XGBClassifier(
        scale_pos_weight=scale_pos_weight_train,
        random_state=seed, n_jobs=1, eval_metric='logloss',
    )

    rf_param_dist: Dict[str, List[Any]] = {
        'n_estimators':     [100, 150, 200, 250],
        'max_depth':        [4, 6, 8, 10],
        'min_samples_leaf': [1, 5, 10],
    }
    xgb_param_dist: Dict[str, List[Any]] = {
        'n_estimators':     [100, 150, 200],
        'max_depth':        [3, 5, 7],
        'learning_rate':    [0.01, 0.05, 0.1],
        'subsample':        [0.7, 0.9, 1.0],
        'colsample_bytree': [0.7, 0.9, 1.0],
    }

    logger.info(f"[{n_feats} feats | seed={seed}] Running RandomizedSearchCV...")

    rf_search = RandomizedSearchCV(
        estimator=rf_base, param_distributions=rf_param_dist,
        n_iter=10, cv=cv_splits_train, scoring='average_precision',
        random_state=seed, n_jobs=1,
    )
    xgb_search = RandomizedSearchCV(
        estimator=xgb_base, param_distributions=xgb_param_dist,
        n_iter=10, cv=cv_splits_train, scoring='average_precision',
        random_state=seed, n_jobs=1,
    )

    rf_search.fit(X_train, y_train)
    xgb_search.fit(X_train, y_train)

    best_rf  = rf_search.best_estimator_
    best_xgb = xgb_search.best_estimator_

    logger.info(f"[{n_feats} feats | seed={seed}] Best RF  params: {rf_search.best_params_}")
    logger.info(f"[{n_feats} feats | seed={seed}] Best XGB params: {xgb_search.best_params_}")

    # ── Isotonic calibration on calib region ─────────────────────────────
    calibrated_rf = CalibratedClassifierCV(
        estimator=FrozenEstimator(best_rf), method='isotonic'
    )
    calibrated_xgb = CalibratedClassifierCV(
        estimator=FrozenEstimator(best_xgb), method='isotonic'
    )
    calibrated_rf.fit(X_calib,  y_calib)
    calibrated_xgb.fit(X_calib, y_calib)

    # ── True OOS evaluation on test region ────────────────────────────────
    ensemble_probs: np.ndarray = (
        calibrated_rf.predict_proba(X_test)[:, 1]
        + calibrated_xgb.predict_proba(X_test)[:, 1]
    ) / 2.0

    oos_pr_auc: float = float(average_precision_score(y_test, ensemble_probs))
    baseline:   float = float(y_test.mean())

    logger.info(
        f"[{n_feats} feats | seed={seed}] "
        f"OOS PR-AUC: {oos_pr_auc:.4f}  baseline: {baseline:.4f}"
    )

    return {
        'true_oos_pr_auc': oos_pr_auc,
        'baseline':        baseline,
        'n_train':         len(X_train),
        'n_calib':         len(X_calib),
        'n_test':          len(X_test),
        'n_features':      n_feats,
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "A/B diagnostic: measure PR-AUC impact of the 6 fundamental "
            "z-scored features (audit item 2a)."
        )
    )
    parser.add_argument(
        '--repeats', type=int, default=1, metavar='N',
        help=(
            "Run each arm N times with seeds 42, 43, … 42+N-1 and report "
            "mean ± std of the delta.  N=1 (default) is a single run.  "
            "N≥5 is recommended for a robust conclusion."
        ),
    )
    args = parser.parse_args()

    df = _load_training_dataframe()
    if df is None:
        logger.error("Cannot proceed — no training data available.")
        sys.exit(1)

    seeds: List[int] = [42 + i for i in range(args.repeats)]
    logger.info(
        f"Running {args.repeats} repeat(s) per arm "
        f"with seeds: {seeds}"
    )

    full_aucs:   List[float] = []
    nofund_aucs: List[float] = []
    baseline_val: float = 0.0
    split_info: Optional[Dict[str, int]] = None

    for seed in seeds:
        logger.info(f"{'─' * 60}")
        logger.info(f"Repeat seed={seed}: Arm A (24 features — WITH fundamentals)")
        metrics_full = _train_and_evaluate(df, FULL_FEATURES, seed=seed)

        logger.info(f"Repeat seed={seed}: Arm B (18 features — WITHOUT fundamentals)")
        metrics_nofund = _train_and_evaluate(df, NO_FUND_FEATURES, seed=seed)

        full_aucs.append(metrics_full['true_oos_pr_auc'])
        nofund_aucs.append(metrics_nofund['true_oos_pr_auc'])
        baseline_val = metrics_full['baseline']

        if split_info is None:
            split_info = {
                'n_train': metrics_full['n_train'],
                'n_calib': metrics_full['n_calib'],
                'n_test':  metrics_full['n_test'],
            }

    assert split_info is not None, "No repeats completed."

    full_arr   = np.array(full_aucs)
    nofund_arr = np.array(nofund_aucs)
    delta_arr  = full_arr - nofund_arr

    full_mean:  float = float(full_arr.mean())
    nofund_mean: float = float(nofund_arr.mean())
    delta_mean: float = float(delta_arr.mean())
    delta_std:  float = float(delta_arr.std(ddof=0))

    full_lift:   float = full_mean   - baseline_val
    nofund_lift: float = nofund_mean - baseline_val
    rel_change:  float = (
        delta_mean / full_lift * 100.0
        if full_lift > 1e-9 else float('nan')
    )

    # ── Final comparison block ────────────────────────────────────────────
    sep = "=" * 57
    print()
    print(sep)
    print("       2a  FUNDAMENTALS  DIAGNOSTIC")
    print(sep)
    print(f"  Splits (identical for both arms):")
    print(f"    Train:  {split_info['n_train']:,} rows")
    print(f"    Calib:  {split_info['n_calib']:,} rows")
    print(f"    Test:   {split_info['n_test']:,} rows")
    print()
    print("  Hyperparameter search was re-run per arm (correct —")
    print("  optimal params legitimately differ by feature count).")
    print()
    print(f"  Random baseline (test positive rate):  {baseline_val:.4f}")
    print()

    if args.repeats > 1:
        print(
            f"  WITH fundamentals    ({len(FULL_FEATURES):>2} feats): "
            f"PR-AUC {full_mean:.4f}  (lift: +{full_lift:.4f})"
        )
        for i, v in enumerate(full_aucs):
            print(f"    seed={42 + i}: {v:.4f}")
        print()
        print(
            f"  WITHOUT fundamentals ({len(NO_FUND_FEATURES):>2} feats): "
            f"PR-AUC {nofund_mean:.4f}  (lift: +{nofund_lift:.4f})"
        )
        for i, v in enumerate(nofund_aucs):
            print(f"    seed={42 + i}: {v:.4f}")
        print()
        print(
            f"  Delta (with - without):          "
            f"{delta_mean:+.4f} ± {delta_std:.4f}  "
            f"({rel_change:.1f}% relative change in lift)"
        )
    else:
        print(
            f"  WITH fundamentals    ({len(FULL_FEATURES):>2} feats): "
            f"PR-AUC {full_mean:.4f}  (lift over baseline: +{full_lift:.4f})"
        )
        print(
            f"  WITHOUT fundamentals ({len(NO_FUND_FEATURES):>2} feats): "
            f"PR-AUC {nofund_mean:.4f}  (lift over baseline: +{nofund_lift:.4f})"
        )
        print()
        print(
            f"  Delta (with - without):          "
            f"{delta_mean:+.4f}  ({rel_change:.1f}% relative change in lift)"
        )

    print("-" * 57)
    print("  INTERPRETATION:")

    small_delta = abs(delta_mean) < 0.01
    small_rel   = (not np.isnan(rel_change)) and abs(rel_change) < 5.0

    if small_delta or small_rel:
        print("    delta small (< ~0.01 abs or < ~5% of lift):")
        print("      → Fundamentals add little measurable signal.")
        print("        Consider dropping them — this removes the documented")
        print("        point-in-time lookahead bias entirely.")
    else:
        print("    delta LARGE — treat as a YELLOW FLAG:")
        print("      → The lift may be partly lookahead leakage (today's")
        print("        fundamentals applied to historical rows), not genuine")
        print("        predictive signal. Investigate before trusting it.")

    print()
    if args.repeats == 1:
        print("  NOTE: single run — PR-AUC has run-to-run variance from the")
        print("  randomised hyperparameter search.  A tiny delta is within")
        print("  noise.  Rerun with --repeats 5 for a robust estimate.")
    else:
        print(
            f"  Std of delta across {args.repeats} repeats: {delta_std:.4f}  "
            f"(SNR: {abs(delta_mean) / (delta_std + 1e-9):.1f}×)"
        )
    print(sep)
    print()


if __name__ == '__main__':
    main()
