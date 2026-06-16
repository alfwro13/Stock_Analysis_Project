"""
tests/test_bubble_radar_engine.py — Bubble Radar Engine Tests

Covers:
  • Scoring functions — known metric values → expected point totals
  • _flag_from_score() — threshold boundary conditions
  • _record_history() + _backfill_outcomes() — DB round-trip
  • run_bubble_scan() — full pipeline with mocked data sources
  • API endpoints: GET/POST /api/bubble-radar/*
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
from bubble_radar_engine import (
    _score_sma_ext,
    _score_rsi,
    _score_ps,
    _score_peg,
    _score_fcf_yield_gap,
    _score_iv_skew,
    _score_spy_rsp,
    _flag_from_score,
    _record_history,
    _backfill_outcomes,
    get_bubble_radar_data,
    get_bubble_ticker_detail,
    get_bubble_radar_history,
    run_bubble_scan,
)


# ── Scoring functions ────────────────────────────────────────────────────────

class TestScoreSmaExt:
    def test_none_returns_zero(self):
        assert _score_sma_ext(None) == 0

    def test_below_15_returns_zero(self):
        assert _score_sma_ext(10.0) == 0
        assert _score_sma_ext(0.0) == 0

    def test_15_to_25_returns_5(self):
        assert _score_sma_ext(20.0) == 5

    def test_25_to_40_returns_12(self):
        assert _score_sma_ext(30.0) == 12

    def test_40_to_60_returns_20(self):
        assert _score_sma_ext(50.0) == 20

    def test_above_60_returns_25(self):
        assert _score_sma_ext(65.0) == 25

    def test_negative_returns_zero(self):
        assert _score_sma_ext(-10.0) == 0


class TestScoreRsi:
    def test_none_returns_zero(self):
        assert _score_rsi(None) == 0

    def test_below_60_returns_zero(self):
        assert _score_rsi(55.0) == 0

    def test_60_to_65_returns_5(self):
        assert _score_rsi(62.0) == 5

    def test_65_to_70_returns_10(self):
        assert _score_rsi(67.0) == 10

    def test_70_to_75_returns_15(self):
        assert _score_rsi(72.0) == 15

    def test_above_75_returns_20(self):
        assert _score_rsi(78.0) == 20


class TestScorePs:
    def test_none_returns_zero(self):
        assert _score_ps(None) == 0

    def test_below_5_returns_zero(self):
        assert _score_ps(3.0) == 0

    def test_5_to_10_returns_5(self):
        assert _score_ps(7.5) == 5

    def test_10_to_20_returns_10(self):
        assert _score_ps(15.0) == 10

    def test_above_20_returns_15(self):
        assert _score_ps(25.0) == 15


class TestScorePeg:
    def test_none_returns_zero(self):
        assert _score_peg(None) == 0

    def test_zero_or_negative_returns_zero(self):
        assert _score_peg(0.0) == 0
        assert _score_peg(-1.0) == 0

    def test_below_1_5_returns_zero(self):
        assert _score_peg(1.2) == 0

    def test_1_5_to_2_5_returns_5(self):
        assert _score_peg(2.0) == 5

    def test_2_5_to_4_returns_10(self):
        assert _score_peg(3.0) == 10

    def test_above_4_returns_15(self):
        assert _score_peg(4.5) == 15


class TestScoreFcfYieldGap:
    def test_none_inputs_return_zero(self):
        assert _score_fcf_yield_gap(None, 2.0) == 0
        assert _score_fcf_yield_gap(1.0, None) == 0
        assert _score_fcf_yield_gap(None, None) == 0

    def test_negative_gap_returns_zero(self):
        assert _score_fcf_yield_gap(5.0, 2.0) == 0

    def test_small_positive_gap_returns_5(self):
        assert _score_fcf_yield_gap(1.5, 2.5) == 5

    def test_gap_2_to_4_returns_8(self):
        assert _score_fcf_yield_gap(0.0, 3.0) == 8

    def test_gap_above_4_returns_10(self):
        assert _score_fcf_yield_gap(-3.0, 2.0) == 10


class TestScoreIvSkew:
    def test_none_returns_zero(self):
        assert _score_iv_skew(None) == 0

    def test_below_1_returns_zero(self):
        assert _score_iv_skew(0.9) == 0

    def test_1_to_1_2_returns_3(self):
        assert _score_iv_skew(1.1) == 3

    def test_1_2_to_1_5_returns_7(self):
        assert _score_iv_skew(1.3) == 7

    def test_above_1_5_returns_10(self):
        assert _score_iv_skew(1.8) == 10


class TestScoreSpyRsp:
    def test_none_returns_zero(self):
        assert _score_spy_rsp(None) == 0

    def test_below_2_returns_zero(self):
        assert _score_spy_rsp(1.0) == 0

    def test_2_to_5_returns_2(self):
        assert _score_spy_rsp(3.0) == 2

    def test_5_to_10_returns_4(self):
        assert _score_spy_rsp(7.0) == 4

    def test_above_10_returns_5(self):
        assert _score_spy_rsp(12.0) == 5


class TestFlagFromScore:
    def test_below_watch_returns_none(self):
        assert _flag_from_score(50, 70, 85) is None
        assert _flag_from_score(69, 70, 85) is None

    def test_at_watch_threshold_returns_watch(self):
        assert _flag_from_score(70, 70, 85) == "watch"
        assert _flag_from_score(84, 70, 85) == "watch"

    def test_at_bubble_threshold_returns_bubble(self):
        assert _flag_from_score(85, 70, 85) == "bubble"
        assert _flag_from_score(100, 70, 85) == "bubble"

    def test_custom_thresholds(self):
        assert _flag_from_score(60, 60, 80) == "watch"
        assert _flag_from_score(80, 60, 80) == "bubble"
        assert _flag_from_score(59, 60, 80) is None


# ── Score sum integration ────────────────────────────────────────────────────

def test_max_score_does_not_exceed_100():
    total = (
        _score_sma_ext(99.0)
        + _score_rsi(99.0)
        + _score_ps(99.0)
        + _score_peg(99.0)
        + _score_fcf_yield_gap(-50.0, 5.0)
        + _score_iv_skew(2.0)
        + _score_spy_rsp(15.0)
    )
    assert total == 100, f"Expected max 100, got {total}"


def test_all_none_metrics_gives_zero_score():
    total = (
        _score_sma_ext(None)
        + _score_rsi(None)
        + _score_ps(None)
        + _score_peg(None)
        + _score_fcf_yield_gap(None, None)
        + _score_iv_skew(None)
        + _score_spy_rsp(None)
    )
    assert total == 0


# ── DB round-trips ────────────────────────────────────────────────────────────

@pytest.fixture()
def conn():
    c = db.get_connection()
    yield c
    c.close()


def test_record_history_inserts_row(conn):
    scan_date = "2026-01-10"
    _record_history("TSTBBL", scan_date, "bubble", 100.0, conn)
    conn.commit()
    row = conn.execute(
        "SELECT * FROM bubble_radar_history WHERE ticker='TSTBBL' AND flagged_date=?", (scan_date,)
    ).fetchone()
    assert row is not None
    assert row["flag_level"] == "bubble"
    assert row["price_at_flag"] == pytest.approx(100.0)
    conn.execute("DELETE FROM bubble_radar_history WHERE ticker='TSTBBL'")
    conn.commit()


def test_record_history_is_idempotent(conn):
    scan_date = "2026-01-11"
    _record_history("TSTBBL2", scan_date, "watch", 50.0, conn)
    _record_history("TSTBBL2", scan_date, "bubble", 55.0, conn)
    conn.commit()
    rows = conn.execute(
        "SELECT * FROM bubble_radar_history WHERE ticker='TSTBBL2' AND flagged_date=?", (scan_date,)
    ).fetchall()
    assert len(rows) == 1, "INSERT OR IGNORE should keep only the first row"
    conn.execute("DELETE FROM bubble_radar_history WHERE ticker='TSTBBL2'")
    conn.commit()


def test_backfill_outcomes_fills_correct(conn):
    flagged_date = (datetime.now(timezone.utc) - timedelta(weeks=5)).strftime("%Y-%m-%d")
    price_at_flag = 200.0
    conn.execute(
        "INSERT OR IGNORE INTO bubble_radar_history (ticker, flagged_date, flag_level, price_at_flag) VALUES (?,?,?,?)",
        ("TSTBCK", flagged_date, "bubble", price_at_flag),
    )
    target_date = (datetime.now(timezone.utc) - timedelta(weeks=1)).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT OR IGNORE INTO quant_signals (ticker, date, close_price) VALUES (?,?,?)",
        ("TSTBCK", target_date, 180.0),
    )
    conn.commit()
    _backfill_outcomes(conn)
    conn.commit()
    row = conn.execute(
        "SELECT * FROM bubble_radar_history WHERE ticker='TSTBCK'"
    ).fetchone()
    assert row["price_4w"] == pytest.approx(180.0)
    assert row["outcome_4w"] == "correct"
    conn.execute("DELETE FROM bubble_radar_history WHERE ticker='TSTBCK'")
    conn.execute("DELETE FROM quant_signals WHERE ticker='TSTBCK'")
    conn.commit()


def test_bubble_radar_metrics_upsert(conn):
    ticker = "UPSTTST"
    scan_date = "2026-06-01"
    conn.execute(
        """INSERT OR REPLACE INTO bubble_radar_metrics
           (ticker, scan_date, bubble_score, flag, sma_ext_pct, rsi_avg_20d,
            ps_ratio, peg_ratio, fcf_yield, riskfree_rate, iv_call_skew, spy_rsp_spread)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ticker, scan_date, 88.0, "bubble", 45.0, 77.0, 22.0, 4.5, -1.0, 2.5, 1.4, 6.0),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM bubble_radar_metrics WHERE ticker=? AND scan_date=?", (ticker, scan_date)
    ).fetchone()
    assert row is not None
    assert row["bubble_score"] == pytest.approx(88.0)
    assert row["flag"] == "bubble"
    conn.execute("DELETE FROM bubble_radar_metrics WHERE ticker=?", (ticker,))
    conn.commit()


