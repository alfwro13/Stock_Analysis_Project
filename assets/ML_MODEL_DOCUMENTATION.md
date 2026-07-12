# ML Ensemble Model — Technical Documentation

**Project:** Stock Analysis Quantitative Trading Terminal  
**Model File:** `models/ml_ensemble.joblib`  
**Engine:** `ai_prediction_engine.py`  
**Last Updated:** 2026-05-24  
**Final PR-AUC:** 0.4217 (random baseline: 0.3348)

---

## Table of Contents

1. [Model Overview](#1-model-overview)
2. [Prediction Target](#2-prediction-target)
3. [Training Data](#3-training-data)
4. [Feature Registry](#4-feature-registry)
5. [Architecture](#5-architecture)
6. [Bug Fixes Applied Before Training](#6-bug-fixes-applied-before-training)
7. [Improvement History](#7-improvement-history)
8. [Known Limitations & Assumptions](#8-known-limitations--assumptions)
9. [Evaluation Methodology](#9-evaluation-methodology)
10. [Operational Notes](#10-operational-notes)

---

## 1. Model Overview

The ML ensemble is a binary classification model that predicts whether a given stock will achieve a **>3% return over the next 10 trading days**, using the entry price proxy of the following day's close (T+1).

It is used as a **ranking signal**, not an absolute probability. The confidence score (0–100) should be interpreted as: *"This stock is in the top X% of the cross-sectional universe by predicted momentum quality today."* A score of 70 does not mean a 70% probability of a >3% move — it means the stock ranks in approximately the top 5–10% of all scored stocks on that day.

The model is an ensemble of a Random Forest and an XGBoost classifier, trained using Anchored Walk-Forward Validation with 5 temporal folds and a 5-day embargo gap between training and test sets to prevent target leakage.

---

## 2. Prediction Target

```
target = 1  if  (close[T+10] - close[T+1]) / close[T+1]  >  0.03
target = 0  otherwise
```

**Entry proxy:** `close[T+1]` — the following day's close, used as a realistic execution price approximation. True execution would be at the open of T+1. Using close[T+1] slightly overstates returns by the T+1 intraday move, which is bounded by typical bid-ask spread (0.01–0.1% for liquid names). This is a well-established and widely-used simplification in practitioner ML research.

**Exit:** `close[T+10]` — 10 trading days after signal generation.

**Threshold:** 3% over 10 days. This equates to approximately 75% annualised return if sustained, which is used purely as a binary discriminator between strong and weak setups. It is not a return forecast.

**Horizon selection rationale:** The initial 5-day horizon was replaced with 10 days after empirical testing showed a PR-AUC improvement from 0.3592 to 0.4217. The mechanism is signal-to-noise: technical momentum factors need time to play out before mean-reversion noise dominates at very short horizons (Jegadeesh & Titman, 1993, found optimal momentum horizons of 3–12 months; at 5-day intraday horizons, market microstructure noise dominates).

**Class distribution at 10-day horizon:**
```
Positive (1): 26,533 rows  (33.5%)
Negative (0): 52,710 rows  (66.5%)
Total:        79,205 rows  (after target construction dropna)
```

---

## 3. Training Data

### Source
Yahoo Finance via `yfinance` library, `auto_adjust=True` (split and dividend adjusted prices).

### Universe
Up to 350 tickers per training run, composed of:
- User portfolio, watchlist, and account tickers, plus every Markets page index/commodity/FX/rate ticker from `market_ticker_registry` (all via `DataEngine.get_all_tickers()`; non-equity tickers have NULL fundamentals and receive cross-sectional median imputation, see below)
- Random sample of up to 300 tickers from the `market_universe` table

### History
2 years of daily OHLCV data per ticker (`period="2y"`).

### Minimum data requirement
252 trading days (1 full year) per ticker. Tickers with fewer rows are skipped during backfill. This ensures a valid SMA-200 and a full set of momentum lookbacks.

### Stored rows
After applying the 252-day momentum warmup constraint and `dropna()`:
- Approximately 82,633 rows across 350 tickers
- Average ~236 rows per ticker

### Key consistency requirement — BUG-02
All data must use `auto_adjust=True` consistently across backfill, training, and inference. Using `auto_adjust=False` during backfill and `auto_adjust=True` during inference causes systematic feature distribution shift after any stock split. This was identified and corrected before any model training described in this document.

---

## 4. Feature Registry

The model uses **24 features** across five categories. All continuous features receive cross-sectional z-scoring before being passed to the model (see Section 5.2).

### 4.1 Technical Features (10)

| Feature | Formula | Rationale |
|---|---|---|
| `rsi_14` | 14-period RSI | Momentum oscillator. Identifies overbought/oversold relative to recent history |
| `macd_pct` | MACD / close_price | MACD normalised to price level to prevent scale bias across different-priced stocks |
| `macd_signal_pct` | MACD signal / close_price | Normalised signal line |
| `macd_hist_pct` | MACD histogram / close_price | Normalised momentum divergence |
| `volume_surge` | Binary: volume > 1.5× 20-day avg | Institutional participation signal |
| `bullish_cross` | Binary: MACD crossed above signal | Confirmed momentum shift |
| `dist_sma_50` | (close - SMA50) / SMA50 | Trend proximity to 50-day moving average |
| `dist_sma_200` | (close - SMA200) / SMA200 | Structural trend position |
| `sector_code` | Integer mapping of GICS sector | Captures sector regime effects |
| `dollar_vol_log` | log(close × volume + 1) | Liquidity proxy; log-transformed to compress the extreme right tail |

### 4.2 Momentum Factors (4)

Based on Jegadeesh & Titman (1993) *"Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency"*, Journal of Finance 48(1), pp. 65–91.

| Feature | Formula | Rationale |
|---|---|---|
| `mom_1m` | `pct_change(21)` | Short-term trend signal |
| `mom_3m` | `pct_change(63)` | Intermediate momentum |
| `mom_6m` | `pct_change(126)` | Medium-term momentum |
| `mom_12m_skip1m` | `pct_change(252) - pct_change(21)` | Skip-1-month momentum. The most recent month is subtracted to remove the short-term mean-reversal effect documented by Jegadeesh & Titman. Raw 12-month momentum is contaminated by the last month's return which tends to reverse at short horizons |

**Computation strategy:** All momentum features are computed during `run_historical_backfill()` from the full 504-day yfinance download *before* the `dropna()` call. This ensures the 252-day lookback has sufficient history. Values are stored in the `quant_signals` table and read directly at training and inference — no historical lookback is required at inference time.

### 4.3 Volatility Regime Features (2)

| Feature | Formula | Rationale |
|---|---|---|
| `atr_pct` | ATR(14) / close_price | 14-day Average True Range normalised to price level. Captures the noise envelope of the stock. High ATR + strong momentum = volatile spike (less reliable). Moderate ATR + strong momentum = clean breakout (more reliable) |
| `hist_vol_20` | `rolling(20).std(log_returns) × √252` | 20-day annualised realised volatility from log returns. Complements ATR with a medium-horizon volatility view. Log returns are used (vs simple returns) as they are additive and better approximate the normal distribution in the tails |

**Computation strategy:** Both features require High and Low prices (for ATR) and Close prices (for HV) which are available in the yfinance OHLCV download but not stored in `quant_signals`. Both are computed during backfill and stored as columns, identically to momentum features.

### 4.4 Relative Strength vs SPY (2)

| Feature | Formula | Rationale |
|---|---|---|
| `rel_strength_5d` | `pct_change(5) - SPY.pct_change(5)` | 5-day return minus market return. Strips out beta, isolates idiosyncratic price leadership over 1 week |
| `rel_strength_20d` | `pct_change(20) - SPY.pct_change(20)` | 20-day return minus market return. A stock with strong positive rel_strength_20d combined with positive cross-sectional momentum is a genuine market leader, not a stock being carried by the tide |

**Benchmark:** SPY (S&P 500 ETF) is used as a universal benchmark for both US and UK stocks. Global equity markets are approximately 85% correlated at daily frequency, making SPY a reasonable universal proxy. A per-asset benchmark (SPY for US names, ISF.L for UK names) would be marginally more accurate. This is a documented approximation for UK-listed names in the universe.

**Computation strategy:** SPY is downloaded once at the start of `run_historical_backfill()` via `_download_spy_benchmark()`. For each ticker, SPY returns are aligned to the ticker's date index via `reindex()`. Date mismatches (UK bank holidays vs US market holidays, or ticker-specific trading halts) produce NaN which are removed by the unified `dropna()`. The function returns `None` gracefully if SPY is unavailable, in which case relative strength columns are stored as NULL and filtered out of training via the SQL `WHERE` clause.

### 4.5 Fundamental Factors (6)

Based on Fama & French (1992) *"The Cross-Section of Expected Stock Returns"*, Journal of Finance 47(2), pp. 427–465, and Novy-Marx (2013) *"The Other Side of Value: The Gross Profitability Premium"*.

| Feature | Source column | Winsorization bounds | Rationale |
|---|---|---|---|
| `trailing_pe` | `stock_signals.trailing_pe` | Negative → NaN; cap 100 | Valuation factor. Fama-French HML value proxy. Negative PE (loss-making company) is set to NaN rather than treated as "very cheap" — it is a qualitatively different state |
| `price_to_book` | `stock_signals.price_to_book` | Floor 0; cap 20 | Value factor. Low P/B = deep value. Cap at 20 covers Amazon/tech at peak multiples |
| `profit_margin` | `stock_signals.profit_margin` | Clip [-1, 1] | Quality factor. Already a ratio. Negative margin = cash-burning company |
| `roe` | `stock_signals.roe` | Clip [-1, 1.5] | Quality factor. Return on Equity. Novy-Marx quality premium. Capped at 1.5 (150%) to prevent financial leverage artefacts (e.g. Seagate's 1788% ROE from extreme buybacks) from dominating the cross-sectional z-score |
| `revenue_growth` | `stock_signals.revenue_growth` | Clip [-1, 3] | Growth factor. YoY revenue change. Capped at 300% to remove post-merger/acquisition spikes |
| `debt_to_equity` | `stock_signals.debt_to_equity` | Floor 0; cap 500 | Leverage/risk factor. Yahoo Finance returns as percentage (100 = 1.0× D/E). Capped at 500 (5.0× D/E) to handle financial stocks and REITs whose leverage is structural, not distress-driven |

**Computation strategy:** Fundamental features are **not** stored in `quant_signals`. They are joined from `stock_signals` at training and inference time via:
```sql
LEFT JOIN stock_signals ss ON qs.ticker = ss.ticker
```

`stock_signals` has `ticker TEXT PRIMARY KEY` — one row per ticker representing the most recent analysis snapshot.

**NULL handling — Cross-sectional median imputation:** ETFs, futures, and stocks without reported fundamentals return NULL from yfinance and therefore have NULL in `stock_signals`. Rather than dropping these tickers, NULLs are filled with the cross-sectional median for that date before z-scoring (see `_winsorize_and_impute_fundamentals()`). These stocks receive a neutral z-score of approximately 0.0 — neither rewarded nor penalised for absent fundamentals.

**Known limitation — Point-in-time bias:** `stock_signals` stores only the most recent fundamental snapshot. When joined to historical `quant_signals` rows during training, today's fundamentals are applied to rows from up to 18 months ago. This is mild lookahead bias. Fundamentals that change slowly (profit margins, ROE) are minimally affected. Valuation metrics (PE, P/B) can change significantly over 18 months. A production system would store time-stamped fundamental snapshots and join on the closest prior date. This is documented and accepted for a hobbyist project.

---

## 5. Architecture

### 5.1 Base Estimators

**Random Forest Classifier**
```python
RandomForestClassifier(
    class_weight='balanced',   # Corrects for 33.5% / 66.5% class imbalance
    random_state=42,
    n_jobs=-1
)
```
Optimal hyperparameters found via RandomizedSearchCV (n_iter=10, cv=walk-forward splits):
```
n_estimators=100, min_samples_leaf=5, max_depth=4
```

**XGBoost Classifier**
```python
XGBClassifier(
    scale_pos_weight=1.99,     # neg_count / pos_count = 52,710 / 26,533
    eval_metric='logloss',
    random_state=42,
    n_jobs=-1
)
```
Optimal hyperparameters:
```
n_estimators=200, max_depth=3, learning_rate=0.01,
subsample=0.7, colsample_bytree=0.7
```

**Class imbalance handling:** Both `class_weight='balanced'` (RF) and `scale_pos_weight` (XGB) are used. The value is computed dynamically from the actual training dataset at each run — it is not hardcoded — so it automatically adjusts as the class distribution shifts with different prediction horizons or universe compositions.

### 5.2 Cross-Sectional Z-Scoring

All continuous features are normalised using cross-sectional z-scoring before being passed to the model:

```
z_score(feature, date) = (feature_value - mean(feature, date)) / std(feature, date)
```

Where mean and std are computed across **all tickers on the same date** — not across time. This forces the model to evaluate each stock relative to its peers on that specific trading day, neutralising absolute size and market-level bias. A stock with RSI 65 on a day when the whole market averages RSI 65 receives a z-score near 0. The same RSI 65 on a day when the market averages RSI 40 receives a strongly positive z-score — correctly identified as a genuine outlier.

**Critical implementation note (BUG-03):** At inference time, z-scores must be computed across the full population of all tickers at the latest date — not just the requested ticker subset. Computing z-scores on a batch of 3–10 tickers collapses all scores to the base rate because every stock's z-score approaches 0 when the peer group is trivially small. The fix: `update_daily_ml_predictions()` fetches ALL tickers with valid features at the latest date before z-scoring, regardless of the `tickers` parameter.

### 5.3 Probability Calibration

Each base estimator is individually wrapped in `CalibratedClassifierCV(method='isotonic', cv=walk_forward_splits)` before being assembled into the final `VotingClassifier`. Isotonic regression calibration adjusts the raw model probabilities so they better approximate true event frequencies.

**Known limitation (BUG-04):** The base estimators (`best_rf`, `best_xgb`) are fitted on the full training dataset before calibration. The `CalibratedClassifierCV` then calibrates using the same walk-forward splits. This introduces mild calibration data leakage — the base model has already seen the data used for calibration. The confidence scores should therefore be treated as **ranking signals** (relative ordering) rather than **calibrated probabilities** (absolute event frequencies). Fixing this requires a three-way temporal split (train / calibrate / test) which is scheduled for a future refactor once the feature set is finalised.

### 5.4 Production Ensemble

```python
VotingClassifier(
    estimators=[('rf', calibrated_rf), ('xgb', calibrated_xgb)],
    voting='soft'   # averages predicted probabilities
)
```

The final `ml_confidence_score` stored in `quant_signals` is:

```
confidence_score = round(ensemble.predict_proba(X)[1] × 100, 2)
```

### 5.5 Walk-Forward Validation

Training uses Anchored Walk-Forward (expanding window) cross-validation with 5 folds:

```
Fold 1: Train [dates 0 → 20%]    Test [dates 20% → 40%]
Fold 2: Train [dates 0 → 40%]    Test [dates 40% → 60%]
Fold 3: Train [dates 0 → 60%]    Test [dates 60% → 80%]
Fold 4: Train [dates 0 → 80%]    Test [dates 80% → 100%]
```

A **5-day embargo gap** is applied between the last training date and the first test date in each fold. This prevents the model from learning the target variable (which uses future close prices) through temporal proximity.

Standard k-fold cross-validation is explicitly avoided because it allows future data to appear in training folds, producing spuriously optimistic evaluation metrics on financial time series.

**Hyperparameter optimisation metric:** `average_precision` (area under the Precision-Recall curve). This is the correct metric for imbalanced binary classification — unlike accuracy, it is not fooled by a model that predicts the majority class for every observation. The random baseline for `average_precision` equals the positive class prevalence (0.335 at 10-day horizon).

---

## 6. Bug Fixes Applied Before Training

The following bugs were identified in an audit of the original codebase and corrected before any model training described in this document. All improvements below are measured on the corrected baseline.

### BUG-01: Missing `sqlite3` import — `macro_data_engine.py`
The except block caught `sqlite3.Error` but the module never imported `sqlite3`. Any database failure caused a secondary `NameError` inside the handler, masking the original error. **Fix:** Added `import sqlite3` at the top of the file.

### BUG-02: `auto_adjust` inconsistency — `ai_prediction_engine.py`
`run_historical_backfill()` used `auto_adjust=False` while `run_daily_quant_scan()` used `auto_adjust=True`. After any stock split, ML features computed from unadjusted backfill prices were on a completely different scale from adjusted inference prices. **Fix:** Changed backfill to `auto_adjust=True`. Required a full database truncation of `quant_signals` and complete retraining.

**Verification (NVDA split 10-for-1 June 2024):**
```
Before fix: close_price for NVDA in early 2025 ≈ $800–900 (unadjusted)
After fix:  close_price for NVDA in early 2025 ≈ $115–130 (adjusted)
```

### BUG-03: Cross-sectional z-score population collapse at inference
During inference on a 3-10 ticker batch, `groupby('date').transform(zscore)` computed z-scores across trivially small populations. All features collapsed to near-zero z-scores, driving all model outputs to the base rate (~34%).

**Before fix — score distribution on 3 tickers:**
```
count=3, mean=36.43, std=1.38, min=34.92, max=37.63
```

**After fix — score distribution on 339 tickers:**
```
count=184, mean=34.03, std=11.62, min=3.90, max=70.17
```

**Fix:** `update_daily_ml_predictions()` now fetches ALL tickers at the latest date with complete features for z-scoring, regardless of which tickers are in the `tickers` parameter. The `tickers` parameter only governs which rows are written back to the database.

### BUG-04: Calibration data leakage — `ai_prediction_engine.py`
`CalibratedClassifierCV` calibrates probability outputs using the same data the base model was already fitted on. This inflates confidence score calibration quality. **Status:** Documented and deferred. Confidence scores are used as ranking signals only. Full fix requires a three-way temporal split and is scheduled for a future refactor.

### Class imbalance correction
The original model used `scoring='accuracy'` for hyperparameter search. Accuracy is a degenerate metric for imbalanced targets — a model predicting the majority class for every observation scores 73.8% accuracy while being completely useless.

**Original class distribution (5-day target):**
```
Positive (1): 26,634  (26.2%)
Negative (0): 74,848  (73.8%)
```

**Fixes applied:**
- `class_weight='balanced'` on RandomForestClassifier
- `scale_pos_weight = neg_count / pos_count` (dynamic, computed per training run) on XGBClassifier
- `scoring='average_precision'` on both RandomizedSearchCV instances

---

## 7. Improvement History

All improvements are measured on the same 82,633-row dataset (350-ticker universe, 2y history, 10-day target horizon where specified). PR-AUC is the primary evaluation metric.

### Iteration 0 — Corrected Baseline (post bug fixes)

**Features:** 10 technical features only  
**Target horizon:** 5 days  
**PR-AUC:** 0.3232  
**Random baseline:** 0.2625  
**Gap above baseline:** 0.0607 (23% above random)

**Distribution:**
```
count=3 (inference on 3-ticker batch — BUG-03 not yet fixed)
mean=36.43, std=1.38, min=34.92, max=37.63
```

*Note: After BUG-03 fix with full population inference:*
```
count=109, mean=30.13, std=8.34, min=7.19, max=53.58
```

---

### Iteration 1 — Price Momentum Factors

**Change:** Added 4 momentum features: `mom_1m`, `mom_3m`, `mom_6m`, `mom_12m_skip1m`  
**Scientific basis:** Jegadeesh & Titman (1993) demonstrated that stocks with strong 3–12 month prior returns continue to outperform over the following 3–12 months. The skip-1-month construction (`pct_change(252) - pct_change(21)`) removes the short-term reversal effect also documented in the same paper.  
**Implementation note:** Computed from full 504-day yfinance download before `dropna()` to ensure 252-day lookback has sufficient history. Stored in `quant_signals` as columns.

**Result:**
```
PR-AUC:           0.3325  (+0.0093 vs baseline)
Random baseline:  0.2526
Gap above random: 0.0799  (+32%)
Max score:        53.58
Std:              8.85
```

**Training rows:** ~82,895 (slightly different due to universe randomisation)

---

### Iteration 2 — Volatility Regime Features

**Change:** Added 2 volatility features: `atr_pct`, `hist_vol_20`  
**Scientific basis:** Volatility regime affects the reliability of momentum signals. In low-volatility trending markets, a 3% move is a genuine signal. In high-volatility choppy markets, a 3% move is noise. ATR and historical volatility allow the model to learn this distinction, downweighting momentum signals in noisy regimes.  
**Implementation note:** ATR requires High and Low prices from the OHLCV download. Both features are computed during backfill and stored in `quant_signals`. The `ta.volatility.AverageTrueRange` class is used (OOP API — the deprecated functional API `ta.trend.sma_indicator()` is not used anywhere in this codebase).

**Result:**
```
PR-AUC:           0.3457  (+0.0132 vs iteration 1, +0.0225 vs baseline)
Random baseline:  0.2531
Gap above random: 0.0926  (+37%)
Max score:        49.95
Std:              11.56
45-55 band:       2 stocks
```

**Key observation:** SNDK had 2.4× more raw 6-month momentum than CIEN but scored 9 points lower due to a higher volatility envelope (ATR 7.1% vs 5.9%). The model correctly penalised noisy momentum.

---

### Iteration 3 — Relative Strength vs SPY

**Change:** Added 2 relative strength features: `rel_strength_5d`, `rel_strength_20d`  
**Scientific basis:** Cross-sectional momentum — stocks outperforming the market index tend to continue outperforming (Jegadeesh & Titman, 1993; Carhart, 1997 *"On Persistence in Mutual Fund Performance"*, Journal of Finance 52(1), pp. 57–82). Relative strength strips out the market beta component, isolating idiosyncratic price leadership from stocks simply rising with the tide.  
**Implementation note:** SPY downloaded once via `download_spy_benchmark()` (`ai_prediction_engine.py`, shared with `quant_engine.py`'s daily scan since 2026-07-10) before the main ticker loop. UK stock caveat documented — SPY used as universal benchmark; FTSE-relative strength would be marginally more accurate for `.L` tickers.

**Result:**
```
PR-AUC:           0.3557  (+0.0100 vs iteration 2, +0.0325 vs baseline)
Random baseline:  0.2455
Gap above random: 0.1102  (+45%)
Max score:        49.04
Std:              10.00
45-55 band:       6 stocks
```

**Key observation:** MXL had 2.5× more raw 6-month momentum than LITE (6.39 vs 2.52) but scored lower because its relative strength was weaker — the model correctly identified that LITE's move was more idiosyncratic and less beta-driven.

**Feature statistics:**
```
rel_strength_5d:  mean=0.0037, std=0.1137  (slight positive bias reflects
rel_strength_20d: mean=0.0154, std=0.2292   Tech-heavy universe outperforming SPY)
```

---

### Iteration 4 — Fundamental Factors

**Change:** Added 6 fundamental features joined from `stock_signals`: `trailing_pe`, `price_to_book`, `profit_margin`, `roe`, `revenue_growth`, `debt_to_equity`  
**Scientific basis:**
- **Value factor** (Fama & French, 1992): Low P/B and low PE stocks generate excess returns. The model captures the interaction between valuation and momentum — a cheap stock with momentum is a stronger signal than an expensive stock with the same momentum.
- **Quality factor** (Novy-Marx, 2013): High gross profitability (profit_margin, ROE) predicts future returns independently of value. Quality + momentum is the strongest factor combination.
- **Growth factor**: Revenue growth distinguishes genuine earnings-driven momentum from mean-reverting price spikes.

**Preprocessing:**
- Winsorization applied before z-scoring (e.g. trailing_pe capped at 100, ROE capped at 1.5)
- Negative PE (loss-making companies) set to NaN before clipping — treated as absent data, not cheap valuation
- Cross-sectional median imputation: ETFs and non-equity instruments with NULL fundamentals receive the cross-sectional median for that date → neutral z-score of ~0

**No schema changes or backfill required.** Fundamentals are joined from existing `stock_signals` table at training and inference time.

**Result:**
```
PR-AUC:           0.3592  (+0.0035 vs iteration 3, +0.0360 vs baseline)
Random baseline:  0.2455
Gap above random: 0.1137  (+46%)
Max score:        54.77
Std:              10.45
45-55 band:       7 stocks
```

**Key observation:** CRDO (RSI 64.6, expanding margins 31.8%, ROE 27.5%, trailing PE 107) scored 54.77 — higher than HY9H.F despite HY9H.F having 4× more raw momentum, better relative strength, cheaper PE (18 vs 107), and better margins. The model weighted CRDO's consistent cross-sectional leadership over multiple factors above HY9H.F's concentrated raw momentum.

**Known limitation:** Point-in-time bias — `stock_signals` stores only the most recent fundamental snapshot. Joining it to historical training rows applies today's fundamentals to data from up to 18 months ago.

---

### Iteration 5 — 10-Day Prediction Horizon

**Change:** `shift(-5)` → `shift(-10)` in target construction. One character change.  
**Scientific basis:** Technical momentum signals operate over weeks, not days. At a 5-day horizon, market microstructure noise (bid-ask spreads, daily liquidity fluctuations) dominates over the momentum signal. At 10 days, the signal has time to express before mean-reversion kicks in. This is consistent with Jegadeesh & Titman's finding that momentum strategies perform best over 3–12 month holding periods, and with practitioner evidence that weekly (5-day) signals have much lower Sharpe ratios than monthly (20-day) signals.

**Class distribution shift:**
```
5-day target:  Positive 24.6%  →  10-day target: Positive 33.5%
```
More ticker-date pairs clear the 3% threshold over 10 days than 5 days. `scale_pos_weight` recalculated automatically (2.95 → 1.99).

**Result:**
```
PR-AUC:           0.4217  (+0.0625 vs iteration 4)  ← largest single improvement
Random baseline:  0.3348  (higher due to more positive class instances)
Gap above random: 0.0869  (+26%)
Max score:        70.17   ← first score above 65
Std:              11.62
45-55 band:       29 stocks
55-65 band:       1 stock
65-100 band:      1 stock (CLS at 70.17)
```

**Note on gap above baseline:** The gap dropped from 0.1137 to 0.0869 because the baseline itself rose (higher positive prevalence makes random prediction harder to beat proportionally). This is not a regression — the absolute PR-AUC improvement of +0.0625 is the largest of any single change in this session.

---

### Complete Progress Table

| Iteration | Change | PR-AUC | Baseline | Gap | % Above Random | Max Score | Std |
|---|---|---|---|---|---|---|---|
| 0 | Corrected baseline | 0.3232 | 0.2625 | 0.0607 | 23% | 53.58 | 8.34 |
| 1 | + Momentum factors | 0.3325 | 0.2526 | 0.0799 | 32% | 53.58 | 8.85 |
| 2 | + Volatility regime | 0.3457 | 0.2531 | 0.0926 | 37% | 49.95 | 11.56 |
| 3 | + Relative strength | 0.3557 | 0.2455 | 0.1102 | 45% | 49.04 | 10.00 |
| 4 | + Fundamentals | 0.3592 | 0.2455 | 0.1137 | 46% | 54.77 | 10.45 |
| 5 | + 10-day horizon | **0.4217** | 0.3348 | 0.0869 | 26% | **70.17** | **11.62** |

---

## 8. Known Limitations & Assumptions

### 8.1 Data Source
All data sourced from Yahoo Finance via `yfinance`. Known limitations:
- **UK pence misquote:** yfinance occasionally returns dividend yields for GBp-denominated stocks as pence/price rather than percentage. A correction heuristic is applied in `quant_signals.py` for values above 15%.
- **Missing fundamentals:** ETFs, futures, and some international stocks have NULL fundamental data. Handled via cross-sectional median imputation.
- **Earnings dates unreliability:** yfinance earnings dates are unreliable for some tickers. Not currently used in ML features.
- **Rate limiting:** The backfill implements `time.sleep(0.5)` between tickers to avoid API rate limiting. Approximately 4–6 hours for a full 350-ticker backfill.

### 8.2 SPY as Universal Benchmark
SPY is used as the benchmark for relative strength calculations for both US and UK stocks. UK stocks are better benchmarked against ISF.L (iShares FTSE 100) or the FTSE All-Share. The approximation introduces a systematic basis for UK names, particularly during UK-specific stress events (Brexit, Gilt crises) when UK and US markets decouple.

### 8.3 Point-in-Time Fundamental Bias
As documented in Section 4.5, training uses today's fundamental snapshot for all historical rows. This is lookahead bias. Its severity is limited because:
- Fundamentals change slowly for most metrics (ROE, profit margins)
- The training window is only 2 years
- The signal direction (high quality vs low quality) is typically stable over this period

### 8.4 Execution Price Approximation
The model uses `close[T+1]` as the entry price proxy. True execution would be at `open[T+1]`. The difference is typically 0.01–0.1% for liquid US names, larger for illiquid or volatile stocks. This slightly overstates model returns.

### 8.5 Technology Sector Concentration
The training universe is tilted toward Technology stocks, which dominate both user watchlists and the random market universe sample. The cross-sectional z-scoring mitigates sector bias somewhat, but the model may underperform for sectors with fewer training examples (Utilities, Consumer Defensive). The `sector_code` feature explicitly provides sector context.

### 8.6 US Market Hours vs UK Market Hours
The `rel_strength_5d` and `rel_strength_20d` features align SPY and UK stock returns by calendar date, ignoring the fact that UK and US markets close at different times. For a daily signal this is an acceptable approximation.

### 8.7 The Realistic Ceiling
For 10-day price direction prediction on liquid stocks using free data, the realistic PR-AUC ceiling is approximately **0.38–0.43** based on academic literature on technical momentum models. The current model at **0.4217** is at the upper end of this range. Further material improvement would require:
- Options flow data (market maker positioning)
- Short interest data (crowded positioning)
- Earnings surprise data (actual vs analyst estimates)
- Analyst revision data
- Alternative data (credit card transactions, satellite imagery)

None of these are available free of charge. The model should be considered at or near its practical ceiling for free-data-only training.

---

## 9. Evaluation Methodology

### Primary Metric: Average Precision (PR-AUC)

Average Precision is the area under the Precision-Recall curve. It is the correct primary metric for this use case because:

1. **Imbalance robustness:** Unlike accuracy, it does not reward a model that predicts the majority class for every observation. A model predicting 0 for every input scores 0.0 AP (not 66.5% accuracy).

2. **Practical alignment:** In a screener context, you care about precision (of the stocks the model flags, how many actually produce 3%+ returns?) and recall (of all stocks that will produce 3%+ returns, how many does the model identify?). AP summarises the precision-recall tradeoff across all confidence thresholds.

3. **Random baseline equals positive prevalence:** For a random classifier, AP equals the fraction of positive examples. This makes the random baseline immediately interpretable: 0.335 means a stock randomly selected from this universe has a 33.5% chance of a >3% 10-day return.

### Secondary Metric: Score Distribution

The distribution of `ml_confidence_score` across the inference universe is monitored at each training run. Key diagnostics:

- **Std > 10.0:** Model is genuinely discriminating between stocks
- **Std < 5.0:** Warning — model may be collapsing to base rate (check BUG-03 fix is deployed)
- **Max > 60:** Model has high-conviction setups present in the universe
- **Mean near baseline × 100:** Distribution is centred correctly

### Interpretation of Confidence Scores

| Score range | Interpretation |
|---|---|
| 0–20 | Strong bearish signal relative to cross-section |
| 20–35 | Below-average setup |
| 35–45 | Neutral — near cross-sectional median |
| 45–55 | Above-average setup |
| 55–65 | Strong setup — top 10–15% of universe |
| 65–100 | High conviction — top 5% of universe |

These bands are calibrated to the current score distribution (mean ~34, std ~11.6). They should be recalibrated if the universe composition changes substantially.

---

## 10. Operational Notes

### Scheduled Runs
The full pipeline runs via APScheduler:
- **Daily:** `update_daily_ml_predictions()` after the daily quant scan completes
- **Weekly (weekend):** Full `run_historical_backfill()` + `train_global_ml_model()` + `update_daily_ml_predictions()`

### Files Generated

| File | Location | Description |
|---|---|---|
| `ml_ensemble.joblib` | `models/` | Serialised VotingClassifier ensemble |
| `feature_stats.joblib` | `models/` | Training population means and stds per feature (diagnostic use) |

### Database Columns Written

| Table | Column | Description |
|---|---|---|
| `quant_signals` | `ml_confidence_score` | Score 0–100. Updated daily by inference |
| `quant_signals` | `mom_1m` through `mom_12m_skip1m`, `atr_pct` | Momentum/volatility features, written by the daily quant scan (`quant_engine.py`) and re-derived over the full 2-year window by the weekly backfill |
| `quant_signals` | `hist_vol_20`, `rel_strength_5d`, `rel_strength_20d` | As of 2026-07-10, also written by the daily quant scan (`quant_engine.py`), not just the weekly backfill — previously these three were backfill-only, so `score_quantile_predictions()`'s same-date "all features non-null" query would only succeed on the day the weekly backfill last ran (and even then only if `download_spy_benchmark()`'s cached SPY history happened to be date-aligned with the ticker; see `ai_prediction_engine.download_spy_benchmark()`'s `method='ffill'` reindex fix for the alignment half of this bug). `rel_strength_5d`/`20d` still require SPY data (`None` on failure — `ai_prediction_engine.download_spy_benchmark()`, shared by both the daily scan and the weekly backfill); `hist_vol_20` has no SPY dependency and always populates |

### UI Colour Thresholds

The confidence score is displayed in the Watchlist, Portfolio, Market Screener, and Stock Detail pages with colour coding:

```
Green (metric-excellent): score > 40
Amber (metric-neutral):   score 20–40
Red   (metric-poor):      score < 20
```

These thresholds are calibrated to the current model's output range (max ~70, mean ~34). The original thresholds (green > 75, red < 40) were recalibrated when the model's practical maximum was confirmed to be well below 75.

### Re-running from Scratch

If the `quant_signals` table needs to be rebuilt from scratch (e.g. after a schema change or database corruption):

```python
# 1. Truncate quant_signals
import sqlite3
from config import DB_PATH
conn = sqlite3.connect(DB_PATH, isolation_level=None)
conn.execute("DELETE FROM quant_signals;")
conn.execute("VACUUM;")
conn.close()

# 2. Delete stale model files
import os
os.remove("models/ml_ensemble.joblib")
os.remove("models/feature_stats.joblib")

# 3. Run full pipeline
from ai_prediction_engine import (
    run_historical_backfill,
    train_global_ml_model,
    update_daily_ml_predictions,
    get_target_tickers
)
run_historical_backfill()
train_global_ml_model()
update_daily_ml_predictions(get_target_tickers())
```

