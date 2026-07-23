from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import joblib
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier

from config import BASE_DIR, HISTORICAL_DIR, load_config
from database import get_connection

logger = logging.getLogger(__name__)

# GUI name: "Alert Confidence Referee". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.

TRAP_MONITOR_ENGINE = "TrapMonitor"
CONFLUENCE_ENGINE = "Confluence"

MODELS_DIR = BASE_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
_MODEL_PATHS = {
    TRAP_MONITOR_ENGINE: MODELS_DIR / "alert_referee_trapmonitor.joblib",
    CONFLUENCE_ENGINE: MODELS_DIR / "alert_referee_confluence.joblib",
}

_CONFIG_KEYS = {
    TRAP_MONITOR_ENGINE: "ALERT_REFEREE_TRAINING",
    CONFLUENCE_ENGINE: "ALERT_REFEREE_TRAINING_CONFLUENCE",
}

# Below this, there isn't enough data for CalibratedClassifierCV to fit any folds at all. A
# shared absolute statistical floor across engines, not a tunable business threshold — unlike
# MIN_TRAINING_SAMPLES/VETO_THRESHOLD/MODE, which are configured independently per engine (see
# _referee_config()).
_HARD_MIN_SAMPLES = 30

_FEATURE_COLUMNS = ["rsi", "ema_distance", "bull_trap_vol_ratio", "cap_vol_zscore", "wyckoff_bb_width"]
_ALERT_PHASES = ["ACTIVE_SELLOFF", "BULL_TRAP_RISK", "CAPITULATION_FORMING", "BEAR_TRAP_RISK"]

# Confluence's feature set is deliberately compact and disjoint from TrapMonitor's own raw TA
# features — it scores Idea A's 3 pillar votes (one-hot up/down per pillar; both 0 means the
# pillar abstained) plus Idea B's regime-weighted score, i.e. the *combined* Idea A/B signal
# itself, not a reimplementation of either source engine's own veto.
_CONFLUENCE_PILLARS = ["pillar_technical", "pillar_statistical", "pillar_ml"]
_CONFLUENCE_FEATURE_COLUMNS = (
    [f"{p}_up" for p in _CONFLUENCE_PILLARS] + [f"{p}_down" for p in _CONFLUENCE_PILLARS] + ["regime_weighted_score"]
)


@dataclass
class RefereeVerdict:
    fire_probability: Optional[float]
    vetoed: bool
    mode: str
    model_available: bool


def _referee_config(engine: str = TRAP_MONITOR_ENGINE) -> dict:
    cfg = load_config()
    return cfg.get("SCHEDULING", {}).get(_CONFIG_KEYS[engine], {})


def _model_path(engine: str):
    return _MODEL_PATHS[engine]


def _build_trap_feature_row(phase: str, row: dict) -> dict:
    features = {col: row.get(col) for col in _FEATURE_COLUMNS}
    for p in _ALERT_PHASES:
        features[f"phase_{p}"] = 1.0 if phase == p else 0.0
    return features


def _build_confluence_feature_row(row: dict) -> dict:
    features = {}
    for pillar in _CONFLUENCE_PILLARS:
        vote = row.get(pillar)
        features[f"{pillar}_up"] = 1.0 if vote == "up" else 0.0
        features[f"{pillar}_down"] = 1.0 if vote == "down" else 0.0
    features["regime_weighted_score"] = row.get("regime_weighted_score")
    return features


def _build_feature_row(engine: str, phase: str, row: dict) -> dict:
    if engine == CONFLUENCE_ENGINE:
        return _build_confluence_feature_row(row)
    return _build_trap_feature_row(phase, row)


def _feature_columns(engine: str = TRAP_MONITOR_ENGINE) -> list[str]:
    if engine == CONFLUENCE_ENGINE:
        return list(_CONFLUENCE_FEATURE_COLUMNS)
    return _FEATURE_COLUMNS + [f"phase_{p}" for p in _ALERT_PHASES]