# ── run_bubble_scan integration ───────────────────────────────────────────────

@patch("bubble_radar_engine._get_spy_rsp_spread", return_value=None)
@patch("bubble_radar_engine._compute_iv_skew", return_value=None)
@patch("bubble_radar_engine._is_us_ticker", return_value=False)
@patch("bubble_radar_engine._backfill_outcomes")
def test_run_bubble_scan_returns_dict(mock_bf, mock_us, mock_iv, mock_spread, conn):
    ticker = "SCANTST"
    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT OR IGNORE INTO quant_signals (ticker, date, close_price, sma_200, rsi_14) VALUES (?,?,?,?,?)",
        (ticker, scan_date, 150.0, 100.0, 78.0),
    )
    conn.execute(
        "INSERT OR IGNORE INTO stock_signals (ticker, current_price, price_to_sales, free_cash_flow, peg_ratio) VALUES (?,?,?,?,?)",
        (ticker, 150.0, 25.0, 5_000_000.0, 5.0),
    )
    conn.execute(
        "INSERT OR IGNORE INTO ticker_metadata (ticker, market_cap) VALUES (?,?)",
        (ticker, 500_000_000.0),
    )
    conn.commit()

    with patch("bubble_radar_engine.load_config", return_value={
        "SCHEDULING": {"BUBBLE_RADAR": {"WATCH_THRESHOLD": 70, "FLAG_THRESHOLD": 85}}
    }):
        results = run_bubble_scan([ticker])

    assert ticker in results
    assert "score" in results[ticker]
    assert results[ticker]["score"] >= 0

    conn.execute("DELETE FROM bubble_radar_metrics WHERE ticker=?", (ticker,))
    conn.execute("DELETE FROM bubble_radar_history WHERE ticker=?", (ticker,))
    conn.execute("DELETE FROM quant_signals WHERE ticker=?", (ticker,))
    conn.execute("DELETE FROM stock_signals WHERE ticker=?", (ticker,))
    conn.execute("DELETE FROM ticker_metadata WHERE ticker=?", (ticker,))
    conn.commit()


