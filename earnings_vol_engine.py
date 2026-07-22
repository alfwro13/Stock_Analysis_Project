import time
import random
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from constants import EARNINGS_DRIFT_HORIZONS
from data_engine import load_or_fetch_daily_history
from database import get_connection, log_notification
from db_helpers import filter_equity_tickers, get_next_earnings_dates
from utils import trading_days_forward
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)


def _get_past_earnings_events(ticker: str, offsets: Sequence[int], max_events: int = 4) -> List[dict]:
    """Fetches the ticker's last `max_events` past earnings dates once and returns, per event,
    the pre-earnings close plus the close at each requested trading-day offset. offset<=0 counts
    backward from the pre-earnings close itself (offset 0 == the pre-earnings close); offset>=1
    counts forward, deliberately skipping the ambiguous earnings-day bar itself (mirrors the
    original pre/post 2-session-window design, which exists because Yahoo's earnings timestamp
    doesn't reliably say BMO vs AMC). Single shared fetch behind get_historical_earnings_move()
    and get_historical_earnings_drift() — avoids repeating the yahoo_engine.get_earnings_dates()
    + load_or_fetch_daily_history() call pair for each."""
    events: List[dict] = []
    try:
        earnings_dates = yahoo_engine.get_earnings_dates(ticker, limit=10)
        if earnings_dates is None or earnings_dates.empty:
            return events

        if earnings_dates.index.tz is None:
            earnings_dates.index = earnings_dates.index.tz_localize(timezone.utc)
        now = pd.Timestamp.now(tz=timezone.utc)
        past_dates = earnings_dates[earnings_dates.index < now].index
        if len(past_dates) == 0:
            return events

        full_hist = load_or_fetch_daily_history(ticker)
        if full_hist is None or full_hist.empty:
            return events

        for e_date in past_dates[:max_events]:
            try:
                # Wide enough to have both a pre-close and a +20-trading-day close available
                start_date = (e_date - timedelta(days=10)).strftime('%Y-%m-%d')
                end_date = (e_date + timedelta(days=35)).strftime('%Y-%m-%d')
                hist = full_hist.loc[start_date:end_date]
                if len(hist) < 2:
                    continue

                # normalize() avoids [s] vs [us] resolution conflicts; utc=True standardises both to UTC-aware midnight
                hist_dates = pd.to_datetime(hist.index, utc=True).normalize()
                target_date = pd.to_datetime(e_date, utc=True).normalize()

                time_diffs = abs(hist_dates - target_date)
                closest_idx = time_diffs.argmin()

                pre_idx = closest_idx - 1
                if pre_idx < 0:
                    continue
                pre_close = hist['Close'].iloc[pre_idx]
                if pre_close <= 0:
                    continue

                closes: Dict[int, float] = {}
                for offset in offsets:
                    pos = (pre_idx + offset) if offset <= 0 else (closest_idx + offset)
                    if 0 <= pos < len(hist):
                        closes[offset] = float(hist['Close'].iloc[pos])

                events.append({
                    "earnings_date": e_date,
                    "pre_close": float(pre_close),
                    "closes": closes,
                })

            except Exception as e:
                logger.debug("Could not resolve earnings event window for %s: %s", ticker, e)
                continue

    except Exception as e:
        logger.debug("Error fetching historical earnings dates for %s: %s", ticker, e)

    return events


def get_historical_earnings_move(ticker: str) -> Optional[float]:
    events = _get_past_earnings_events(ticker, offsets=[1])
    moves = [
        abs((e["closes"][1] - e["pre_close"]) / e["pre_close"]) * 100.0
        for e in events if 1 in e["closes"]
    ]
    if moves:
        return float(np.mean(moves))
    return None


def get_historical_earnings_drift(ticker: str, horizons: Sequence[int] = EARNINGS_DRIFT_HORIZONS) -> Dict[int, dict]:
    """{horizon_days: {"avg_pct": float|None (signed), "avg_abs_pct": float|None, "up_count": int,
    "sample_size": int}} — a horizon's stats only include past events where that offset actually
    exists in history (a very recent earnings event won't have a +20-trading-day close yet)."""
    events = _get_past_earnings_events(ticker, offsets=list(horizons))
    result: Dict[int, dict] = {}
    for h in horizons:
        pct_changes = [
            (e["closes"][h] - e["pre_close"]) / e["pre_close"] * 100.0
            for e in events if h in e["closes"]
        ]
        if pct_changes:
            result[h] = {
                "avg_pct": float(np.mean(pct_changes)),
                "avg_abs_pct": float(np.mean([abs(p) for p in pct_changes])),
                "up_count": sum(1 for p in pct_changes if p > 0),
                "sample_size": len(pct_changes),
            }
        else:
            result[h] = {"avg_pct": None, "avg_abs_pct": None, "up_count": 0, "sample_size": 0}
    return result