def backfill_historical_features() -> dict:
    """Recomputes RSI/EMA-distance/volume-ratio/Bollinger-width for trap_phase_history rows
    logged before these columns existed, from the same 2-year parquet history the live scan
    itself reads, so already-resolved historical rows become usable training data immediately
    instead of only accumulating from new scans going forward. Idempotent — already-backfilled
    rows (ema_distance NOT NULL) are excluded from the candidate query, so a safe no-op once done.
    Recomputed phase must match the originally-recorded phase before a row is updated — a mismatch
    means the parquet has since been revised and the recomputed features would no longer describe
    the outcome that was actually recorded, so that row is left NULL rather than backfilled wrong."""
    from bull_bear_trap_engine import TrapEngine

    conn = None
    try:
        conn = get_connection()
        rows = [
            dict(r) for r in conn.execute(
                """SELECT id, ticker, phase, scan_date FROM trap_phase_history
                   WHERE ema_distance IS NULL AND phase != 'NEUTRAL'"""
            ).fetchall()
        ]
    except Exception as e:
        logger.error("backfill_historical_features: failed to load candidate rows: %s", e)
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()

    if not rows:
        return {"status": "done", "updated": 0, "skipped": 0, "total_candidates": 0}

    trap_engine = TrapEngine(load_config())
    by_ticker: dict[str, list] = {}
    for row in rows:
        by_ticker.setdefault(row["ticker"], []).append(row)

    updates = []
    skipped = 0
    for ticker, ticker_rows in by_ticker.items():
        path = HISTORICAL_DIR / f"{ticker}.parquet"
        if not path.exists():
            skipped += len(ticker_rows)
            continue
        try:
            full_df = pd.read_parquet(path, columns=["Open", "High", "Low", "Close", "Volume"])
            full_df = full_df.dropna(subset=["Close", "Volume"])
            full_df = full_df[full_df["Volume"] > 0]
        except Exception as e:
            logger.error("backfill_historical_features: failed to load history for %s: %s", ticker, e)
            skipped += len(ticker_rows)
            continue

        for row in ticker_rows:
            as_of = full_df[full_df.index <= pd.Timestamp(row["scan_date"])].tail(60)
            result = trap_engine._analyse_ticker(ticker, as_of)
            if result is None or result["phase"] != row["phase"]:
                skipped += 1
                continue
            updates.append((
                result.get("rsi"), result.get("ema_distance"),
                result.get("bull_trap_vol_ratio"), result.get("cap_vol_zscore"),
                result.get("wyckoff_bb_width"), row["id"],
            ))

    if updates:
        conn = None
        try:
            conn = get_connection()
            conn.executemany(
                """UPDATE trap_phase_history
                   SET rsi=?, ema_distance=?, bull_trap_vol_ratio=?, cap_vol_zscore=?, wyckoff_bb_width=?
                   WHERE id=?""",
                updates,
            )
            conn.commit()
        except Exception as e:
            logger.error("backfill_historical_features: failed to write updates: %s", e)
            return {"status": "error", "message": str(e)}
        finally:
            if conn:
                conn.close()

    logger.info(
        "Alert Confidence Referee: backfilled features for %d historical trap_phase_history rows (%d skipped).",
        len(updates), skipped,
    )
    return {"status": "done", "updated": len(updates), "skipped": skipped, "total_candidates": len(rows)}


