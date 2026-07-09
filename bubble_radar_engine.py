# GUI name: "Bubble Radar". Canonical scheduled-job name lives in scheduler_engine.JOB_GRAPH.
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd

from config import HISTORICAL_DIR, load_config
from database import get_connection

logger = logging.getLogger(__name__)

_METRIC_LABELS = {
    "sma_ext_pct":    "SMA-200 Extension %",
    "rsi_avg_20d":    "RSI 20-Day Average",
    "ps_ratio":       "Price/Sales Ratio",
    "peg_ratio":      "PEG Ratio",
    "fcf_yield":      "FCF Yield vs Real 10Y",
    "iv_call_skew":   "IV Call Skew",
    "spy_rsp_spread": "SPY vs RSP Spread (20d)",
}


def _score_sma_ext(pct: Optional[float]) -> int:
    if pct is None:
        return 0
    if pct > 60:
        return 25
    if pct > 40:
        return 20
    if pct > 25:
        return 12
    if pct > 15:
        return 5
    return 0


def _score_rsi(avg: Optional[float]) -> int:
    if avg is None:
        return 0
    if avg > 75:
        return 20
    if avg > 70:
        return 15
    if avg > 65:
        return 10
    if avg > 60:
        return 5
    return 0


def _score_ps(ps: Optional[float]) -> int:
    if ps is None:
        return 0
    if ps > 20:
        return 15
    if ps > 10:
        return 10
    if ps > 5:
        return 5
    return 0


def _score_peg(peg: Optional[float]) -> int:
    if peg is None or peg <= 0:
        return 0
    if peg > 4.0:
        return 15
    if peg > 2.5:
        return 10
    if peg > 1.5:
        return 5
    return 0


def _score_fcf_yield_gap(fcf_yield: Optional[float], riskfree: Optional[float]) -> int:
    if fcf_yield is None or riskfree is None:
        return 0
    gap = riskfree - fcf_yield
    if gap > 4.0:
        return 10
    if gap > 2.0:
        return 8
    if gap > 0:
        return 5
    return 0


def _score_iv_skew(skew: Optional[float]) -> int:
    if skew is None:
        return 0
    if skew > 1.5:
        return 10
    if skew > 1.2:
        return 7
    if skew >= 1.0:
        return 3
    return 0


def _score_spy_rsp(spread: Optional[float]) -> int:
    if spread is None:
        return 0
    if spread > 10:
        return 5
    if spread > 5:
        return 4
    if spread > 2:
        return 2
    return 0



def _get_quant_signals(ticker: str, conn) -> tuple[Optional[float], Optional[float]]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT close_price, sma_200, rsi_14 FROM quant_signals WHERE ticker=? ORDER BY date DESC LIMIT 20",
        (ticker,),
    )
    rows = cursor.fetchall()
    if not rows:
        return None, None
    latest = rows[0]
    close = latest["close_price"]
    sma200 = latest["sma_200"]
    sma_ext = ((close - sma200) / sma200 * 100) if close and sma200 and sma200 > 0 else None
    rsi_vals = [r["rsi_14"] for r in rows if r["rsi_14"] is not None]
    rsi_avg = sum(rsi_vals) / len(rsi_vals) if rsi_vals else None
    return sma_ext, rsi_avg


def _get_fundamentals(ticker: str, conn) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT price_to_sales, free_cash_flow, peg_ratio, current_price FROM stock_signals WHERE ticker=?",
        (ticker,),
    )
    row = cursor.fetchone()
    if not row:
        return None, None, None, None
    ps = row["price_to_sales"]
    fcf = row["free_cash_flow"]
    peg = row["peg_ratio"]
    price = row["current_price"]

    fcf_yield_pct: Optional[float] = None
    if fcf is not None:
        cursor.execute("SELECT market_cap FROM ticker_metadata WHERE ticker=?", (ticker,))
        mc_row = cursor.fetchone()
        if mc_row and mc_row["market_cap"] and mc_row["market_cap"] > 0:
            fcf_yield_pct = (fcf / mc_row["market_cap"]) * 100

    return ps, fcf_yield_pct, peg, price


