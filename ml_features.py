from typing import Dict, Optional, Tuple

import pandas as pd

from config import BASE_DIR


MODELS_DIR = BASE_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
MODEL_PATH          = MODELS_DIR / "ml_ensemble.joblib"
FEATURE_STATS_PATH  = MODELS_DIR / "feature_stats.joblib"
QUANTILE_Q10_PATH   = MODELS_DIR / "quantile_q10.joblib"
QUANTILE_Q90_PATH   = MODELS_DIR / "quantile_q90.joblib"

# ─────────────────────────────────────────────────────────────────────────────
# FEATURE REGISTRY  (18 features — 6 fundamental features removed 2026-05-29)
#
# TECHNICAL FEATURES (10):
#   rsi_14, macd_pct, macd_signal_pct, macd_hist_pct, volume_surge,
#   bullish_cross, dist_sma_50, dist_sma_200, sector_code, dollar_vol_log
#
# MOMENTUM FACTORS (4) — Jegadeesh & Titman (1993):
#   mom_1m, mom_3m, mom_6m, mom_12m_skip1m
#
# VOLATILITY REGIME (2):
#   atr_pct, hist_vol_20
#
# RELATIVE STRENGTH VS SPY (2):
#   rel_strength_5d, rel_strength_20d
#
# FUNDAMENTAL FACTORS (6) — Fama-French value/quality/growth:
#   trailing_pe     — Valuation. Fama-French HML value factor proxy.
#                     Negative PE (loss-making) → NaN → median-imputed.
#                     Capped at 100 to prevent growth-stock outliers
#                     dominating the cross-sectional z-score.
#
#   price_to_book   — Value factor. Low P/B = deep value.
#                     Capped at 20 (covers Amazon/tech at peak multiples).
#
#   profit_margin   — Quality factor. Already a ratio [-1, 1].
#                     Negative margin = the company is burning cash.
#
#   roe             — Quality factor (Fama-French QMJ). Return on Equity.
#                     Capped at 2.0 to handle asset-light businesses with
#                     very high ROE (e.g. NVIDIA at 120% ROE → clipped to 2.0).
#
#   revenue_growth  — Growth factor. YoY revenue change as a decimal.
#                     Capped at 3.0 (300%) to remove post-merger spikes.
#
#   debt_to_equity  — Leverage/risk factor. Yahoo Finance returns this as
#                     a percentage (100 = 1.0x D/E ratio). Capped at 500
#                     (= 5.0x D/E) to handle financial stocks and REITs
#                     whose leverage is structural not distress-driven.
#
# KNOWN LIMITATION — POINT-IN-TIME BIAS:
#   stock_signals stores only the most recent fundamental snapshot (ticker
#   is the PRIMARY KEY). When this snapshot is joined to historical
#   quant_signals rows during training, it applies today's fundamentals
#   to rows from 18 months ago — mild lookahead bias.
#   Acceptable for a hobbyist project where fundamentals change slowly
#   (margins, ROE) or the signal direction is stable (high-PE stocks tend
#   to remain high-PE within a 2-year window). Documented here for
#   transparency. A production system would store time-stamped fundamental
#   snapshots and join on the closest prior date.
#
# NULL HANDLING — CROSS-SECTIONAL MEDIAN IMPUTATION:
#   ETFs, futures, and stocks without reported fundamentals return NULL
#   from yfinance. Rather than dropping these tickers entirely (which would
#   bias the universe toward pure equities), NULLs are filled with the
#   cross-sectional median for that date before z-scoring. These stocks
#   receive a neutral z-score of ~0.0 — they are neither rewarded nor
#   penalised for absent fundamentals.
# ─────────────────────────────────────────────────────────────────────────────

FEATURE_COLS = [
    # Technical
    'rsi_14_z', 'macd_pct_z', 'macd_signal_pct_z', 'macd_hist_pct_z',
    'volume_surge', 'bullish_cross', 'dist_sma_50_z', 'dist_sma_200_z',
    'sector_code', 'dollar_vol_log_z',
    # Momentum
    'mom_1m_z', 'mom_3m_z', 'mom_6m_z', 'mom_12m_skip1m_z',
    # Volatility regime
    'atr_pct_z', 'hist_vol_20_z',
    # Relative strength vs SPY
    'rel_strength_5d_z', 'rel_strength_20d_z',
    #
    # FUNDAMENTAL FACTORS REMOVED — 2026-05-29 (audit item 2a)
    # A/B diagnostic (debug_scripts/ab_fundamentals_diagnostic.py) ran 5 seeds
    # comparing 24-feature vs 18-feature model on 250k rows / 1,060 tickers:
    #
    #   WITH    fundamentals (24 feats): mean PR-AUC 0.4015  lift +0.0746
    #   WITHOUT fundamentals (18 feats): mean PR-AUC 0.4047  lift +0.0778
    #   Delta: −0.0032 ± 0.0043  SNR 0.7×  (18-feat arm won all 5 seeds)
    #
    # Delta is statistically indistinguishable from zero (noise > signal).
    # The 18-feature model never scored lower across any seed.
    # Removing them closes the documented point-in-time lookahead bias
    # (stock_signals stores only the latest snapshot; joining it to
    # historical rows applies today's fundamentals to 18-month-old data).
    #
    # 'trailing_pe_z', 'price_to_book_z', 'profit_margin_z',
    # 'roe_z', 'revenue_growth_z', 'debt_to_equity_z',
]