def backfill_historical_confluence_features() -> dict:
    """Reconstructs Idea A pillar votes + Idea B regime-weighted score, as of each row's own
    scan_date, for trap_phase_history/pattern_detection_history rows logged before this feature
    shipped (confluence_features_ts IS NULL) — so the Cross-Engine Alert Referee can train on
    every already-resolved historical call across both engines immediately, not only on rows
    logged after this change. Unlike backfill_historical_features() (TrapMonitor's own RSI/EMA
    backfill, which re-runs TrapEngine._analyse_ticker against parquet), this reads only
    already-persisted history tables via score_analysis's as-of variants — no engine re-run and
    no parquet access needed, since pillar votes and regime are themselves just windowed queries
    over other tables. Idempotent — already-backfilled rows (confluence_features_ts NOT NULL)
    are excluded from the candidate query, so a safe no-op once caught up."""
    from score_analysis import compute_regime_weighted_score_as_of, evaluate_pillar_confluence_as_of

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = None
    try:
        conn = get_connection()
        trap_rows = [
            dict(r) for r in conn.execute(
                """SELECT id, ticker, scan_date FROM trap_phase_history
                   WHERE confluence_features_ts IS NULL AND phase != 'NEUTRAL'"""
            ).fetchall()
        ]
        pattern_rows = [
            dict(r) for r in conn.execute(
                """SELECT id, ticker, scan_date FROM pattern_detection_history
                   WHERE confluence_features_ts IS NULL AND phase = 'CONFIRMED'"""
            ).fetchall()
        ]
    except Exception as e:
        logger.error("backfill_historical_confluence_features: failed to load candidate rows: %s", e)
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()

    if not trap_rows and not pattern_rows:
        return {"status": "done", "updated": 0, "total_candidates": 0}

    def _reconstruct(row: dict) -> tuple:
        confluence = evaluate_pillar_confluence_as_of(row["ticker"], row["scan_date"])
        regime_score = compute_regime_weighted_score_as_of(row["ticker"], row["scan_date"])
        pillar_technical = "up" if "technical" in confluence["bullish_pillars"] else (
            "down" if "technical" in confluence["bearish_pillars"] else None
        )
        pillar_statistical = "up" if "statistical" in confluence["bullish_pillars"] else (
            "down" if "statistical" in confluence["bearish_pillars"] else None
        )
        pillar_ml = "up" if "ml" in confluence["bullish_pillars"] else (
            "down" if "ml" in confluence["bearish_pillars"] else None
        )
        score = regime_score.get("score") if regime_score else None
        return (pillar_technical, pillar_statistical, pillar_ml, score, now, row["id"])

    trap_updates = [_reconstruct(row) for row in trap_rows]
    pattern_updates = [_reconstruct(row) for row in pattern_rows]

    conn = None
    try:
        conn = get_connection()
        if trap_updates:
            conn.executemany(
                """UPDATE trap_phase_history
                   SET pillar_technical=?, pillar_statistical=?, pillar_ml=?, regime_weighted_score=?,
                       confluence_features_ts=?
                   WHERE id=?""",
                trap_updates,
            )
        if pattern_updates:
            conn.executemany(
                """UPDATE pattern_detection_history
                   SET pillar_technical=?, pillar_statistical=?, pillar_ml=?, regime_weighted_score=?,
                       confluence_features_ts=?
                   WHERE id=?""",
                pattern_updates,
            )
        conn.commit()
    except Exception as e:
        logger.error("backfill_historical_confluence_features: failed to write updates: %s", e)
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()

    total_updated = len(trap_updates) + len(pattern_updates)
    logger.info(
        "Alert Confidence Referee: backfilled Confluence features for %d historical row(s) "
        "(%d trap_phase_history, %d pattern_detection_history).",
        total_updated, len(trap_updates), len(pattern_updates),
    )
    return {"status": "done", "updated": total_updated, "total_candidates": len(trap_rows) + len(pattern_rows)}


def _trap_readiness_counts(conn) -> dict:
    row = conn.execute(
        """SELECT COUNT(*) AS n, MIN(scan_date) AS earliest, MAX(scan_date) AS latest
           FROM trap_phase_history
           WHERE phase != 'NEUTRAL' AND direction_correct_14d IS NOT NULL AND ema_distance IS NOT NULL"""
    ).fetchone()
    current = row["n"] or 0
    earliest, latest = row["earliest"], row["latest"]
    # Feature-bearing but not yet resolved (14-day outcome still pending) — a leading
    # indicator so an operator isn't staring at "0" for the full 14-day resolution window.
    pending = conn.execute(
        """SELECT COUNT(*) AS n FROM trap_phase_history
           WHERE phase != 'NEUTRAL' AND direction_correct_14d IS NULL AND ema_distance IS NOT NULL"""
    ).fetchone()["n"] or 0
    # Pre-migration rows with a resolved outcome but no features yet — these are what
    # backfill_historical_features() would recompute (an upper bound: a handful may be
    # skipped if the parquet has since been revised and the recomputed phase no longer
    # matches). Surfaced so "0 current" on a freshly-deployed/never-trained instance doesn't
    # read as "no usable data exists" when a backfill has simply never been triggered yet.
    backfill_available = conn.execute(
        """SELECT COUNT(*) AS n FROM trap_phase_history
           WHERE phase != 'NEUTRAL' AND ema_distance IS NULL AND direction_correct_14d IS NOT NULL"""
    ).fetchone()["n"] or 0
    return {"current": current, "earliest": earliest, "latest": latest,
            "pending": pending, "backfill_available": backfill_available}


