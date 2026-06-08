# ML inference (Stage 5) requires fundamentals (Stage 1) + momentum backfill (Stage 4) + technicals (Stage 3) — order is mandatory.
import logging
from typing import List

from config import load_config
from database import get_connection, log_notification

logger = logging.getLogger(__name__)


def _get_universe_target_tickers(freetrade_firewall: bool) -> List[str]:
    # Different from universe_fundamentals_engine._get_pending_tickers: includes HARDCODED rows so portfolio/watchlist technicals stay fresh.
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        firewall_clause = "AND is_freetrade = 1" if freetrade_firewall else ""
        query = f"""
            SELECT ticker
            FROM market_universe
            WHERE is_index = 1
              {firewall_clause}
            ORDER BY ticker
        """
        cursor.execute(query)
        return [r['ticker'] for r in cursor.fetchall()]
    finally:
        if conn:
            conn.close()


def run_universe_deep_sync() -> None:
    # 5-stage weekly pipeline: fundamentals → metadata → technicals → momentum backfill → ML inference.
    config = load_config()
    freetrade_firewall: bool = bool(
        config.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)
    )

    target_tickers: List[str] = _get_universe_target_tickers(freetrade_firewall)
    if not target_tickers:
        msg = (
            "Universe Deep Sync aborted: no eligible target tickers found. "
            "Check market_universe table and is_index flag."
        )
        logger.warning(msg)
        log_notification("Warning", msg)
        return

    firewall_note = (
        " (Freetrade firewall: ON)" if freetrade_firewall else " (Freetrade firewall: OFF)"
    )
    log_notification(
        "Scheduler",
        f"Universe Deep Sync pipeline started — {len(target_tickers)} target tickers{firewall_note}."
    )
    logger.info("Universe Deep Sync started — %s tickers%s", len(target_tickers), firewall_note)

    stage_status: dict = {
        "fundamentals":       "PENDING",
        "metadata":           "PENDING",
        "technicals":         "PENDING",
        "momentum_backfill":  "PENDING",
        "ml_inference":       "PENDING",
    }

    logger.info("[Stage 1/5] Universe fundamentals sync starting...")
    try:
        from universe_fundamentals_engine import run_universe_fundamentals_sync
        # batch_size=2000 effectively means "process all pending" — queue bounded by universe size (~600 tickers).
        run_universe_fundamentals_sync(
            batch_size=2000,
            freetrade_firewall=freetrade_firewall,
        )
        stage_status["fundamentals"] = "OK"
        logger.info("[Stage 1/5] Fundamentals sync completed.")
    except Exception as e:
        stage_status["fundamentals"] = f"FAILED ({type(e).__name__})"
        logger.error("[Stage 1/5] Fundamentals stage FAILED: %s", e, exc_info=True)
        log_notification(
            "Error",
            f"Universe Deep Sync — Stage 1 (Fundamentals) failed: {e}"
        )

    logger.info("[Stage 2/5] Ticker metadata sync starting...")
    try:
        from ai_prediction_engine import sync_ticker_metadata
        sync_ticker_metadata(target_tickers)
        stage_status["metadata"] = "OK"
        logger.info("[Stage 2/5] Metadata sync completed.")
    except Exception as e:
        stage_status["metadata"] = f"FAILED ({type(e).__name__})"
        logger.error("[Stage 2/5] Metadata stage FAILED: %s", e, exc_info=True)
        log_notification(
            "Error",
            f"Universe Deep Sync — Stage 2 (Metadata) failed: {e}"
        )

    logger.info("[Stage 3/5] Technicals quant scan starting...")
    try:
        from quant_engine import run_daily_quant_scan
        # Distinct scan_type isolates this pipeline's resumability state from the daily scan (which uses 'daily').
        run_daily_quant_scan(target_tickers, scan_type='universe_deep_sync')
        stage_status["technicals"] = "OK"
        logger.info("[Stage 3/5] Technicals scan completed.")
    except Exception as e:
        stage_status["technicals"] = f"FAILED ({type(e).__name__})"
        logger.error("[Stage 3/5] Technicals stage FAILED: %s", e, exc_info=True)
        log_notification(
            "Error",
            f"Universe Deep Sync — Stage 3 (Technicals) failed: {e}"
        )

    logger.info("[Stage 4/5] Momentum/Vol/RS backfill starting...")
    try:
        from ai_prediction_engine import run_historical_backfill
        # Without this stage, Stage 5 silently skips every universe ticker because the 18-feature ML input set is incomplete.
        run_historical_backfill(tickers=target_tickers)
        stage_status["momentum_backfill"] = "OK"
        logger.info("[Stage 4/5] Momentum backfill completed.")
    except Exception as e:
        stage_status["momentum_backfill"] = f"FAILED ({type(e).__name__})"
        logger.error("[Stage 4/5] Momentum backfill stage FAILED: %s", e, exc_info=True)
        log_notification(
            "Error",
            f"Universe Deep Sync — Stage 4 (Momentum Backfill) failed: {e}"
        )

    logger.info("[Stage 5/5] ML inference starting...")
    try:
        from ai_prediction_engine import update_daily_ml_predictions
        update_daily_ml_predictions(target_tickers)
        stage_status["ml_inference"] = "OK"
        logger.info("[Stage 5/5] ML inference completed.")
    except Exception as e:
        stage_status["ml_inference"] = f"FAILED ({type(e).__name__})"
        logger.error("[Stage 5/5] ML inference stage FAILED: %s", e, exc_info=True)
        log_notification(
            "Error",
            f"Universe Deep Sync — Stage 5 (ML Inference) failed: {e}"
        )

    summary_line = " | ".join(f"{k}={v}" for k, v in stage_status.items())
    all_ok: bool = all(v == "OK" for v in stage_status.values())

    if all_ok:
        msg = f"Universe Deep Sync COMPLETED. {summary_line}"
        log_notification("Success", msg)
        logger.info(msg)
    else:
        msg = f"Universe Deep Sync FINISHED WITH FAILURES. {summary_line}"
        log_notification("Warning", msg)
        logger.warning(msg)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    run_universe_deep_sync()
