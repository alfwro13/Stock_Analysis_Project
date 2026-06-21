import json
import logging
from typing import Optional

from database import get_connection

logger = logging.getLogger(__name__)


def get_etf_predictor_configs(include_deleted: bool = False) -> list:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if include_deleted:
            cursor.execute("SELECT * FROM etf_predictor_configs ORDER BY created_at")
        else:
            cursor.execute(
                "SELECT * FROM etf_predictor_configs WHERE deleted_at IS NULL ORDER BY created_at"
            )
        rows = []
        for row in cursor.fetchall():
            r = dict(row)
            try:
                r["constituents"] = json.loads(r["constituents"])
            except Exception:
                r["constituents"] = []
            rows.append(r)
        return rows
    except Exception as e:
        logger.error("Failed to get ETF predictor configs: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def get_etf_predictor_config(config_id: int) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM etf_predictor_configs WHERE id = ? AND deleted_at IS NULL",
            (config_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        r = dict(row)
        try:
            r["constituents"] = json.loads(r["constituents"])
        except Exception:
            r["constituents"] = []
        return r
    except Exception as e:
        logger.error("Failed to get ETF predictor config %s: %s", config_id, e)
        return None
    finally:
        if conn:
            conn.close()


def create_etf_predictor_config(
    name: str,
    etf_ticker: str,
    constituents: list,
    enabled: bool = True,
    auto_schedule: bool = False,
    pre_run_time: str = "13:30",
    post_run_time: str = "22:00",
) -> Optional[int]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO etf_predictor_configs
                   (name, etf_ticker, constituents, enabled, auto_schedule, pre_run_time, post_run_time)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, etf_ticker, json.dumps(constituents),
             1 if enabled else 0, 1 if auto_schedule else 0,
             pre_run_time, post_run_time)
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        logger.error("Failed to create ETF predictor config: %s", e)
        return None
    finally:
        if conn:
            conn.close()


def update_etf_predictor_config(config_id: int, **fields) -> bool:
    if not fields:
        return True
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if "constituents" in fields:
            fields["constituents"] = json.dumps(fields["constituents"])
        if "enabled" in fields:
            fields["enabled"] = 1 if fields["enabled"] else 0
        if "auto_schedule" in fields:
            fields["auto_schedule"] = 1 if fields["auto_schedule"] else 0
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [config_id]
        cursor.execute(
            f"UPDATE etf_predictor_configs SET {set_clause} WHERE id = ? AND deleted_at IS NULL",
            values
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("Failed to update ETF predictor config %s: %s", config_id, e)
        return False
    finally:
        if conn:
            conn.close()


def soft_delete_etf_predictor_config(config_id: int) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE etf_predictor_configs SET deleted_at = datetime('now') WHERE id = ? AND deleted_at IS NULL",
            (config_id,)
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("Failed to soft-delete ETF predictor config %s: %s", config_id, e)
        return False
    finally:
        if conn:
            conn.close()


def log_etf_prediction(config_id: int, result: dict) -> None:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        reg = result.get("regression_engine") or {}
        hold = result.get("holdings_engine") or {}
        # prediction_type computed in run_prediction (knows both exchanges); fall back to signal heuristic
        prediction_type = result.get("prediction_type") or (
            "us_open_impact"
            if result.get("signal_source", "") in ("intraday_premarket", "intraday_live")
            else "next_open"
        )
        run_at = result.get("as_of_utc", "")
        cursor.execute(
            """INSERT INTO etf_predictor_predictions (
                   config_id, run_at, prediction_date, target_date, prediction_type,
                   predicted_price, predicted_change_pct, last_etf_close,
                   holdings_predicted_price, regression_predicted_price,
                   signal_source, data_source, fx_rate, r_squared, constituent_snapshot
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(config_id, target_date, prediction_type) DO NOTHING""",
            (
                config_id,
                run_at,
                run_at[:10] if run_at else "",
                result.get("next_open_date"),
                prediction_type,
                result.get("predicted_price"),
                result.get("predicted_change_pct"),
                result.get("last_etf_close"),
                hold.get("predicted_price"),
                reg.get("predicted_price"),
                result.get("signal_source"),
                result.get("data_source"),
                result.get("fx_rate"),
                reg.get("r_squared"),
                result.get("constituent_snapshot"),
            )
        )
        conn.commit()
    except Exception as e:
        logger.error("Failed to log ETF prediction for config %s: %s", config_id, e, exc_info=True)
        raise
    finally:
        if conn:
            conn.close()


def fill_etf_actual(
    config_id: int,
    target_date: str,
    actual_price: float,
    prediction_type: str = "next_open",
) -> None:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT predicted_price, last_etf_close FROM etf_predictor_predictions
               WHERE config_id = ? AND target_date = ? AND prediction_type = ?
                 AND actual_open IS NULL""",
            (config_id, target_date, prediction_type)
        )
        row = cursor.fetchone()
        if row is None:
            return
        predicted = row["predicted_price"]
        last_close = row["last_etf_close"]
        absolute_error = round(abs(predicted - actual_price), 4) if predicted is not None else None
        pct_error = round(abs(predicted - actual_price) / actual_price * 100, 4) if predicted and actual_price else None
        actual_change_pct = round((actual_price - last_close) / last_close * 100, 4) if last_close else None
        predicted_change_sign = predicted - last_close if predicted and last_close else None
        actual_change_sign = actual_price - last_close if last_close else None
        direction_correct = None
        if predicted_change_sign is not None and actual_change_sign is not None:
            direction_correct = 1 if (predicted_change_sign >= 0) == (actual_change_sign >= 0) else 0
        cursor.execute(
            """UPDATE etf_predictor_predictions SET
                   actual_open = ?, actual_change_pct = ?,
                   absolute_error = ?, pct_error = ?, direction_correct = ?
               WHERE config_id = ? AND target_date = ? AND prediction_type = ?""",
            (actual_price, actual_change_pct, absolute_error, pct_error, direction_correct,
             config_id, target_date, prediction_type)
        )
        conn.commit()
    except Exception as e:
        logger.error("Failed to fill ETF actual for config %s, %s (%s): %s",
                     config_id, target_date, prediction_type, e)
    finally:
        if conn:
            conn.close()


def get_etf_accuracy(config_id: int) -> dict:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        def _type_stats(ptype: str) -> dict:
            cursor.execute(
                """SELECT * FROM etf_predictor_predictions
                   WHERE config_id = ? AND prediction_type = ?
                   ORDER BY target_date DESC LIMIT 60""",
                (config_id, ptype)
            )
            rows = [dict(r) for r in cursor.fetchall()]
            cursor.execute(
                """SELECT COUNT(*) as total,
                          SUM(CASE WHEN actual_open IS NOT NULL THEN 1 ELSE 0 END) as resolved,
                          AVG(CASE WHEN direction_correct IS NOT NULL THEN direction_correct END) as dir_acc,
                          AVG(CASE WHEN absolute_error IS NOT NULL THEN absolute_error END) as mae,
                          AVG(CASE WHEN pct_error IS NOT NULL THEN pct_error END) as mape
                   FROM etf_predictor_predictions WHERE config_id = ? AND prediction_type = ?""",
                (config_id, ptype)
            )
            agg = dict(cursor.fetchone())

            def _window_dir(n: int) -> Optional[float]:
                cursor.execute(
                    """SELECT AVG(direction_correct) FROM (
                           SELECT direction_correct FROM etf_predictor_predictions
                           WHERE config_id = ? AND prediction_type = ? AND direction_correct IS NOT NULL
                           ORDER BY target_date DESC LIMIT ?
                       )""",
                    (config_id, ptype, n)
                )
                val = cursor.fetchone()[0]
                return round(val * 100, 1) if val is not None else None

            return {
                "rows": rows,
                "summary": {
                    "total_predictions": agg["total"] or 0,
                    "resolved_count": agg["resolved"] or 0,
                    "direction_accuracy_pct": round(agg["dir_acc"] * 100, 1) if agg["dir_acc"] is not None else None,
                    "mae": round(agg["mae"], 4) if agg["mae"] is not None else None,
                    "mape_pct": round(agg["mape"], 2) if agg["mape"] is not None else None,
                    "last_10_direction_pct": _window_dir(10),
                    "last_30_direction_pct": _window_dir(30),
                },
            }

        return {
            "next_open": _type_stats("next_open"),
            "us_open_impact": _type_stats("us_open_impact"),
        }
    except Exception as e:
        logger.error("Failed to get ETF accuracy for config %s: %s", config_id, e)
        def _empty():
            return {"rows": [], "summary": {
                "total_predictions": 0, "resolved_count": 0,
                "direction_accuracy_pct": None, "mae": None, "mape_pct": None,
                "last_10_direction_pct": None, "last_30_direction_pct": None,
            }}
        return {"next_open": _empty(), "us_open_impact": _empty()}
    finally:
        if conn:
            conn.close()
