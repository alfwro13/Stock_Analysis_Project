"""
tests/test_ai_contagion_engine.py — AI Contagion Engine Unit Tests

Covers:
  • scan() returns [] when market is closed
  • scan() returns [] when _fetch_basket_data returns no data
  • scan() two-tier: leaders trigger but ETFs don't → []
  • scan() two-tier: both tiers confirm → single event dict
  • event dict structure (ticker="SECTOR", severity_score bounds)
  • record_scan_snapshot: inserts a row with UTC timestamp and correct payload
  • record_scan_snapshot: works when alerts list is empty
  • record_scan_snapshot: prunes rows older than 7 days inline
"""

import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
from ai_contagion_engine import AIContagionEngine, record_scan_snapshot

# ── minimal config ────────────────────────────────────────────────────────────

_CFG = {
    "NOTIFICATIONS": {
        "AI_CONTAGION": {
            "BELLWETHER_TICKERS": ["NVDA", "AMD"],
            "ETF_BASKET": ["SMH"],
            "LEADER_THRESHOLD_PCT": 4.0,
            "ETF_CONFIRMATION_THRESHOLD_PCT": 2.5,
            "VOLUME_SPIKE_MULTIPLIER": 1.8,
        }
    }
}

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_intraday_df(prev_close: float, curr_close: float, volume: float = 500_000.0,
                       today: datetime = None) -> pd.DataFrame:
    """Minimal 2-day 15-min DataFrame suitable for _evaluate_ticker. Defaults the "current" day
    to real UTC today, since _evaluate_ticker now requires the frame's last date to actually be
    today (see the stale-data-reuse fix, 2026-07-16) — pass an explicit `today` to build a
    deliberately-stale frame for that regression test."""
    today = today or datetime.now(timezone.utc)
    yesterday = today - timedelta(days=1)
    idx = pd.DatetimeIndex([
        yesterday.replace(hour=15, minute=0, second=0, microsecond=0),
        yesterday.replace(hour=15, minute=15, second=0, microsecond=0),
        today.replace(hour=15, minute=0, second=0, microsecond=0),
        today.replace(hour=15, minute=15, second=0, microsecond=0),
    ])
    return pd.DataFrame(
        {"Close": [prev_close, prev_close, curr_close - 0.5, curr_close], "Volume": [volume] * 4},
        index=idx,
    )


def _get_conn():
    return db.get_connection()


# ── scan() — market closed ────────────────────────────────────────────────────

class TestScanMarketClosed:
    def test_returns_empty_when_market_closed(self):
        engine = AIContagionEngine(_CFG)
        conn = _get_conn()
        try:
            with patch("ai_contagion_engine.is_quote_settled", return_value=False):
                result = engine.scan()
        finally:
            conn.close()
        assert result == []


# ── scan() — no data ─────────────────────────────────────────────────────────

class TestScanNoData:
    def test_returns_empty_when_fetch_returns_none(self):
        engine = AIContagionEngine(_CFG)
        conn = _get_conn()
        try:
            with (
                patch("ai_contagion_engine.is_quote_settled", return_value=True),
                patch.object(engine, "_fetch_basket_data", return_value=None),
            ):
                result = engine.scan()
        finally:
            conn.close()
        assert result == []

    def test_returns_empty_when_fetch_returns_empty_dict(self):
        engine = AIContagionEngine(_CFG)
        conn = _get_conn()
        try:
            with (
                patch("ai_contagion_engine.is_quote_settled", return_value=True),
                patch.object(engine, "_fetch_basket_data", return_value={}),
            ):
                result = engine.scan()
        finally:
            conn.close()
        assert result == []


# ── scan() — two-tier confirmation ────────────────────────────────────────────