SECTOR_MAP = {
    "Technology": 1, "Healthcare": 2, "Financials": 3,
    "Financial Services": 3, "Real Estate": 4, "Energy": 5,
    "Basic Materials": 6, "Consumer Cyclical": 7, "Industrials": 8,
    "Utilities": 9, "Consumer Defensive": 10, "Communication Services": 11,
    "Broad Market ETF": 12, "ETF": 12, "Futures": 13,
    "Unknown": 99
}

# All continuous features that receive cross-sectional z-scoring.
# Fundamental features are included here — winsorization is applied
# before z-scoring in the feature engineering block.
CONTINUOUS_FEATURES = [
    'rsi_14', 'macd_pct', 'macd_signal_pct', 'macd_hist_pct',
    'dist_sma_50', 'dist_sma_200', 'dollar_vol_log',
    'mom_1m', 'mom_3m', 'mom_6m', 'mom_12m_skip1m',
    'atr_pct', 'hist_vol_20',
    'rel_strength_5d', 'rel_strength_20d',
    'trailing_pe', 'price_to_book', 'profit_margin',
    'roe', 'revenue_growth', 'debt_to_equity',
]

# Fundamental features requiring winsorization + median imputation
FUNDAMENTAL_FEATURES = [
    'trailing_pe', 'price_to_book', 'profit_margin',
    'roe', 'revenue_growth', 'debt_to_equity',
]

# Winsorization bounds per fundamental feature.
# (lower_bound, upper_bound) — None means no bound on that side.
FUNDAMENTAL_BOUNDS: Dict[str, Tuple[Optional[float], Optional[float]]] = {
    'trailing_pe':    (0.0,   300.0),   # raised: p95=265, was clipping 15%
    'price_to_book':  (-20.0, 80.0),    # raised cap: p95=65, was clipping 27%
                                         # floor lowered: negative P/B is valid data
    'profit_margin':  (-1.0,  1.0),     # correct — no change
    'roe':            (-1.0,  1.5),     # correct — no change
    'revenue_growth': (-1.0,  5.0),     # raised: p95=2.04, minor headroom added
    'debt_to_equity': (0.0,   500.0),   # correct — no change
}


def cross_sectional_zscore(series: pd.Series) -> pd.Series:
    """Calculates Z-Score dynamically. Safe against 0 standard deviation."""
    std = series.std()
    if pd.isna(std) or std == 0:
        return series - series.mean()
    return (series - series.mean()) / std


def _winsorize_and_impute_fundamentals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Applies per-feature winsorization and cross-sectional median imputation
    to the fundamental feature columns.

    Winsorization: clips values to [lower, upper] bounds defined in
    FUNDAMENTAL_BOUNDS. For trailing_pe, negative values are set to NaN
    before clipping because a negative PE (loss-making company) is a
    qualitatively different state from a cheap company — it should not be
    treated as "very low PE" and should receive the neutral median instead.

    Imputation: fills NaN values (including original NULLs from ETFs/futures
    and the negative-PE NaNs just created) with the cross-sectional median
    for that date. This gives non-equity assets a neutral fundamental profile
    rather than dropping them from the universe.

    Args:
        df: DataFrame containing raw fundamental columns and a 'date' column.

    Returns:
        DataFrame with fundamentals winsorized and NULLs imputed.
    """
    # trailing_pe: negative values are loss-making companies, not cheap stocks
    if 'trailing_pe' in df.columns:
        df['trailing_pe'] = df['trailing_pe'].where(df['trailing_pe'] > 0)

    for col, (lo, hi) in FUNDAMENTAL_BOUNDS.items():
        if col not in df.columns:
            continue
        df[col] = df[col].clip(lower=lo, upper=hi)

    # Cross-sectional median imputation per date
    for col in FUNDAMENTAL_FEATURES:
        if col not in df.columns:
            continue
        null_count = df[col].isna().sum()
        if null_count > 0:
            df[col] = df.groupby('date')[col].transform(
                lambda x: x.fillna(x.median())
            )

    return df
