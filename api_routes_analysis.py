import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd
import numpy as np
import time_engine
from fastapi import APIRouter, BackgroundTasks, Path as PathParam, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api_deps import limiter, _error_500

from config import HISTORICAL_DIR
from database import get_connection, get_auction_summary
from ai_engine import AIPromptEngine
from ai_regime_engine import AIRegimePromptEngine
from ai_sentiment_engine import AISentimentPromptEngine
from data_engine import DataEngine
from sentiment_engine import get_latest_fear_greed
from utils import normalize_ticker, safe_ticker_filename
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

analysis_router = APIRouter()


@analysis_router.get("/ai-contagion/status")
async def get_ai_contagion_status():
    """Returns the last 20 AI Contagion scan snapshots for the market-sentiment status panel."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT scan_ts, leader_count, etf_count, alert_fired, payload_json "
            "FROM ai_contagion_snapshots ORDER BY scan_ts DESC LIMIT 20"
        )
        rows = cursor.fetchall()
        def _parse_payload(raw_json: str) -> tuple:
            raw = json.loads(raw_json or '{"tickers":[],"severity_score":0.0}')
            if isinstance(raw, list):
                return raw, 0.0
            return raw.get("tickers", []), raw.get("severity_score", 0.0)

        snapshots = []
        for row in rows:
            tickers, severity_score = _parse_payload(row["payload_json"])
            snapshots.append({
                "scan_ts": row["scan_ts"],
                "leader_count": row["leader_count"],
                "etf_count": row["etf_count"],
                "alert_fired": bool(row["alert_fired"]),
                "tickers": tickers,
                "severity_score": severity_score,
            })
        return JSONResponse(content={"status": "success", "snapshots": snapshots})
    except Exception as e:
        logger.error(f"ai-contagion/status failed: {e}")
        return _error_500(e)
    finally:
        if conn:
            conn.close()


@analysis_router.post("/ai-contagion/trigger")
@limiter.limit("4/minute")
async def trigger_ai_contagion(request: Request, background_tasks: BackgroundTasks):
    """Manually triggers an AI Contagion scan in the background (useful for testing)."""
    try:
        from scheduler_engine import run_ai_contagion_job
        background_tasks.add_task(run_ai_contagion_job)
        return JSONResponse(content={"status": "success", "message": "AI Contagion scan triggered."})
    except Exception as e:
        logger.error(f"Failed to trigger AI Contagion scan: {e}")
        return _error_500(e)


@analysis_router.get("/trap-monitor/results")
@limiter.limit("20/minute")
async def get_trap_monitor_results(request: Request):
    """Returns all trap monitor scan results ordered by phase severity (most severe first)."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trap_monitor_results ORDER BY scan_ts DESC")
        rows = [dict(r) for r in cursor.fetchall()]
        from bull_bear_trap_engine import _phase_severity
        rows.sort(key=lambda r: _phase_severity(r.get("phase", "NEUTRAL")))
        return JSONResponse(content={"status": "success", "results": rows})
    except Exception as e:
        logger.error("trap-monitor/results failed: %s", e)
        return _error_500(e)
    finally:
        if conn:
            conn.close()


@analysis_router.post("/trap-monitor/run")
@limiter.limit("4/minute")
async def run_trap_monitor(request: Request, background_tasks: BackgroundTasks):
    """Manually triggers a Trap Monitor scan in the background."""
    try:
        from scheduler_engine import run_trap_monitor_job
        background_tasks.add_task(run_trap_monitor_job)
        return JSONResponse(content={"status": "success", "message": "Trap Monitor scan triggered."})
    except Exception as e:
        logger.error("Failed to trigger Trap Monitor scan: %s", e)
        return _error_500(e)


@analysis_router.get("/trap-monitor/accuracy")
@limiter.limit("20/minute")
async def get_trap_monitor_accuracy(request: Request):
    """Returns per-phase prediction accuracy at 14-day and 30-day horizons."""
    from database import get_trap_phase_accuracy
    data = get_trap_phase_accuracy()
    return JSONResponse(content={"status": "success", **data})


@analysis_router.get("/alert-referee/status")
@limiter.limit("20/minute")
async def get_alert_referee_status(request: Request):
    """Returns Alert Confidence Referee readiness, latest trained model, and recent shadow-mode log for the Trap Monitor pilot."""
    try:
        from alert_referee_engine import get_referee_summary, TRAP_MONITOR_ENGINE
        data = get_referee_summary(TRAP_MONITOR_ENGINE)
        return JSONResponse(content={"status": "success", **data})
    except Exception as e:
        logger.error("alert-referee/status failed: %s", e)
        return _error_500(e)


@analysis_router.post("/alert-referee/train")
@limiter.limit("4/minute")
async def train_alert_referee(request: Request, background_tasks: BackgroundTasks):
    """Manually triggers Alert Confidence Referee training in the background (the 'Run Now' Settings action)."""
    try:
        from scheduler_engine import run_alert_referee_training_job
        background_tasks.add_task(run_alert_referee_training_job)
        return JSONResponse(content={"status": "success", "message": "Alert Confidence Referee training triggered."})
    except Exception as e:
        logger.error("Failed to trigger Alert Confidence Referee training: %s", e)
        return _error_500(e)


@analysis_router.get("/bubble-radar/data")
@limiter.limit("20/minute")
async def get_bubble_radar_data(request: Request):
    """Returns all currently-flagged tickers with their latest bubble metrics."""
    from bubble_radar_engine import get_bubble_radar_data
    data = get_bubble_radar_data()
    return JSONResponse(content={"status": "success", "results": data})


