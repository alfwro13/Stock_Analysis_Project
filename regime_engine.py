import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from database import get_connection
from config import HISTORICAL_DIR
from yahoo_engine import yahoo_engine
from constants import (
    MACRO_HMM_N_ITER, MACRO_HMM_N_STATES, REGIME_CRASH_VOL, REGIME_VOLATILE_VOL,
    IF_STRESS_N_ESTIMATORS, IF_STRESS_CONTAMINATION, IF_STRESS_MIN_ROWS,
    IF_STRESS_VOL_WINDOW, IF_STRESS_ALERT_THRESHOLD, IF_STRESS_ALERT_DAYS,
)

logger = logging.getLogger(__name__)

_HMM_CACHE_PATH = HISTORICAL_DIR / "SPY_hmm.parquet"
_HMM_LABEL_MAP = {0: "Bull", 1: "Chop", 2: "Crash"}
_HMM_EWMA_LAMBDA = 0.94

_IF_CACHE_PATH = HISTORICAL_DIR / "market_stress_if.parquet"
_IF_MODEL_PATH = HISTORICAL_DIR / "market_stress_if.joblib"
_IF_TICKERS = ["^VIX", "HYG", "^TNX", "SPY"]
_IF_FEATURE_COLS = ["vix_level", "vix_ma_ratio", "hyg_return", "tnx_change", "spy_vol_zscore", "spy_return"]


