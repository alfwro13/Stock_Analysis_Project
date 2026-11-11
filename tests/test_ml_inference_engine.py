"""
tests/test_ml_inference_engine.py — Daily ML Inference unit tests

Covers:
  • update_daily_ml_predictions mirrors ml_confidence_score onto stock_signals.ml_confidence
  • the coverage guard and drift check read the JSON feature statistics, and a null moment skips only that feature
  • inference scores only feature rows within the trading-day age limit, on dates that meet the coverage threshold,
    stamps ml_scored_at, clears the stock_signals mirror of an unscored ticker whose last score has aged out,
    and returns the requested tickers it left unscored
  • the Portfolio/Watchlist warning fires once per held ticker that had a score and lost it, survives a restart
    (alert_state) and rearms when the ticker is scored again; a never-scored ticker is only logged
  • score_quantile_predictions stamps quantile_scored_at with every band it writes
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import database as _db_module
import time_engine
from ml_features import LATEST_FEATURES_QUERY

pytestmark = pytest.mark.restore_tables("alert_state", "quant_signals")


def _session_date(sessions_back=0):
    day = datetime.now(timezone.utc).date()
    while sessions_back > 0 or not time_engine.exchange_had_session("NYSE", day):
        if time_engine.exchange_had_session("NYSE", day):
            sessions_back -= 1
        day -= timedelta(days=1)
    return day.isoformat()


@pytest.fixture(autouse=True)
def _remove_seeded_universe():
    yield
    conn = None
    try:
        conn = _db_module.get_connection()
        conn.execute("DELETE FROM quant_signals WHERE ticker LIKE 'MLU%'")
        conn.execute("DELETE FROM stock_signals WHERE ticker LIKE 'MLU%'")
        conn.commit()
    finally:
        if conn:
            conn.close()


class TestUpdateDailyMlPredictionsSyncsStockSignals:
    """stock_signals.ml_confidence mirrors quant_signals.ml_confidence_score so consumers that
    join off stock_signals (the Regime-Weighted Conviction Score) don't need a second
    quant_signals subselect. Was previously dead — no write site anywhere — until this."""

    @staticmethod
    def _seed_universe(n=30, date_str=None, first=0):
        date_str = date_str or _session_date()
        conn = None
        try:
            conn = _db_module.get_connection()
            rng = np.random.default_rng(3)
            for i in range(first, first + n):
                ticker = f"MLU{i:02d}"
                conn.execute("INSERT OR REPLACE INTO stock_signals (ticker) VALUES (?)", (ticker,))
                conn.execute(
                    """INSERT OR REPLACE INTO quant_signals
                       (ticker, date, close_price, volume, rsi_14, macd, macd_signal, macd_hist,
                        sma_50, sma_200, volume_surge, bullish_cross,
                        mom_1m, mom_3m, mom_6m, mom_12m_skip1m, atr_pct, hist_vol_20,
                        rel_strength_5d, rel_strength_20d)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ticker, date_str,
                        100.0 + rng.normal(0, 5), 1_000_000 + rng.normal(0, 1000),
                        50.0 + rng.normal(0, 5), rng.normal(0, 1), rng.normal(0, 1), rng.normal(0, 1),
                        95.0 + rng.normal(0, 5), 90.0 + rng.normal(0, 5), 0, 0,
                        rng.normal(0, 0.05), rng.normal(0, 0.1), rng.normal(0, 0.15), rng.normal(0, 0.2),
                        0.02 + abs(rng.normal(0, 0.005)), 0.2 + abs(rng.normal(0, 0.05)),
                        rng.normal(0, 0.02), rng.normal(0, 0.03),
                    ),
                )
            conn.commit()
        finally:
            if conn:
                conn.close()
        return [f"MLU{i:02d}" for i in range(first, first + n)]

    def test_ml_confidence_synced_to_stock_signals(self, fake_inference_df, tmp_path):
        tickers = self._seed_universe()
        target = tickers[:2]
        fake_df = fake_inference_df(tickers, date_str=_session_date())

        fake_model = MagicMock()
        fake_model.predict_proba.return_value = np.array([[0.2, 0.8]])

        from ml_inference_engine import update_daily_ml_predictions
        with patch("ml_inference_engine.MODEL_PATH") as mock_path, \
             patch("ml_inference_engine.FEATURE_STATS_PATH", tmp_path / "absent.json"), \
             patch("model_compatibility_engine.joblib.load", return_value=fake_model), \
             patch("ml_inference_engine.pd.read_sql_query", return_value=fake_df):
            mock_path.exists.return_value = True
            update_daily_ml_predictions(target)

        conn = None
        try:
            conn = _db_module.get_connection()
            for ticker in target:
                qs_row = conn.execute(
                    "SELECT ml_confidence_score, ml_scored_at FROM quant_signals WHERE ticker = ? AND date = ?",
                    (ticker, _session_date()),
                ).fetchone()
                ss_row = conn.execute(
                    "SELECT ml_confidence FROM stock_signals WHERE ticker = ?", (ticker,)
                ).fetchone()
                assert qs_row["ml_confidence_score"] == 80.0
                assert qs_row["ml_scored_at"] is not None
                assert ss_row["ml_confidence"] == 80.0
        finally:
            conn.close()