@analysis_router.get("/bubble-radar/ticker/{ticker}")
@limiter.limit("20/minute")
async def get_bubble_radar_ticker(request: Request, ticker: str):
    """Returns the latest bubble metrics and per-metric score breakdown for a single ticker."""
    from bubble_radar_engine import get_bubble_ticker_detail
    data = get_bubble_ticker_detail(ticker)
    if data is None:
        return JSONResponse(content={"status": "success", "result": None})
    return JSONResponse(content={"status": "success", "result": data})


@analysis_router.get("/bubble-radar/history")
@limiter.limit("10/minute")
async def get_bubble_radar_history(request: Request):
    """Returns historical bubble flag events with outcome tracking."""
    from bubble_radar_engine import get_bubble_radar_history
    data = get_bubble_radar_history()
    return JSONResponse(content={"status": "success", "results": data})


@analysis_router.post("/bubble-radar/run")
@limiter.limit("4/minute")
async def run_bubble_radar(request: Request, background_tasks: BackgroundTasks):
    """Manually triggers a Bubble Radar scan in the background."""
    try:
        from scheduler_engine import run_bubble_radar_job
        background_tasks.add_task(run_bubble_radar_job)
        return JSONResponse(content={"status": "success", "message": "Bubble Radar scan triggered."})
    except Exception as e:
        logger.error("Failed to trigger Bubble Radar scan: %s", e)
        return _error_500(e)


@analysis_router.get("/pairs-spread/results")
@limiter.limit("20/minute")
async def get_pairs_spread_results(
    request: Request,
    scope: str = Query(default="portfolio_watchlist", pattern=r"^(portfolio_watchlist|universe)$"),
):
    """Returns all monitored pairs from the latest scan for `scope`, ordered by absolute z-score (most divergent first), enriched with company names."""
    from db_helpers import get_company_names
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pairs_spread_results WHERE scope = ?", (scope,))
        rows = [dict(r) for r in cursor.fetchall()]
        rows.sort(key=lambda r: abs(r.get("zscore") or 0), reverse=True)

        tickers = sorted({r["ticker_a"] for r in rows} | {r["ticker_b"] for r in rows})
        names = get_company_names(tickers)
        for r in rows:
            r["company_name_a"] = names.get(r["ticker_a"])
            r["company_name_b"] = names.get(r["ticker_b"])

        return JSONResponse(content={"status": "success", "results": rows})
    except Exception as e:
        logger.error("pairs-spread/results failed: %s", e)
        return _error_500(e)
    finally:
        if conn:
            conn.close()


@analysis_router.post("/pairs-spread/run")
@limiter.limit("4/minute")
async def run_pairs_spread_scan(request: Request, background_tasks: BackgroundTasks):
    """Manually triggers a Pairs Spread Monitor scan (Portfolio + Watchlist scope) in the background."""
    try:
        from scheduler_engine import run_pairs_spread_monitor_job
        background_tasks.add_task(run_pairs_spread_monitor_job)
        return JSONResponse(content={"status": "success", "message": "Pairs Spread Monitor scan triggered."})
    except Exception as e:
        logger.error("Failed to trigger Pairs Spread Monitor scan: %s", e)
        return _error_500(e)


@analysis_router.post("/pairs-spread/run-universe")
@limiter.limit("2/minute")
async def run_pairs_spread_universe(request: Request, background_tasks: BackgroundTasks):
    """Manually triggers an on-demand-only full market-universe Pairs Spread scan in the background. No scheduled equivalent — too expensive to run nightly."""
    try:
        from scheduler_engine import run_pairs_spread_universe_scan
        background_tasks.add_task(run_pairs_spread_universe_scan)
        return JSONResponse(content={"status": "success", "message": "Pairs Spread Monitor universe scan triggered — this can take a minute or two."})
    except Exception as e:
        logger.error("Failed to trigger Pairs Spread Monitor universe scan: %s", e)
        return _error_500(e)


@analysis_router.get("/pairs-spread/chart/{ticker_a}/{ticker_b}")
@limiter.limit("20/minute")
async def get_pairs_spread_chart(request: Request, ticker_a: str, ticker_b: str):
    """Returns aligned normalized price series for both tickers plus correlation/z-score, recomputed on demand from parquet."""
    try:
        from pairs_spread_engine import build_chart_series
        data = build_chart_series(normalize_ticker(ticker_a), normalize_ticker(ticker_b))
        if data is None:
            return JSONResponse(content={"status": "error", "message": "Not enough overlapping price history for this pair."}, status_code=404)
        return JSONResponse(content={"status": "success", "chart": data})
    except Exception as e:
        logger.error("pairs-spread/chart failed: %s", e)
        return _error_500(e)