def run_price_regime_hmm() -> dict:
    """
    Fits a 3-state GaussianHMM on 5-year daily SPY log-returns + EWMA vol.
    States are remapped ascending by mean EWMA vol: 0=Bull, 1=Chop, 2=Crash.
    Maintains an incremental Parquet cache at data/historical/SPY_hmm.parquet so
    only a 1-month tail is fetched on subsequent daily runs.
    Returns {state, label, probability, previous_state, previous_label, date}.
    """
    from hmmlearn import hmm as hmmlib
    from sklearn.preprocessing import StandardScaler

    # --- Load / incrementally update the 5-year SPY cache ---
    try:
        if _HMM_CACHE_PATH.exists():
            df_cached = pd.read_parquet(_HMM_CACHE_PATH)
            tail_dfs = yahoo_engine.get_price_history(["SPY"], period="1mo", interval="1d")
            if tail_dfs and "SPY" in tail_dfs and not tail_dfs["SPY"].empty:
                df_tail = tail_dfs["SPY"][["Close"]].copy()
                df_spy = pd.concat([df_cached[["Close"]], df_tail])
                df_spy = df_spy[~df_spy.index.duplicated(keep="last")].sort_index()
            else:
                df_spy = df_cached[["Close"]].copy()
            logger.info("HMM: merged cache (%d rows) with 1-month tail", len(df_cached))
        else:
            full_dfs = yahoo_engine.get_price_history(["SPY"], period="5y", interval="1d")
            if not full_dfs or "SPY" not in full_dfs or full_dfs["SPY"].empty:
                logger.error("HMM: 5y SPY bootstrap fetch failed.")
                return {}
            df_spy = full_dfs["SPY"][["Close"]].copy()
            logger.info("HMM: bootstrapped 5y SPY cache (%d rows)", len(df_spy))

        df_spy.to_parquet(_HMM_CACHE_PATH, engine="pyarrow")

    except Exception as e:
        logger.error("HMM: Parquet cache update failed: %s", e)
        return {}

    # --- Compute features: daily log returns + 20-day EWMA annualised vol ---
    df_spy = df_spy.dropna(subset=["Close"])
    log_returns = np.log(df_spy["Close"] / df_spy["Close"].shift(1))
    ewma_vol = (log_returns ** 2).ewm(
        alpha=(1 - _HMM_EWMA_LAMBDA), adjust=False
    ).mean().apply(lambda v: np.sqrt(v) * np.sqrt(252) * 100.0)

    features = pd.DataFrame({"returns": log_returns, "vol": ewma_vol}).dropna()

    if len(features) < 60:
        logger.warning("HMM: only %d feature rows — insufficient to fit.", len(features))
        return {}

    # --- Fit GaussianHMM ---
    scaler = StandardScaler()
    X = scaler.fit_transform(features.values)

    model = hmmlib.GaussianHMM(
        n_components=MACRO_HMM_N_STATES,
        covariance_type="full",
        n_iter=MACRO_HMM_N_ITER,
        random_state=42,
    )
    model.fit(X)

    # Canonical ordering: sort states ascending by mean EWMA vol (feature col 1)
    state_order = np.argsort(model.means_[:, 1])
    remap = np.empty(len(state_order), dtype=int)
    remap[state_order] = np.arange(len(state_order))

    raw_states = model.predict(X)
    canonical_states = remap[raw_states]
    posterior_probs = model.predict_proba(X)  # shape (n_obs, n_components)

    today_raw = int(raw_states[-1])
    today_canonical = int(remap[today_raw])
    today_prob = float(posterior_probs[-1, today_raw])
    today_label = _HMM_LABEL_MAP[today_canonical]
    today_date = features.index[-1].strftime("%Y-%m-%d")

    # --- Persist to DB ---
    conn = None
    prev_state, prev_label = None, None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT price_hmm_state, price_hmm_label FROM market_regimes "
            "WHERE date < ? AND price_hmm_state IS NOT NULL ORDER BY date DESC LIMIT 1",
            (today_date,)
        )
        prev_row = cursor.fetchone()
        if prev_row:
            prev_state = prev_row["price_hmm_state"]
            prev_label = prev_row["price_hmm_label"]

        cursor.execute(
            """INSERT INTO market_regimes (date, price_hmm_state, price_hmm_label, price_hmm_prob)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   price_hmm_state = excluded.price_hmm_state,
                   price_hmm_label = excluded.price_hmm_label,
                   price_hmm_prob  = excluded.price_hmm_prob""",
            (today_date, today_canonical, today_label, round(today_prob, 4))
        )

        # Backfill full Viterbi path (INSERT OR IGNORE to avoid clobbering historical runs)
        history_rows = [
            (
                features.index[i].strftime("%Y-%m-%d"),
                int(canonical_states[i]),
                _HMM_LABEL_MAP[int(canonical_states[i])],
                round(float(posterior_probs[i, int(raw_states[i])]), 4),
            )
            for i in range(len(features))
        ]
        cursor.executemany(
            "INSERT OR IGNORE INTO price_hmm_states (date, state, label, probability) VALUES (?, ?, ?, ?)",
            history_rows,
        )
        # Always update today's row (state can shift as new data arrives)
        cursor.execute(
            """INSERT INTO price_hmm_states (date, state, label, probability) VALUES (?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   state = excluded.state,
                   label = excluded.label,
                   probability = excluded.probability""",
            (today_date, today_canonical, today_label, round(today_prob, 4))
        )

        conn.commit()
        logger.info(
            "HMM regime: %s (state %d, conf %.0f%%) | prev: %s | %s",
            today_label, today_canonical, today_prob * 100, prev_label or "—", today_date,
        )
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error("HMM: DB write failed: %s", e)
        return {}
    finally:
        if conn:
            conn.close()

    return {
        "state": today_canonical,
        "label": today_label,
        "probability": today_prob,
        "previous_state": prev_state,
        "previous_label": prev_label,
        "date": today_date,
    }