def test_latest_features_query_returns_only_each_tickers_newest_complete_row():
    seed = TestUpdateDailyMlPredictionsSyncsStockSignals._seed_universe
    seed(2, "2026-01-01")
    seed(2, "2026-01-05")
    conn = None
    try:
        conn = _db_module.get_connection()
        rows = [r for r in conn.execute(LATEST_FEATURES_QUERY).fetchall() if r["ticker"].startswith("MLU")]
    finally:
        if conn:
            conn.close()
    assert sorted((r["ticker"], r["date"]) for r in rows) == [("MLU00", "2026-01-05"), ("MLU01", "2026-01-05")]


class TestFeatureStatsFromJson:
    @staticmethod
    def _run(stats, target_count=2):
        tickers = TestUpdateDailyMlPredictionsSyncsStockSignals._seed_universe()
        fake_model = MagicMock()
        fake_model.predict_proba.return_value = np.array([[0.2, 0.8]])
        from ml_inference_engine import update_daily_ml_predictions
        with patch("ml_inference_engine.MODEL_PATH") as mock_path, \
             patch("ml_inference_engine.load_feature_stats", return_value=stats) as load, \
             patch("ml_inference_engine.log_notification") as notify, \
             patch("model_compatibility_engine.joblib.load", return_value=fake_model):
            mock_path.exists.return_value = True
            update_daily_ml_predictions(tickers[:target_count])
        return load, notify

    def test_universe_below_json_threshold_refuses_to_score(self):
        _, notify = self._run({"_meta": {"train_universe_size": 100000}, "features": {}})
        assert notify.call_args.args[0] == "Error"
        assert "cross-section" in notify.call_args.args[1]

    def test_null_moment_does_not_abort_the_drift_check(self):
        stats = {"_meta": {"train_universe_size": 10}, "features": {"rsi_14": {"mean": None, "std": None}}}
        load, notify = self._run(stats)
        load.assert_called_once()
        notify.assert_not_called()


