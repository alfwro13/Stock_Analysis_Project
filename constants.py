# constants.py — single source of truth for every threshold shared across modules.

# ── ML prediction ─────────────────────────────────────────────────────────────
PREDICTION_HORIZON_DAYS     = 10    # trading days from T+1 entry to T+10 exit
PREDICTION_RETURN_THRESHOLD = 0.03  # 3% return required for a positive label

# ── Market regime classification ───────────────────────────────────────────────
REGIME_CRASH_VOL    = 35.0  # annualised vol % at or above which regime = Crash
REGIME_VOLATILE_VOL = 20.0  # annualised vol % at or above which regime = Volatile

# ── RSI bands ─────────────────────────────────────────────────────────────────
RSI_OVERSOLD            = 30    # oversold reversal trigger
RSI_OVERBOUGHT          = 70    # overbought warning (normal regime) + momentum surge upper bound
RSI_OVERBOUGHT_STRESSED = 65    # tightened overbought threshold in Crash / Volatile regimes
RSI_MOMENTUM_MIN        = 50    # lower bound of healthy momentum surge band
RSI_HEALTHY_MIN         = 40.0  # scoring model: RSI "room to run" lower bound
RSI_HEALTHY_MAX         = 65.0  # scoring model: RSI "room to run" upper bound

# ── Composite score → signal label boundaries ─────────────────────────────────
SCORE_STRONG_BUY  =  40
SCORE_BULLISH     =  20
SCORE_NEUTRAL     =   0
SCORE_BEARISH     = -30
SCORE_STRONG_SELL = -60

# ── ML screener veto floor ────────────────────────────────────────────────────
ML_CONFIDENCE_THRESHOLD = 40.0

# ── Defensive sectors (used in stressed-regime filters) ───────────────────────
DEFENSIVE_SECTORS = ['Healthcare', 'Utilities', 'Consumer Defensive', 'Consumer Staples']

# ── Data freshness thresholds (UI staleness badge) ────────────────────────────
FRESHNESS_MODEL_WARN_DAYS   = 7   # model file older than this → amber
FRESHNESS_MODEL_STALE_DAYS  = 14  # model file older than this → red
FRESHNESS_PRICES_WARN_DAYS  = 3   # price data older than this → amber (covers normal weekends)
FRESHNESS_PRICES_STALE_DAYS = 5   # price data older than this → red

# ── Macro AI engine ────────────────────────────────────────────────────────────
# Training data minimums
MACRO_HMM_MIN_TRAIN_ROWS   = 50    # macro_indicators rows required to fit HMM
MACRO_CAL_MIN_TRAIN_ROWS   = 10    # macro_calendar rows required for RF and XGBoost

# HMM architecture
MACRO_HMM_N_STATES         = 3     # hidden states: 0=expansion, 1=choppy, 2=recession
MACRO_HMM_N_ITER           = 100   # EM iterations for GaussianHMM.fit()
MACRO_HMM_TRAIN_FRAC       = 0.8   # fraction of rows used to fit HMM; remainder scored for log-likelihood

# Random Forest hyperparameters (consensus miss probability)
MACRO_RF_N_ESTIMATORS      = 100
MACRO_RF_MAX_DEPTH         = 4

# XGBoost hyperparameters (volatility magnitude)
MACRO_XGB_N_ESTIMATORS     = 100
MACRO_XGB_MAX_DEPTH        = 4
MACRO_XGB_LEARNING_RATE    = 0.05

# Cross-validation
MACRO_CV_N_SPLITS          = 3     # TimeSeriesSplit folds for supervised CV

# Inference thresholds
MACRO_VIX_DEFAULT          = 20.0  # VIX fallback when market_regimes has no data
MACRO_SEVERE_VOL_THRESHOLD = 2.0   # predicted SPY gap % that triggers a warning log

# ── Market Stress Isolation Forest ───────────────────────────────────────────
IF_STRESS_N_ESTIMATORS    = 200    # trees in the market-wide IsolationForest
IF_STRESS_CONTAMINATION   = 0.05   # expected anomalous fraction of training data
IF_STRESS_MIN_ROWS        = 100    # minimum aligned rows required to fit/score
IF_STRESS_VOL_WINDOW      = 20     # rolling window for VIX MA and SPY volume z-score
IF_STRESS_ALERT_THRESHOLD = 0.75   # score in [0,1] above which the alert check runs
IF_STRESS_ALERT_DAYS      = 2      # consecutive days above threshold before firing

# ── Static asset versioning ───────────────────────────────────────────────────
CSS_VERSION = "5.72"  # bump this whenever styles.css (or any versioned static/js/*.js file) changes to bust browser caches

# ── FinBERT / NLP sentiment ────────────────────────────────────────────────────
NLP_FINBERT_MAX_TOKENS     = 512   # HuggingFace token limit for ProsusAI/finbert
NLP_TEXT_TRUNCATE_CHARS    = 5000  # perf guard only — tokeniser truncation=True handles semantic cut
NLP_NEWS_FETCH_LIMIT       = 15    # headlines scored per ticker in the routine scan
NLP_CB_NEWS_FETCH_LIMIT    = 20    # headlines scored for central bank NLP alerts
NLP_CB_TONE_THRESHOLD      = 0.15  # |score| above which tone is hawkish/dovish vs neutral

# ── Sentiment chart rendering ─────────────────────────────────────────────────
SENTIMENT_CHART_FIGSIZE    = (12, 6)  # matplotlib figure size for Nextcloud alert chart
SENTIMENT_CHART_DPI        = 300      # export resolution for Nextcloud alert chart
