"""Tests for score_analysis pure functions and DB-backed get_score_analysis."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from db import database as _db_module, schema as _db_schema
from score_analysis import (
    _regime_as_of,
    _available_from,
    _compute_return,
    _pillar_vote,
    buy_recommendation_label,
    compute_regime_weighted_score,
    compute_regime_weighted_score_as_of,
    compute_regime_weighted_score_batch,
    evaluate_buy_recommendation,
    evaluate_buy_recommendation_batch,
    evaluate_pillar_confluence,
    evaluate_pillar_confluence_as_of,
    evaluate_pillar_confluence_batch,
    get_score_analysis,
    pillar_confluence_label,
)

pytestmark = pytest.mark.restore_tables("data_migrations", "market_regimes", "market_stress_states", "pattern_detection_history", "price_hmm_states", "quant_signals", "stock_signals", "trap_phase_history")

_DEFAULT_META_SCORING = {
    "REGIME_WEIGHTS": {
        "Bull": {"composite_score": 0.40, "ml_confidence": 0.30, "pattern": 0.20, "trap": 0.10},
        "Chop": {"composite_score": 0.25, "ml_confidence": 0.25, "pattern": 0.35, "trap": 0.15},
        "High Volatility": {"composite_score": 0.20, "ml_confidence": 0.20, "pattern": 0.30, "trap": 0.30},
    },
    "CRASH_VETO": {"MARKET_STRESS_THRESHOLD": 0.75, "HMM_MEAN_RETURN_FLOOR": 0.0},
}


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


def _seed_stock_signals(ticker, composite_score, ml_confidence):
    conn = _db_module.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO stock_signals (ticker, composite_score, ml_confidence) VALUES (?, ?, ?)",
        (ticker, composite_score, ml_confidence),
    )
    conn.commit()
    conn.close()


_AS_OF_DATE = "2020-01-15"


@pytest.fixture(autouse=True)
def clean_tables():
    conn = _db_module.get_connection()
    conn.execute("DELETE FROM score_history")
    conn.execute("DELETE FROM quant_signals")
    conn.execute("DELETE FROM pattern_detection_history")
    conn.execute("DELETE FROM trap_phase_history")
    conn.execute("DELETE FROM earnings_volatility_history")
    conn.execute("DELETE FROM stock_signals WHERE ticker LIKE 'RW%'")
    conn.execute("DELETE FROM market_regimes WHERE date = ?", (_AS_OF_DATE,))
    conn.execute("DELETE FROM price_hmm_states WHERE date <= ?", (_AS_OF_DATE,))
    conn.execute("DELETE FROM market_stress_states WHERE date <= ?", (_AS_OF_DATE,))
    conn.commit()
    conn.close()


def _seed_market_regime_as_of(date_, price_hmm_label, market_stress_score=0.1, mean_return=None):
    conn = _db_module.get_connection()
    if price_hmm_label is not None:
        conn.execute(
            "INSERT OR REPLACE INTO price_hmm_states (date, state, label, probability, mean_return) VALUES (?, 0, ?, 1.0, ?)",
            (date_, price_hmm_label, mean_return),
        )
    if market_stress_score is not None:
        conn.execute(
            "INSERT OR REPLACE INTO market_stress_states (date, score) VALUES (?, ?)",
            (date_, market_stress_score),
        )
    conn.commit()
    conn.close()


def _seed_quant_signal_as_of(ticker, date_, composite_score, ml_confidence_score):
    conn = _db_module.get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO quant_signals (ticker, date, composite_score, ml_confidence_score)
           VALUES (?, ?, ?, ?)""",
        (ticker, date_, composite_score, ml_confidence_score),
    )
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