def _confluence_readiness_counts(conn) -> dict:
    """Training rows union trap_phase_history's 14d-resolved outcomes with
    pattern_detection_history's 14d AND 30d-resolved outcomes (each resolved horizon on a
    pattern row is counted as its own training sample — same features, a different-horizon
    label), gated on confluence_features_ts NOT NULL (already reconstructed, live or backfilled).
    See backfill_historical_confluence_features() for what populates that column."""
    trap_row = conn.execute(
        """SELECT COUNT(*) AS n, MIN(scan_date) AS earliest, MAX(scan_date) AS latest
           FROM trap_phase_history
           WHERE phase != 'NEUTRAL' AND direction_correct_14d IS NOT NULL AND confluence_features_ts IS NOT NULL"""
    ).fetchone()
    pattern_14d = conn.execute(
        """SELECT COUNT(*) AS n, MIN(scan_date) AS earliest, MAX(scan_date) AS latest
           FROM pattern_detection_history
           WHERE phase = 'CONFIRMED' AND direction_correct_14d IS NOT NULL AND confluence_features_ts IS NOT NULL"""
    ).fetchone()
    pattern_30d = conn.execute(
        """SELECT COUNT(*) AS n FROM pattern_detection_history
           WHERE phase = 'CONFIRMED' AND direction_correct_30d IS NOT NULL AND confluence_features_ts IS NOT NULL"""
    ).fetchone()
    current = (trap_row["n"] or 0) + (pattern_14d["n"] or 0) + (pattern_30d["n"] or 0)
    dates = [d for d in (trap_row["earliest"], pattern_14d["earliest"]) if d]
    earliest = min(dates) if dates else None
    dates = [d for d in (trap_row["latest"], pattern_14d["latest"]) if d]
    latest = max(dates) if dates else None

    pending = (
        (conn.execute(
            """SELECT COUNT(*) AS n FROM trap_phase_history
               WHERE phase != 'NEUTRAL' AND direction_correct_14d IS NULL AND confluence_features_ts IS NOT NULL"""
        ).fetchone()["n"] or 0)
        + (conn.execute(
            """SELECT COUNT(*) AS n FROM pattern_detection_history
               WHERE phase = 'CONFIRMED' AND direction_correct_14d IS NULL AND confluence_features_ts IS NOT NULL"""
        ).fetchone()["n"] or 0)
    )
    backfill_available = (
        (conn.execute(
            """SELECT COUNT(*) AS n FROM trap_phase_history
               WHERE phase != 'NEUTRAL' AND direction_correct_14d IS NOT NULL AND confluence_features_ts IS NULL"""
        ).fetchone()["n"] or 0)
        + (conn.execute(
            """SELECT COUNT(*) AS n FROM pattern_detection_history
               WHERE phase = 'CONFIRMED' AND confluence_features_ts IS NULL
                 AND (direction_correct_14d IS NOT NULL OR direction_correct_30d IS NOT NULL)"""
        ).fetchone()["n"] or 0)
    )
    return {"current": current, "earliest": earliest, "latest": latest,
            "pending": pending, "backfill_available": backfill_available}


def training_sample_count(engine: str = TRAP_MONITOR_ENGINE) -> int:
    conn = None
    try:
        conn = get_connection()
        counts = _confluence_readiness_counts(conn) if engine == CONFLUENCE_ENGINE else _trap_readiness_counts(conn)
        return counts["current"]
    except Exception as e:
        logger.error("training_sample_count failed for %s: %s", engine, e)
        return 0
    finally:
        if conn:
            conn.close()


