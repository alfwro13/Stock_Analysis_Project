"""
tests/test_pattern_detection_engine.py — the generic Pattern Detection orchestrator: registry
dispatch, ticker scoping, DB save/dedup, accuracy resolution, historical backfill, and the
scheduler runner's alert wiring. Per-family detection math lives in
tests/test_head_shoulders_engine.py, tests/test_double_top_bottom_engine.py,
tests/test_flag_engine.py, and tests/test_triangle_engine.py.
"""

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
import double_top_bottom_engine
import flag_engine
import head_shoulders_engine
import triangle_engine
from pattern_detection_engine import (
    PatternDetectionEngine,
    DETECTORS,
    fill_pattern_outcomes,
    backfill_historical_patterns,
)

_CFG = {
    "SCHEDULING": {"PATTERN_DETECTION": {}},
    "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
}


class TestRegistry:
    def test_all_families_registered(self):
        assert DETECTORS["head_shoulders"] is head_shoulders_engine
        assert DETECTORS["double_top_bottom"] is double_top_bottom_engine
        assert DETECTORS["flag"] is flag_engine
        assert DETECTORS["triangle"] is triangle_engine


class TestGetTickerList:
    def test_tbill_ticker_excluded_from_portfolio_scope(self):
        engine = PatternDetectionEngine(_CFG)
        engine.monitor_portfolio = True
        holdings = {"AAPL": {}, "TBILL-606": {}}
        with patch("accounts_engine.get_combined_holdings", return_value=holdings):
            tickers = engine._get_ticker_list()
        assert "AAPL" in tickers
        assert not any(t.startswith("TBILL-") for t in tickers)

    def test_watchlist_excluded_by_default(self):
        engine = PatternDetectionEngine(_CFG)
        engine.monitor_portfolio = False
        assert engine.monitor_watchlist is False
        with patch("database.get_watchlist_tickers", return_value=["NVDA"]) as mock_wl:
            tickers = engine._get_ticker_list()
        mock_wl.assert_not_called()
        assert "NVDA" not in tickers

    def test_watchlist_included_when_enabled(self):
        engine = PatternDetectionEngine(_CFG)
        engine.monitor_portfolio = False
        engine.monitor_watchlist = True
        with patch("database.get_watchlist_tickers", return_value=["nvda"]):
            tickers = engine._get_ticker_list()
        assert "NVDA" in tickers