@analysis_router.get("/pattern-detection/results")
@limiter.limit("20/minute")
async def get_pattern_detection_results(request: Request, family: Optional[str] = Query(None)):
    """Returns all current pattern candidates across every registered family (or one family
    if `family` is given), confirmed patterns first. Each result carries a `direction`
    ("up"/"down") resolved from its family's PATTERN_TYPES registry entry, and the response
    also carries the current Portfolio/Watchlist ticker sets so the page can filter by scope
    without a second round-trip."""
    from pattern_detection_engine import DETECTORS
    from accounts_engine import get_combined_holdings
    from database import get_watchlist_tickers
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if family:
            cursor.execute(
                "SELECT * FROM pattern_detection_results WHERE pattern_family = ? ORDER BY phase = 'CONFIRMED' DESC, scan_ts DESC",
                (family,),
            )
        else:
            cursor.execute("SELECT * FROM pattern_detection_results ORDER BY phase = 'CONFIRMED' DESC, scan_ts DESC")
        rows = []
        for r in cursor.fetchall():
            row = dict(r)
            row["points"] = json.loads(row.pop("points_json") or "[]")
            row["lines"] = json.loads(row.pop("lines_json") or "[]")
            module = DETECTORS.get(row["pattern_family"])
            row["direction"] = module.PATTERN_TYPES.get(row["pattern_type"]) if module else None
            rows.append(row)

        portfolio_tickers = sorted({str(t).upper() for t in get_combined_holdings().keys()})
        watchlist_tickers = sorted({str(t).upper() for t in get_watchlist_tickers()})

        return JSONResponse(content={
            "status": "success", "results": rows,
            "portfolio_tickers": portfolio_tickers, "watchlist_tickers": watchlist_tickers,
        })
    except Exception as e:
        logger.error("pattern-detection/results failed: %s", e)
        return _error_500(e)
    finally:
        if conn:
            conn.close()


@analysis_router.post("/pattern-detection/run")
@limiter.limit("4/minute")
async def run_pattern_detection_scan(request: Request, background_tasks: BackgroundTasks):
    """Manually triggers a Pattern Detection scan (every registered family) in the background."""
    try:
        from scheduler_engine import run_pattern_detection_job
        background_tasks.add_task(run_pattern_detection_job)
        return JSONResponse(content={"status": "success", "message": "Pattern Detection scan triggered."})
    except Exception as e:
        logger.error("Failed to trigger Pattern Detection scan: %s", e)
        return _error_500(e)


@analysis_router.post("/pattern-detection/backfill")
@limiter.limit("1/minute")
async def run_pattern_detection_backfill(request: Request, background_tasks: BackgroundTasks):
    """Manually triggers a one-time historical backtest over each monitored ticker's full parquet history — can take several minutes."""
    try:
        from pattern_detection_engine import backfill_historical_patterns
        background_tasks.add_task(backfill_historical_patterns)
        return JSONResponse(content={"status": "success", "message": "Pattern Detection historical backfill triggered — this can take several minutes."})
    except Exception as e:
        logger.error("Failed to trigger Pattern Detection historical backfill: %s", e)
        return _error_500(e)


@analysis_router.get("/pattern-detection/accuracy")
@limiter.limit("20/minute")
async def get_pattern_detection_accuracy_route(request: Request, family: Optional[str] = Query(None)):
    """Returns per-family, per-pattern-type prediction accuracy at 14-day and 30-day horizons."""
    from database import get_pattern_detection_accuracy
    data = get_pattern_detection_accuracy(family)
    return JSONResponse(content={"status": "success", **data})


@analysis_router.get("/pattern-detection/chart/{ticker}")
@limiter.limit("20/minute")
async def get_pattern_detection_chart(request: Request, ticker: str):
    """Returns the ticker's recent daily close series plus every currently-active pattern's
    stored geometry (points/lines) across all registered families, for client-side overlay
    on a single chart. Each pattern carries a `direction` ("up"/"down") resolved from its
    family's PATTERN_TYPES registry entry, so the frontend can group patterns into
    Bullish/Bearish without hardcoding any family-specific knowledge."""
    from pattern_detection_engine import DETECTORS
    conn = None
    try:
        ticker = normalize_ticker(ticker)
        safe_ticker = safe_ticker_filename(ticker)
        if not safe_ticker:
            return JSONResponse(content={"status": "error", "message": "Invalid ticker."}, status_code=400)
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pattern_detection_results WHERE ticker = ?", (ticker,))
        rows = cursor.fetchall()
        if not rows:
            return JSONResponse(content={"status": "error", "message": "No pattern on file for this ticker."}, status_code=404)

        patterns = []
        for r in rows:
            pattern = dict(r)
            pattern["points"] = json.loads(pattern.pop("points_json") or "[]")
            pattern["lines"] = json.loads(pattern.pop("lines_json") or "[]")
            module = DETECTORS.get(pattern["pattern_family"])
            pattern["direction"] = module.PATTERN_TYPES.get(pattern["pattern_type"]) if module else None
            patterns.append(pattern)

        path = HISTORICAL_DIR / f"{safe_ticker}.parquet"
        if not path.exists():
            return JSONResponse(content={"status": "error", "message": "No price history on file for this ticker."}, status_code=404)
        df = pd.read_parquet(path, columns=["Close"]).dropna().tail(180)
        series = {
            "dates": [d.strftime("%Y-%m-%d") for d in df.index],
            "close": [round(float(c), 4) for c in df["Close"]],
        }
        return JSONResponse(content={"status": "success", "series": series, "patterns": patterns})
    except Exception as e:
        logger.error("pattern-detection/chart failed: %s", e)
        return _error_500(e)
    finally:
        if conn:
            conn.close()


@analysis_router.get("/predicted-movers/leaderboard")
@limiter.limit("20/minute")
async def get_predicted_movers_leaderboard(
    request: Request,
    scope: str = Query(default="portfolio_watchlist", pattern=r"^(portfolio_watchlist|universe)$"),
    sort: str = Query(default="movers", pattern=r"^(gainers|losers|movers)$"),
    limit: int = Query(default=200, ge=1, le=1000),
):
    """Returns tickers ranked by ML-predicted 10-trading-day forward % move (quantile band midpoint vs current price) for `scope`, sorted by `sort`."""
    try:
        from predicted_movers_engine import get_leaderboard
        results = get_leaderboard(scope=scope, sort_mode=sort, limit=limit)
        return JSONResponse(content={"status": "success", "results": results})
    except Exception as e:
        logger.error("predicted-movers/leaderboard failed: %s", e)
        return _error_500(e)