class TestInferenceUsesOnlyFreshWellCoveredRows:
    @staticmethod
    def _run(target, stats=None):
        fake_model = MagicMock()
        fake_model.predict_proba.return_value = np.array([[0.2, 0.8]])
        from ml_inference_engine import update_daily_ml_predictions
        with patch("ml_inference_engine.MODEL_PATH") as mock_path, \
             patch("ml_inference_engine.load_feature_stats", return_value=stats), \
             patch("ml_inference_engine.log_notification"), \
             patch("model_compatibility_engine.joblib.load", return_value=fake_model):
            mock_path.exists.return_value = True
            return update_daily_ml_predictions(target)

    @staticmethod
    def _row(ticker):
        conn = _db_module.get_connection()
        try:
            return conn.execute(
                "SELECT ml_confidence_score, ml_scored_at FROM quant_signals WHERE ticker = ?", (ticker,)
            ).fetchone()
        finally:
            conn.close()

    def test_feature_rows_older_than_the_limit_are_not_scored_and_are_reported_unscored(self):
        seed = TestUpdateDailyMlPredictionsSyncsStockSignals._seed_universe
        seed(30)
        seed(1, _session_date(10), first=30)
        unscored = self._run([f"MLU{i:02d}" for i in range(31)])
        assert unscored == ["MLU30"]
        assert self._row("MLU30")["ml_confidence_score"] is None
        assert self._row("MLU00")["ml_confidence_score"] == 80.0

    def test_a_date_below_the_coverage_threshold_is_not_scored(self):
        seed = TestUpdateDailyMlPredictionsSyncsStockSignals._seed_universe
        seed(30)
        seed(3, _session_date(1), first=30)
        unscored = self._run([f"MLU{i:02d}" for i in range(33)])
        assert unscored == ["MLU30", "MLU31", "MLU32"]
        assert self._row("MLU31")["ml_scored_at"] is None

    def test_unscored_ticker_loses_its_stock_signals_mirror_unless_a_fresh_score_exists(self):
        seed = TestUpdateDailyMlPredictionsSyncsStockSignals._seed_universe
        seed(30)
        seed(2, _session_date(10), first=30)
        conn = _db_module.get_connection()
        try:
            conn.execute("UPDATE stock_signals SET ml_confidence = 55.0 WHERE ticker IN ('MLU30', 'MLU31')")
            conn.execute(
                "UPDATE quant_signals SET ml_confidence_score = 55.0, ml_scored_at = ? WHERE ticker = 'MLU31'",
                (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),),
            )
            conn.commit()
        finally:
            conn.close()
        self._run(["MLU30", "MLU31"] + [f"MLU{i:02d}" for i in range(30)])
        conn = _db_module.get_connection()
        try:
            mirror = {r["ticker"]: r["ml_confidence"] for r in conn.execute(
                "SELECT ticker, ml_confidence FROM stock_signals WHERE ticker IN ('MLU30', 'MLU31')")}
        finally:
            conn.close()
        assert mirror == {"MLU30": None, "MLU31": 55.0}

    def test_returns_none_when_inference_does_not_run(self):
        from ml_inference_engine import update_daily_ml_predictions
        assert update_daily_ml_predictions([]) is None


class TestAlertUnscoredHoldings:
    SCORED_BEFORE = ("MLHA", "MLHB")
    NEVER_SCORED = "MLHN"

    @pytest.fixture(autouse=True)
    def _seed(self):
        conn = None
        try:
            conn = _db_module.get_connection()
            for ticker in self.SCORED_BEFORE:
                conn.execute(
                    "INSERT OR REPLACE INTO quant_signals (ticker, date, ml_scored_at) VALUES (?, ?, ?)",
                    (ticker, _session_date(5), "2000-01-03 02:00:00"),
                )
            conn.execute(
                "INSERT OR REPLACE INTO quant_signals (ticker, date) VALUES (?, ?)", (self.NEVER_SCORED, _session_date(5))
            )
            conn.commit()
        finally:
            if conn:
                conn.close()

    @staticmethod
    def _alert(unscored, held=("MLHA", "MLHB", "MLHN")):
        from ml_inference_engine import alert_unscored_holdings
        with patch("ml_inference_engine.get_portfolio_watchlist_tickers", return_value=list(held)), \
             patch("ml_inference_engine.notify") as notify:
            alert_unscored_holdings(unscored)
        return notify

    @staticmethod
    def _warned():
        conn = None
        try:
            conn = _db_module.get_connection()
            rows = conn.execute("SELECT ticker FROM alert_state WHERE engine = 'ml_inference_stale_features'").fetchall()
            return {row["ticker"] for row in rows}
        finally:
            if conn:
                conn.close()

    def test_warns_about_a_held_ticker_that_lost_its_score_without_a_universe_count(self):
        notify = self._alert(["MLHA", "UNIV1", "UNIV2"])
        assert notify.call_args.args[:2] == ("ml_inference_stale_features", "Warning")
        text = notify.call_args.args[2]
        assert "1 Portfolio/Watchlist ticker(s)" in text and "MLHA" in text and "other universe" not in text
        assert self._warned() == {"MLHA"}

    def test_a_never_scored_ticker_is_only_logged_at_info(self, caplog):
        with caplog.at_level("INFO", logger="ml_inference_engine"):
            notify = self._alert(["MLHN"])
        assert not notify.called
        assert self._warned() == set()
        assert any(
            r.levelname == "INFO" and "never had a score" in r.getMessage() and "MLHN" in r.getMessage()
            for r in caplog.records
        )

    def test_universe_only_misses_do_not_warn(self):
        assert not self._alert(["UNIV1", "UNIV2"]).called

    def test_a_never_scored_ticker_is_not_named_in_the_warning(self):
        text = self._alert(["MLHA", "MLHN"]).call_args.args[2]
        assert "MLHA" in text and "MLHN" not in text

    def test_same_set_warns_once_and_a_new_member_warns_again(self):
        assert self._alert(["MLHA"]).call_count == 1
        assert self._alert(["MLHA"]).call_count == 0
        notify = self._alert(["MLHA", "MLHB"])
        assert notify.call_count == 1 and self._warned() == {"MLHA", "MLHB"}

    def test_the_memory_survives_a_restart(self):
        self._alert(["MLHA"])
        import ml_inference_engine
        assert not hasattr(ml_inference_engine, "_warned_unscored_holdings")
        assert self._alert(["MLHA"]).call_count == 0

    def test_rearms_after_the_ticker_is_scored_again(self):
        self._alert(["MLHA"])
        assert self._alert([]).call_count == 0 and self._warned() == set()
        assert self._alert(["MLHA"]).call_count == 1

    def test_a_ticker_that_leaves_the_portfolio_rearms(self):
        self._alert(["MLHA"])
        self._alert(["MLHA"], held=("MLHB",))
        assert self._warned() == set()

    def test_a_still_missing_ticker_keeps_its_row_alive_for_the_alert_state_prune(self):
        self._alert(["MLHA"])
        conn = None
        try:
            conn = _db_module.get_connection()
            conn.execute("UPDATE alert_state SET state_date = '2000-01-01' WHERE engine = 'ml_inference_stale_features'")
            conn.commit()
        finally:
            if conn:
                conn.close()
        self._alert(["MLHA"])
        conn = None
        try:
            conn = _db_module.get_connection()
            state_date = conn.execute(
                "SELECT state_date FROM alert_state WHERE engine = 'ml_inference_stale_features' AND ticker = 'MLHA'"
            ).fetchone()["state_date"]
        finally:
            if conn:
                conn.close()
        assert state_date == datetime.now(timezone.utc).date().isoformat()

    def test_inference_that_did_not_run_neither_warns_nor_rearms(self):
        self._alert(["MLHA"])
        assert not self._alert(None).called
        assert self._warned() == {"MLHA"}
        assert self._alert(["MLHA"]).call_count == 0