def _get_real_yield(conn) -> Optional[float]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT us_real_yield_10y FROM macro_indicators WHERE us_real_yield_10y IS NOT NULL ORDER BY date DESC LIMIT 1"
    )
    row = cursor.fetchone()
    return row["us_real_yield_10y"] if row else None



def _get_spy_rsp_spread() -> Optional[float]:
    try:
        spy_path = HISTORICAL_DIR / "SPY_BASELINE.parquet"
        rsp_path = HISTORICAL_DIR / "RSP_BASELINE.parquet"
        if not spy_path.exists() or not rsp_path.exists():
            return None
        spy = pd.read_parquet(spy_path)["Close"].dropna()
        rsp = pd.read_parquet(rsp_path)["Close"].dropna()
        if len(spy) < 21 or len(rsp) < 21:
            return None
        spy_ret = (spy.iloc[-1] / spy.iloc[-21] - 1) * 100
        rsp_ret = (rsp.iloc[-1] / rsp.iloc[-21] - 1) * 100
        return float(spy_ret - rsp_ret)
    except Exception as e:
        logger.warning("SPY/RSP spread computation failed: %s", e)
        return None


def _is_us_ticker(ticker: str, conn) -> bool:
    cursor = conn.cursor()
    cursor.execute("SELECT currency, exchange FROM asset_profiles WHERE ticker=?", (ticker,))
    row = cursor.fetchone()
    if not row:
        return not ("." in ticker)
    currency = row["currency"] or ""
    exchange = row["exchange"] or ""
    return currency == "USD" and exchange not in ("LSE", "XETRA", "TSE", "ASX")


def _compute_iv_skew(ticker: str, current_price: Optional[float]) -> Optional[float]:
    if not current_price:
        return None
    try:
        from options_engine import fetch_front_month_chain
        chain = fetch_front_month_chain(ticker)
        if "error" in chain:
            return None
        calls = [c for c in chain.get("calls", []) if c.get("strike") and c["strike"] > current_price and c.get("impliedVolatility")]
        puts = [p for p in chain.get("puts", []) if p.get("strike") and p["strike"] < current_price and p.get("impliedVolatility")]
        if not calls or not puts:
            return None
        otm_call_iv = sum(c["impliedVolatility"] for c in calls) / len(calls)
        otm_put_iv = sum(p["impliedVolatility"] for p in puts) / len(puts)
        if otm_put_iv <= 0:
            return None
        return round(otm_call_iv / otm_put_iv, 3)
    except Exception as e:
        logger.debug("IV skew fetch failed for %s: %s", ticker, e)
        return None


def _flag_from_score(score: int, watch_threshold: int, flag_threshold: int) -> Optional[str]:
    if score >= flag_threshold:
        return "bubble"
    if score >= watch_threshold:
        return "watch"
    return None


def _record_history(ticker: str, scan_date: str, flag: str, price: Optional[float], conn) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO bubble_radar_history (ticker, flagged_date, flag_level, price_at_flag) VALUES (?,?,?,?)",
        (ticker, scan_date, flag, price),
    )