def _seed_ml_confidence(ticker, date_, score, scored_at=None):
    conn = _db_module.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO quant_signals (ticker, date, ml_confidence_score, ml_scored_at) VALUES (?, ?, ?, ?)",
        (ticker, date_, score, scored_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
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

    @staticmethod
    def _ml_votes(ticker, as_of=None):
        from score_analysis import _ml_signals_batch
        conn = _db_module.get_connection()
        try:
            return _ml_signals_batch([ticker], conn, as_of=as_of)[ticker]
        finally:
            conn.close()

    def test_ml_score_older_than_the_freshness_window_does_not_vote_live(self):
        _seed_ml_confidence("STALEML", _d(0), 80, scored_at="2020-01-01 01:30:00")
        assert self._ml_votes("STALEML") == []

    def test_unstamped_ml_score_does_not_vote_live(self):
        conn = _db_module.get_connection()
        conn.execute("INSERT OR REPLACE INTO quant_signals (ticker, date, ml_confidence_score) VALUES ('UNSTAMPEDML', ?, 80)", (_d(0),))
        conn.commit()
        conn.close()
        assert self._ml_votes("UNSTAMPEDML") == []

    def test_ml_pillar_votes_up_above_the_base_rate_and_down_below_it(self):
        from constants import ML_CONFIDENCE_BASE_RATE
        _seed_ml_confidence("MLABOVE", _d(0), ML_CONFIDENCE_BASE_RATE + 1)
        _seed_ml_confidence("MLBELOW", _d(0), ML_CONFIDENCE_BASE_RATE - 1)
        assert self._ml_votes("MLABOVE") == ["up"]
        assert self._ml_votes("MLBELOW") == ["down"]

    def test_fresh_newest_score_keeps_older_scores_in_the_window(self):
        _seed_ml_confidence("FRESHML", _d(2), 20, scored_at="2020-01-01 01:30:00")
        _seed_ml_confidence("FRESHML", _d(0), 80)
        assert self._ml_votes("FRESHML") == ["up", "down"]

    def test_stale_ml_score_still_votes_when_reconstructing_a_past_date(self):
        _seed_ml_confidence("STALEASOF", _d(3), 80, scored_at="2020-01-01 01:30:00")
        assert self._ml_votes("STALEASOF", as_of=_d(1)) == ["up"]

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


# ---------------------------------------------------------------------------
# compute_regime_weighted_score_batch
# ---------------------------------------------------------------------------

def _patched_regime(regime, market_stress_score=0.1, meta_scoring=None, mean_return=None):
    regime_row = (
        {"price_hmm_label": regime, "price_hmm_mean_return": mean_return, "market_stress_score": market_stress_score}
        if regime else None
    )
    cfg = {"META_SCORING": meta_scoring or _DEFAULT_META_SCORING}
    return (
        patch("regime_engine.get_latest_regime", return_value=regime_row),
        patch("config.load_config", return_value=cfg),
    )


class TestComputeRegimeWeightedScore:
    def test_bull_regime_weighting(self):
        _seed_stock_signals("RWA", composite_score=80, ml_confidence=60)
        p1, p2 = _patched_regime("Bull")
        with p1, p2:
            result = compute_regime_weighted_score("RWA")
        assert result is not None
        assert result["regime"] == "Bull"
        # 90(80 rescaled)*.40 + 60*.30 + 50(neutral pattern)*.20 + 50(neutral trap)*.10
        assert result["score"] == pytest.approx(69.0)
        assert result["components"] == {"composite_score": 90.0, "ml_confidence": 60.0, "pattern": 50.0, "trap": 50.0}

    def test_chop_regime_uses_different_weights(self):
        _seed_stock_signals("RWB", composite_score=80, ml_confidence=60)
        p1, p2 = _patched_regime("Chop")
        with p1, p2:
            result = compute_regime_weighted_score("RWB")
        assert result is not None
        assert result["regime"] == "Chop"
        # 90*.25 + 60*.25 + 50*.35 + 50*.15
        assert result["score"] == pytest.approx(62.5)

    def test_negative_composite_is_rescaled_not_subtracted(self):
        _seed_stock_signals("RWK", composite_score=-40, ml_confidence=60)
        p1, p2 = _patched_regime("Bull")
        with p1, p2:
            result = compute_regime_weighted_score("RWK")
        # 30(-40 rescaled)*.40 + 60*.30 + 50*.20 + 50*.10
        assert result["components"]["composite_score"] == pytest.approx(30.0)
        assert result["score"] == pytest.approx(45.0)

    @pytest.mark.parametrize("composite, expected_component", [(-100, 0.0), (0, 50.0), (100, 100.0)])
    def test_composite_extremes_keep_every_component_within_0_100(self, composite, expected_component):
        ticker = f"RWX{composite + 100}"
        _seed_stock_signals(ticker, composite_score=composite, ml_confidence=0)
        p1, p2 = _patched_regime("Bull")
        with p1, p2:
            result = compute_regime_weighted_score(ticker)
        assert result["components"]["composite_score"] == expected_component
        assert all(0.0 <= v <= 100.0 for v in result["components"].values())
        assert 0.0 <= result["score"] <= 100.0

    def test_high_volatility_with_negative_mean_return_returns_none(self):
        _seed_stock_signals("RWC", composite_score=80, ml_confidence=60)
        p1, p2 = _patched_regime("High Volatility", mean_return=-0.05)
        with p1, p2:
            result = compute_regime_weighted_score("RWC")
        assert result is None

    def test_high_volatility_with_positive_mean_return_is_scored_with_its_weights(self):
        _seed_stock_signals("RWC2", composite_score=80, ml_confidence=60)
        p1, p2 = _patched_regime("High Volatility", mean_return=0.02)
        with p1, p2:
            result = compute_regime_weighted_score("RWC2")
        assert result["regime"] == "High Volatility"
        comps = result["components"]
        w = _DEFAULT_META_SCORING["REGIME_WEIGHTS"]["High Volatility"]
        expected = sum(w[k] * comps[k] for k in w if comps.get(k) is not None) / sum(w[k] for k in w if comps.get(k) is not None)
        assert result["score"] == pytest.approx(round(expected, 1), abs=0.1)

    def test_high_volatility_with_unknown_mean_return_returns_none(self):
        _seed_stock_signals("RWC3", composite_score=80, ml_confidence=60)
        p1, p2 = _patched_regime("High Volatility", mean_return=None)
        with p1, p2:
            result = compute_regime_weighted_score("RWC3")
        assert result is None

    def test_configured_floor_above_mean_return_returns_none(self):
        _seed_stock_signals("RWC4", composite_score=80, ml_confidence=60)
        meta = {**_DEFAULT_META_SCORING, "CRASH_VETO": {"MARKET_STRESS_THRESHOLD": 0.75, "HMM_MEAN_RETURN_FLOOR": 0.05}}
        p1, p2 = _patched_regime("High Volatility", mean_return=0.02, meta_scoring=meta)
        with p1, p2:
            result = compute_regime_weighted_score("RWC4")
        assert result is None

    def test_market_stress_threshold_vetoes_even_in_bull_regime(self):
        _seed_stock_signals("RWD", composite_score=80, ml_confidence=60)
        p1, p2 = _patched_regime("Bull", market_stress_score=0.9)
        with p1, p2:
            result = compute_regime_weighted_score("RWD")
        assert result is None

    def test_as_of_path_reads_mean_return_from_price_hmm_states(self):
        _seed_market_regime_as_of(_AS_OF_DATE, "High Volatility", mean_return=0.07)
        assert _regime_as_of(_AS_OF_DATE)["price_hmm_mean_return"] == 0.07

    def test_missing_regime_data_returns_none(self):
        _seed_stock_signals("RWE", composite_score=80, ml_confidence=60)
        p1, p2 = _patched_regime(None)
        with p1, p2:
            result = compute_regime_weighted_score("RWE")
        assert result is None

    def test_missing_composite_score_returns_none_for_that_ticker_only(self):
        _seed_stock_signals("RWF", composite_score=None, ml_confidence=60)
        _seed_stock_signals("RWG", composite_score=80, ml_confidence=60)
        p1, p2 = _patched_regime("Bull")
        with p1, p2:
            batch = compute_regime_weighted_score_batch(["RWF", "RWG"])
        assert batch["RWF"] is None
        assert batch["RWG"] is not None

    def test_pattern_and_trap_folded_in_independently(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _seed_stock_signals("RWH", composite_score=50, ml_confidence=50)
        _seed_quant_signals([{"ticker": "RWH", "date": today, "close_price": 100.0}])
        conn = _db_module.get_connection()
        conn.execute(
            """INSERT OR REPLACE INTO pattern_detection_history
               (ticker, pattern_family, pattern_type, phase, scan_date, scan_ts)
               VALUES ('RWH', 'flag', 'bull_flag', 'CONFIRMED', ?, ?)""",
            (today, f"{today} 00:00:00"),
        )
        conn.commit()
        conn.close()

        p1, p2 = _patched_regime("Bull")
        with p1, p2:
            result = compute_regime_weighted_score("RWH")
        assert result["components"]["pattern"] == 100.0
        assert result["components"]["trap"] == 50.0
        # 75(50 rescaled)*.40 + 50*.30 + 100*.20 + 50*.10
        assert result["score"] == pytest.approx(70.0)

    def test_batch_matches_single_ticker_result(self):
        _seed_stock_signals("RWI", composite_score=70, ml_confidence=40)
        p1, p2 = _patched_regime("Bull")
        with p1, p2:
            batch_result = compute_regime_weighted_score_batch(["RWI"])
            single_result = compute_regime_weighted_score("RWI")
        assert batch_result["RWI"] == single_result

    def test_empty_ticker_list(self):
        assert compute_regime_weighted_score_batch([]) == {}

    def test_unknown_ticker_returns_none(self):
        p1, p2 = _patched_regime("Bull")
        with p1, p2:
            result = compute_regime_weighted_score("ZZZZ")
        assert result is None

    def test_malformed_weight_vector_missing_key_does_not_crash(self):
        """META_SCORING is user-editable config, not internal state — a manually-edited
        config.json missing one regime weight key must degrade that component to a zero
        contribution rather than crashing the whole batch with a KeyError."""
        _seed_stock_signals("RWJ", composite_score=80, ml_confidence=60)
        broken_meta_scoring = {
            "REGIME_WEIGHTS": {"Bull": {"composite_score": 0.5, "ml_confidence": 0.5}},  # missing pattern/trap
            "CRASH_VETO": {"MARKET_STRESS_THRESHOLD": 0.75},
        }
        p1, p2 = _patched_regime("Bull", meta_scoring=broken_meta_scoring)
        with p1, p2:
            result = compute_regime_weighted_score("RWJ")
        assert result is not None
        # 90(80 rescaled)*.5 + 60*.5 + 50*0(missing) + 50*0(missing)
        assert result["score"] == pytest.approx(75.0)


# ---------------------------------------------------------------------------
# evaluate_pillar_confluence_as_of / compute_regime_weighted_score_as_of
#
# Used by alert_referee_engine.backfill_historical_confluence_features() to reconstruct Idea
# A/B features for an already-resolved historical trap_phase_history/pattern_detection_history
# row, as of that row's own scan_date rather than today.
# ---------------------------------------------------------------------------

class TestEvaluatePillarConfluenceAsOf:
    def test_matches_live_result_when_as_of_is_today(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _seed_quant_date("ASOF1", today)
        _seed_pattern_history("ASOF1", "flag", "bull_flag", "CONFIRMED", today)
        _seed_ml_confidence("ASOF1", today, 80)
        live = evaluate_pillar_confluence("ASOF1")
        as_of = evaluate_pillar_confluence_as_of("ASOF1", today)
        assert as_of == live

    def test_excludes_signals_after_the_as_of_date(self):
        """A pattern/quant row dated AFTER as_of_date must not leak into a historical
        reconstruction — the whole point of as_of is to see only what existed at that time."""
        past = _d(10)
        future = _d(-5)  # 5 days from now, i.e. after `past`
        _seed_quant_date("ASOF2", past)
        _seed_quant_date("ASOF2", future)
        _seed_pattern_history("ASOF2", "flag", "bull_flag", "CONFIRMED", future)
        result = evaluate_pillar_confluence_as_of("ASOF2", past)
        assert result["bullish_pillars"] == []

    def test_includes_signals_on_or_before_the_as_of_date(self):
        past = _d(10)
        _seed_quant_date("ASOF3", past)
        _seed_pattern_history("ASOF3", "flag", "bull_flag", "CONFIRMED", past)
        result = evaluate_pillar_confluence_as_of("ASOF3", past)
        assert "technical" in result["bullish_pillars"]


class TestComputeRegimeWeightedScoreAsOf:
    def test_reads_hmm_and_stress_histories_and_quant_signals(self):
        _seed_market_regime_as_of(_AS_OF_DATE, "Bull")
        _seed_quant_signal_as_of("ASOF4", _AS_OF_DATE, composite_score=80, ml_confidence_score=60)
        with patch("config.load_config", return_value={"META_SCORING": _DEFAULT_META_SCORING}):
            result = compute_regime_weighted_score_as_of("ASOF4", _AS_OF_DATE)
        assert result is not None
        assert result["regime"] == "Bull"
        # 90(80 rescaled)*.40 + 60*.30 + 50(neutral pattern)*.20 + 50(neutral trap)*.10
        assert result["score"] == pytest.approx(69.0)

    def test_negative_composite_is_rescaled_as_of_too(self):
        _seed_market_regime_as_of(_AS_OF_DATE, "Bull")
        _seed_quant_signal_as_of("ASOF8", _AS_OF_DATE, composite_score=-40, ml_confidence_score=60)
        with patch("config.load_config", return_value={"META_SCORING": _DEFAULT_META_SCORING}):
            result = compute_regime_weighted_score_as_of("ASOF8", _AS_OF_DATE)
        assert result["components"]["composite_score"] == pytest.approx(30.0)
        assert result["score"] == pytest.approx(45.0)

    def test_uses_most_recent_regime_on_or_before_as_of_date(self):
        _seed_market_regime_as_of("2020-01-10", "Chop")
        _seed_market_regime_as_of(_AS_OF_DATE, "Bull")
        _seed_quant_signal_as_of("ASOF5", _AS_OF_DATE, composite_score=80, ml_confidence_score=60)
        with patch("config.load_config", return_value={"META_SCORING": _DEFAULT_META_SCORING}):
            result = compute_regime_weighted_score_as_of("ASOF5", _AS_OF_DATE)
        assert result["regime"] == "Bull"
        conn = _db_module.get_connection()

    def test_high_volatility_negative_mean_return_as_of_returns_none(self):
        _seed_market_regime_as_of(_AS_OF_DATE, "High Volatility", mean_return=-0.05)
        _seed_quant_signal_as_of("ASOF6", _AS_OF_DATE, composite_score=80, ml_confidence_score=60)
        with patch("config.load_config", return_value={"META_SCORING": _DEFAULT_META_SCORING}):
            result = compute_regime_weighted_score_as_of("ASOF6", _AS_OF_DATE)
        assert result is None

    def test_high_volatility_positive_mean_return_as_of_is_scored(self):
        _seed_market_regime_as_of(_AS_OF_DATE, "High Volatility", mean_return=0.02)
        _seed_quant_signal_as_of("ASOF10", _AS_OF_DATE, composite_score=80, ml_confidence_score=60)
        with patch("config.load_config", return_value={"META_SCORING": _DEFAULT_META_SCORING}):
            result = compute_regime_weighted_score_as_of("ASOF10", _AS_OF_DATE)
        assert result["regime"] == "High Volatility"

    def test_high_volatility_without_stored_mean_return_as_of_returns_none(self):
        _seed_market_regime_as_of(_AS_OF_DATE, "High Volatility", mean_return=None)
        _seed_quant_signal_as_of("ASOF11", _AS_OF_DATE, composite_score=80, ml_confidence_score=60)
        with patch("config.load_config", return_value={"META_SCORING": _DEFAULT_META_SCORING}):
            result = compute_regime_weighted_score_as_of("ASOF11", _AS_OF_DATE)
        assert result is None

    def test_stress_score_at_crash_veto_threshold_returns_none(self):
        from regime_engine import crash_veto_stress_threshold
        _seed_market_regime_as_of(_AS_OF_DATE, "Bull", market_stress_score=crash_veto_stress_threshold())
        _seed_quant_signal_as_of("ASOF9", _AS_OF_DATE, composite_score=80, ml_confidence_score=60)
        with patch("config.load_config", return_value={"META_SCORING": _DEFAULT_META_SCORING}):
            result = compute_regime_weighted_score_as_of("ASOF9", _AS_OF_DATE)
        assert result is None

    def test_no_regime_row_on_or_before_as_of_returns_none(self):
        _seed_quant_signal_as_of("ASOF7", _AS_OF_DATE, composite_score=80, ml_confidence_score=60)
        with patch("config.load_config", return_value={"META_SCORING": _DEFAULT_META_SCORING}):
            result = compute_regime_weighted_score_as_of("ASOF7", _AS_OF_DATE)
        assert result is None


# ---------------------------------------------------------------------------
# evaluate_buy_recommendation_batch (Buy-Signal Confluence Pipeline Part D)
#
# Combines Part A's Pillar Confluence with Part D's Risk/Reward Gate — tested here at the
# combining-logic level, with both source functions mocked, since each is already covered by
# its own dedicated tests (TestEvaluatePillarConfluence above, TestPassesRiskRewardGate in
# tests/test_position_sizing.py).
# ---------------------------------------------------------------------------

_BULLISH_CONFLUENCE = {"bullish_pillars": ["technical", "ml"], "bearish_pillars": [], "confluence": True, "direction": "bullish"}
_BEARISH_CONFLUENCE = {"bullish_pillars": [], "bearish_pillars": ["technical", "ml"], "confluence": True, "direction": "bearish"}
_NO_CONFLUENCE = {"bullish_pillars": [], "bearish_pillars": [], "confluence": False, "direction": None}
_PASSING_RR = {"entry_price": 100.0, "stop_price": 92.0, "take_profit": 120.0,
               "take_profit_source": "pattern", "risk_reward": 2.5, "min_risk_reward": 1.5, "passes": True}
_FAILING_RR = {**_PASSING_RR, "risk_reward": 0.5, "passes": False}


class TestEvaluateBuyRecommendation:
    def test_bullish_confluence_and_passing_gate_is_recommended(self):
        with patch("score_analysis.evaluate_pillar_confluence_batch", return_value={"BR1": _BULLISH_CONFLUENCE}), \
             patch("position_sizing.passes_risk_reward_gate_batch", return_value={"BR1": _PASSING_RR}):
            result = evaluate_buy_recommendation("BR1")
        assert result is not None
        assert result["recommended"] is True
        assert result["risk_reward"] == _PASSING_RR
        assert result["pillar_confluence"] == _BULLISH_CONFLUENCE

    def test_bullish_confluence_but_failing_gate_is_not_recommended_but_not_none(self):
        """A bullish setup that fails the R:R gate still returns a result (so the UI can show
        why), just with recommended=False — only a genuine non-candidate maps to None."""
        with patch("score_analysis.evaluate_pillar_confluence_batch", return_value={"BR2": _BULLISH_CONFLUENCE}), \
             patch("position_sizing.passes_risk_reward_gate_batch", return_value={"BR2": _FAILING_RR}):
            result = evaluate_buy_recommendation("BR2")
        assert result is not None
        assert result["recommended"] is False

    def test_bearish_confluence_never_calls_rr_gate_and_returns_none(self):
        with patch("score_analysis.evaluate_pillar_confluence_batch", return_value={"BR3": _BEARISH_CONFLUENCE}), \
             patch("position_sizing.passes_risk_reward_gate_batch") as mock_rr:
            result = evaluate_buy_recommendation("BR3")
        assert result is None
        mock_rr.assert_not_called()

    def test_no_confluence_returns_none(self):
        with patch("score_analysis.evaluate_pillar_confluence_batch", return_value={"BR4": _NO_CONFLUENCE}), \
             patch("position_sizing.passes_risk_reward_gate_batch") as mock_rr:
            result = evaluate_buy_recommendation("BR4")
        assert result is None
        mock_rr.assert_not_called()

    def test_bullish_confluence_but_rr_gate_has_no_signal_returns_none(self):
        with patch("score_analysis.evaluate_pillar_confluence_batch", return_value={"BR5": _BULLISH_CONFLUENCE}), \
             patch("position_sizing.passes_risk_reward_gate_batch", return_value={"BR5": None}):
            result = evaluate_buy_recommendation("BR5")
        assert result is None

    def test_empty_ticker_list(self):
        assert evaluate_buy_recommendation_batch([]) == {}

    def test_batch_matches_single_ticker_result(self):
        with patch("score_analysis.evaluate_pillar_confluence_batch", return_value={"BR6": _BULLISH_CONFLUENCE}), \
             patch("position_sizing.passes_risk_reward_gate_batch", return_value={"BR6": _PASSING_RR}), \
             patch("score_analysis.compute_regime_weighted_score_batch", return_value={"BR6": None}):
            batch_result = evaluate_buy_recommendation_batch(["BR6"])
            single_result = evaluate_buy_recommendation("BR6")
        assert batch_result["BR6"] == single_result


class TestBuyRecommendationLabel:
    def test_recommended_result_returns_label_with_ratio(self):
        result = {"recommended": True, "risk_reward": _PASSING_RR}
        assert buy_recommendation_label(result) == "Buy Recommendation (R:R 2.5:1)"

    def test_not_recommended_returns_none(self):
        result = {"recommended": False, "risk_reward": _FAILING_RR}
        assert buy_recommendation_label(result) is None

    def test_none_result_returns_none(self):
        assert buy_recommendation_label(None) is None


@pytest.mark.parametrize("rr", [_PASSING_RR, _FAILING_RR, None])
def test_buy_recommendation_reuses_request_results_exactly(rr):
    confluence = {"UP": _BULLISH_CONFLUENCE, "DOWN": _BEARISH_CONFLUENCE}
    regime = {"UP": {"score": 78.5, "regime": "Bull"}, "DOWN": None}
    with patch("score_analysis.evaluate_pillar_confluence_batch", return_value=confluence) as pillars, \
         patch("score_analysis.compute_regime_weighted_score_batch", return_value=regime) as scores, \
         patch("position_sizing.passes_risk_reward_gate_batch", return_value={"UP": rr}):
        expected = evaluate_buy_recommendation_batch(["UP", "DOWN"])
        pillars.reset_mock()
        scores.reset_mock()
        actual = evaluate_buy_recommendation_batch(
            ["UP", "DOWN", "UP"], confluence_by_ticker=confluence, regime_score_by_ticker=regime,
        )
        pillars.assert_not_called()
        scores.assert_not_called()
    assert actual == expected


def test_buy_recommendation_empty_supplied_maps_do_not_recompute():
    with patch("score_analysis.evaluate_pillar_confluence_batch") as pillars, \
         patch("score_analysis.compute_regime_weighted_score_batch") as scores:
        assert evaluate_buy_recommendation_batch(["UP"], confluence_by_ticker={}, regime_score_by_ticker={}) == {"UP": None}
    pillars.assert_not_called()
    scores.assert_not_called()


@pytest.mark.parametrize("as_of", [None, "2020-08-01"])
def test_sparse_observation_queries_return_only_five_per_ticker(as_of):
    import sqlite3
    from score_analysis import _ml_signals_batch, _statistical_signals_batch

    conn = None
    try:
        conn = _db_module.get_connection()
        for ticker in ("BOUND1", "BOUND2"):
            for index in range(20):
                day = (datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(days=index * 30)).strftime("%Y-%m-%d")
                conn.execute(
                    "INSERT INTO earnings_volatility_history (ticker, scan_date, edge_score, drift_avg_pct_5d) VALUES (?, ?, ?, ?)",
                    (ticker, day, 0 if index % 2 else 1, -2 if index % 3 else 2),
                )
                conn.execute(
                    "INSERT INTO quant_signals (ticker, date, ml_confidence_score, ml_scored_at) VALUES (?, ?, ?, ?)",
                    (ticker, day, None if index % 3 == 0 else (75 if index % 2 else 25),
                     datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
                )
        conn.commit()
        for function, table, date_column, non_null in (
            (_statistical_signals_batch, "earnings_volatility_history", "scan_date", ""),
            (_ml_signals_batch, "quant_signals", "date", " AND ml_confidence_score IS NOT NULL"),
        ):
            expected = {}
            for ticker in ("BOUND1", "BOUND2"):
                rows = conn.execute(
                    f"SELECT * FROM {table} WHERE ticker = ?{non_null} ORDER BY {date_column} DESC", (ticker,),
                ).fetchall()
                rows = [row for row in rows if not as_of or row[date_column] <= as_of][:5]
                if table == "quant_signals":
                    expected[ticker] = ["up" if row["ml_confidence_score"] > 50 else "down" for row in rows]
                else:
                    expected[ticker] = ["up" if row["drift_avg_pct_5d"] > 0 else "down" for row in rows if row["edge_score"] > 0]
            returned = []

            def count_rows(cursor, row):
                returned.append(row)
                return sqlite3.Row(cursor, row)

            conn.row_factory = count_rows
            assert function(["BOUND1", "BOUND2"], conn, as_of=as_of) == expected
            assert len(returned) == 10
            conn.row_factory = sqlite3.Row
    finally:
        if conn:
            conn.close()


def test_trading_and_technical_queries_keep_ticker_specific_as_of_windows():
    import sqlite3
    from score_analysis import _trading_windows_batch, _pattern_signals_batch, _trap_signals_batch

    conn = None
    try:
        conn = _db_module.get_connection()
        for ticker, dates in (("WINDOW1", range(1, 21)), ("WINDOW2", range(1, 21, 2))):
            for day in dates:
                scan_date = f"2020-01-{day:02d}"
                conn.execute("INSERT INTO quant_signals (ticker, date) VALUES (?, ?)", (ticker, scan_date))
                conn.execute(
                    "INSERT INTO pattern_detection_history (ticker, scan_date, pattern_family, pattern_type, phase, scan_ts) VALUES (?, ?, ?, ?, ?, ?)",
                    (ticker, scan_date, "double_top_bottom", "double_bottom", "CONFIRMED", scan_date),
                )
                conn.execute("INSERT INTO trap_phase_history (ticker, scan_date, phase, scan_ts) VALUES (?, ?, ?, ?)", (ticker, scan_date, "ACCUMULATION", scan_date))
        conn.commit()
        rows_read = []

        def count_rows(cursor, row):
            rows_read.append(row)
            return sqlite3.Row(cursor, row)

        conn.row_factory = count_rows
        windows = _trading_windows_batch(["WINDOW1", "WINDOW2"], conn, as_of="2020-01-15")
        assert windows == {
            "WINDOW1": {f"2020-01-{day:02d}" for day in range(11, 16)},
            "WINDOW2": {f"2020-01-{day:02d}" for day in range(7, 16, 2)},
        }
        assert len(rows_read) == 10
        rows_read.clear()
        patterns = _pattern_signals_batch(["WINDOW1", "WINDOW2"], conn, windows)
        traps = _trap_signals_batch(["WINDOW1", "WINDOW2"], conn, windows)
        assert patterns == {"WINDOW1": ["up"] * 5, "WINDOW2": ["up"] * 5}
        assert traps == {"WINDOW1": ["up"] * 5, "WINDOW2": ["up"] * 5}
        assert len(rows_read) < 60
        assert _trading_windows_batch(["WINDOW1"], conn, as_of="2021-01-15") == {}
        assert _pattern_signals_batch(["WINDOW1"], conn, {}) == {"WINDOW1": []}
        assert _trap_signals_batch(["WINDOW1"], conn, {}) == {"WINDOW1": []}
    finally:
        if conn:
            conn.close()


class TestRescaledCompositeMigration:
    _NAME = "regime_weighted_score_composite_rescaled"

    def _insert(self, conn):
        ts = "2031-01-02 00:00:00"
        for ticker, phase in (("MIGA", "BULL_TRAP"), ("MIGB", "NEUTRAL")):
            conn.execute(
                """INSERT INTO trap_phase_history (ticker, phase, scan_date, scan_ts, regime_weighted_score, confluence_features_ts)
                   VALUES (?, ?, '2031-01-02', ?, 55.0, ?)""",
                (ticker, phase, ts, ts),
            )
        for ticker, phase in (("MIGC", "CONFIRMED"), ("MIGD", "FORMING")):
            conn.execute(
                """INSERT INTO pattern_detection_history
                   (ticker, pattern_family, pattern_type, phase, scan_date, scan_ts, regime_weighted_score, confluence_features_ts)
                   VALUES (?, 'flag', 'bull_flag', ?, '2031-01-02', ?, 55.0, ?)""",
                (ticker, phase, ts, ts),
            )
        conn.execute(
            """INSERT INTO trap_phase_history (ticker, phase, scan_date, scan_ts, regime_weighted_score, confluence_features_ts)
               VALUES ('MIGE', 'BULL_TRAP', '2031-01-02', ?, NULL, ?)""",
            (ts, ts),
        )
        conn.commit()

    def test_queues_training_rows_for_recompute_and_drops_unused_scores_once(self):
        conn = _db_module.get_connection()
        try:
            conn.execute("DELETE FROM data_migrations WHERE name = ?", (self._NAME,))
            self._insert(conn)
            _db_schema.migrate_db(conn, conn.cursor())

            def state(table, ticker):
                row = conn.execute(f"SELECT regime_weighted_score, confluence_features_ts FROM {table} WHERE ticker = ?", (ticker,)).fetchone()
                return row["regime_weighted_score"], row["confluence_features_ts"]

            assert state("trap_phase_history", "MIGA") == (55.0, None)
            assert state("pattern_detection_history", "MIGC") == (55.0, None)
            assert state("trap_phase_history", "MIGB") == (None, "2031-01-02 00:00:00")
            assert state("pattern_detection_history", "MIGD") == (None, "2031-01-02 00:00:00")
            assert state("trap_phase_history", "MIGE") == (None, "2031-01-02 00:00:00")
            assert conn.execute("SELECT COUNT(*) FROM data_migrations WHERE name = ?", (self._NAME,)).fetchone()[0] == 1

            conn.execute("UPDATE trap_phase_history SET confluence_features_ts = '2031-02-01 00:00:00' WHERE ticker = 'MIGA'")
            conn.commit()
            _db_schema.migrate_db(conn, conn.cursor())
            assert state("trap_phase_history", "MIGA") == (55.0, "2031-02-01 00:00:00")
        finally:
            conn.close()


class TestMlConfidenceRelativeLabelMigration:
    _NAME = "ml_confidence_relative_label"

    def _insert(self, conn):
        ts = "2031-01-02 00:00:00"
        conn.execute(
            "INSERT OR REPLACE INTO quant_signals (ticker, date, ml_confidence_score, ml_scored_at) VALUES ('MLMIG', '2031-01-02', 61.0, ?)",
            (ts,),
        )
        conn.execute("INSERT OR REPLACE INTO stock_signals (ticker, ml_confidence) VALUES ('MLMIG', 61.0)")
        for ticker, phase, pillar, rws in (
            ("MLA", "BULL_TRAP", "up", None), ("MLB", "BULL_TRAP", None, 55.0),
            ("MLC", "NEUTRAL", "up", 55.0), ("MLD", "BULL_TRAP", None, None),
        ):
            conn.execute(
                """INSERT INTO trap_phase_history (ticker, phase, scan_date, scan_ts, pillar_ml, regime_weighted_score, confluence_features_ts)
                   VALUES (?, ?, '2031-01-02', ?, ?, ?, ?)""",
                (ticker, phase, ts, pillar, rws, ts),
            )
        for ticker, phase, pillar, rws in (
            ("MLE", "CONFIRMED", "up", None), ("MLF", "CONFIRMED", None, 55.0),
            ("MLG", "FORMING", "up", 55.0), ("MLH", "CONFIRMED", None, None),
        ):
            conn.execute(
                """INSERT INTO pattern_detection_history
                   (ticker, pattern_family, pattern_type, phase, scan_date, scan_ts, pillar_ml, regime_weighted_score, confluence_features_ts)
                   VALUES (?, 'flag', 'bull_flag', ?, '2031-01-02', ?, ?, ?, ?)""",
                (ticker, phase, ts, pillar, rws, ts),
            )
        conn.commit()

    def _state(self, conn, table, ticker):
        row = conn.execute(f"SELECT pillar_ml, regime_weighted_score, confluence_features_ts FROM {table} WHERE ticker = ?", (ticker,)).fetchone()
        return row["pillar_ml"], row["regime_weighted_score"], row["confluence_features_ts"]

    def test_clears_scores_requeues_training_rows_and_runs_once(self):
        conn = _db_module.get_connection()
        try:
            conn.execute("DELETE FROM data_migrations WHERE name = ?", (self._NAME,))
            self._insert(conn)
            _db_schema.migrate_db(conn, conn.cursor())

            quant = conn.execute("SELECT ml_confidence_score, ml_scored_at FROM quant_signals WHERE ticker = 'MLMIG'").fetchone()
            assert (quant["ml_confidence_score"], quant["ml_scored_at"]) == (None, None)
            assert conn.execute("SELECT ml_confidence FROM stock_signals WHERE ticker = 'MLMIG'").fetchone()[0] is None
            assert conn.execute("SELECT COUNT(*) FROM quant_signals WHERE ml_confidence_score IS NOT NULL OR ml_scored_at IS NOT NULL").fetchone()[0] == 0

            ts = "2031-01-02 00:00:00"
            assert self._state(conn, "trap_phase_history", "MLA") == ("up", None, None)
            assert self._state(conn, "trap_phase_history", "MLB") == (None, 55.0, None)
            assert self._state(conn, "trap_phase_history", "MLC") == (None, None, ts)
            assert self._state(conn, "trap_phase_history", "MLD") == (None, None, ts)
            assert self._state(conn, "pattern_detection_history", "MLE") == ("up", None, None)
            assert self._state(conn, "pattern_detection_history", "MLF") == (None, 55.0, None)
            assert self._state(conn, "pattern_detection_history", "MLG") == (None, None, ts)
            assert self._state(conn, "pattern_detection_history", "MLH") == (None, None, ts)
            assert conn.execute("SELECT COUNT(*) FROM data_migrations WHERE name = ?", (self._NAME,)).fetchone()[0] == 1

            conn.execute("UPDATE trap_phase_history SET pillar_ml = 'down', confluence_features_ts = '2031-02-01 00:00:00' WHERE ticker = 'MLA'")
            conn.execute("INSERT OR REPLACE INTO stock_signals (ticker, ml_confidence) VALUES ('MLMIG', 62.0)")
            conn.commit()
            _db_schema.migrate_db(conn, conn.cursor())
            assert self._state(conn, "trap_phase_history", "MLA") == ("down", None, "2031-02-01 00:00:00")
            assert conn.execute("SELECT ml_confidence FROM stock_signals WHERE ticker = 'MLMIG'").fetchone()[0] == 62.0
        finally:
            conn.close()


class TestRegimeAsOf:
    def test_label_and_stress_come_from_their_own_tables_independently(self):
        _seed_market_regime_as_of("2020-01-10", "Chop", market_stress_score=None)
        _seed_market_regime_as_of("2020-01-12", None, market_stress_score=0.3)
        _seed_market_regime_as_of("2020-01-20", "High Volatility", market_stress_score=0.9)
        assert _regime_as_of(_AS_OF_DATE) == {"price_hmm_label": "Chop", "price_hmm_mean_return": None, "market_stress_score": 0.3}

    def test_only_one_table_populated_leaves_the_other_none(self):
        _seed_market_regime_as_of("2020-01-10", "Bull", market_stress_score=None)
        assert _regime_as_of(_AS_OF_DATE) == {"price_hmm_label": "Bull", "price_hmm_mean_return": None, "market_stress_score": None}

    def test_nothing_on_or_before_returns_none(self):
        _seed_market_regime_as_of("2020-01-20", "Bull")
        assert _regime_as_of(_AS_OF_DATE) is None

    def test_market_regimes_rows_are_ignored(self):
        conn = _db_module.get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO market_regimes (date, price_hmm_label, market_stress_score) VALUES (?, 'High Volatility', 0.99)",
            (_AS_OF_DATE,),
        )
        conn.commit()
        conn.close()
        assert _regime_as_of(_AS_OF_DATE) is None


class TestHmmHistoryRequeueMigration:
    _NAME = "regime_weighted_score_hmm_history_requeue"
    _TS = "2031-01-02 00:00:00"

    def _trap(self, conn, ticker, phase, score, ts):
        conn.execute(
            """INSERT INTO trap_phase_history (ticker, phase, scan_date, scan_ts, regime_weighted_score, confluence_features_ts)
               VALUES (?, ?, '2031-01-02', ?, ?, ?)""",
            (ticker, phase, self._TS, score, ts),
        )

    def _pattern(self, conn, ticker, phase, score, ts):
        conn.execute(
            """INSERT INTO pattern_detection_history
               (ticker, pattern_family, pattern_type, phase, scan_date, scan_ts, regime_weighted_score, confluence_features_ts)
               VALUES (?, 'flag', 'bull_flag', ?, '2031-01-02', ?, ?, ?)""",
            (ticker, phase, self._TS, score, ts),
        )

    def test_resets_only_stamped_null_score_in_scope_rows_once(self):
        conn = _db_module.get_connection()
        try:
            conn.execute("DELETE FROM data_migrations WHERE name = ?", (self._NAME,))
            self._trap(conn, "HQA", "BULL_TRAP", None, self._TS)
            self._trap(conn, "HQB", "BULL_TRAP", 55.0, self._TS)
            self._trap(conn, "HQC", "NEUTRAL", None, self._TS)
            self._trap(conn, "HQD", "BULL_TRAP", None, None)
            self._pattern(conn, "HQE", "CONFIRMED", None, self._TS)
            self._pattern(conn, "HQF", "CONFIRMED", 55.0, self._TS)
            self._pattern(conn, "HQG", "FORMING", None, self._TS)
            conn.commit()
            _db_schema.migrate_db(conn, conn.cursor())

            def ts(table, ticker):
                return conn.execute(f"SELECT confluence_features_ts FROM {table} WHERE ticker = ?", (ticker,)).fetchone()[0]

            assert ts("trap_phase_history", "HQA") is None
            assert ts("trap_phase_history", "HQB") == self._TS
            assert ts("trap_phase_history", "HQC") == self._TS
            assert ts("trap_phase_history", "HQD") is None
            assert ts("pattern_detection_history", "HQE") is None
            assert ts("pattern_detection_history", "HQF") == self._TS
            assert ts("pattern_detection_history", "HQG") == self._TS
            assert conn.execute("SELECT COUNT(*) FROM data_migrations WHERE name = ?", (self._NAME,)).fetchone()[0] == 1

            conn.execute("UPDATE trap_phase_history SET confluence_features_ts = '2031-02-01 00:00:00' WHERE ticker = 'HQA'")
            conn.commit()
            _db_schema.migrate_db(conn, conn.cursor())
            assert ts("trap_phase_history", "HQA") == "2031-02-01 00:00:00"
        finally:
            conn.close()