class TestScanTwoTier:
    def _make_basket(self, leader_drop_pct: float, etf_drop_pct: float) -> dict:
        """
        Build a fake basket dict for NVDA, AMD (bellwethers) and SMH (ETF).
        leader_drop_pct and etf_drop_pct are positive fractions (e.g. 0.05 = 5% drop).
        """
        prev = 100.0
        return {
            "NVDA": _make_intraday_df(prev, prev * (1 - leader_drop_pct)),
            "AMD":  _make_intraday_df(prev, prev * (1 - leader_drop_pct)),
            "SMH":  _make_intraday_df(prev, prev * (1 - etf_drop_pct)),
        }

    def test_no_event_when_leaders_below_threshold(self):
        # 2% drop, threshold is 4%
        basket = self._make_basket(0.02, 0.03)
        engine = AIContagionEngine(_CFG)
        conn = _get_conn()
        try:
            with (
                patch("ai_contagion_engine.is_quote_settled", return_value=True),
                patch.object(engine, "_fetch_basket_data", return_value=basket),
                patch.object(engine, "_check_volume_spike", return_value=False),
            ):
                result = engine.scan()
        finally:
            conn.close()
        assert result == []

    def test_no_event_when_leaders_trigger_but_etf_does_not(self):
        # Leaders drop 5% (above 4% threshold), ETF only 1% (below 2.5% threshold)
        basket = self._make_basket(0.05, 0.01)
        engine = AIContagionEngine(_CFG)
        conn = _get_conn()
        try:
            with (
                patch("ai_contagion_engine.is_quote_settled", return_value=True),
                patch.object(engine, "_fetch_basket_data", return_value=basket),
                patch.object(engine, "_check_volume_spike", return_value=False),
            ):
                result = engine.scan()
        finally:
            conn.close()
        assert result == []

    def test_event_returned_when_both_tiers_confirm(self):
        # Leaders drop 5% (above 4%), ETF drops 3% (above 2.5%)
        basket = self._make_basket(0.05, 0.03)
        engine = AIContagionEngine(_CFG)
        conn = _get_conn()
        try:
            with (
                patch("ai_contagion_engine.is_quote_settled", return_value=True),
                patch.object(engine, "_fetch_basket_data", return_value=basket),
                patch.object(engine, "_check_volume_spike", return_value=False),
            ):
                result = engine.scan()
        finally:
            conn.close()
        assert len(result) == 1

    def test_event_ticker_is_sector(self):
        basket = self._make_basket(0.05, 0.03)
        engine = AIContagionEngine(_CFG)
        conn = _get_conn()
        try:
            with (
                patch("ai_contagion_engine.is_quote_settled", return_value=True),
                patch.object(engine, "_fetch_basket_data", return_value=basket),
                patch.object(engine, "_check_volume_spike", return_value=False),
            ):
                result = engine.scan()
        finally:
            conn.close()
        assert result[0]["ticker"] == "SECTOR"

    def test_event_has_leader_shocks_and_etf_hits(self):
        basket = self._make_basket(0.05, 0.03)
        engine = AIContagionEngine(_CFG)
        conn = _get_conn()
        try:
            with (
                patch("ai_contagion_engine.is_quote_settled", return_value=True),
                patch.object(engine, "_fetch_basket_data", return_value=basket),
                patch.object(engine, "_check_volume_spike", return_value=False),
            ):
                result = engine.scan()
        finally:
            conn.close()
        event = result[0]
        assert len(event["leader_shocks"]) >= 1
        assert len(event["etf_hits"]) >= 1

    def test_severity_score_in_unit_interval(self):
        basket = self._make_basket(0.05, 0.03)
        engine = AIContagionEngine(_CFG)
        conn = _get_conn()
        try:
            with (
                patch("ai_contagion_engine.is_quote_settled", return_value=True),
                patch.object(engine, "_fetch_basket_data", return_value=basket),
                patch.object(engine, "_check_volume_spike", return_value=False),
            ):
                result = engine.scan()
        finally:
            conn.close()
        score = result[0]["severity_score"]
        assert 0.0 <= score <= 1.0, f"severity_score {score} out of [0, 1]"

    def test_intraday_pct_is_negative_on_drop(self):
        basket = self._make_basket(0.05, 0.03)
        engine = AIContagionEngine(_CFG)
        conn = _get_conn()
        try:
            with (
                patch("ai_contagion_engine.is_quote_settled", return_value=True),
                patch.object(engine, "_fetch_basket_data", return_value=basket),
                patch.object(engine, "_check_volume_spike", return_value=False),
            ):
                result = engine.scan()
        finally:
            conn.close()
        assert result[0]["intraday_pct"] < 0