def log_near_earnings_predictions(ticker_list: Optional[List[str]] = None, days_ahead: int = 4) -> int:
    """Self-sufficient daily step (piggybacked onto the daily overnight_quant_scan_job, not the
    up-to-14-days-early weekly earnings scan): for any tracked ticker whose earnings falls within
    the next `days_ahead` calendar days, (re-)logs a post-earnings drift prediction anchored to
    today's close. days_ahead=4 bridges a Friday run through to a Tuesday earnings date (the job
    only runs Mon-Fri). Re-running on each subsequent day before the earnings date converges the
    baseline on the actual last close before the print — see
    db_helpers.log_earnings_drift_prediction's ON-CONFLICT-DO-UPDATE guard, which stops refreshing
    once a row has begun resolving. Independent of run_earnings_vol_scan()'s cached
    earnings_volatility columns — computes its own historical drift directly."""
    from db_helpers import log_earnings_drift_prediction

    if ticker_list is None:
        from data_engine import DataEngine
        ticker_list = DataEngine().get_all_tickers()
    ticker_list = filter_equity_tickers(ticker_list)
    if not ticker_list:
        return 0

    today = datetime.now(timezone.utc)
    cutoff_date = today + timedelta(days=days_ahead)
    cached_earnings_dates = get_next_earnings_dates(ticker_list)
    now_ts = today.strftime("%Y-%m-%d %H:%M:%S")

    logged = 0
    for ticker in ticker_list:
        try:
            e_date_str = cached_earnings_dates.get(ticker, {}).get('next_earnings_date')
            if not e_date_str or e_date_str == 'Unknown':
                continue
            try:
                earnings_date = datetime.strptime(e_date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if not (today <= earnings_date <= cutoff_date):
                continue

            hist = load_or_fetch_daily_history(ticker)
            if hist is None or hist.empty:
                continue
            pre_earnings_close = float(hist['Close'].iloc[-1])
            if pre_earnings_close <= 0:
                continue

            drift = get_historical_earnings_drift(ticker)
            sample_size = drift.get(EARNINGS_DRIFT_HORIZONS[0], {}).get("sample_size")
            target_dates = {h: trading_days_forward(e_date_str, h) for h in EARNINGS_DRIFT_HORIZONS}

            log_earnings_drift_prediction(
                ticker, e_date_str, now_ts, pre_earnings_close, sample_size,
                drift.get(1, {}).get("avg_pct"), target_dates.get(1),
                drift.get(5, {}).get("avg_pct"), target_dates.get(5),
                drift.get(20, {}).get("avg_pct"), target_dates.get(20),
            )
            logged += 1

        except Exception as e:
            logger.error("log_near_earnings_predictions failed for %s: %s", ticker, e)

    return logged


def backfill_earnings_drift_outcomes() -> int:
    """Resolves every earnings_drift_predictions row with at least one horizon whose target_date
    has passed, per horizon independently, via the same 'first quant_signals close on/after
    target_date' lookup used by predicted_movers_engine.backfill_actual_outcomes(). Scans the
    whole unresolved set each run (catch-up discipline), not just the newest."""
    from db_helpers import batch_update_earnings_drift_actuals, get_unresolved_earnings_drift

    cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pending = get_unresolved_earnings_drift(cutoff)
    if not pending:
        return 0

    conn = None
    payloads: List[Tuple[int, int, float, str, int]] = []
    try:
        conn = get_connection()
        for row in pending:
            pre_close = row["pre_earnings_close"]
            for horizon in EARNINGS_DRIFT_HORIZONS:
                target_date = row.get(f"target_date_{horizon}d")
                direction_correct = row.get(f"direction_correct_{horizon}d")
                predicted_pct = row.get(f"predicted_pct_{horizon}d")
                if not target_date or direction_correct is not None or predicted_pct is None:
                    continue
                if target_date > cutoff:
                    continue
                future = conn.execute(
                    """SELECT date, close_price FROM quant_signals
                       WHERE ticker=? AND date>=? ORDER BY date ASC LIMIT 1""",
                    (row["ticker"], target_date),
                ).fetchone()
                if not future or future["close_price"] is None:
                    continue
                actual_price = future["close_price"]
                actual_date = future["date"]
                dc = 1 if np.sign(predicted_pct) == np.sign(actual_price - pre_close) and actual_price != pre_close else 0
                payloads.append((row["id"], horizon, actual_price, actual_date, dc))
    except Exception as e:
        logger.error("backfill_earnings_drift_outcomes failed while resolving actuals: %s", e)
    finally:
        if conn:
            conn.close()

    batch_update_earnings_drift_actuals(payloads)
    return len(payloads)


def get_earnings_drift_accuracy_summary() -> dict:
    from db_helpers import get_company_names, get_earnings_drift_accuracy

    data = get_earnings_drift_accuracy()
    tickers = [r["ticker"] for r in data.get("by_ticker", [])]
    names = get_company_names(tickers) if tickers else {}
    for row in data.get("by_ticker", []):
        row["company_name"] = names.get(row["ticker"])
    return data


def get_implied_straddle_move(ticker: str, underlying_price: float, target_date: datetime) -> Tuple[Optional[float], int, Optional[str]]:
    try:
        options = yahoo_engine.get_options_expirations(ticker)
        if not options:
            return None, 0, None

        valid_expiries = [opt for opt in options if datetime.strptime(opt, '%Y-%m-%d').replace(tzinfo=timezone.utc) >= target_date]
        if not valid_expiries:
            return None, 0, None

        target_expiry = valid_expiries[0]
        chain_result = yahoo_engine.get_options_chain(ticker, target_expiry)
        if chain_result is None:
            return None, 0, None

        calls, puts = chain_result

        if calls.empty or puts.empty:
            return None, 0, None

        atm_strike = calls.iloc[(calls['strike'] - underlying_price).abs().argsort()[:1]]['strike'].values[0]

        atm_call = calls[calls['strike'] == atm_strike].iloc[0]
        atm_put = puts[puts['strike'] == atm_strike].iloc[0]

        # STRICT LIQUIDITY REQUIREMENT: Reject lastPrice fallbacks for untradable illiquid chains
        def get_price(opt_row) -> Optional[float]:
            if opt_row['bid'] > 0 and opt_row['ask'] > 0:
                return (opt_row['bid'] + opt_row['ask']) / 2.0
            return None

        call_price = get_price(atm_call)
        put_price = get_price(atm_put)

        if call_price is None or put_price is None:
            return None, 0, None

        # Straddle cost / underlying = literal market-priced move; OI not volume as proxy (OI persists outside hours)
        implied_move_pct = (call_price + put_price) / underlying_price * 100.0
        volume = int(atm_call.get('openInterest', 0)) + int(atm_put.get('openInterest', 0))

        return implied_move_pct, volume, target_expiry

    except Exception as e:
        logger.debug("Error calculating implied straddle: %s", e)
        return None, 0, None

def run_earnings_vol_scan(ticker_list: List[str]) -> List[str]:
    """Returns the tickers that were due to be scanned (earnings within 14 days) but couldn't
    be — a Yahoo fetch failure (network error, or genuinely no data available), not a case
    correctly skipped by the date-window check. Caller may retry these later rather than
    waiting a full week for the next scheduled run."""
    ticker_list = filter_equity_tickers(ticker_list)
    total_tickers = len(ticker_list)
    if not ticker_list:
        logger.warning("Ticker list is empty. Aborting scan.")
        return []

    failed_tickers: List[str] = []

    logger.info("Starting earnings volatility scan for %d assets...", total_tickers)
    log_notification("Info", f"Earnings Volatility Scan initiated for {total_tickers} assets.")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        today = datetime.now(timezone.utc)
        cutoff_date = today + timedelta(days=14)

        cached_earnings_dates = get_next_earnings_dates(ticker_list)

        for i, ticker in enumerate(ticker_list):
            try:
                e_date_str = cached_earnings_dates.get(ticker, {}).get('next_earnings_date')
                if not e_date_str or e_date_str == 'Unknown':
                    continue

                try:
                    earnings_date = datetime.strptime(e_date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

                if not (today <= earnings_date <= cutoff_date):
                    continue

                logger.info("Analyzing %s (Earnings Date: %s)...", ticker, e_date_str)

                hist = load_or_fetch_daily_history(ticker)
                hist = hist.tail(30) if hist is not None else pd.DataFrame()

                if hist.empty or len(hist) < 20:
                    logger.warning("Insufficient underlying price data available for %s. Skipping.", ticker)
                    failed_tickers.append(ticker)
                    continue

                underlying_price = hist['Close'].iloc[-1]

                hist = hist.copy()
                hist['Returns'] = np.log(hist['Close'] / hist['Close'].shift(1))
                historical_hv = hist['Returns'].std() * np.sqrt(252)

                if pd.isna(historical_hv) or historical_hv == 0:
                    failed_tickers.append(ticker)
                    continue

                drift = get_historical_earnings_drift(ticker)
                hist_move_pct = drift.get(1, {}).get("avg_abs_pct")

                if hist_move_pct is None:
                    logger.debug("No historical earnings-move data for %s. Skipping.", ticker)
                    failed_tickers.append(ticker)
                    continue

                # Options leg is optional now the page's primary content is drift/ML-band, not
                # options mispricing — a ticker with no liquid ATM straddle still gets a row with
                # implied_move_pct/edge_score/options_volume left NULL.
                implied_move_pct, opt_volume, target_expiry = get_implied_straddle_move(ticker, underlying_price, earnings_date)

                isolated_implied_move = None
                edge_score = None
                if implied_move_pct is not None and target_expiry is not None:
                    # Subtract diffusion over (days_to_expiry - 1) days to isolate the earnings jump from theta
                    target_expiry_date = datetime.strptime(target_expiry, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                    days_to_expiry = max((target_expiry_date - datetime.now(timezone.utc)).days, 1)
                    non_earnings_days = max(days_to_expiry - 1, 0)

                    daily_hv = historical_hv / np.sqrt(252)
                    total_implied_pct = implied_move_pct / 100.0
                    non_earn_pct = daily_hv * np.sqrt(non_earnings_days)
                    isolated_variance = max(total_implied_pct**2 - non_earn_pct**2, 0)
                    isolated_implied_move = np.sqrt(isolated_variance) * 100.0 if isolated_variance > 0 else 0.01

                    edge_score = hist_move_pct - isolated_implied_move

                last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

                # Store isolated_implied_move in implied_move_pct column — reflects true event variance, not raw straddle
                cursor.execute('''
                    INSERT OR REPLACE INTO earnings_volatility
                    (ticker, next_earnings_date, implied_move_pct, historical_avg_move_pct, edge_score, options_volume, last_updated,
                     drift_avg_pct_1d, drift_up_count_1d, drift_sample_size_1d,
                     drift_avg_pct_5d, drift_up_count_5d, drift_sample_size_5d,
                     drift_avg_pct_20d, drift_up_count_20d, drift_sample_size_20d)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    ticker,
                    e_date_str,
                    round(isolated_implied_move, 2) if isolated_implied_move is not None else None,
                    round(hist_move_pct, 2),
                    round(edge_score, 2) if edge_score is not None else None,
                    opt_volume if implied_move_pct is not None else None,
                    last_updated,
                    round(drift[1]["avg_pct"], 2) if drift[1]["avg_pct"] is not None else None,
                    drift[1]["up_count"], drift[1]["sample_size"],
                    round(drift[5]["avg_pct"], 2) if drift[5]["avg_pct"] is not None else None,
                    drift[5]["up_count"], drift[5]["sample_size"],
                    round(drift[20]["avg_pct"], 2) if drift[20]["avg_pct"] is not None else None,
                    drift[20]["up_count"], drift[20]["sample_size"],
                ))
                conn.commit()

                logger.info("[%s] Edge: %s | Isolated Implied: %s | Hist: %.2f%%",
                            ticker,
                            f"{edge_score:.2f}%" if edge_score is not None else "N/A",
                            f"{isolated_implied_move:.2f}%" if isolated_implied_move is not None else "N/A",
                            hist_move_pct)

            except Exception as e:
                logger.error("Error analyzing %s: %s", ticker, e)
                failed_tickers.append(ticker)
                conn.rollback()
            finally:
                # Wider, randomised gap between tickers — Yahoo's guce.yahoo.com consent gate has
                # been observed intermittently refusing connections mid-scan when this ran too
                # tight a cadence across 100+ sequential tickers (each doing 2-3 Yahoo calls).
                time.sleep(random.uniform(2.5, 5.0))

            processed = i + 1
            if total_tickers >= 4 and processed % max(1, total_tickers // 4) == 0 and processed < total_tickers:
                pct = int((processed / total_tickers) * 100)
                log_notification("Info", f"Earnings Volatility Scan Progress: {pct}% ({processed}/{total_tickers} tickers evaluated).")

        if failed_tickers:
            logger.warning("Earnings volatility scan: %d ticker(s) failed and may need a retry: %s",
                            len(failed_tickers), failed_tickers)
        logger.info("Earnings volatility options scan complete.")
        log_notification("Success", f"Earnings Volatility Options Scan completed successfully across {total_tickers} tracked assets.")

    except Exception as e:
        logger.error("Fatal error during Earnings Scan: %s", e)
        log_notification("Error", f"Earnings Volatility Scan failed with a fatal error: {str(e)}")
    finally:
        if conn:
            conn.close()

    return failed_tickers

if __name__ == "__main__":
    # Standalone execution logic for testing
    test_tickers = ["AAPL", "NVDA", "MSFT", "TSLA"]
    run_earnings_vol_scan(test_tickers)