@analysis_router.get("/predicted-movers/accuracy")
@limiter.limit("20/minute")
async def get_predicted_movers_accuracy_data(request: Request):
    """Returns per-ticker + overall direction-match and within-band-match hit rates for logged Portfolio+Watchlist predictions."""
    try:
        from predicted_movers_engine import get_accuracy_summary
        data = get_accuracy_summary()
        return JSONResponse(content={"status": "success", **data})
    except Exception as e:
        logger.error("predicted-movers/accuracy failed: %s", e)
        return _error_500(e)


@analysis_router.get("/earnings-volatility/accuracy")
@limiter.limit("20/minute")
async def get_earnings_drift_accuracy_data(request: Request):
    """Returns per-ticker + overall direction-match hit rates at 1/5/20 trading days for logged post-earnings drift predictions."""
    try:
        from earnings_vol_engine import get_earnings_drift_accuracy_summary
        data = get_earnings_drift_accuracy_summary()
        return JSONResponse(content={"status": "success", **data})
    except Exception as e:
        logger.error("earnings-volatility/accuracy failed: %s", e)
        return _error_500(e)


@analysis_router.get("/forensic-scores")
@limiter.limit("20/minute")
async def get_forensic_scores(request: Request):
    """Returns Piotroski F-Score, Altman Z-Score, and Beneish M-Score for all portfolio and watchlist tickers."""
    conn = None
    try:
        from accounts_engine import get_combined_holdings
        engine = DataEngine()
        portfolio_tickers = {normalize_ticker(t) for t in get_combined_holdings().keys() if t}
        watchlist_tickers = {
            normalize_ticker(t)
            for t in (engine.watchlist.get("watchlist") or [])
            if t
        }

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ss.ticker, ss.company_name, ss.sector,
                   ss.piotroski_f_score, ss.altman_z_score, ss.beneish_m_score, ss.forensic_last_updated
            FROM stock_signals ss
            LEFT JOIN asset_profiles ap ON ap.ticker = ss.ticker
            WHERE (ss.score_method != 'UNIVERSE_FUNDAMENTALS' OR ss.score_method IS NULL)
              AND (ap.quote_type = 'EQUITY' OR ap.quote_type IS NULL OR ap.quote_type = 'NONE')
            ORDER BY ss.ticker
        """)
        rows = cursor.fetchall()
        results = []
        for r in rows:
            f = r['piotroski_f_score']
            z = r['altman_z_score']
            m = r['beneish_m_score']
            t = r['ticker']
            in_portfolio = t in portfolio_tickers
            in_watchlist  = t in watchlist_tickers
            source = "portfolio" if in_portfolio else ("watchlist" if in_watchlist else "other")
            results.append({
                "ticker":               t,
                "company_name":         r['company_name'] or t,
                "sector":               r['sector'] or 'Unknown',
                "piotroski_f_score":    f,
                "altman_z_score":       z,
                "beneish_m_score":      m,
                "forensic_last_updated": (
                    time_engine.fmt_datetime(
                        datetime.strptime(r['forensic_last_updated'], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    ) if r['forensic_last_updated'] else None
                ),
                "flag_piotroski":       f is not None and f < 4,
                "flag_altman":          z is not None and z < 1.81,
                "flag_beneish":         m is not None and m > -1.78,
                "source":               source,
            })
        return JSONResponse(content={"status": "success", "results": results})
    except Exception as e:
        logger.error("Failed to fetch forensic scores: %s", e)
        return _error_500(e)
    finally:
        if conn:
            conn.close()


@analysis_router.post("/forensic-scores/run-fetch")
@limiter.limit("4/minute")
async def trigger_forensic_fetch(request: Request, background_tasks: BackgroundTasks):
    """Manually triggers the Forensic Quarterly Data Fetch in the background."""
    try:
        from scheduler_engine import run_forensic_quarterly_fetch_job, _with_job_source
        background_tasks.add_task(_with_job_source("forensic_quarterly_fetch_job", run_forensic_quarterly_fetch_job))
        return JSONResponse(content={"status": "success", "message": "Forensic Quarterly Data Fetch triggered."})
    except Exception as e:
        logger.error("Failed to trigger Forensic Quarterly Data Fetch: %s", e)
        return _error_500(e)


@analysis_router.post("/forensic-scores/run-score")
@limiter.limit("4/minute")
async def trigger_forensic_scores(request: Request, background_tasks: BackgroundTasks):
    """Manually triggers the Forensic Accounting Scores computation in the background."""
    try:
        from scheduler_engine import run_forensic_scores_job, _with_job_source
        background_tasks.add_task(_with_job_source("forensic_scores_job", run_forensic_scores_job))
        return JSONResponse(content={"status": "success", "message": "Forensic Accounting Scores triggered."})
    except Exception as e:
        logger.error("Failed to trigger Forensic Accounting Scores: %s", e)
        return _error_500(e)


@analysis_router.get("/market-regime/current")
@limiter.limit("30/minute")
async def get_market_regime_current(request: Request):
    """Returns the latest HMM price regime state and the most recent regime transition."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT date, price_hmm_state, price_hmm_label, price_hmm_prob, "
            "us_regime_label, uk_regime_label "
            "FROM market_regimes WHERE price_hmm_state IS NOT NULL ORDER BY date DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if not row:
            return JSONResponse(content={"status": "success", "current": None, "last_change": None})

        current = {
            "state": row["price_hmm_state"],
            "label": row["price_hmm_label"],
            "probability": row["price_hmm_prob"],
            "as_of": row["date"],
            # us/uk_regime_label are the EWMA-turbulence classifier's Normal/Volatile/Crash
            # labels (calculate_market_regime()) — a different taxonomy than the HMM's
            # Bull/Chop/Crash above, though both live on this same row/date.
            "us_regime_label": row["us_regime_label"],
            "uk_regime_label": row["uk_regime_label"],
        }

        cursor.execute(
            "SELECT date, price_hmm_label FROM market_regimes "
            "WHERE price_hmm_label IS NOT NULL ORDER BY date DESC LIMIT 60"
        )
        history = cursor.fetchall()
        last_change = None
        current_label = current["label"]
        for i, h in enumerate(history[1:], 1):
            if h["price_hmm_label"] != current_label:
                last_change = {
                    "date": history[i - 1]["date"],
                    "from_label": h["price_hmm_label"],
                    "to_label": current_label,
                }
                break

        return JSONResponse(content={"status": "success", "current": current, "last_change": last_change})
    except Exception as e:
        logger.error("market-regime/current failed: %s", e)
        return _error_500(e)
    finally:
        if conn:
            conn.close()