def _build_if_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the 6 market-stress feature columns from a merged OHLCV DataFrame.

    Expected input columns: vix_close, hyg_close, tnx_close, spy_close, spy_volume.
    Returns a DataFrame indexed by date with _IF_FEATURE_COLS columns; rows with any
    NaN are dropped by the caller.
    """
    out = pd.DataFrame(index=df.index)
    vix_ma = df["vix_close"].rolling(IF_STRESS_VOL_WINDOW).mean()
    out["vix_level"]     = df["vix_close"]
    out["vix_ma_ratio"]  = df["vix_close"] / vix_ma.replace(0, np.nan)
    out["hyg_return"]    = df["hyg_close"].pct_change() * 100.0
    out["tnx_change"]    = df["tnx_close"].diff()
    spy_vol_mean = df["spy_volume"].rolling(IF_STRESS_VOL_WINDOW).mean()
    spy_vol_std  = df["spy_volume"].rolling(IF_STRESS_VOL_WINDOW).std()
    out["spy_vol_zscore"] = (df["spy_volume"] - spy_vol_mean) / spy_vol_std.replace(0, np.nan)
    out["spy_return"]    = df["spy_close"].pct_change() * 100.0
    return out[_IF_FEATURE_COLS]


def run_market_stress_if() -> dict:
    """
    Fits a market-wide IsolationForest on 2 years of daily macro features and scores
    today's observation. Features: VIX level, VIX/20-day-MA ratio, HYG daily return,
    10Y yield daily change, SPY volume z-score, SPY daily return.

    Uses an incremental Parquet cache at data/historical/market_stress_if.parquet so
    only a 1-month tail is fetched on subsequent daily runs.

    Returns a dict:
        score        float in [0.0, 1.0] — 1.0 is maximally anomalous
        features     dict[str, float] — the 6 raw feature values for today
        alert        bool — True when score >= threshold on IF_STRESS_ALERT_DAYS
                     consecutive calendar days (checked via market_regimes DB)
        date         str  — YYYY-MM-DD of the scored observation
    Returns {} on any unrecoverable failure.
    """
    # ── 1. Load / incrementally update the 2-year raw-price cache ──────────────
    try:
        if _IF_CACHE_PATH.exists():
            df_cached = pd.read_parquet(_IF_CACHE_PATH)
            tail_dfs = yahoo_engine.get_price_history(_IF_TICKERS, period="1mo", interval="1d")
            if tail_dfs and all(t in tail_dfs for t in _IF_TICKERS):
                pieces = []
                for t in _IF_TICKERS:
                    s = tail_dfs[t][["Close"]].rename(columns={"Close": t})
                    if "Volume" in tail_dfs[t].columns and t == "SPY":
                        s["SPY_Vol"] = tail_dfs[t]["Volume"]
                    pieces.append(s)
                df_tail = pieces[0].join(pieces[1:], how="outer")
                df_raw = pd.concat([df_cached, df_tail])
                df_raw = df_raw[~df_raw.index.duplicated(keep="last")].sort_index()
            else:
                df_raw = df_cached.copy()
            logger.info("Market stress IF: merged cache (%d rows) with 1-month tail", len(df_cached))
        else:
            full_dfs = yahoo_engine.get_price_history(_IF_TICKERS, period="2y", interval="1d")
            if not full_dfs or not all(t in full_dfs for t in _IF_TICKERS):
                logger.error("Market stress IF: 2y bootstrap fetch failed — missing tickers.")
                return {}
            pieces = []
            for t in _IF_TICKERS:
                s = full_dfs[t][["Close"]].rename(columns={"Close": t})
                if "Volume" in full_dfs[t].columns and t == "SPY":
                    s["SPY_Vol"] = full_dfs[t]["Volume"]
                pieces.append(s)
            df_raw = pieces[0].join(pieces[1:], how="outer")
            df_raw = df_raw.sort_index()
            logger.info("Market stress IF: bootstrapped 2y cache (%d rows)", len(df_raw))

        df_raw.to_parquet(_IF_CACHE_PATH, engine="pyarrow")
    except Exception as e:
        logger.error("Market stress IF: cache update failed: %s", e)
        return {}

    # ── 2. Rename raw columns and build features ────────────────────────────────
    rename = {"^VIX": "vix_close", "HYG": "hyg_close", "^TNX": "tnx_close",
              "SPY": "spy_close", "SPY_Vol": "spy_volume"}
    df_raw = df_raw.rename(columns=rename)
    required = list(rename.values())
    if not all(c in df_raw.columns for c in required):
        missing = [c for c in required if c not in df_raw.columns]
        logger.error("Market stress IF: missing raw columns after rename: %s", missing)
        return {}

    feature_df = _build_if_features(df_raw).dropna()
    if len(feature_df) < IF_STRESS_MIN_ROWS:
        logger.warning(
            "Market stress IF: only %d clean rows after NaN-drop (need %d).",
            len(feature_df), IF_STRESS_MIN_ROWS,
        )
        return {}

    # ── 3. Fit IsolationForest on full history, score today ─────────────────────
    X = feature_df.values
    model = IsolationForest(
        n_estimators=IF_STRESS_N_ESTIMATORS,
        contamination=IF_STRESS_CONTAMINATION,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X)
    raw_scores = model.decision_function(X)
    score_min, score_max = float(raw_scores.min()), float(raw_scores.max())

    if score_max == score_min:
        logger.warning("Market stress IF: degenerate score range — skipping.")
        return {}

    # Normalise: raw decision_function higher = more normal → invert to [0,1] anomaly scale
    norm_scores = np.clip(1.0 - (raw_scores - score_min) / (score_max - score_min), 0.0, 1.0)
    today_score = float(norm_scores[-1])
    today_date  = feature_df.index[-1].strftime("%Y-%m-%d")
    today_features = {col: round(float(feature_df.iloc[-1][col]), 4) for col in _IF_FEATURE_COLS}

    joblib.dump({"model": model, "score_min": score_min, "score_max": score_max,
                 "trained_at": datetime.utcnow().isoformat()}, _IF_MODEL_PATH)

    # ── 4. Upsert score into market_regimes ─────────────────────────────────────
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO market_regimes (date, market_stress_score, market_stress_features)
               VALUES (?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   market_stress_score    = excluded.market_stress_score,
                   market_stress_features = excluded.market_stress_features""",
            (today_date, round(today_score, 4), json.dumps(today_features)),
        )

        # ── 5. Check IF_STRESS_ALERT_DAYS consecutive days above threshold ───────
        cursor.execute(
            "SELECT market_stress_score FROM market_regimes "
            "WHERE market_stress_score IS NOT NULL ORDER BY date DESC LIMIT ?",
            (IF_STRESS_ALERT_DAYS,),
        )
        recent_scores = [r["market_stress_score"] for r in cursor.fetchall()]
        alert = (
            len(recent_scores) >= IF_STRESS_ALERT_DAYS
            and all(s >= IF_STRESS_ALERT_THRESHOLD for s in recent_scores)
        )
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error("Market stress IF: DB write failed: %s", e)
        return {}
    finally:
        if conn:
            conn.close()

    logger.info(
        "Market stress IF: score=%.3f | alert=%s | %s",
        today_score, alert, today_date,
    )
    return {
        "score": today_score,
        "features": today_features,
        "alert": alert,
        "date": today_date,
    }


