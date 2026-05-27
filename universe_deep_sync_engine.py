# universe_deep_sync_engine.py
"""
Universe Deep Sync — unified weekly pipeline orchestrator.

Single entry point used by both the APScheduler weekly job (registered in
scheduler_engine.py) and the manual "▶️ Run Sync Now" button (api_routes.py).
Sequences the existing per-domain engines so the universe of FTSE100 + S&P500
index stocks ends each weekly run with:

    1. stock_signals.peter_lynch_peg populated (universe_fundamentals_engine)
    2. ticker_metadata.market_cap populated  (ai_prediction_engine.sync_ticker_metadata)
    3. quant_signals technicals freshly computed (quant_engine.run_daily_quant_scan)
    4. quant_signals.ml_confidence_score scored (ai_prediction_engine.update_daily_ml_predictions)

These four data points are exactly what the GARP "Tenbaggers" market report
filters on.

Order matters — ML inference (Stage 4) requires both fundamentals (Stage 1) and
technicals (Stage 3) to be in place.

Freetrade firewall: when UI_PREFERENCES.FREETRADE_ONLY_MODE is enabled in
config, the universe is further restricted to is_freetrade = 1 tickers so
yfinance calls are not spent on assets that cannot actually be traded.

Failure handling: each stage is wrapped in its own try/except so a partial
failure (e.g. yfinance rate-limit during metadata) does not abort the entire
pipeline. Per-stage status is captured and posted to system_notifications.

Idempotency: the underlying quant_engine.run_daily_quant_scan uses
quant_scan_states keyed by (scan_date, scan_type='universe_deep_sync'),
so a re-run within the same calendar day skips the technicals stage if it
already completed. This is intentional — prevents wasted yfinance calls when
"Run Now" is clicked twice.
"""
import logging
from typing import List

from config import load_config
from database import get_connection, log_notification

logger = logging.getLogger(__name__)


def _get_universe_target_tickers(freetrade_firewall: bool) -> List[str]:
    """
    Return the complete universe ticker list for downstream stages 2-4.

    This is INTENTIONALLY DIFFERENT from universe_fundamentals_engine's
    _get_pending_tickers (which is fundamentals-only and excludes HARDCODED
    rows). Stages 2-4 (metadata, technicals, ML) refresh the entire universe
    on every weekly run — HARDCODED portfolio/watchlist tickers in the universe
    are included so their technicals stay fresh.

    Selection rules:
      - is_index = 1 defines the universe scope (FTSE100 + S&P500 + future indexes)
      - When freetrade_firewall is True, additionally require is_freetrade = 1
    """
    conn = get_connection()
    try:
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
        conn.close()


def run_universe_deep_sync() -> None:
    """
    Execute the full universe deep sync pipeline:
        Stage 1: Fundamentals  → stock_signals (incl. peter_lynch_peg)
        Stage 2: Metadata      → ticker_metadata (market_cap, sector, beta)
        Stage 3: Technicals    → quant_signals (rsi_14, macd, sma_50, sma_200, ...)
        Stage 4: ML Inference  → quant_signals.ml_confidence_score

    Triggered weekly by APScheduler (cron) or manually via the Settings UI
    "Run Sync Now" button. Approximate runtime: 30-45 minutes for ~600 tickers
    at the polite rate limits used by each downstream engine.
    """
    # ─────────────────────────────────────────────────────────────────────
    # CONFIGURATION & TARGETING
    # ─────────────────────────────────────────────────────────────────────
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
    logger.info(
        f"Universe Deep Sync started — {len(target_tickers)} tickers{firewall_note}"
    )

    stage_status: dict = {
        "fundamentals":       "PENDING",
        "metadata":           "PENDING",
        "technicals":         "PENDING",
        "momentum_backfill":  "PENDING",
        "ml_inference":       "PENDING",
    }

    # ─────────────────────────────────────────────────────────────────────
    # STAGE 1 of 4 — FUNDAMENTALS (writes stock_signals incl. peter_lynch_peg)
    # ─────────────────────────────────────────────────────────────────────
    logger.info("[Stage 1/4] Universe fundamentals sync starting...")
    try:
        from universe_fundamentals_engine import run_universe_fundamentals_sync
        # batch_size=2000 effectively means "process all pending" — the queue
        # is naturally bounded by universe size (~600 tickers max).
        run_universe_fundamentals_sync(
            batch_size=2000,
            freetrade_firewall=freetrade_firewall,
        )
        stage_status["fundamentals"] = "OK"
        logger.info("[Stage 1/4] Fundamentals sync completed.")
    except Exception as e:
        stage_status["fundamentals"] = f"FAILED ({type(e).__name__})"
        logger.error(f"[Stage 1/4] Fundamentals stage FAILED: {e}", exc_info=True)
        log_notification(
            "Error",
            f"Universe Deep Sync — Stage 1 (Fundamentals) failed: {e}"
        )

    # ─────────────────────────────────────────────────────────────────────
    # STAGE 2 of 4 — METADATA (writes ticker_metadata.market_cap/sector/beta)
    # ─────────────────────────────────────────────────────────────────────
    logger.info("[Stage 2/4] Ticker metadata sync starting...")
    try:
        from ai_prediction_engine import sync_ticker_metadata
        sync_ticker_metadata(target_tickers)
        stage_status["metadata"] = "OK"
        logger.info("[Stage 2/4] Metadata sync completed.")
    except Exception as e:
        stage_status["metadata"] = f"FAILED ({type(e).__name__})"
        logger.error(f"[Stage 2/4] Metadata stage FAILED: {e}", exc_info=True)
        log_notification(
            "Error",
            f"Universe Deep Sync — Stage 2 (Metadata) failed: {e}"
        )