# ── stale-data-reuse guard (2026-07-16) ───────────────────────────────────────

class TestStaleFrameGuard:
    """_evaluate_ticker must not treat a frame whose last bar isn't actually today as fresh —
    otherwise a lagging premarket feed silently reuses yesterday's already-alerted drawdown."""

    def _make_stale_basket(self, leader_drop_pct: float, etf_drop_pct: float) -> dict:
        """Every ticker's frame's last bar is yesterday (or older), not real UTC today."""
        stale_today = datetime.now(timezone.utc) - timedelta(days=1)
        prev = 100.0
        return {
            "NVDA": _make_intraday_df(prev, prev * (1 - leader_drop_pct), today=stale_today),
            "AMD":  _make_intraday_df(prev, prev * (1 - leader_drop_pct), today=stale_today),
            "SMH":  _make_intraday_df(prev, prev * (1 - etf_drop_pct), today=stale_today),
        }

    def test_stale_frame_produces_no_candidate(self):
        basket = self._make_stale_basket(0.05, 0.03)
        engine = AIContagionEngine(_CFG)
        conn = _get_conn()
        try:
            with (
                patch("ai_contagion_engine.is_quote_settled", return_value=True),
                patch.object(engine, "_fetch_basket_data", return_value=basket),
                patch.object(engine, "_check_volume_spike", return_value=False),
            ):
                result = engine.scan()
        finally:
            conn.close()
        assert result == []

    def test_fresh_frame_still_produces_candidate(self):
        """Control: the same shock, with a genuinely fresh last bar, still fires — the guard
        must not be so strict it blocks real same-day events."""
        basket = self._make_basket = TestScanTwoTier()._make_basket(0.05, 0.03)
        engine = AIContagionEngine(_CFG)
        conn = _get_conn()
        try:
            with (
                patch("ai_contagion_engine.is_quote_settled", return_value=True),
                patch.object(engine, "_fetch_basket_data", return_value=basket),
                patch.object(engine, "_check_volume_spike", return_value=False),
            ):
                result = engine.scan()
        finally:
            conn.close()
        assert len(result) == 1


# ── record_scan_snapshot() ────────────────────────────────────────────────────

