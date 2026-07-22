"""Tests for score_analysis pure functions and DB-backed get_score_analysis."""
from datetime import datetime, timedelta, timezone

import pytest
import database as _db_module
from score_analysis import (
    _available_from,
    _compute_return,
    _pillar_vote,
    evaluate_pillar_confluence,
    evaluate_pillar_confluence_batch,
    get_score_analysis,
    pillar_confluence_label,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_score_history(rows):
    conn = _db_module.get_connection()
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO score_history (ticker, date, score, signal, close_price) "
            "VALUES (?, ?, ?, ?, ?)",
            (r["ticker"], r["date"], r["score"], r["signal"], r["close_price"]),
        )
    conn.commit()
    conn.close()


def _seed_quant_signals(rows):
    conn = _db_module.get_connection()
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO quant_signals (ticker, date, close_price) VALUES (?, ?, ?)",
            (r["ticker"], r["date"], r["close_price"]),
        )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def clean_tables():
    conn = _db_module.get_connection()
    conn.execute("DELETE FROM score_history")
    conn.execute("DELETE FROM quant_signals")
    conn.execute("DELETE FROM pattern_detection_history")
    conn.execute("DELETE FROM trap_phase_history")
    conn.execute("DELETE FROM earnings_volatility_history")
    conn.commit()
    conn.close()


def _seed_pattern_history(ticker, pattern_family, pattern_type, phase, scan_date):
    conn = _db_module.get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO pattern_detection_history
           (ticker, pattern_family, pattern_type, phase, scan_date, scan_ts)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ticker, pattern_family, pattern_type, phase, scan_date, f"{scan_date} 00:00:00"),
    )
    conn.commit()
    conn.close()


def _seed_trap_history(ticker, phase, scan_date):
    conn = _db_module.get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO trap_phase_history (ticker, phase, scan_date, scan_ts)
           VALUES (?, ?, ?, ?)""",
        (ticker, phase, scan_date, f"{scan_date} 00:00:00"),
    )
    conn.commit()
    conn.close()


def _seed_earnings_vol_history(ticker, scan_date, edge_score, drift_avg_pct_5d):
    conn = _db_module.get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO earnings_volatility_history (ticker, scan_date, edge_score, drift_avg_pct_5d)
           VALUES (?, ?, ?, ?)""",
        (ticker, scan_date, edge_score, drift_avg_pct_5d),
    )
    conn.commit()
    conn.close()


def _seed_ml_confidence(ticker, date_, score):
    conn = _db_module.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO quant_signals (ticker, date, ml_confidence_score) VALUES (?, ?, ?)",
        (ticker, date_, score),
    )
    conn.commit()
    conn.close()


def _d(days_ago):
    """A date string `days_ago` days before today — keeps pillar-confluence test dates inside
    the technical pillar's rolling cutoff (score_analysis._technical_signals_batch) regardless
    of when the suite actually runs, rather than hardcoding dates that age out over time."""
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _seed_quant_date(ticker, date_):
    """The technical pillar's trading-day window is derived from quant_signals (written every
    trading day), not from pattern_detection_history/trap_phase_history's own dates — see
    score_analysis._technical_signals_batch(). A test asserting a pattern/trap signal is
    in-window must seed a matching quant_signals date even with no ml_confidence_score."""
    conn = _db_module.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO quant_signals (ticker, date) VALUES (?, ?)",
        (ticker, date_),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# _pillar_vote
# ---------------------------------------------------------------------------

class TestPillarVote:
    def test_empty_abstains(self):
        assert _pillar_vote([]) is None

    def test_single_signal_wins(self):
        assert _pillar_vote(["up"]) == "up"

    def test_unanimous_up(self):
        assert _pillar_vote(["up", "up", "up"]) == "up"

    def test_unanimous_down(self):
        assert _pillar_vote(["down", "down"]) == "down"

    def test_mixed_abstains(self):
        assert _pillar_vote(["up", "down"]) is None


# ---------------------------------------------------------------------------
# evaluate_pillar_confluence_batch
# ---------------------------------------------------------------------------