@analysis_router.get("/market-stress")
@limiter.limit("30/minute")
async def get_market_stress(request: Request):
    """Returns the latest market-wide Isolation Forest stress score and the last 30 daily values."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT date, market_stress_score, market_stress_features "
            "FROM market_regimes WHERE market_stress_score IS NOT NULL ORDER BY date DESC LIMIT 30"
        )
        rows = cursor.fetchall()
        if not rows:
            return JSONResponse(content={"status": "success", "current": None, "history": []})

        latest = rows[0]
        try:
            import json as _json
            features = _json.loads(latest["market_stress_features"] or "{}")
        except Exception:
            features = {}

        current = {
            "score": round(float(latest["market_stress_score"]), 4),
            "features": features,
            "date": latest["date"],
        }
        history = [
            {"date": r["date"], "score": round(float(r["market_stress_score"]), 4)}
            for r in reversed(rows)
        ]
        return JSONResponse(content={"status": "success", "current": current, "history": history})
    except Exception as e:
        logger.error("market-stress endpoint failed: %s", e)
        return _error_500(e)
    finally:
        if conn:
            conn.close()


@analysis_router.get("/macro-conditions")
@limiter.limit("30/minute")
async def get_macro_conditions(request: Request):
    """Returns the latest sovereign-yield threat levels, Treasury auction demand, and Fear &
    Greed — the Home Assistant integration's "Market Health" device reads this in one call.
    Threat levels are returned as raw GREEN/YELLOW/RED (same convention as
    /api/macro-regime-allocation); Low/Elevated/High presentation mapping is a display-layer
    concern for callers, not this endpoint."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT date, us_threat_level, uk_threat_level, us_yield_velocity, uk_yield_velocity, "
            "tyx_close, tnx_close, uk_gilt_close, dxy_close, gbpusd_close "
            "FROM macro_regimes ORDER BY date DESC LIMIT 1"
        )
        row = cursor.fetchone()

        auction_rows = get_auction_summary()
        if not auction_rows:
            # Distinct from "auctions happened and were all fine" — no data to judge at all.
            auction_healthy = None
        else:
            auction_healthy = not any(r["alert_fired"] for r in auction_rows)

        fear_greed = get_latest_fear_greed()

        return JSONResponse(content={
            "status": "success",
            "as_of": row["date"] if row else None,
            "us_threat_level": row["us_threat_level"] if row else None,
            "uk_threat_level": row["uk_threat_level"] if row else None,
            "us_yield_velocity": row["us_yield_velocity"] if row else None,
            "uk_yield_velocity": row["uk_yield_velocity"] if row else None,
            "tyx_close": row["tyx_close"] if row else None,
            "tnx_close": row["tnx_close"] if row else None,
            "uk_gilt_close": row["uk_gilt_close"] if row else None,
            "dxy_close": row["dxy_close"] if row else None,
            "gbpusd_close": row["gbpusd_close"] if row else None,
            "treasury_auction": {"healthy": auction_healthy, "recent": auction_rows},
            "fear_greed": fear_greed,
        })
    except Exception as e:
        logger.error("macro-conditions endpoint failed: %s", e)
        return _error_500(e)
    finally:
        if conn:
            conn.close()