def _backfill_outcomes(conn) -> None:
    cursor = conn.cursor()
    today = datetime.now(timezone.utc).date()
    cursor.execute(
        "SELECT id, ticker, flagged_date, price_at_flag FROM bubble_radar_history "
        "WHERE (price_4w IS NULL OR price_8w IS NULL OR price_12w IS NULL)"
    )
    rows = cursor.fetchall()
    for row in rows:
        flagged = datetime.strptime(row["flagged_date"], "%Y-%m-%d").date()
        price_at_flag = row["price_at_flag"]
        if not price_at_flag:
            continue
        for weeks, col_price, col_outcome in ((4, "price_4w", "outcome_4w"), (8, "price_8w", "outcome_8w"), (12, "price_12w", "outcome_12w")):
            target_date = flagged + timedelta(weeks=weeks)
            if today < target_date:
                continue
            q2 = conn.cursor()
            q2.execute(
                "SELECT close_price FROM quant_signals WHERE ticker=? AND date>=? ORDER BY date ASC LIMIT 1",
                (row["ticker"], target_date.isoformat()),
            )
            price_row = q2.fetchone()
            if not price_row:
                continue
            actual = price_row["close_price"]
            outcome = "correct" if actual < price_at_flag else "incorrect"
            conn.cursor().execute(
                f"UPDATE bubble_radar_history SET {col_price}=?, {col_outcome}=? WHERE id=?",
                (actual, outcome, row["id"]),
            )