class TestSaveResults:
    def setup_method(self):
        self.engine = PatternDetectionEngine(_CFG)

    @staticmethod
    def _row(ticker: str, family: str = "head_shoulders", phase: str = "CONFIRMED", pattern_type: str = "regular") -> dict:
        return {
            "ticker": ticker, "pattern_family": family, "pattern_type": pattern_type, "phase": phase,
            "points": [{"label": "Head", "date": "2026-01-20", "price": 120.0}],
            "lines": [{"label": "Neckline", "date_from": "2026-01-10", "price_from": 95.0,
                       "date_to": "2026-01-30", "price_to": 96.0, "dash": True}],
            "key_level": 97.0,
            "breakout_date": "2026-02-10" if phase == "CONFIRMED" else None,
            "breakout_price": 90.0 if phase == "CONFIRMED" else None,
            "measured_target": 74.0, "volume_confirms": True, "rsi_divergence": True,
            "pattern_r2": 0.85, "prior_trend_pct": 12.0, "close_price": 90.0,
            "scan_ts": "2026-02-10 22:20:00",
        }

    def _cleanup(self, ticker: str):
        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM pattern_detection_results WHERE ticker = ?", (ticker,))
            conn.execute("DELETE FROM pattern_detection_history WHERE ticker = ?", (ticker,))
            conn.commit()
        finally:
            conn.close()

    def test_row_readable_after_save(self):
        self.engine._save_results([self._row("PDTST1")])
        conn = db.get_connection()
        try:
            saved = conn.execute("SELECT * FROM pattern_detection_results WHERE ticker = 'PDTST1'").fetchone()
        finally:
            conn.close()
            self._cleanup("PDTST1")
        assert saved is not None
        assert saved["phase"] == "CONFIRMED"
        assert abs(saved["measured_target"] - 74.0) < 1e-6

    def test_upsert_overwrites_existing_row_for_same_family(self):
        self.engine._save_results([self._row("PDTST2", phase="FORMING")])
        self.engine._save_results([self._row("PDTST2", phase="CONFIRMED")])
        conn = db.get_connection()
        try:
            rows = conn.execute("SELECT phase FROM pattern_detection_results WHERE ticker = 'PDTST2'").fetchall()
        finally:
            conn.close()
            self._cleanup("PDTST2")
        assert len(rows) == 1
        assert rows[0]["phase"] == "CONFIRMED"

    def test_two_families_coexist_on_same_ticker(self):
        self.engine._save_results([
            self._row("PDTST3", family="head_shoulders"),
            self._row("PDTST3", family="double_top_bottom", pattern_type="double_top"),
        ])
        conn = db.get_connection()
        try:
            rows = conn.execute("SELECT pattern_family FROM pattern_detection_results WHERE ticker = 'PDTST3'").fetchall()
        finally:
            conn.close()
            self._cleanup("PDTST3")
        families = {r["pattern_family"] for r in rows}
        assert families == {"head_shoulders", "double_top_bottom"}

    def test_save_results_populates_history(self):
        self.engine._save_results([self._row("PDTST4")])
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT measured_target, volume_confirms, rsi_divergence FROM pattern_detection_history WHERE ticker = 'PDTST4'"
            ).fetchone()
        finally:
            conn.close()
            self._cleanup("PDTST4")
        assert row is not None
        assert abs(row["measured_target"] - 74.0) < 1e-6
        assert row["volume_confirms"] == 1

    def test_unchanged_pattern_not_relogged_to_history(self):
        self.engine._save_results([self._row("PDTST5")])
        self.engine._save_results([self._row("PDTST5")])
        conn = db.get_connection()
        try:
            count = conn.execute("SELECT COUNT(*) FROM pattern_detection_history WHERE ticker = 'PDTST5'").fetchone()[0]
        finally:
            conn.close()
            self._cleanup("PDTST5")
        assert count == 1

    def test_new_pattern_geometry_still_logs(self):
        from datetime import datetime, timezone
        first = self._row("PDTST6")
        second = self._row("PDTST6")
        second["points"] = [{"label": "Head", "date": "2026-03-20", "price": 125.0}]
        with patch("pattern_detection_engine.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 1, tzinfo=timezone.utc)
            self.engine._save_results([first])
        with patch("pattern_detection_engine.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 2, tzinfo=timezone.utc)
            self.engine._save_results([second])
        conn = db.get_connection()
        try:
            count = conn.execute("SELECT COUNT(*) FROM pattern_detection_history WHERE ticker = 'PDTST6'").fetchone()[0]
        finally:
            conn.close()
            self._cleanup("PDTST6")
        assert count == 2


class TestFillPatternOutcomes:
    @staticmethod
    def _cleanup():
        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM pattern_detection_history WHERE ticker='PDFATST'")
            conn.commit()
        finally:
            conn.close()

    def test_returns_zero_when_no_pending_rows(self):
        assert fill_pattern_outcomes() == 0

    def test_back_fills_14d_outcome_from_parquet(self, tmp_path):
        scan_date = (date.today() - timedelta(days=20)).strftime("%Y-%m-%d")
        db.log_pattern_detection("PDFATST", "head_shoulders", "regular", "CONFIRMED", scan_date, 100.0, f"{scan_date} 10:00:00")
        try:
            n = 30
            idx = pd.date_range(scan_date, periods=n, freq="D")
            prices = np.linspace(100.0, 90.0, n)  # declining — regular H&S "down" prediction correct
            df = pd.DataFrame({"Close": prices, "Open": prices, "High": prices + 1, "Low": prices - 1, "Volume": np.ones(n)}, index=idx)
            (tmp_path / "PDFATST.parquet").parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(tmp_path / "PDFATST.parquet", engine="pyarrow")

            with patch("pattern_detection_engine.HISTORICAL_DIR", tmp_path):
                count = fill_pattern_outcomes()

            conn = db.get_connection()
            try:
                row = dict(conn.execute(
                    "SELECT * FROM pattern_detection_history WHERE ticker='PDFATST' AND scan_date=?", (scan_date,)
                ).fetchone())
            finally:
                conn.close()
            assert count >= 1
            assert row.get("actual_price_14d") is not None
            assert row.get("direction_correct_14d") == 1
        finally:
            self._cleanup()


class TestBackfillPatternLock:
    def test_same_geometry_logged_once_across_many_steps(self, tmp_path):
        base_seg1 = np.linspace(90, 110, 26)
        base_seg2 = np.linspace(110, 95, 11)[1:]
        base_seg3 = np.linspace(95, 120, 16)[1:]
        base_seg4 = np.linspace(120, 96, 11)[1:]
        base_seg5 = np.linspace(96, 108, 16)[1:]
        confirm_seg = np.linspace(108, 90, 11)[1:]
        tail = np.linspace(89, 60, 60)
        prices = np.concatenate([base_seg1, base_seg2, base_seg3, base_seg4, base_seg5, confirm_seg, tail])
        volume = np.concatenate([
            np.linspace(2_000_000, 800_000, len(prices) - 60),
            np.full(60, 800_000.0),
        ])
        df = pd.DataFrame({
            "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
            "Close": prices, "Volume": volume,
        })
        df.index = pd.date_range("2024-01-01", periods=len(df), freq="B")

        pq_path = tmp_path / "PDBACKF.parquet"
        df.to_parquet(pq_path, engine="pyarrow")

        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM pattern_detection_history WHERE ticker='PDBACKF'")
            conn.commit()
        finally:
            conn.close()

        try:
            with patch("pattern_detection_engine.HISTORICAL_DIR", tmp_path):
                backfill_historical_patterns(tickers=["PDBACKF"])

            conn = db.get_connection()
            try:
                rows = conn.execute(
                    "SELECT scan_date FROM pattern_detection_history WHERE ticker='PDBACKF' AND pattern_family='head_shoulders' AND pattern_type='regular'"
                ).fetchall()
            finally:
                conn.close()
            # Without the lock this would be one row per ~5-bar step across the whole
            # confirmed tail (a dozen+ near-duplicate rows) instead of a single instance.
            assert len(rows) == 1
        finally:
            conn = db.get_connection()
            try:
                conn.execute("DELETE FROM pattern_detection_history WHERE ticker='PDBACKF'")
                conn.commit()
            finally:
                conn.close()


class TestRunPatternDetectionJobMarketGating:
    @staticmethod
    def _row(ticker: str, phase: str = "CONFIRMED", pattern_type: str = "regular") -> dict:
        return {
            "ticker": ticker, "pattern_family": "head_shoulders", "pattern_type": pattern_type, "phase": phase,
            "points": [{"label": "Head", "date": "2026-01-20", "price": 120.0}],
            "lines": [{"label": "Neckline", "date_from": "2026-01-10", "price_from": 95.0,
                       "date_to": "2026-01-30", "price_to": 96.0, "dash": True}],
            "key_level": 97.0,
            "breakout_date": "2026-02-10", "breakout_price": 90.0,
            "measured_target": 74.0, "volume_confirms": True, "rsi_divergence": True,
            "pattern_r2": 0.85, "prior_trend_pct": 12.0, "close_price": 90.0,
            "scan_ts": "2026-02-10 22:20:00",
        }

    def test_suppresses_alert_when_ticker_exchange_closed(self):
        import scheduler_jobs

        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM alert_state WHERE engine = 'PatternDetector'")
            conn.commit()
        finally:
            conn.close()

        with patch(
            "pattern_detection_engine.PatternDetectionEngine.run_scan",
            return_value=[self._row("AAPL")],
        ), patch("scheduler_jobs.is_quote_settled", return_value=False) as mock_open, \
           patch("scheduler_jobs.notify") as mock_notify:
            scheduler_jobs.run_pattern_detection_job()

        mock_open.assert_called()
        mock_notify.assert_not_called()

    def test_fires_alert_when_ticker_exchange_open(self):
        import scheduler_jobs

        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM alert_state WHERE engine = 'PatternDetector'")
            conn.commit()
        finally:
            conn.close()

        with patch(
            "pattern_detection_engine.PatternDetectionEngine.run_scan",
            return_value=[self._row("AAPL")],
        ), patch("scheduler_jobs.is_quote_settled", return_value=True), \
           patch("scheduler_jobs.notify", return_value=True) as mock_notify:
            scheduler_jobs.run_pattern_detection_job()

        mock_notify.assert_called_once()


class TestChartAPIPathSafety:
    """GET /api/pattern-detection/chart/{ticker} builds a HISTORICAL_DIR filesystem
    path from the raw URL path parameter — must reject path-traversal-style tickers rather
    than passing them through to disk (CodeQL py/path-injection precedent, alert #70)."""

    def test_encoded_slash_traversal_blocked_by_routing(self, client):
        resp = client.get(
            "/api/pattern-detection/chart/..%2F..%2F..%2Fetc%2Fpasswd",
            headers={"X-API-Key": "test-api-key-do-not-use-in-production"},
        )
        assert resp.status_code == 404

    def test_ticker_with_disallowed_characters_rejected(self, client):
        resp = client.get(
            "/api/pattern-detection/chart/FOO%20BAR",
            headers={"X-API-Key": "test-api-key-do-not-use-in-production"},
        )
        assert resp.status_code == 400
        assert resp.json()["status"] == "error"

    def test_valid_ticker_with_no_data_returns_404_not_400(self, client):
        resp = client.get(
            "/api/pattern-detection/chart/ZZZNOPE",
            headers={"X-API-Key": "test-api-key-do-not-use-in-production"},
        )
        assert resp.status_code == 404


class TestChartAPIMultiPattern:
    """GET /api/pattern-detection/chart/{ticker} must return every currently-active family
    for the ticker (not just one), each tagged with a direction resolved from its family's
    PATTERN_TYPES registry entry — this is what the per-ticker overlay page groups on."""

    @staticmethod
    def _cleanup(ticker: str):
        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM pattern_detection_results WHERE ticker = ?", (ticker,))
            conn.commit()
        finally:
            conn.close()

    def test_returns_all_families_with_direction(self, client, tmp_path):
        ticker = "PDCHARTMULTI"
        self._cleanup(ticker)
        engine = PatternDetectionEngine(_CFG)
        engine._save_results([
            TestSaveResults._row(ticker, family="head_shoulders", pattern_type="regular"),
            TestSaveResults._row(ticker, family="double_top_bottom", pattern_type="double_bottom"),
        ])
        try:
            idx = pd.date_range("2026-01-01", periods=30, freq="D")
            df = pd.DataFrame({"Close": np.linspace(100.0, 110.0, 30)}, index=idx)
            with patch("api_routes_analysis.HISTORICAL_DIR", tmp_path):
                df.to_parquet(tmp_path / f"{ticker}.parquet", engine="pyarrow")
                resp = client.get(
                    f"/api/pattern-detection/chart/{ticker}",
                    headers={"X-API-Key": "test-api-key-do-not-use-in-production"},
                )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            patterns = {p["pattern_family"]: p for p in data["patterns"]}
            assert set(patterns.keys()) == {"head_shoulders", "double_top_bottom"}
            assert patterns["head_shoulders"]["direction"] == "down"
            assert patterns["double_top_bottom"]["direction"] == "up"
        finally:
            self._cleanup(ticker)


class TestResultsAPIDirectionAndScope:
    """GET /api/pattern-detection/results must resolve a direction ("up"/"down") per row and
    return the current Portfolio/Watchlist ticker sets so the list page can filter by
    direction and scope without a second round-trip."""

    @staticmethod
    def _cleanup(ticker: str):
        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM pattern_detection_results WHERE ticker = ?", (ticker,))
            conn.commit()
        finally:
            conn.close()

    def test_results_include_direction_and_ticker_scopes(self, client):
        ticker = "PDRESULTSCOPE"
        self._cleanup(ticker)
        engine = PatternDetectionEngine(_CFG)
        engine._save_results([TestSaveResults._row(ticker, family="double_top_bottom", pattern_type="double_bottom")])
        try:
            with patch("accounts_engine.get_combined_holdings", return_value={ticker: {"ticker": ticker}}), \
                 patch("database.get_watchlist_tickers", return_value=[]):
                resp = client.get(
                    "/api/pattern-detection/results",
                    headers={"X-API-Key": "test-api-key-do-not-use-in-production"},
                )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert "portfolio_tickers" in data and "watchlist_tickers" in data
            assert ticker in data["portfolio_tickers"]
            row = next(r for r in data["results"] if r["ticker"] == ticker)
            assert row["direction"] == "up"
        finally:
            self._cleanup(ticker)