def readiness_status(engine: str = TRAP_MONITOR_ENGINE) -> dict:
    cfg = _referee_config(engine)
    target = int(cfg.get("MIN_TRAINING_SAMPLES", 200))
    conn = None
    try:
        conn = get_connection()
        counts = _confluence_readiness_counts(conn) if engine == CONFLUENCE_ENGINE else _trap_readiness_counts(conn)
        current, earliest, latest = counts["current"], counts["earliest"], counts["latest"]
        pending, backfill_available = counts["pending"], counts["backfill_available"]
    except Exception as e:
        logger.error("readiness_status failed for %s: %s", engine, e)
        current, earliest, latest, pending, backfill_available = 0, None, None, 0, 0
    finally:
        if conn:
            conn.close()

    remaining = max(0, target - current)
    eta_days = None
    eta_date = None
    # Projects forward using the historical resolution rate (resolved rows / days spanned) as a
    # proxy for the rate of NEW feature-bearing rows — the underlying phase-firing frequency this
    # rate reflects is unchanged by adding feature columns, only whether they're recorded is new.
    if remaining > 0 and earliest and latest and current > 0:
        span_days = (datetime.strptime(latest, "%Y-%m-%d") - datetime.strptime(earliest, "%Y-%m-%d")).days + 1
        rate_per_day = current / span_days if span_days > 0 else 0
        if rate_per_day > 0:
            eta_days = int(remaining / rate_per_day) + 1
            eta_date = (datetime.now(timezone.utc) + timedelta(days=eta_days)).strftime("%Y-%m-%d")

    return {
        "current": current,
        "target": target,
        "pending": pending,
        "backfill_available": backfill_available,
        "hard_min": _HARD_MIN_SAMPLES,
        "can_train": current >= _HARD_MIN_SAMPLES,
        "can_train_after_backfill": (current + backfill_available) >= _HARD_MIN_SAMPLES,
        "ready_for_active": current >= target,
        "eta_days": eta_days,
        "eta_date": eta_date,
    }


def _load_trap_training_rows(conn) -> list[dict]:
    return [
        dict(r) for r in conn.execute(
            """SELECT phase, rsi, ema_distance, bull_trap_vol_ratio, cap_vol_zscore, wyckoff_bb_width,
                      direction_correct_14d AS label
               FROM trap_phase_history
               WHERE phase != 'NEUTRAL' AND direction_correct_14d IS NOT NULL AND ema_distance IS NOT NULL"""
        ).fetchall()
    ]


def _load_confluence_training_rows(conn) -> list[dict]:
    """Unions trap_phase_history's 14d-resolved outcomes with pattern_detection_history's 14d
    AND 30d-resolved outcomes — each resolved horizon on a pattern row becomes its own training
    row (same point-in-time pillar/regime features, a different-horizon label), per the task's
    'resolved 14d/30d direction-correct outcomes' scope for pattern_detection_history (trap's own
    contribution stays 14d-only, matching TrapMonitor's own trainer above)."""
    feature_cols = ", ".join(_CONFLUENCE_PILLARS + ["regime_weighted_score"])
    trap_rows = conn.execute(
        f"""SELECT {feature_cols}, direction_correct_14d AS label FROM trap_phase_history
            WHERE phase != 'NEUTRAL' AND direction_correct_14d IS NOT NULL AND confluence_features_ts IS NOT NULL"""
    ).fetchall()
    pattern_14d = conn.execute(
        f"""SELECT {feature_cols}, direction_correct_14d AS label FROM pattern_detection_history
            WHERE phase = 'CONFIRMED' AND direction_correct_14d IS NOT NULL AND confluence_features_ts IS NOT NULL"""
    ).fetchall()
    pattern_30d = conn.execute(
        f"""SELECT {feature_cols}, direction_correct_30d AS label FROM pattern_detection_history
            WHERE phase = 'CONFIRMED' AND direction_correct_30d IS NOT NULL AND confluence_features_ts IS NOT NULL"""
    ).fetchall()
    return [dict(r) for r in list(trap_rows) + list(pattern_14d) + list(pattern_30d)]


