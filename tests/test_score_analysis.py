"""Tests for score_analysis pure functions and DB-backed get_score_analysis."""
import pytest
import database as _db_module
from score_analysis import _available_from, _compute_return, get_score_analysis


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
    conn.commit()
    conn.close()


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