class TestQuantileScoringStampsBands:
    def test_each_written_band_carries_quantile_scored_at(self, fake_inference_df, tmp_path):
        tickers = TestUpdateDailyMlPredictionsSyncsStockSignals._seed_universe()
        fake_df = fake_inference_df(tickers, date_str=_session_date())
        q10_model, q90_model = MagicMock(), MagicMock()
        q10_model.predict.return_value = np.array([-0.05])
        q90_model.predict.return_value = np.array([0.10])

        from ml_inference_engine import score_quantile_predictions
        with patch("ml_inference_engine.QUANTILE_Q10_PATH") as q10_path, \
             patch("ml_inference_engine.QUANTILE_Q90_PATH") as q90_path, \
             patch("ml_inference_engine.FEATURE_STATS_PATH", tmp_path / "absent.json"), \
             patch("ml_inference_engine.load_sklearn_artifact", side_effect=[q10_model, q90_model]), \
             patch("ml_inference_engine.pd.read_sql_query", return_value=fake_df):
            q10_path.exists.return_value = True
            q90_path.exists.return_value = True
            score_quantile_predictions(tickers[:2])

        conn = _db_module.get_connection()
        try:
            rows = {r["ticker"]: r for r in conn.execute(
                "SELECT ticker, price_q10, price_q90, quantile_scored_at FROM quant_signals "
                "WHERE ticker LIKE 'MLU%' AND date = ?", (_session_date(),))}
        finally:
            conn.close()
        closes = fake_df.set_index("ticker")["close_price"]
        for ticker in tickers[:2]:
            assert rows[ticker]["price_q10"] == pytest.approx(closes[ticker] * 0.95)
            assert rows[ticker]["price_q90"] == pytest.approx(closes[ticker] * 1.10)
            assert rows[ticker]["quantile_scored_at"] is not None
        assert rows[tickers[2]]["quantile_scored_at"] is None