class TestRecordScanSnapshot:
    def test_inserts_row_when_no_alerts(self):
        conn = _get_conn()
        try:
            record_scan_snapshot(conn, [])
            row = conn.execute(
                "SELECT leader_count, etf_count, alert_fired FROM ai_contagion_snapshots "
                "ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["leader_count"] == 0
        assert row["etf_count"] == 0
        assert row["alert_fired"] == 0

    def test_inserts_row_with_alert_data(self):
        alerts = [{
            "ticker": "SECTOR",
            "price": 5.0,
            "intraday_pct": -5.0,
            "leader_shocks": [
                {"ticker": "NVDA", "intraday_pct": -5.1, "volume_spike": True, "is_etf": False},
            ],
            "etf_hits": [
                {"ticker": "SMH", "intraday_pct": -3.2, "volume_spike": False, "is_etf": True},
            ],
            "reason": "FLASH CRASH LEADER SHOCK",
            "volume_spikes": ["NVDA"],
            "severity_score": 0.45,
        }]
        conn = _get_conn()
        try:
            record_scan_snapshot(conn, alerts)
            row = conn.execute(
                "SELECT leader_count, etf_count, alert_fired, payload_json FROM ai_contagion_snapshots "
                "ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        assert row["leader_count"] == 1
        assert row["etf_count"] == 1
        assert row["alert_fired"] == 1
        payload = json.loads(row["payload_json"])
        assert payload["severity_score"] == 0.45
        assert any(t["ticker"] == "NVDA" for t in payload["tickers"])

    def test_scan_ts_stored_as_utc(self):
        conn = _get_conn()
        before = datetime.now(timezone.utc).replace(microsecond=0)
        try:
            record_scan_snapshot(conn, [])
            row = conn.execute(
                "SELECT scan_ts FROM ai_contagion_snapshots ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        after = datetime.now(timezone.utc)
        stored = datetime.strptime(row["scan_ts"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        assert before <= stored <= after

    def test_prunes_rows_older_than_7_days(self):
        conn = _get_conn()
        old_ts = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            conn.execute(
                "INSERT INTO ai_contagion_snapshots "
                "(scan_ts, leader_count, etf_count, alert_fired, payload_json) "
                "VALUES (?, 0, 0, 0, '{}')",
                (old_ts,),
            )
            conn.commit()
            # record_scan_snapshot triggers the DELETE for rows older than 7 days
            record_scan_snapshot(conn, [])
            count = conn.execute(
                "SELECT COUNT(*) FROM ai_contagion_snapshots WHERE scan_ts = ?",
                (old_ts,),
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 0, "Rows older than 7 days should be pruned"


# ── shared live-price cache write-back ────────────────────────────────────────

class TestSharesLivePriceOnEveryScan:
    """_evaluate_ticker() must upsert market_pulse_cache even when no event fires."""

    def test_writes_market_pulse_cache_even_without_an_event(self):
        prev = 100.0
        basket = {
            "NVDA": _make_intraday_df(prev, prev * 0.98),
            "AMD":  _make_intraday_df(prev, prev * 0.98),
            "SMH":  _make_intraday_df(prev, prev * 0.98),
        }
        engine = AIContagionEngine(_CFG)
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM market_pulse_cache WHERE ticker IN ('NVDA','AMD','SMH')")
            conn.commit()
            with (
                patch("ai_contagion_engine.is_quote_settled", return_value=True),
                patch.object(engine, "_fetch_basket_data", return_value=basket),
                patch.object(engine, "_check_volume_spike", return_value=False),
            ):
                result = engine.scan()
            row = conn.execute(
                "SELECT price FROM market_pulse_cache WHERE ticker = 'NVDA'"
            ).fetchone()
        finally:
            conn.execute("DELETE FROM market_pulse_cache WHERE ticker IN ('NVDA','AMD','SMH')")
            conn.commit()
            conn.close()
        assert result == []
        assert row is not None
        assert row["price"] == pytest.approx(98.0)


# ── run_ai_contagion_job() — MAX_ALERTS_PER_DAY gating ─────────────────────────

class TestRunAiContagionJobDailyGate:
    """The scheduler wrapper gates via _evaluate_daily_alert_gate() (max_per_day from
    NOTIFICATIONS.AI_CONTAGION.MAX_ALERTS_PER_DAY, default 1) rather than the
    worsened/recovered/cooldown model — see AGENTS.md rule 19's AI Contagion exception."""

    _EVENT = {
        "ticker": "SECTOR", "price": None, "reason": "AI_SECTOR_CONTAGION",
        "leader_shocks": [], "severity_score": 0.5,
    }

    def teardown_method(self):
        conn = _get_conn()
        conn.execute("DELETE FROM alert_state WHERE engine = 'AIContagion' AND ticker = 'SECTOR'")
        conn.commit()
        conn.close()

    def test_second_alert_same_day_suppressed_at_default_limit(self):
        import scheduler_jobs

        with patch("ai_contagion_engine.AIContagionEngine.scan", return_value=[self._EVENT]), \
             patch("ai_contagion_engine.record_scan_snapshot"), \
             patch("scheduler_jobs.load_config", return_value=_CFG), \
             patch("scheduler_jobs.notify", return_value=True) as mock_notify:
            scheduler_jobs.run_ai_contagion_job()
            scheduler_jobs.run_ai_contagion_job()

        mock_notify.assert_called_once()

    def test_configured_max_per_day_allows_more_than_one(self):
        import scheduler_jobs

        cfg = json.loads(json.dumps(_CFG))
        cfg["NOTIFICATIONS"]["AI_CONTAGION"]["MAX_ALERTS_PER_DAY"] = 2

        with patch("ai_contagion_engine.AIContagionEngine.scan", return_value=[self._EVENT]), \
             patch("ai_contagion_engine.record_scan_snapshot"), \
             patch("scheduler_jobs.load_config", return_value=cfg), \
             patch("scheduler_jobs.notify", return_value=True) as mock_notify:
            scheduler_jobs.run_ai_contagion_job()
            scheduler_jobs.run_ai_contagion_job()

        assert mock_notify.call_count == 2
