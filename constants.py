# constants.py
# Single source of truth for every threshold shared across Python modules and
# templates. Change a value here; all callers update automatically.

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