def calculate_market_regime() -> dict:
    """Fetches 1y of SPY/VIX/FTSE, computes RiskMetrics EWMA vol, classifies regimes, and persists to market_regimes."""
    logger.info("Initiating daily Dual-Region Market Regime calculation...")

    try:
        ticker_dfs = yahoo_engine.get_price_history(["SPY", "^VIX", "^FTSE"], period="1y", interval="1d")

        if not ticker_dfs or not all(t in ticker_dfs for t in ["SPY", "^VIX", "^FTSE"]):
            logger.error("Critical market indices missing from Yahoo Finance response.")
            return

        spy_data = ticker_dfs["SPY"].dropna(subset=['Close'])
        vix_data = ticker_dfs["^VIX"].dropna(subset=['Close'])
        ftse_data = ticker_dfs["^FTSE"].dropna(subset=['Close'])

        if spy_data.empty or vix_data.empty or ftse_data.empty:
            logger.error("Incomplete data received for core regime tickers.")
            return

        # RiskMetrics EWMA (λ=0.94): minimizes lag vs simple rolling window
        LAMBDA = 0.94

        spy_log_returns = np.log(spy_data['Close'] / spy_data['Close'].shift(1)).dropna()
        spy_var_ewma = (spy_log_returns ** 2).ewm(alpha=(1 - LAMBDA), adjust=False).mean()
        spy_vol_ewma = np.sqrt(spy_var_ewma) * np.sqrt(252) * 100.0

        ftse_log_returns = np.log(ftse_data['Close'] / ftse_data['Close'].shift(1)).dropna()
        ftse_var_ewma = (ftse_log_returns ** 2).ewm(alpha=(1 - LAMBDA), adjust=False).mean()
        ftse_vol_ewma = np.sqrt(ftse_var_ewma) * np.sqrt(252) * 100.0

        latest_date = spy_data.index[-1].strftime('%Y-%m-%d')
        latest_vix = float(vix_data['Close'].iloc[-1])
        latest_spy_vol = float(spy_vol_ewma.iloc[-1])
        latest_ftse_vol = float(ftse_vol_ewma.iloc[-1])

        if pd.isna(latest_spy_vol) or pd.isna(latest_ftse_vol):
            logger.warning("Not enough data to calculate EWMA volatilities.")
            return

        # US: 100% EWMA Realized (VIX is fetched for display purposes only, not blended in)
        us_turbulence = latest_spy_vol
        # UK: 100% EWMA Realized (no robust UK implied vol feed available on Yahoo Finance)
        uk_turbulence = latest_ftse_vol

        us_regime_label = 'Crash' if us_turbulence >= REGIME_CRASH_VOL else \
                          'Volatile' if us_turbulence >= REGIME_VOLATILE_VOL else 'Normal'

        uk_regime_label = 'Crash' if uk_turbulence >= REGIME_CRASH_VOL else \
                          'Volatile' if uk_turbulence >= REGIME_VOLATILE_VOL else 'Normal'

        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO market_regimes
                (date, vix_close, spy_volatility, us_turbulence, us_regime_label,
                 ftse_volatility, uk_turbulence, uk_regime_label)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                latest_date,
                round(latest_vix, 2),
                round(latest_spy_vol, 2),
                round(us_turbulence, 2),
                us_regime_label,
                round(latest_ftse_vol, 2),
                round(uk_turbulence, 2),
                uk_regime_label
            ))
            conn.commit()
            logger.info("Regimes recorded | Date: %s | US: %s (%.2f) | UK: %s (%.2f)",
                        latest_date, us_regime_label, us_turbulence, uk_regime_label, uk_turbulence)
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error("Database insertion failed during regime calculation: %s", e)
            raise
        finally:
            if conn:
                conn.close()

    except Exception as e:
        logger.error("Fatal error calculating market regime: %s", e)

    hmm_result = run_price_regime_hmm()
    stress_result = run_market_stress_if()
    return {"hmm": hmm_result, "market_stress": stress_result}

