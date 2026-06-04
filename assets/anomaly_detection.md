# Isolation Forest Anomaly Detection — Technical Documentation

**Project:** Stock Analysis Quantitative Trading Terminal  
**Engine:** `anomaly_engine.py`  
**Model Files:** `data/anomaly_models/{ticker}.joblib`  
**Last Updated:** 2026-06-04  

---

## Table of Contents

1. [Overview](#1-overview)
2. [Why Isolation Forest](#2-why-isolation-forest)
3. [Feature Vector](#3-feature-vector)
4. [Model Architecture](#4-model-architecture)
5. [Score Normalisation](#5-score-normalisation)
6. [Training Pipeline](#6-training-pipeline)
7. [Historical Backfill](#7-historical-backfill)
8. [Intraday Scoring](#8-intraday-scoring)
9. [Alert Integration](#9-alert-integration)
10. [Visualisation](#10-visualisation)
11. [Settings & Configuration](#11-settings--configuration)
12. [Scheduler](#12-scheduler)
13. [Database Schema](#13-database-schema)
14. [Known Limitations](#14-known-limitations)

---

## 1. Overview

The anomaly detection engine is an **unsupervised early-warning layer** that sits alongside the crash detection engine (`crash_engine.py`) and the ML ensemble (`ai_prediction_engine.py`). Its role is distinct from both:

| Engine | Question it answers |
|--------|---------------------|
| `crash_engine.py` | *Has a threshold already been breached?* (reactive, rule-based) |
| `ai_prediction_engine.py` | *Is a >3% return likely over the next 10 days?* (predictive, supervised) |
| `anomaly_engine.py` | *Is the stock behaving in a statistically unusual way right now?* (early warning, unsupervised) |

The key design principle is multi-dimensional detection. Traditional risk engines fire on a single threshold — for example, "volume > 1.5× rolling mean". The anomaly engine detects compound states that are individually unremarkable but statistically unusual in combination: a volume spike alongside an RSI divergence alongside an SMA break, for instance. These compound signals frequently precede significant price moves before any single-dimension threshold is reached.

---

## 2. Why Isolation Forest

The Isolation Forest algorithm was chosen over alternatives for the following reasons:

**vs. rolling standard deviation (current crash_engine approach):**  
Rolling std is univariate. It cannot detect multi-dimensional anomalies. Isolation Forest operates natively in N-dimensional space.

**vs. One-Class SVM:**  
One-Class SVM is computationally expensive at training time and requires careful kernel and parameter tuning. Isolation Forest trains in `O(n log n)` time and is robust to hyperparameter choice.

**vs. DBSCAN / clustering:**  
Clustering methods require a density assumption and do not produce a continuous anomaly score. Isolation Forest produces a real-valued score for each observation that can be normalised, thresholded, and stored.

**vs. Autoencoder (neural network):**  
Autoencoders require substantially more data (thousands of rows) for stable training. With ~250 trading days per ticker, a tree-based method is more robust.

**Key properties exploited:**  
- No assumption about the distribution of returns (financial returns are leptokurtic/fat-tailed — a normal assumption is inappropriate)
- Scale-invariant (tree-based, no StandardScaler required)
- Per-ticker models: each model learns what *normal* looks like for that specific stock, not the market in general
- Contamination parameter (5%) defines the expected fraction of anomalous training-set observations, used to set the decision boundary

---

## 3. Feature Vector

Each observation is a 6-dimensional vector. The features are computed identically during training (from Parquet OHLCV history) and during live intraday scoring (from live data + `quant_signals` metadata).

| # | Feature | Formula | Source (training) | Source (live) |
|---|---------|---------|------------------|---------------|
| 1 | `volume_ratio` | `Volume / rolling_20_mean(Volume)` | Parquet `Volume` column | `df_intraday['Volume'].sum() / df_hist['Volume'].tail(20).mean()` |
| 2 | `rsi_14` | Wilder RSI, 14-period | `ta.momentum.RSIIndicator` on Parquet Close | `asset_meta['rsi_14']` from `quant_signals` |
| 3 | `daily_return_pct` | `(Close_t - Close_{t-1}) / Close_{t-1} × 100` | Parquet Close pct_change | `(current_price - df_hist['Close'].iloc[-1]) / df_hist['Close'].iloc[-1] × 100` |
| 4 | `sma50_dist_pct` | `(Close - SMA50) / SMA50 × 100` | `ta.trend.SMAIndicator`, window=50 | `(current_price - asset_meta['sma_50']) / asset_meta['sma_50'] × 100` |
| 5 | `hist_vol_20` | `std(log_returns, 20) × √252` | Rolling log-return std, annualised | `asset_meta['hist_vol_20']` from `quant_signals` |
| 6 | `beta` | Clamped to `[0.5, 2.0]` | `clamp_beta(beta)` from `stock_signals` | `clamp_beta(asset_meta['beta'])` |

**Feature design rationale:**

- `volume_ratio` captures institutional flow — heavy volume during price flat or declining is a distribution signal.
- `rsi_14` captures momentum divergence from price. RSI at 80 during a price spike is different from RSI at 80 during a price decline.
- `daily_return_pct` is the raw price shock magnitude.
- `sma50_dist_pct` captures distance from the institutional reference level. A -5% distance (well below SMA50) combined with high volume is structurally different from the same volume at SMA50.
- `hist_vol_20` provides the volatility regime context. A 5% daily return in a low-vol regime is more anomalous than the same move in a high-vol regime.
- `beta` normalises market sensitivity. High-beta stocks naturally exhibit larger moves; without beta, a 2× market-beta stock would always score more anomalous than a 0.5× stock during macro events.

**Missing value handling:**  
If any live feature is unavailable (e.g. `rsi_14` is `None` because the ticker was added before the first overnight quant scan), the following neutral defaults are used:

| Feature | Default |
|---------|---------|
| `rsi_14` | 50.0 (neutral midpoint) |
| `sma50_dist_pct` | 0.0 (at SMA) |
| `hist_vol_20` | 0.2 (20% annualised, typical for liquid US equities) |

---

## 4. Model Architecture

```
IsolationForest(
    n_estimators    = 100,
    contamination   = 0.05,
    random_state    = 42,
    n_jobs          = -1,
)
```

- **`n_estimators = 100`**: 100 isolation trees. Sufficient for stable anomaly scores on ~250-row datasets. Higher values show diminishing returns beyond 100 trees.
- **`contamination = 0.05`**: The algorithm expects 5% of training observations to be anomalous. This is a soft prior — it sets the decision boundary, not a hard label. For financial daily data, 5% ≈ 12 anomalous days per year, which is empirically reasonable (earnings, macro shocks, halts).
- **`random_state = 42`**: Ensures reproducible training output. Models trained on the same data always produce the same scores.
- **`n_jobs = -1`**: Parallelises tree construction across all available CPU cores.

Each ticker has its own independent model. There is no cross-ticker or cross-sector model. This is intentional: a 3% daily move is anomalous for a utility stock but unremarkable for a speculative small-cap. Per-ticker models capture the correct baseline.

**Persistence format:**  
Each model is saved as a dict via `joblib.dump`:
```python
{
    'model':     IsolationForest,   # fitted model object
    'score_min': float,             # min decision_function score over training set
    'score_max': float,             # max decision_function score over training set
}
```
The `score_min` and `score_max` are required for normalisation (see Section 5) and must be computed at training time, not inferred from live data.

---

## 5. Score Normalisation

`IsolationForest.decision_function()` returns a raw score where **negative = anomalous** and **~0 or positive = normal**. The scale is unbounded and non-intuitive.

To produce a user-readable `[0.0, 1.0]` score where **0 = normal** and **1 = maximally anomalous**, the following normalisation is applied:

```python
# At training time:
raw_scores = model.decision_function(X_train)
score_min = raw_scores.min()   # most anomalous training point
score_max = raw_scores.max()   # most normal training point

# At inference time:
raw = model.decision_function(X_live)[0]
anomaly_score = 1.0 - (raw - score_min) / (score_max - score_min)
anomaly_score = max(0.0, min(1.0, anomaly_score))  # clamp to [0, 1]
```

**Why training-set min/max, not live-data normalisation:**  
Normalising against a single live point has no statistical meaning. The training-set range anchors the scale to the model's learned distribution. A live observation more extreme than anything in training is clamped to 1.0.

**Edge case:** If `score_max == score_min` (degenerate training set — effectively impossible with 50+ rows and 100 trees), `score()` returns `None` and the orchestrator skips that ticker silently.

---

## 6. Training Pipeline

**Entry point:** `AnomalyEngine.train_all(tickers, parquet_dir)`  
**Triggered by:** `run_anomaly_training_job()` in `scheduler_engine.py` (nightly 18:30) or the "Train Models Now" button in Settings → Machine Learning & AI Engine.

```
For each ticker:
  1. Load OHLCV from data/historical/{ticker}.parquet
  2. Validate: must have Open, High, Low, Close, Volume columns
  3. Compute 6-feature matrix (rolling features require leading rows → NaN-drop)
  4. Validate: must have ≥ 50 clean rows after NaN-drop
  5. Fit IsolationForest on feature matrix
  6. Compute score_min, score_max over training set
  7. Save {model, score_min, score_max} → data/anomaly_models/{ticker}.joblib
```

**Minimum data requirement:** 50 clean rows. This ensures:
- RSI (14-period) has stabilised
- Volume ratio rolling mean (20-period) has converged
- Historical volatility (20-period) is meaningful
- SMA50 has enough data points (50 periods)

In practice, most portfolio tickers have 250–500+ rows. New tickers added to the portfolio will produce their first model on the first training run after accumulating 50 days of quant scan history.

**Tickers skipped at training:**  
- No Parquet file in `data/historical/`
- Fewer than 50 clean rows after NaN-drop
- Missing required OHLCV columns
- Beta is `None` in `stock_signals` (model is written but beta defaults to 1.0 via `clamp_beta`)

---

## 7. Historical Backfill

**Entry point:** `AnomalyEngine.backfill_all(tickers, parquet_dir)`  
**Triggered by:** Automatically after `train_all` in `run_anomaly_training_job()`

The backfill scores all existing `quant_signals` rows for trained tickers and writes the `anomaly_score` column. Without this, the stock detail chart would show no data until the next live intraday scan.

```
For each ticker with a trained model:
  1. Fetch beta from stock_signals (single bulk query for all tickers)
  2. Load Parquet, compute full feature matrix
  3. Call model.decision_function(X_all) — vectorised, one call for all rows
  4. Normalise each score
  5. UPDATE quant_signals SET anomaly_score = ? WHERE ticker = ? AND date = ?
     (matched by date string, only updates rows that exist)
  6. Commit after each ticker
```

**Performance:** On a 120-ticker portfolio with ~309 rows per ticker (≈37,000 rows total), backfill completes in approximately 3–5 seconds. The vectorised `decision_function` call processes all rows for a ticker simultaneously, so there is no Python-level per-row loop in the scoring step.

---

## 8. Intraday Scoring

**Called from:** `IntradayOrchestrator._run()` per-ticker loop (every 5–10 minutes during market hours)  
**Controlled by:** `NOTIFICATIONS.ANOMALY_ALERTS.ENABLED` in `config.json`

```
For each portfolio ticker (if anomaly_enabled and Volume available):
  1. Compute live feature vector (6 values from df_hist + df_intraday + asset_meta)
  2. Call AnomalyEngine.score(ticker, feature_vector)
     → Returns float [0,1] or None if no model
  3. UPDATE quant_signals SET anomaly_score = ? WHERE ticker = ? AND date = today
  4. If anomaly_score > THRESHOLD:
     a. If crash alert also fired this scan:
        → Append "🤖 Anomaly Score: X.XX (Isolation Forest)" to crash alert reason
     b. Otherwise:
        → Fire standalone ⚠️ ANOMALY ALERT via Nextcloud (subject to dedup gate)
```

**Deduplication:** Anomaly alerts pass through `_evaluate_alert_gate("Anomaly", ...)`, which uses the same cooldown/fingerprint/hysteresis logic as Crash and Moonshot alerts. The `alert_state` table stores the dedup state for the `"Anomaly"` engine key.

---

## 9. Alert Integration

### Standalone Pre-Alert (score > threshold, no crash fired)

```
⚠️ ANOMALY ALERT: {ticker} ⚠️

Price: {formatted_price}
Anomaly Score: X.XX / 1.00 (Threshold: 0.70)
Trigger: Isolation Forest detected a multi-dimensional statistical outlier.

📊 Context:
• AI Confidence: {ml_confidence}
• Downside Log-Return VaR: {var_95}
• NLP Sentiment: {sentiment}

🔗 [View Breakdown]({url})
```

### Crash Alert Corroboration (score > threshold AND crash fired)

The crash alert reason string is extended with:
```
🤖 Anomaly Score: X.XX (Isolation Forest)
```
This appears in the existing `**Trigger:**` block of the crash alert message.

### Alert Deduplication Settings

Configured under `NOTIFICATIONS.ANOMALY_ALERTS` in `config.json`:

| Key | Default | Description |
|-----|---------|-------------|
| `ENABLED` | `true` | Master switch for anomaly scoring and alerts |
| `THRESHOLD` | `0.70` | Score above which alerts fire |
| `COOLDOWN_MINUTES` | `120` | Minimum time between repeat alerts for the same ticker |
| `RETRIGGER_PERCENT` | `2.0` | Price must worsen by this % after cooldown for re-alert |
| `REARM_PERCENT` | `3.0` | Price must recover by this % before the alert can re-arm |

---

## 10. Visualisation

**Location:** Stock detail page, below the Macro Trend chart  
**Function:** `create_anomaly_score_chart(df, ticker, threshold)` in `visuals.py`

The chart is a two-panel Plotly dark-theme interactive:

**Top panel — Anomaly Score:**
- Single continuous grey line connecting all 90 data points
- Per-point markers: **cyan** (score ≤ threshold), **red** (score > threshold)
- Subtle red shaded band filling the alert zone above the threshold
- Gold dotted horizontal threshold line with annotation
- Y-axis: fixed `[0, 1]`

**Bottom panel — Close Price:**
- White line of the daily closing price
- Shared x-axis with the top panel for temporal alignment

**Data source:**
```sql
SELECT date, anomaly_score, close_price
FROM quant_signals
WHERE ticker = ? AND anomaly_score IS NOT NULL
ORDER BY date DESC LIMIT 90
```
Results are sorted ascending after fetch for correct chronological display.

---

## 11. Settings & Configuration

**Settings page location:** Settings → 🧠 Machine Learning & AI Engine → 🔬 Isolation Forest Anomaly Detection

Controls:
- Enable/disable toggle (`ANOMALY_ALERTS.ENABLED`)
- Alert threshold input (0–1, default 0.70)
- **▶️ Train Models Now** button — triggers `POST /api/ml/trigger-anomaly-training`, which runs `train_all` + `backfill_all` as a FastAPI background task

**Diagnostics panel:** Settings → System Diagnostics → ML Artifacts & System Ledgers  
Shows: `Isolation Forest Models — N Models` (count of `.joblib` files in `data/anomaly_models/`). Displayed in amber if zero.

**Scheduler diagnostics table:** Shows `Isolation Forest Anomaly Training` row with `18:30 MON-FRI` schedule and last-run timestamp.

---

## 12. Scheduler

| Job | Function | Schedule | Config key |
|-----|----------|----------|-----------|
| Anomaly Training + Backfill | `run_anomaly_training_job()` | Mon–Fri 18:30 | `NOTIFICATIONS.ANOMALY_ALERTS.ENABLED` |

**Timing rationale:**  
18:30 UTC is after the overnight `quant_analysis_job` at 18:00 (which writes fresh `quant_signals` rows) and before the `xray_risk_cache_job` at 19:00. This ensures models are trained on the most recent daily data.

---

## 13. Database Schema

**Column added to `quant_signals`:**

```sql
anomaly_score REAL  -- Isolation Forest normalised score [0.0, 1.0], NULL until first training run
```

Primary key is `(ticker, date)` — unchanged. The column is added by `migrate_db()` in `database.py` on app startup if absent.

**Model files (not database):**

```
data/anomaly_models/
  AAPL.joblib
  NVDA.joblib
  MU.joblib
  ...
```

Each `.joblib` contains `{'model': IsolationForest, 'score_min': float, 'score_max': float}`.

**Alert state (shared with Crash and Moonshot):**

```sql
SELECT * FROM alert_state WHERE engine = 'Anomaly';
```

No schema changes required — the existing `alert_state` table handles the `"Anomaly"` engine key natively.

---

## 14. Known Limitations

**1. No directional prediction**  
The anomaly score flags structural divergence but does not predict whether the subsequent move will be up or down. A score of 0.90 is equally consistent with an imminent crash or a breakout.

**2. Per-ticker training requires sufficient history**  
Tickers with fewer than 50 trading days of OHLCV data in `data/historical/` are skipped. New tickers added to the portfolio will not have anomaly models until they accumulate sufficient history and the next training run executes.

**3. Live scoring uses yesterday's RSI and SMA50**  
`rsi_14` and `sma_50` in `asset_meta` come from the latest `quant_signals` row, which is written by the overnight scan (T-1 close). The live `daily_return_pct` and `volume_ratio` use real-time data; the RSI and SMA50 lag by one session. This is acceptable for a daily-resolution anomaly signal but means intraday RSI divergences are not captured.

**4. Static contamination parameter**  
`contamination=0.05` is fixed. For tickers that have been through extraordinary regimes (e.g. a meme-stock episode during the training window), 5% may under- or over-represent the true anomaly rate. Retraining after a regime normalises the model.

**5. No cross-asset correlation signal**  
The six features are all single-ticker. Correlated anomalies across a sector (e.g. all semiconductor stocks becoming anomalous simultaneously) are not aggregated into a systemic signal. The `regime_engine.py` and `macro_data_engine.py` handle sector/macro-level signals separately.
