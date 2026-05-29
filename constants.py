# constants.py
# Single source of truth for ML model parameters shared across the engine,
# templates, and documentation. Change here; everything else stays in sync.

PREDICTION_HORIZON_DAYS    = 10   # trading days from T+1 entry to T+10 exit
PREDICTION_RETURN_THRESHOLD = 0.03  # 3% return required for a positive label