def run_bubble_scan(tickers: list[str]) -> dict:
    cfg = load_config()
    bubble_cfg = cfg.get("SCHEDULING", {}).get("BUBBLE_RADAR", {})
    watch_threshold = int(bubble_cfg.get("WATCH_THRESHOLD", 70))
    flag_threshold = int(bubble_cfg.get("FLAG_THRESHOLD", 85))

    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    spy_rsp = _get_spy_rsp_spread()

    # Phase 1: back-fill outcomes — short write transaction, connection released immediately.
    try:
        conn = get_connection()
        _backfill_outcomes(conn)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Bubble radar back-fill failed: %s", e)

    # Phase 2: read all per-ticker data from DB — connection released before any network I/O.
    ticker_data: dict[str, dict] = {}
    real_yield: Optional[float] = None
    try:
        conn = get_connection()
        real_yield = _get_real_yield(conn)
        for ticker in tickers:
            try:
                sma_ext, rsi_avg = _get_quant_signals(ticker, conn)
                ps, fcf_yield_pct, peg, price = _get_fundamentals(ticker, conn)
                is_us = _is_us_ticker(ticker, conn)
                ticker_data[ticker] = {
                    "sma_ext": sma_ext, "rsi_avg": rsi_avg,
                    "ps": ps, "fcf_yield_pct": fcf_yield_pct,
                    "peg": peg, "price": price, "is_us": is_us,
                }
            except Exception as e:
                logger.error("Bubble scan read failed for %s: %s", ticker, e)
        conn.close()
    except Exception as e:
        logger.error("Bubble radar read phase aborted: %s", e)
        return {}

    # Phase 3: network I/O — options chain fetches — no DB connection held.
    for ticker, data in ticker_data.items():
        data["iv_skew"] = _compute_iv_skew(ticker, data.get("price")) if data.get("is_us") else None

    # Phase 4: compute scores (pure CPU, no I/O).
    results: dict[str, dict] = {}
    metric_rows: list[tuple] = []
    history_rows: list[tuple] = []
    for ticker, data in ticker_data.items():
        try:
            score = min(100, (
                _score_sma_ext(data["sma_ext"])
                + _score_rsi(data["rsi_avg"])
                + _score_ps(data["ps"])
                + _score_peg(data["peg"])
                + _score_fcf_yield_gap(data["fcf_yield_pct"], real_yield)
                + _score_iv_skew(data["iv_skew"])
                + _score_spy_rsp(spy_rsp)
            ))
            flag = _flag_from_score(score, watch_threshold, flag_threshold)
            metric_rows.append((
                ticker, scan_date, score, flag,
                data["sma_ext"], data["rsi_avg"],
                data["ps"], data["peg"], data["fcf_yield_pct"],
                real_yield, data["iv_skew"], spy_rsp,
            ))
            if flag:
                history_rows.append((ticker, scan_date, flag, data["price"]))
            results[ticker] = {"score": score, "flag": flag}
        except Exception as e:
            logger.error("Bubble scan score failed for %s: %s", ticker, e)

    # Phase 5: write all results in one short transaction — connection held only for the commit.
    try:
        conn = get_connection()
        for row in metric_rows:
            conn.execute(
                """INSERT OR REPLACE INTO bubble_radar_metrics
                   (ticker, scan_date, bubble_score, flag, sma_ext_pct, rsi_avg_20d,
                    ps_ratio, peg_ratio, fcf_yield, riskfree_rate, iv_call_skew, spy_rsp_spread)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
        for ticker, scan_date_, flag, price in history_rows:
            _record_history(ticker, scan_date_, flag, price, conn)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Bubble radar write phase failed: %s", e)

    flagged = sum(1 for v in results.values() if v.get("flag"))
    logger.info("Bubble radar scan complete — %s tickers, %s flagged.", len(results), flagged)
    return results


def get_bubble_radar_data() -> list[dict]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT b.ticker, b.scan_date, b.bubble_score, b.flag,
                      b.sma_ext_pct, b.rsi_avg_20d, b.ps_ratio, b.peg_ratio,
                      b.fcf_yield, b.riskfree_rate, b.iv_call_skew, b.spy_rsp_spread,
                      s.company_name, s.sector
               FROM bubble_radar_metrics b
               LEFT JOIN stock_signals s ON b.ticker = s.ticker
               WHERE b.flag IS NOT NULL
               AND b.scan_date = (
                   SELECT MAX(scan_date) FROM bubble_radar_metrics b2 WHERE b2.ticker = b.ticker
               )
               ORDER BY b.bubble_score DESC"""
        )
        return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.error("get_bubble_radar_data failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def get_bubble_ticker_detail(ticker: str) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT b.*, s.company_name, s.sector
               FROM bubble_radar_metrics b
               LEFT JOIN stock_signals s ON b.ticker = s.ticker
               WHERE b.ticker=?
               ORDER BY b.scan_date DESC LIMIT 1""",
            (ticker.upper(),),
        )
        row = cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        data["metric_scores"] = {
            "sma_ext_pct":    {"label": _METRIC_LABELS["sma_ext_pct"],    "value": data.get("sma_ext_pct"),    "score": _score_sma_ext(data.get("sma_ext_pct"))},
            "rsi_avg_20d":    {"label": _METRIC_LABELS["rsi_avg_20d"],    "value": data.get("rsi_avg_20d"),    "score": _score_rsi(data.get("rsi_avg_20d"))},
            "ps_ratio":       {"label": _METRIC_LABELS["ps_ratio"],       "value": data.get("ps_ratio"),       "score": _score_ps(data.get("ps_ratio"))},
            "peg_ratio":      {"label": _METRIC_LABELS["peg_ratio"],      "value": data.get("peg_ratio"),      "score": _score_peg(data.get("peg_ratio"))},
            "fcf_yield":      {"label": _METRIC_LABELS["fcf_yield"],      "value": data.get("fcf_yield"),      "score": _score_fcf_yield_gap(data.get("fcf_yield"), data.get("riskfree_rate"))},
            "iv_call_skew":   {"label": _METRIC_LABELS["iv_call_skew"],   "value": data.get("iv_call_skew"),   "score": _score_iv_skew(data.get("iv_call_skew"))},
            "spy_rsp_spread": {"label": _METRIC_LABELS["spy_rsp_spread"], "value": data.get("spy_rsp_spread"), "score": _score_spy_rsp(data.get("spy_rsp_spread"))},
        }
        return data
    except Exception as e:
        logger.error("get_bubble_ticker_detail failed for %s: %s", ticker, e)
        return None
    finally:
        if conn:
            conn.close()


def get_bubble_radar_history() -> list[dict]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT h.*, s.company_name
               FROM bubble_radar_history h
               LEFT JOIN stock_signals s ON h.ticker = s.ticker
               ORDER BY h.flagged_date DESC
               LIMIT 200"""
        )
        return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.error("get_bubble_radar_history failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()