@analysis_router.get("/market-regime")
@limiter.limit("10/minute")
async def get_market_regime_full(request: Request):
    """Returns full HMM regime history, transition matrix, and per-state statistics."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT date, price_hmm_state, price_hmm_label, price_hmm_prob "
            "FROM market_regimes WHERE price_hmm_state IS NOT NULL ORDER BY date DESC LIMIT 1"
        )
        cur_row = cursor.fetchone()
        if not cur_row:
            return JSONResponse(content={"status": "success", "current": None, "history": [], "transition_matrix": None, "regime_stats": {}})

        current = {
            "state": cur_row["price_hmm_state"],
            "label": cur_row["price_hmm_label"],
            "probability": cur_row["price_hmm_prob"],
            "as_of": cur_row["date"],
        }

        cursor.execute("SELECT date, state, label, probability FROM price_hmm_states ORDER BY date ASC")
        history = [dict(r) for r in cursor.fetchall()]

        last_change = None
        current_label = current["label"]
        for i in range(len(history) - 2, -1, -1):
            if history[i]["label"] != current_label:
                last_change = {
                    "date": history[i + 1]["date"],
                    "from_label": history[i]["label"],
                    "to_label": current_label,
                }
                break

        n_states = 3
        counts = [[0] * n_states for _ in range(n_states)]
        for i in range(len(history) - 1):
            s_from = history[i]["state"]
            s_to = history[i + 1]["state"]
            if 0 <= s_from < n_states and 0 <= s_to < n_states:
                counts[s_from][s_to] += 1
        transition_matrix = []
        for row_counts in counts:
            total = sum(row_counts)
            transition_matrix.append(
                [round(c / total, 3) if total > 0 else 0.0 for c in row_counts]
            )

        cursor.execute(
            "SELECT h.date, h.state, h.label, r.spy_volatility "
            "FROM price_hmm_states h "
            "LEFT JOIN market_regimes r ON h.date = r.date "
            "ORDER BY h.date ASC"
        )
        stat_rows = cursor.fetchall()

        hmm_cache = HISTORICAL_DIR / "SPY_hmm.parquet"
        spy_returns: dict = {}
        if hmm_cache.exists():
            df_spy = pd.read_parquet(hmm_cache)
            log_ret = np.log(df_spy["Close"] / df_spy["Close"].shift(1)).dropna()
            spy_returns = {d.strftime("%Y-%m-%d"): float(v) for d, v in log_ret.items()}

        regime_stats: dict = {}
        for label in ("Bull", "Chop", "Crash"):
            label_rows = [r for r in stat_rows if r["label"] == label]
            days = len(label_rows)
            vols = [r["spy_volatility"] for r in label_rows if r["spy_volatility"] is not None]
            rets = [spy_returns[r["date"]] for r in label_rows if r["date"] in spy_returns]
            regime_stats[label] = {
                "days": days,
                "mean_daily_return": round(float(np.mean(rets)), 5) if rets else None,
                "mean_vol": round(float(np.mean(vols)), 2) if vols else None,
            }

        return JSONResponse(content={
            "status": "success",
            "current": current,
            "last_change": last_change,
            "history": history,
            "transition_matrix": transition_matrix,
            "regime_stats": regime_stats,
        })
    except Exception as e:
        logger.error("market-regime full endpoint failed: %s", e)
        return _error_500(e)
    finally:
        if conn:
            conn.close()


@analysis_router.post("/market-regime/run")
@limiter.limit("4/minute")
async def run_market_regime_now(request: Request, background_tasks: BackgroundTasks):
    """Manually triggers the HMM price regime calculation in the background."""
    try:
        from regime_engine import run_price_regime_hmm
        background_tasks.add_task(run_price_regime_hmm)
        return JSONResponse(content={"status": "success", "message": "HMM regime calculation triggered."})
    except Exception as e:
        logger.error("market-regime/run failed: %s", e)
        return _error_500(e)


class StressTestRequest(BaseModel):
    account_id: str = "all"
    scenario_id: str
    custom_drop: Optional[float] = None


@analysis_router.get("/stress-test/scenarios")
@limiter.limit("30/minute")
async def get_stress_test_scenarios(request: Request):
    """Returns the list of available stress-test scenarios."""
    try:
        from stress_engine import SCENARIOS
        out = {}
        for key, sc in SCENARIOS.items():
            out[key] = {k: v for k, v in sc.items() if v is not None}
        return JSONResponse(content={"status": "success", "scenarios": out})
    except Exception as e:
        logger.error("stress-test/scenarios failed: %s", e)
        return _error_500(e)


@analysis_router.post("/stress-test/run")
@limiter.limit("10/minute")
async def run_stress_test(request: Request, body: StressTestRequest):
    """Applies a beta-adjusted scenario shock to the portfolio and returns a monetary impact report."""
    try:
        from stress_engine import run_stress_test as _run
        result = _run(
            account_id=body.account_id,
            scenario_id=body.scenario_id,
            custom_drop=body.custom_drop,
        )
        return JSONResponse(content={"status": "success", "result": result})
    except (ValueError, RuntimeError) as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    except Exception as e:
        logger.error("stress-test/run failed: %s", e)
        return _error_500(e)


class EtfConstituentItem(BaseModel):
    ticker: str
    weight: float


class EtfPredictorConfigBody(BaseModel):
    name: str
    etf_ticker: str
    constituents: List[EtfConstituentItem]
    enabled: Optional[bool] = True
    auto_schedule: Optional[bool] = False
    pre_run_time: Optional[str] = "13:30"
    post_run_time: Optional[str] = "22:00"


class EtfValidateBody(BaseModel):
    etf_ticker: str
    constituents: List[EtfConstituentItem]


def _normalise_constituents(items: List[EtfConstituentItem]) -> List[dict]:
    total = sum(i.weight for i in items)
    if total <= 0:
        return []
    return [{"ticker": i.ticker.upper().strip(), "weight": i.weight / total} for i in items]


@analysis_router.post("/etf-predictors/validate")
@limiter.limit("10/minute")
async def validate_etf_predictor_config(request: Request, body: EtfValidateBody):
    try:
        from etf_predictor_engine import _ticker_exchange_explicit, find_unknown_exchange_tickers
        import time_engine as _te

        etf_ticker = body.etf_ticker.upper().strip()
        etf_info = yahoo_engine.get_ticker_info(etf_ticker)
        etf_result = {
            "ticker": etf_ticker,
            "valid": etf_info is not None,
            "name": (etf_info.get("longName") or etf_info.get("shortName", "")) if etf_info else None,
        }

        constituent_results = []
        total_weight = 0.0
        constituent_tickers = []
        for item in body.constituents:
            t = item.ticker.upper().strip()
            constituent_tickers.append(t)
            info = yahoo_engine.get_ticker_info(t)
            exchange = _ticker_exchange_explicit(t)
            exchange_known = exchange in _te.EXCHANGE_HOURS
            constituent_results.append({
                "ticker": t,
                "weight": item.weight,
                "valid": info is not None,
                "name": (info.get("longName") or info.get("shortName", "")) if info else None,
                "exchange": exchange,
                "exchange_known": exchange_known,
            })
            total_weight += item.weight

        unknown_tickers = find_unknown_exchange_tickers(constituent_tickers)
        unknown_warning = None
        if unknown_tickers:
            unknown_warning = (
                f"The following tickers have suffixes not found in the exchange registry "
                f"(data/exchange_hours.json): {', '.join(unknown_tickers)}. "
                f"Exchange open/close markers will default to NYSE. "
                f"Add the exchange definition to fix."
            )

        return JSONResponse(content={
            "status": "success",
            "etf": etf_result,
            "constituents": constituent_results,
            "total_weight": round(total_weight, 4),
            "weight_ok": abs(total_weight - 100.0) < 1.0 or abs(total_weight - 1.0) < 0.01,
            "unknown_exchange_tickers": unknown_tickers,
            "unknown_exchange_warning": unknown_warning,
        })
    except Exception as e:
        logger.error("validate_etf_predictor_config failed: %s", e)
        return _error_500(e)


@analysis_router.get("/etf-predictors")
@limiter.limit("20/minute")
async def list_etf_predictors(request: Request):
    try:
        from database import get_etf_predictor_configs
        configs = get_etf_predictor_configs()
        return JSONResponse(content={"status": "success", "configs": configs})
    except Exception as e:
        logger.error("list_etf_predictors failed: %s", e)
        return _error_500(e)


@analysis_router.post("/etf-predictors")
@limiter.limit("10/minute")
async def create_etf_predictor(request: Request, body: EtfPredictorConfigBody):
    try:
        from database import create_etf_predictor_config
        from scheduler_engine import register_etf_predictor_jobs
        if not body.constituents:
            return JSONResponse(status_code=422, content={"status": "error", "message": "At least one constituent required."})
        constituents = _normalise_constituents(body.constituents)
        if not constituents:
            return JSONResponse(status_code=422, content={"status": "error", "message": "Constituent weights must sum to > 0."})
        config_id = create_etf_predictor_config(
            name=body.name,
            etf_ticker=body.etf_ticker.upper().strip(),
            constituents=constituents,
            enabled=body.enabled,
            auto_schedule=body.auto_schedule,
            pre_run_time=body.pre_run_time,
            post_run_time=body.post_run_time,
        )
        if config_id is None:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to save config."})
        if body.auto_schedule and body.enabled:
            register_etf_predictor_jobs({
                "id": config_id, "enabled": True, "deleted_at": None,
                "pre_run_time": body.pre_run_time, "post_run_time": body.post_run_time,
            })
        return JSONResponse(content={"status": "success", "message": "Predictor created.", "id": config_id})
    except Exception as e:
        logger.error("create_etf_predictor failed: %s", e)
        return _error_500(e)


@analysis_router.put("/etf-predictors/{config_id}")
@limiter.limit("10/minute")
async def update_etf_predictor(request: Request, config_id: int, body: EtfPredictorConfigBody):
    try:
        from database import update_etf_predictor_config, get_etf_predictor_config
        from scheduler_engine import register_etf_predictor_jobs, unregister_etf_predictor_jobs
        if get_etf_predictor_config(config_id) is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Config not found."})
        constituents = _normalise_constituents(body.constituents) if body.constituents else None
        if constituents is not None and not constituents:
            return JSONResponse(status_code=422, content={"status": "error", "message": "Constituent weights must sum to > 0."})
        fields: dict = {
            "name": body.name,
            "etf_ticker": body.etf_ticker.upper().strip(),
            "enabled": body.enabled,
            "auto_schedule": body.auto_schedule,
            "pre_run_time": body.pre_run_time,
            "post_run_time": body.post_run_time,
        }
        if constituents is not None:
            fields["constituents"] = constituents
        update_etf_predictor_config(config_id, **fields)
        unregister_etf_predictor_jobs(config_id)
        if body.auto_schedule and body.enabled:
            register_etf_predictor_jobs({
                "id": config_id, "enabled": True, "deleted_at": None,
                "pre_run_time": body.pre_run_time, "post_run_time": body.post_run_time,
            })
        return JSONResponse(content={"status": "success", "message": "Predictor updated."})
    except Exception as e:
        logger.error("update_etf_predictor %s failed: %s", config_id, e)
        return _error_500(e)


@analysis_router.delete("/etf-predictors/{config_id}")
@limiter.limit("10/minute")
async def delete_etf_predictor(request: Request, config_id: int):
    try:
        from database import soft_delete_etf_predictor_config, get_etf_predictor_config
        from scheduler_engine import unregister_etf_predictor_jobs
        if get_etf_predictor_config(config_id) is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Config not found."})
        unregister_etf_predictor_jobs(config_id)
        soft_delete_etf_predictor_config(config_id)
        return JSONResponse(content={"status": "success", "message": "Predictor deleted."})
    except Exception as e:
        logger.error("delete_etf_predictor %s failed: %s", config_id, e)
        return _error_500(e)


@analysis_router.post("/etf-predictors/{config_id}/run")
@limiter.limit("5/minute")
async def run_etf_predictor(request: Request, config_id: int, background_tasks: BackgroundTasks):
    try:
        from database import get_etf_predictor_config
        if get_etf_predictor_config(config_id) is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Config not found."})

        def _bg():
            try:
                from etf_predictor_engine import run_prediction
                from scheduler_engine import log_sched_notification
                result = run_prediction(config_id)
                if result.get("status") != "success":
                    log_sched_notification("Warning", f"ETF predictor [{config_id}] run: {result.get('error')}")
                else:
                    ptype = result.get("prediction_type", "next_open")
                    price = result.get("predicted_price")
                    chg = result.get("predicted_change_pct")
                    signal = result.get("signal_source", "")
                    log_sched_notification(
                        "Success",
                        f"ETF predictor [{config_id}] ({ptype}) — "
                        f"{price} ({chg:+.2f}%) | signal: {signal}" if price and chg is not None else
                        f"ETF predictor [{config_id}] prediction complete."
                    )
            except Exception as exc:
                from scheduler_engine import log_sched_notification
                log_sched_notification("Error", f"ETF predictor [{config_id}] run failed: {exc}")

        background_tasks.add_task(_bg)
        return JSONResponse(content={"status": "success", "message": f"ETF predictor {config_id} run initiated."})
    except Exception as e:
        logger.error("run_etf_predictor %s failed: %s", config_id, e)
        return _error_500(e)


@analysis_router.post("/etf-predictors/{config_id}/fill-actuals")
@limiter.limit("5/minute")
async def fill_etf_predictor_actuals(request: Request, config_id: int, background_tasks: BackgroundTasks):
    try:
        from database import get_etf_predictor_config
        if get_etf_predictor_config(config_id) is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Config not found."})

        def _bg():
            try:
                from etf_predictor_engine import fill_actuals_for_config
                fill_actuals_for_config(config_id)
                from scheduler_engine import log_sched_notification
                log_sched_notification("Success", f"ETF predictor [{config_id}] actuals filled.")
            except Exception as exc:
                from scheduler_engine import log_sched_notification
                log_sched_notification("Error", f"ETF predictor [{config_id}] fill-actuals failed: {exc}")

        background_tasks.add_task(_bg)
        return JSONResponse(content={"status": "success", "message": f"ETF predictor {config_id} fill-actuals initiated."})
    except Exception as e:
        logger.error("fill_etf_predictor_actuals %s failed: %s", config_id, e)
        return _error_500(e)


@analysis_router.get("/etf-predictors/{config_id}/predictions")
@limiter.limit("20/minute")
async def get_etf_predictor_predictions(request: Request, config_id: int):
    try:
        from database import get_etf_accuracy, get_etf_predictor_config
        if get_etf_predictor_config(config_id) is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Config not found."})
        return JSONResponse(content={"status": "success", **get_etf_accuracy(config_id)})
    except Exception as e:
        logger.error("get_etf_predictor_predictions %s failed: %s", config_id, e)
        return _error_500(e)


_REGIME_MODES = frozenset([
    "Plain English Briefing",
    "What Happens Next?",
    "How Should I Position?",
    "Red Flags Check",
])

_SENTIMENT_US_MODES = frozenset([
    "US Market Health Check",
    "This Week's US Risk Events",
    "Recession Radar",
    "Inflation & Rate Impact",
])

_SENTIMENT_UK_MODES = frozenset([
    "UK Market Health Check",
    "This Week's UK Risk Events",
    "Pound & Gilt Impact",
    "UK vs US Comparison",
    "UK Investor in US Exposure",
])


@analysis_router.get("/ai-prompt/market-regime")
async def get_ai_prompt_market_regime(mode: str = "Plain English Briefing"):
    if mode not in _REGIME_MODES:
        return JSONResponse(status_code=400, content={"status": "error", "message": f"Unrecognised mode: {mode}"})
    try:
        engine = AIRegimePromptEngine()
        prompt = engine.generate_prompt(mode)
        return JSONResponse(content={"status": "success", "prompt": prompt})
    except Exception as e:
        return _error_500(e)


@analysis_router.get("/ai-prompt/market-sentiment/us")
async def get_ai_prompt_sentiment_us(mode: str = "US Market Health Check"):
    if mode not in _SENTIMENT_US_MODES:
        return JSONResponse(status_code=400, content={"status": "error", "message": f"Unrecognised mode: {mode}"})
    try:
        engine = AISentimentPromptEngine()
        prompt = engine.generate_us_prompt(mode)
        return JSONResponse(content={"status": "success", "prompt": prompt})
    except Exception as e:
        return _error_500(e)


@analysis_router.get("/ai-prompt/market-sentiment/uk")
async def get_ai_prompt_sentiment_uk(mode: str = "UK Market Health Check"):
    if mode not in _SENTIMENT_UK_MODES:
        return JSONResponse(status_code=400, content={"status": "error", "message": f"Unrecognised mode: {mode}"})
    try:
        engine = AISentimentPromptEngine()
        prompt = engine.generate_uk_prompt(mode)
        return JSONResponse(content={"status": "success", "prompt": prompt})
    except Exception as e:
        return _error_500(e)


@analysis_router.get("/ai-prompt/{ticker}")
async def get_ai_prompt(ticker: str = PathParam(..., pattern=r"^[A-Z0-9.\-\^=]{1,20}$"), mode: str = "Quantamental Deep-Dive"):
    try:
        ticker = normalize_ticker(ticker)
        engine = AIPromptEngine()
        prompt = engine.generate_prompt(ticker, mode)
        if not prompt:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Stock data not found in local database."})
        return JSONResponse(content={"status": "success", "prompt": prompt})
    except Exception as e:
        return _error_500(e)