def _classify_regime(
    us_yield_curve: Optional[float],
    us_cpi: Optional[float],
    us_hy_spread: Optional[float],
    ai_hmm_state: Optional[int],
    us_real_yield: Optional[float],
) -> str:
    """Returns a named macro regime label based on the provided signal values."""
    inverted = us_yield_curve is not None and us_yield_curve < 0

    # Stagflation: persistent inflation + financial stress or negative real yield
    if us_cpi is not None and us_cpi > 4.0:
        if (us_hy_spread is not None and us_hy_spread > 500) or \
                (us_real_yield is not None and us_real_yield < 0):
            return "Stagflation"

    # Contraction: inverted yield curve + recession-mode HMM or blown spreads
    if inverted:
        if ai_hmm_state == 2 or (us_hy_spread is not None and us_hy_spread > 600):
            return "Contraction"

    # Late Cycle: flattening curve with elevated CPI
    if us_yield_curve is not None and 0.0 <= us_yield_curve <= 0.20:
        if us_cpi is not None and us_cpi > 3.0:
            return "Late Cycle"

    # Recovery: HMM transitioning (choppy) with positive curve
    if ai_hmm_state == 1 and not inverted:
        return "Recovery"

    return "Risk-On"


def classify_macro_regime() -> None:
    """Reads latest macro signals, computes regime label + inversion streak, writes to macro_regimes."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT us_yield_curve, us_cpi_inflation, us_high_yield_spread, us_real_yield_10y "
            "FROM macro_indicators WHERE us_yield_curve IS NOT NULL ORDER BY date DESC LIMIT 1"
        )
        ind_row = cursor.fetchone()
        if not ind_row:
            logger.warning("classify_macro_regime: no macro_indicators data available.")
            return

        us_yield_curve = ind_row['us_yield_curve']
        us_cpi = ind_row['us_cpi_inflation']
        us_hy_spread = ind_row['us_high_yield_spread']
        us_real_yield = ind_row['us_real_yield_10y']

        cursor.execute("SELECT ai_hmm_state FROM market_regimes ORDER BY date DESC LIMIT 1")
        hmm_row = cursor.fetchone()
        ai_hmm_state = hmm_row['ai_hmm_state'] if hmm_row and hmm_row['ai_hmm_state'] is not None else None

        # Count consecutive days with inverted yield curve (most recent streak)
        cursor.execute(
            "SELECT us_yield_curve FROM macro_indicators "
            "WHERE us_yield_curve IS NOT NULL ORDER BY date DESC LIMIT 365"
        )
        history = [r['us_yield_curve'] for r in cursor.fetchall()]
        days_inverted = 0
        for val in history:
            if val < 0:
                days_inverted += 1
            else:
                break

        currently_inverted = us_yield_curve is not None and us_yield_curve < 0
        regime_label = _classify_regime(us_yield_curve, us_cpi, us_hy_spread, ai_hmm_state, us_real_yield)

        cursor.execute("SELECT date FROM macro_regimes ORDER BY date DESC LIMIT 1")
        date_row = cursor.fetchone()
        if not date_row:
            logger.warning("classify_macro_regime: no macro_regimes row to update.")
            return

        cursor.execute(
            "UPDATE macro_regimes SET yield_curve_inverted=?, days_inverted=?, regime_label=? WHERE date=?",
            (1 if currently_inverted else 0, days_inverted, regime_label, date_row['date'])
        )
        conn.commit()
        logger.info(
            "Macro regime: %s | Inverted: %s | Days inverted: %d",
            regime_label, currently_inverted, days_inverted,
        )
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error("classify_macro_regime failed: %s", e)
    finally:
        if conn:
            conn.close()


def get_latest_regime() -> Optional[Dict[str, Any]]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM market_regimes ORDER BY date DESC LIMIT 1")
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to fetch latest regime: %s", e)
        return None
    finally:
        if conn:
            conn.close()

def calculate_systemic_macro_threat() -> None:
    try:
        # pull 10 days to survive weekends and holidays
        _macro_dfs = yahoo_engine.get_price_history(
            ["^TYX", "^TNX", "DX-Y.NYB", "GBPUSD=X"], period="10d", interval="1d"
        )
        tyx = _macro_dfs.get("^TYX", pd.DataFrame())
        tnx = _macro_dfs.get("^TNX", pd.DataFrame())
        dxy = _macro_dfs.get("DX-Y.NYB", pd.DataFrame())
        gbpusd = _macro_dfs.get("GBPUSD=X", pd.DataFrame())

        if tnx.empty or len(tnx) < 4:
            logger.warning("Insufficient ^TNX data for macro threat evaluation.")
            return

        curr_tnx = float(tnx['Close'].iloc[-1])
        # iloc[-4] is fragile across holidays; use date lookback instead
        target_past = tnx.index[-1] - pd.Timedelta(days=4)
        tnx_past_rows = tnx[tnx.index <= target_past]
        past_tnx = float(tnx_past_rows['Close'].iloc[-1]) if not tnx_past_rows.empty else curr_tnx

        curr_tyx = float(tyx['Close'].iloc[-1]) if not tyx.empty else curr_tnx
        curr_dxy = float(dxy['Close'].iloc[-1]) if not dxy.empty else 0.0
        curr_gbpusd = float(gbpusd['Close'].iloc[-1]) if not gbpusd.empty else 0.0

        uk_gilt_path = HISTORICAL_DIR / "UK_GILT_BASELINE.parquet"
        if uk_gilt_path.exists():
            gilt_df = pd.read_parquet(uk_gilt_path)
            # Strip out weekend padding (Saturday=5, Sunday=6) to align with trading days
            gilt_df = gilt_df[gilt_df.index.dayofweek < 5]
            curr_gilt = float(gilt_df['Close'].iloc[-1]) if len(gilt_df) >= 1 else curr_tnx
            target_past_gilt = gilt_df.index[-1] - pd.Timedelta(days=4)
            gilt_past_rows = gilt_df[gilt_df.index <= target_past_gilt]
            past_gilt = float(gilt_past_rows['Close'].iloc[-1]) if not gilt_past_rows.empty else curr_tnx
        else:
            logger.warning("UK Gilt Baseline Parquet missing. Falling back to TNX equivalence.")
            curr_gilt, past_gilt = curr_tnx, past_tnx

        us_velocity_bps = (curr_tnx - past_tnx) * 100.0
        gilt_velocity_bps = (curr_gilt - past_gilt) * 100.0

        # calibrated to post-2022 rate environment: 30bps/3-day or 4.75% = RED, 15bps or 4.25% = YELLOW
        if us_velocity_bps >= 30.0 or curr_tnx >= 4.75:
            us_threat_level = "RED"
        elif us_velocity_bps >= 15.0 or curr_tnx >= 4.25:
            us_threat_level = "YELLOW"
        else:
            us_threat_level = "GREEN"

        # calibrated to higher UK gilt premiums: 30bps/3-day or 5.0% = RED, 15bps or 4.5% = YELLOW
        if gilt_velocity_bps >= 30.0 or curr_gilt >= 5.0:
            uk_threat_level = "RED"
        elif gilt_velocity_bps >= 15.0 or curr_gilt >= 4.5:
            uk_threat_level = "YELLOW"
        else:
            uk_threat_level = "GREEN"

        latest_date = tnx.index[-1].strftime('%Y-%m-%d')

        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO macro_regimes
                (date, tyx_close, tnx_close, dxy_close, uk_gilt_close, gbpusd_close,
                 us_yield_velocity, us_threat_level, uk_yield_velocity, uk_threat_level)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                latest_date,
                round(curr_tyx, 3),
                round(curr_tnx, 3),
                round(curr_dxy, 3),
                round(curr_gilt, 3),
                round(curr_gbpusd, 4),
                round(us_velocity_bps, 2),
                us_threat_level,
                round(gilt_velocity_bps, 2),
                uk_threat_level
            ))
            conn.commit()
            logger.info("Macro Risk Evaluated | US: %s (Vel: %+.2f bps, Lvl: %.2f%%) | UK: %s (Vel: %+.2f bps, Lvl: %.2f%%)",
                        us_threat_level, us_velocity_bps, curr_tnx, uk_threat_level, gilt_velocity_bps, curr_gilt)
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error("Database insertion failed during macro threat calculation: %s", e)
            raise
        finally:
            if conn:
                conn.close()

        classify_macro_regime()

    except Exception as e:
        logger.error("Fatal crash inside systemic threat calculator: %s", e)