# ── API endpoints ─────────────────────────────────────────────────────────────

def _json(resp):
    return resp.json()


@pytest.mark.api
def test_bubble_radar_data_returns_200(client):
    resp = client.get("/api/bubble-radar/data")
    assert resp.status_code == 200
    data = _json(resp)
    assert data.get("status") == "success"
    assert isinstance(data.get("results"), list)


@pytest.mark.api
def test_bubble_radar_data_empty_on_fresh_db(client):
    resp = client.get("/api/bubble-radar/data")
    data = _json(resp)
    assert data["results"] == []


@pytest.mark.api
def test_bubble_radar_ticker_returns_200(client):
    resp = client.get("/api/bubble-radar/ticker/NVDA")
    assert resp.status_code == 200
    data = _json(resp)
    assert data.get("status") == "success"
    assert "result" in data


@pytest.mark.api
def test_bubble_radar_ticker_unknown_returns_null_result(client):
    resp = client.get("/api/bubble-radar/ticker/XXXTICKER")
    assert resp.status_code == 200
    data = _json(resp)
    assert data.get("status") == "success"
    assert data.get("result") is None


@pytest.mark.api
def test_bubble_radar_history_returns_200(client):
    resp = client.get("/api/bubble-radar/history")
    assert resp.status_code == 200
    data = _json(resp)
    assert data.get("status") == "success"
    assert isinstance(data.get("results"), list)


@pytest.mark.api
def test_bubble_radar_run_post_returns_success(client):
    with patch("scheduler_engine.run_bubble_radar_job"):
        resp = client.post("/api/bubble-radar/run")
    assert resp.status_code == 200
    data = _json(resp)
    assert data.get("status") == "success"