class TestEvaluatePillarConfluence:
    def test_no_data_no_confluence(self):
        result = evaluate_pillar_confluence("ZZZZ")
        assert result == {"bullish_pillars": [], "bearish_pillars": [], "confluence": False, "direction": None}

    def test_two_bullish_pillars_confluence_true(self):
        _seed_pattern_history("AAPL", "flag", "bull_flag", "CONFIRMED", _d(0))
        _seed_quant_date("AAPL", _d(0))
        _seed_earnings_vol_history("AAPL", _d(0), edge_score=1.5, drift_avg_pct_5d=2.0)
        result = evaluate_pillar_confluence("AAPL")
        assert result["confluence"] is True
        assert result["direction"] == "bullish"
        assert set(result["bullish_pillars"]) == {"technical", "statistical"}
        assert result["bearish_pillars"] == []

    def test_dissenting_pillar_blocks_confluence(self):
        _seed_pattern_history("MSFT", "flag", "bull_flag", "CONFIRMED", _d(0))
        _seed_earnings_vol_history("MSFT", _d(0), edge_score=1.5, drift_avg_pct_5d=2.0)
        _seed_ml_confidence("MSFT", _d(0), 20)  # < 50 -> down
        result = evaluate_pillar_confluence("MSFT")
        assert result["confluence"] is False
        assert result["direction"] is None
        assert "ml" in result["bearish_pillars"]

    def test_bearish_confluence(self):
        _seed_pattern_history("TSLA", "flag", "bear_flag", "CONFIRMED", _d(0))
        _seed_ml_confidence("TSLA", _d(0), 30)
        result = evaluate_pillar_confluence("TSLA")
        assert result["confluence"] is True
        assert result["direction"] == "bearish"
        assert set(result["bearish_pillars"]) == {"technical", "ml"}

    def test_new_pattern_family_picked_up_automatically(self):
        # wedge_engine wasn't part of this feature's original spec — proves the technical
        # pillar resolves direction dynamically via DETECTORS/PATTERN_TYPES, not a hardcoded list.
        _seed_pattern_history("NVDA", "wedge", "falling_wedge", "CONFIRMED", _d(0))
        _seed_ml_confidence("NVDA", _d(0), 80)
        result = evaluate_pillar_confluence("NVDA")
        assert result["confluence"] is True
        assert result["direction"] == "bullish"

    def test_forming_phase_pattern_not_counted(self):
        _seed_pattern_history("GOOG", "flag", "bull_flag", "FORMING", _d(0))
        _seed_ml_confidence("GOOG", _d(0), 70)
        result = evaluate_pillar_confluence("GOOG")
        assert "technical" not in result["bullish_pillars"]

    def test_earnings_vol_requires_positive_edge_score(self):
        _seed_earnings_vol_history("AMD", _d(0), edge_score=-1.0, drift_avg_pct_5d=2.0)
        _seed_ml_confidence("AMD", _d(0), 70)
        result = evaluate_pillar_confluence("AMD")
        assert "statistical" not in result["bullish_pillars"]

    def test_signal_outside_window_ignored(self):
        # 6 distinct trading days: the oldest (stale) row falls outside the 5-day window.
        for i, days_ago in enumerate([6, 5, 4, 3, 2, 0]):
            _seed_pattern_history("META", "flag", "bear_flag" if i == 0 else "bull_flag", "CONFIRMED", _d(days_ago))
            _seed_quant_date("META", _d(days_ago))
        _seed_ml_confidence("META", _d(0), 70)
        result = evaluate_pillar_confluence("META")
        assert result["confluence"] is True
        assert result["direction"] == "bullish"

    def test_conflicting_signals_within_pillar_abstain(self):
        _seed_pattern_history("AMZN", "flag", "bull_flag", "CONFIRMED", _d(1))
        _seed_quant_date("AMZN", _d(1))
        _seed_trap_history("AMZN", "BULL_TRAP_RISK", _d(0))  # down
        _seed_ml_confidence("AMZN", _d(0), 70)
        result = evaluate_pillar_confluence("AMZN")
        assert "technical" not in result["bullish_pillars"]
        assert "technical" not in result["bearish_pillars"]

    def test_batch_matches_single_ticker_result(self):
        _seed_pattern_history("AAPL", "flag", "bull_flag", "CONFIRMED", _d(0))
        _seed_quant_date("AAPL", _d(0))
        _seed_earnings_vol_history("AAPL", _d(0), edge_score=1.5, drift_avg_pct_5d=2.0)
        batch_result = evaluate_pillar_confluence_batch(["AAPL", "ZZZZ"])
        assert batch_result["AAPL"] == evaluate_pillar_confluence("AAPL")
        assert batch_result["ZZZZ"]["confluence"] is False

    def test_batch_empty_list(self):
        assert evaluate_pillar_confluence_batch([]) == {}


class TestPillarConfluenceLabel:
    def test_none_result(self):
        assert pillar_confluence_label(None) is None

    def test_no_confluence_returns_none(self):
        assert pillar_confluence_label({"bullish_pillars": [], "bearish_pillars": [], "confluence": False, "direction": None}) is None

    def test_bullish_label(self):
        result = {"bullish_pillars": ["technical", "ml"], "bearish_pillars": [], "confluence": True, "direction": "bullish"}
        assert pillar_confluence_label(result) == "Bullish (2/3)"

    def test_bearish_label(self):
        result = {"bullish_pillars": [], "bearish_pillars": ["technical", "statistical", "ml"], "confluence": True, "direction": "bearish"}
        assert pillar_confluence_label(result) == "Bearish (3/3)"


# ---------------------------------------------------------------------------
# _available_from
# ---------------------------------------------------------------------------