def train_referee_model(engine: str = TRAP_MONITOR_ENGINE) -> dict:
    if engine == CONFLUENCE_ENGINE:
        backfill_historical_confluence_features()
    else:
        backfill_historical_features()

    conn = None
    try:
        conn = get_connection()
        rows = _load_confluence_training_rows(conn) if engine == CONFLUENCE_ENGINE else _load_trap_training_rows(conn)
    except Exception as e:
        logger.error("train_referee_model: failed to load training rows for %s: %s", engine, e)
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()

    sample_count = len(rows)
    if sample_count < _HARD_MIN_SAMPLES:
        return {"status": "insufficient_data", "sample_count": sample_count, "hard_min": _HARD_MIN_SAMPLES}

    df = pd.DataFrame(rows)
    if engine == CONFLUENCE_ENGINE:
        feature_rows = [_build_confluence_feature_row(row) for row in rows]
    else:
        for p in _ALERT_PHASES:
            df[f"phase_{p}"] = (df["phase"] == p).astype(float)
        feature_rows = None
    feature_cols = _feature_columns(engine)
    X = pd.DataFrame(feature_rows)[feature_cols].fillna(0.0) if feature_rows is not None else df[feature_cols].fillna(0.0)
    y = df["label"].astype(int)

    if y.nunique() < 2:
        return {"status": "insufficient_data", "sample_count": sample_count,
                "message": "Only one outcome class present so far."}

    cv_folds = min(3, int(y.value_counts().min()))
    if cv_folds < 2:
        return {"status": "insufficient_data", "sample_count": sample_count,
                "message": "Not enough samples in the minority outcome class yet for cross-validation."}

    base = RandomForestClassifier(n_estimators=200, max_depth=4, class_weight="balanced", random_state=42, n_jobs=1)
    model = CalibratedClassifierCV(base, method="isotonic", cv=cv_folds)
    model.fit(X, y)

    train_accuracy = float(model.score(X, y))

    cfg = _referee_config(engine)
    target = int(cfg.get("MIN_TRAINING_SAMPLES", 200))
    veto_threshold = float(cfg.get("VETO_THRESHOLD", 0.3))
    effective_mode = cfg.get("MODE", "shadow") if sample_count >= target else "shadow"

    probs = model.predict_proba(X)[:, 1]
    veto_rate = float((probs < veto_threshold).mean())

    trained_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    model_path = _model_path(engine)
    joblib.dump({"model": model, "feature_cols": feature_cols, "trained_at": trained_at,
                 "sample_count": sample_count}, model_path)

    conn = None
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO alert_referee_models
               (engine, trained_at, sample_count, positive_count, train_accuracy, veto_rate, effective_mode, model_path)
               VALUES (?,?,?,?,?,?,?,?)""",
            (engine, trained_at, sample_count, int(y.sum()), round(train_accuracy, 4),
             round(veto_rate, 4), effective_mode, str(model_path)),
        )
        conn.commit()
    except Exception as e:
        logger.error("train_referee_model: failed to log model metadata for %s: %s", engine, e)
    finally:
        if conn:
            conn.close()

    logger.info(
        "Alert Confidence Referee: trained on %d samples for %s (effective mode: %s).",
        sample_count, engine, effective_mode,
    )
    return {
        "status": "trained",
        "sample_count": sample_count,
        "train_accuracy": round(train_accuracy, 4),
        "veto_rate": round(veto_rate, 4),
        "effective_mode": effective_mode,
    }


def load_model(engine: str = TRAP_MONITOR_ENGINE) -> Optional[dict]:
    path = _model_path(engine)
    if not path.exists():
        return None
    try:
        return joblib.load(path)
    except Exception as e:
        logger.error("alert_referee_engine: failed to load model for %s: %s", engine, e)
        return None


def _latest_model_id(conn, engine: str = TRAP_MONITOR_ENGINE) -> Optional[int]:
    try:
        row = conn.execute(
            "SELECT id FROM alert_referee_models WHERE engine=? ORDER BY id DESC LIMIT 1", (engine,)
        ).fetchone()
        return row["id"] if row else None
    except Exception as e:
        logger.error("alert_referee_engine: failed to resolve latest model id for %s: %s", engine, e)
        return None


def log_veto_evaluation(
    engine: str,
    ticker: str,
    phase: str,
    fire_probability: float,
    vetoed: bool,
    mode: str,
    model_id: Optional[int],
    conn,
    direction: Optional[str] = None,
) -> None:
    try:
        conn.execute(
            """INSERT INTO alert_referee_log
               (engine, ticker, phase, direction, fire_probability, vetoed, mode, model_id, scan_ts)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (engine, ticker, phase, direction, fire_probability, int(vetoed), mode, model_id,
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    except Exception as e:
        logger.error("alert_referee_engine: failed to log veto evaluation for %s: %s", ticker, e)


def evaluate_alert(engine: str, ticker: str, phase: str, row: dict, conn) -> RefereeVerdict:
    cfg = _referee_config(engine)
    if not cfg.get("ENABLED", False):
        return RefereeVerdict(fire_probability=None, vetoed=False, mode="off", model_available=False)

    bundle = load_model(engine)
    if bundle is None:
        return RefereeVerdict(fire_probability=None, vetoed=False, mode=cfg.get("MODE", "shadow"), model_available=False)

    readiness = readiness_status(engine)
    configured_mode = cfg.get("MODE", "shadow")
    effective_mode = configured_mode if readiness["ready_for_active"] else "shadow"

    try:
        features = _build_feature_row(engine, phase, row)
        X = pd.DataFrame([features])[bundle["feature_cols"]].fillna(0.0)
        fire_probability = float(bundle["model"].predict_proba(X)[0][1])
    except Exception as e:
        logger.error("alert_referee_engine: inference failed for %s: %s", ticker, e)
        return RefereeVerdict(fire_probability=None, vetoed=False, mode=effective_mode, model_available=False)

    veto_threshold = float(cfg.get("VETO_THRESHOLD", 0.3))
    would_veto = fire_probability < veto_threshold
    vetoed = would_veto and effective_mode == "active"

    model_id = _latest_model_id(conn, engine)
    # Confluence's "phase" argument is actually a bullish/bearish direction, not a Trap Monitor
    # phase label — logged into its own `direction` column rather than overloading `phase`,
    # since the two concepts differ per engine.
    log_phase = None if engine == CONFLUENCE_ENGINE else phase
    log_direction = phase if engine == CONFLUENCE_ENGINE else None
    log_veto_evaluation(engine, ticker, log_phase, fire_probability, would_veto, effective_mode, model_id, conn,
                         direction=log_direction)

    return RefereeVerdict(fire_probability=fire_probability, vetoed=vetoed, mode=effective_mode, model_available=True)


def get_recent_evaluations(
    engine: str = TRAP_MONITOR_ENGINE,
    limit: int = 25,
    offset: int = 0,
    ticker: Optional[str] = None,
    vetoed: Optional[bool] = None,
) -> list[dict]:
    conn = None
    try:
        conn = get_connection()
        clauses = ["engine=?"]
        params: list = [engine]
        if ticker:
            clauses.append("ticker LIKE ?")
            params.append(f"%{ticker}%")
        if vetoed is not None:
            clauses.append("vetoed=?")
            params.append(int(vetoed))
        params.extend([limit, offset])
        rows = conn.execute(
            f"""SELECT ticker, phase, direction, fire_probability, vetoed, mode, scan_ts
               FROM alert_referee_log WHERE {' AND '.join(clauses)}
               ORDER BY id DESC LIMIT ? OFFSET ?""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_recent_evaluations failed for %s: %s", engine, e)
        return []
    finally:
        if conn:
            conn.close()


def get_referee_summary(engine: str = TRAP_MONITOR_ENGINE) -> dict:
    cfg = _referee_config(engine)
    conn = None
    try:
        conn = get_connection()
        model_row = conn.execute(
            "SELECT * FROM alert_referee_models WHERE engine=? ORDER BY id DESC LIMIT 1", (engine,)
        ).fetchone()
        latest_model = dict(model_row) if model_row else None
        log_row = conn.execute(
            "SELECT COUNT(*) AS total, SUM(vetoed) AS vetoed_count FROM alert_referee_log WHERE engine=?", (engine,)
        ).fetchone()
        log_total = log_row["total"] or 0
        log_vetoed = log_row["vetoed_count"] or 0
    except Exception as e:
        logger.error("get_referee_summary failed for %s: %s", engine, e)
        latest_model, log_total, log_vetoed = None, 0, 0
    finally:
        if conn:
            conn.close()

    return {
        "enabled": cfg.get("ENABLED", False),
        "configured_mode": cfg.get("MODE", "shadow"),
        "veto_threshold": cfg.get("VETO_THRESHOLD", 0.3),
        "min_training_samples": cfg.get("MIN_TRAINING_SAMPLES", 200),
        "readiness": readiness_status(engine),
        "latest_model": latest_model,
        "log_total": log_total,
        "log_vetoed": log_vetoed,
        "recent_log": get_recent_evaluations(engine),
    }