# ─────────────────────────────────────────────────────────────────────
    # STAGE 3 of 5 — TECHNICALS (writes basic quant_signals columns)
    # ─────────────────────────────────────────────────────────────────────
    logger.info("[Stage 3/5] Technicals quant scan starting...")
    try:
        from quant_engine import run_daily_quant_scan
        # Distinct scan_type isolates this pipeline's resumability state
        # from the daily portfolio/watchlist scan (which uses 'daily').
        run_daily_quant_scan(target_tickers, scan_type='universe_deep_sync')
        stage_status["technicals"] = "OK"
        logger.info("[Stage 3/5] Technicals scan completed.")
    except Exception as e:
        stage_status["technicals"] = f"FAILED ({type(e).__name__})"
        logger.error(f"[Stage 3/5] Technicals stage FAILED: {e}", exc_info=True)
        log_notification(
            "Error",
            f"Universe Deep Sync — Stage 3 (Technicals) failed: {e}"
        )

    # ─────────────────────────────────────────────────────────────────────
    # STAGE 4 of 5 — MOMENTUM/VOL/RS BACKFILL (writes the 8 ML-required features:
    #   mom_1m, mom_3m, mom_6m, mom_12m_skip1m, atr_pct, hist_vol_20,
    #   rel_strength_5d, rel_strength_20d)
    # Without this stage, Stage 5 (ML inference) silently skips every universe
    # ticker because the model's 24-feature input set is incomplete.
    # ─────────────────────────────────────────────────────────────────────
    logger.info("[Stage 4/5] Momentum/Vol/RS backfill starting...")
    try:
        from ai_prediction_engine import run_historical_backfill
        run_historical_backfill(tickers=target_tickers)
        stage_status["momentum_backfill"] = "OK"
        logger.info("[Stage 4/5] Momentum backfill completed.")
    except Exception as e:
        stage_status["momentum_backfill"] = f"FAILED ({type(e).__name__})"
        logger.error(f"[Stage 4/5] Momentum backfill stage FAILED: {e}", exc_info=True)
        log_notification(
            "Error",
            f"Universe Deep Sync — Stage 4 (Momentum Backfill) failed: {e}"
        )

    # ─────────────────────────────────────────────────────────────────────
    # STAGE 5 of 5 — ML INFERENCE (writes quant_signals.ml_confidence_score)
    # ─────────────────────────────────────────────────────────────────────
    logger.info("[Stage 5/5] ML inference starting...")
    try:
        from ai_prediction_engine import update_daily_ml_predictions
        update_daily_ml_predictions(target_tickers)
        stage_status["ml_inference"] = "OK"
        logger.info("[Stage 5/5] ML inference completed.")
    except Exception as e:
        stage_status["ml_inference"] = f"FAILED ({type(e).__name__})"
        logger.error(f"[Stage 5/5] ML inference stage FAILED: {e}", exc_info=True)
        log_notification(
            "Error",
            f"Universe Deep Sync — Stage 5 (ML Inference) failed: {e}"
        )

    # ─────────────────────────────────────────────────────────────────────
    # SUMMARY
    # ─────────────────────────────────────────────────────────────────────
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
    # CLI test entry: python universe_deep_sync_engine.py
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    run_universe_deep_sync()