class TestAvailableFrom:
    def test_adds_days_to_earliest(self):
        result = _available_from("2026-01-01", 90)
        assert result == "2026-04-01"

    def test_crosses_year_boundary(self):
        result = _available_from("2025-12-01", 90)
        assert result == "2026-03-01"

    def test_zero_days(self):
        assert _available_from("2026-06-01", 0) == "2026-06-01"


# ---------------------------------------------------------------------------
# _compute_return
# ---------------------------------------------------------------------------

class TestComputeReturn:
    def test_positive_return(self):
        result = _compute_return(100.0, 110.0)
        assert result == 10.0

    def test_negative_return(self):
        result = _compute_return(100.0, 90.0)
        assert result == -10.0

    def test_none_when_future_price_none(self):
        assert _compute_return(100.0, None) is None

    def test_none_when_entry_price_none(self):
        assert _compute_return(None, 110.0) is None

    def test_rounds_to_two_decimal_places(self):
        result = _compute_return(100.0, 101.1)
        assert result == 1.1


# ---------------------------------------------------------------------------
# get_score_analysis — empty DB
# ---------------------------------------------------------------------------

class TestGetScoreAnalysisEmpty:
    def test_empty_db_returns_zero_events(self):
        result = get_score_analysis()
        assert result["total_events"] == 0

    def test_empty_db_earliest_date_none(self):
        result = get_score_analysis()
        assert result["earliest_date"] is None

    def test_empty_db_summary_is_empty_list(self):
        result = get_score_analysis()
        assert result["summary"] == []

    def test_empty_db_events_is_empty_list(self):
        result = get_score_analysis()
        assert result["events"] == []

    def test_empty_db_horizons_all_not_ready(self):
        result = get_score_analysis()
        for h in result["horizons"].values():
            assert h["ready"] is False


# ---------------------------------------------------------------------------
# get_score_analysis — with data
# ---------------------------------------------------------------------------

class TestGetScoreAnalysisWithData:
    def _setup(self):
        _seed_score_history([
            {"ticker": "AAPL", "date": "2025-01-01", "score": 80, "signal": "STRONG BUY", "close_price": 150.0},
            {"ticker": "AAPL", "date": "2025-02-01", "score": 60, "signal": "BULLISH / HOLD", "close_price": 155.0},
            {"ticker": "MSFT", "date": "2025-01-15", "score": 40, "signal": "NEUTRAL", "close_price": 300.0},
        ])
        _seed_quant_signals([
            {"ticker": "AAPL", "date": "2025-04-01", "close_price": 165.0},  # ~90 days after 2025-01-01
            {"ticker": "AAPL", "date": "2025-04-30", "close_price": 170.0},  # ~90 days after 2025-02-01
        ])

    def test_total_events_correct(self):
        self._setup()
        result = get_score_analysis()
        assert result["total_events"] == 3

    def test_earliest_date_returned(self):
        self._setup()
        result = get_score_analysis()
        assert result["earliest_date"] == "2025-01-01"

    def test_events_have_return_keys(self):
        self._setup()
        result = get_score_analysis()
        for ev in result["events"]:
            assert "return_3m" in ev
            assert "return_6m" in ev
            assert "return_12m" in ev

    def test_summary_groups_by_signal(self):
        self._setup()
        result = get_score_analysis()
        signals_in_summary = [s["signal"] for s in result["summary"]]
        assert "STRONG BUY" in signals_in_summary
        assert "BULLISH / HOLD" in signals_in_summary
        assert "NEUTRAL" in signals_in_summary

    def test_summary_count_matches_bucket_size(self):
        self._setup()
        result = get_score_analysis()
        strong_buy = next(s for s in result["summary"] if s["signal"] == "STRONG BUY")
        assert strong_buy["count"] == 1

    def test_forward_return_computed_when_price_available(self):
        self._setup()
        result = get_score_analysis()
        aapl_jan = next(e for e in result["events"] if e["ticker"] == "AAPL" and e["date"] == "2025-01-01")
        # 2025-04-01 is within the 90-day window (±3/+7 days of 2025-04-01)
        assert aapl_jan["return_3m"] is not None

    def test_forward_return_none_when_no_price_data(self):
        self._setup()
        result = get_score_analysis()
        msft = next(e for e in result["events"] if e["ticker"] == "MSFT")
        assert msft["return_3m"] is None

    def test_events_capped_at_500(self):
        rows = [
            {"ticker": "X", "date": f"2025-{str(m).zfill(2)}-{str(d).zfill(2)}", "score": 50, "signal": "NEUTRAL", "close_price": 100.0}
            for m in range(1, 13) for d in range(1, 42 + 1)
            if m <= 12 and d <= 31 and not (m in (4, 6, 9, 11) and d > 30) and not (m == 2 and d > 28)
        ][:600]
        _seed_score_history(rows)
        result = get_score_analysis()
        assert len(result["events"]) <= 500
