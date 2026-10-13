"""Tests for system_check_engine.run_system_checks and the /api/system/checks endpoint."""
from unittest.mock import patch

import pytest
import database as _db_module
import system_check_engine as _sce
from ml_features import save_feature_stats


_BASE_CONFIG = {
    "SCHEDULING": {
        "ML_TRAINING": {"ENABLED": True, "DAYS": ["sun"], "TIME": "04:00"},
        "ML_BACKFILL": {"ENABLED": False, "DAYS": ["sat"], "TIME": "02:00"},
    }
}


@pytest.fixture(autouse=True)
def clean_tables():
    conn = _db_module.get_connection()
    conn.execute("DELETE FROM quant_signals")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Check 1 — ml_training_without_backfill
# ---------------------------------------------------------------------------

class TestTrainingWithoutBackfill:
    def test_warns_when_backfill_disabled(self, monkeypatch):
        monkeypatch.setattr(_sce, "load_config", lambda: {
            "SCHEDULING": {
                "ML_TRAINING": {"ENABLED": True, "DAYS": ["sun"], "TIME": "04:00"},
                "ML_BACKFILL": {"ENABLED": False, "DAYS": ["sat"], "TIME": "02:00"},
            }
        })
        issues = _sce.run_system_checks()
        keys = [i["key"] for i in issues]
        assert "ml_training_without_backfill" in keys

    def test_no_warning_when_backfill_enabled(self, monkeypatch):
        monkeypatch.setattr(_sce, "load_config", lambda: {
            "SCHEDULING": {
                "ML_TRAINING": {"ENABLED": True, "DAYS": ["sun"], "TIME": "04:00"},
                "ML_BACKFILL": {"ENABLED": True, "DAYS": ["sat"], "TIME": "02:00"},
            }
        })
        issues = _sce.run_system_checks()
        keys = [i["key"] for i in issues]
        assert "ml_training_without_backfill" not in keys

    def test_no_warning_when_training_disabled(self, monkeypatch):
        monkeypatch.setattr(_sce, "load_config", lambda: {
            "SCHEDULING": {
                "ML_TRAINING": {"ENABLED": False, "DAYS": ["sun"], "TIME": "04:00"},
                "ML_BACKFILL": {"ENABLED": False, "DAYS": ["sat"], "TIME": "02:00"},
            }
        })
        issues = _sce.run_system_checks()
        keys = [i["key"] for i in issues]
        assert "ml_training_without_backfill" not in keys


# ---------------------------------------------------------------------------
# Check 2 — ml_training_before_backfill
# ---------------------------------------------------------------------------

class TestTrainingBeforeBackfill:
    def test_warns_when_training_before_backfill_same_day(self, monkeypatch):
        monkeypatch.setattr(_sce, "load_config", lambda: {
            "SCHEDULING": {
                "ML_TRAINING": {"ENABLED": True, "DAYS": ["sat"], "TIME": "01:00"},
                "ML_BACKFILL": {"ENABLED": True, "DAYS": ["sat"], "TIME": "03:00"},
            }
        })
        issues = _sce.run_system_checks()
        keys = [i["key"] for i in issues]
        assert "ml_training_before_backfill" in keys

    def test_no_warning_when_training_after_backfill(self, monkeypatch):
        monkeypatch.setattr(_sce, "load_config", lambda: {
            "SCHEDULING": {
                "ML_TRAINING": {"ENABLED": True, "DAYS": ["sat"], "TIME": "04:00"},
                "ML_BACKFILL": {"ENABLED": True, "DAYS": ["sat"], "TIME": "02:00"},
            }
        })
        issues = _sce.run_system_checks()
        keys = [i["key"] for i in issues]
        assert "ml_training_before_backfill" not in keys

    def test_no_warning_when_different_days(self, monkeypatch):
        monkeypatch.setattr(_sce, "load_config", lambda: {
            "SCHEDULING": {
                "ML_TRAINING": {"ENABLED": True, "DAYS": ["sun"], "TIME": "01:00"},
                "ML_BACKFILL": {"ENABLED": True, "DAYS": ["sat"], "TIME": "03:00"},
            }
        })
        issues = _sce.run_system_checks()
        keys = [i["key"] for i in issues]
        assert "ml_training_before_backfill" not in keys


# ---------------------------------------------------------------------------
# Check 3 — low_inference_coverage
# ---------------------------------------------------------------------------

class TestLowInferenceCoverage:
    def test_error_on_empty_db(self, monkeypatch):
        monkeypatch.setattr(_sce, "load_config", lambda: {
            "SCHEDULING": {
                "ML_TRAINING": {"ENABLED": False},
                "ML_BACKFILL": {"ENABLED": True},
            }
        })
        monkeypatch.setattr(_sce, "_load_train_universe_size", lambda: None)
        issues = _sce.run_system_checks()
        keys = [i["key"] for i in issues]
        assert "low_inference_coverage" in keys
        error_issues = [i for i in issues if i["key"] == "low_inference_coverage"]
        assert error_issues[0]["level"] == "error"

    def test_no_error_when_coverage_sufficient(self, monkeypatch):
        monkeypatch.setattr(_sce, "load_config", lambda: {
            "SCHEDULING": {
                "ML_TRAINING": {"ENABLED": False},
                "ML_BACKFILL": {"ENABLED": True},
            }
        })
        monkeypatch.setattr(_sce, "_load_train_universe_size", lambda: 100)
        conn = _db_module.get_connection()
        for i in range(30):
            conn.execute(
                "INSERT OR IGNORE INTO quant_signals "
                "(ticker, date, close_price, volume, mom_1m, atr_pct, rel_strength_5d, rel_strength_20d) "
                "VALUES (?, '2026-01-02', 100.0, 1000, 0.05, 0.01, 0.02, 0.03)",
                (f"TICK{i:03d}",)
            )
        conn.commit()
        conn.close()
        issues = _sce.run_system_checks()
        keys = [i["key"] for i in issues]
        assert "low_inference_coverage" not in keys


# ---------------------------------------------------------------------------
# Check 4 — ghostfolio_files_not_purged
# ---------------------------------------------------------------------------

class TestGhostfolioFilesNotPurged:
    def test_warns_when_disabled_and_portfolio_json_exists(self, monkeypatch, tmp_path):
        portfolio_path = tmp_path / "portfolio.json"
        watchlist_path = tmp_path / "watchlist.json"
        portfolio_path.write_text("{}")
        monkeypatch.setattr(_sce, "PORTFOLIO_PATH", portfolio_path)
        monkeypatch.setattr(_sce, "WATCHLIST_PATH", watchlist_path)
        monkeypatch.setattr(_sce, "load_config", lambda: {**_BASE_CONFIG, "GHOSTFOLIO_ENABLED": False})
        issues = _sce.run_system_checks()
        keys = [i["key"] for i in issues]
        assert "ghostfolio_files_not_purged" in keys

    def test_no_warning_when_disabled_and_files_absent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_sce, "PORTFOLIO_PATH", tmp_path / "portfolio.json")
        monkeypatch.setattr(_sce, "WATCHLIST_PATH", tmp_path / "watchlist.json")
        monkeypatch.setattr(_sce, "load_config", lambda: {**_BASE_CONFIG, "GHOSTFOLIO_ENABLED": False})
        issues = _sce.run_system_checks()
        keys = [i["key"] for i in issues]
        assert "ghostfolio_files_not_purged" not in keys

    def test_no_warning_when_enabled_even_if_files_exist(self, monkeypatch, tmp_path):
        portfolio_path = tmp_path / "portfolio.json"
        portfolio_path.write_text("{}")
        monkeypatch.setattr(_sce, "PORTFOLIO_PATH", portfolio_path)
        monkeypatch.setattr(_sce, "WATCHLIST_PATH", tmp_path / "watchlist.json")
        monkeypatch.setattr(_sce, "load_config", lambda: {**_BASE_CONFIG, "GHOSTFOLIO_ENABLED": True})
        issues = _sce.run_system_checks()
        keys = [i["key"] for i in issues]
        assert "ghostfolio_files_not_purged" not in keys


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------

class TestSystemChecksEndpoint:
    def test_returns_200_with_issues_list(self, client):
        resp = client.get("/api/system/checks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert isinstance(body["issues"], list)

    def test_issues_have_required_fields(self, client):
        resp = client.get("/api/system/checks")
        for issue in resp.json()["issues"]:
            assert "key" in issue
            assert "level" in issue
            assert "message" in issue
            assert issue["level"] in ("warning", "error")


# ---------------------------------------------------------------------------
# Training universe size comes from the JSON feature statistics, never a pickle
# ---------------------------------------------------------------------------

class TestLoadTrainUniverseSize:
    def test_reads_universe_size_from_json_stats(self, tmp_path, monkeypatch):
        path = tmp_path / "feature_stats.json"
        save_feature_stats({"_meta": {"train_universe_size": 1200}, "features": {}}, path)
        monkeypatch.setattr(_sce, "FEATURE_STATS_PATH", path)
        with patch("joblib.load", side_effect=AssertionError("unpickled")):
            assert _sce._load_train_universe_size() == 1200

    def test_none_when_stats_file_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_sce, "FEATURE_STATS_PATH", tmp_path / "absent.json")
        assert _sce._load_train_universe_size() is None


@pytest.mark.api
def test_market_status_output_is_unchanged_and_never_unpickles(client, tmp_path, monkeypatch):
    path = tmp_path / "feature_stats.json"
    save_feature_stats({"_meta": {"train_universe_size": 1200}, "features": {}}, path)
    monkeypatch.setattr(_sce, "FEATURE_STATS_PATH", path)
    with patch("joblib.load", side_effect=AssertionError("unpickled")):
        resp = client.get("/api/system/market-status")
    assert resp.status_code == 200
    assert set(resp.json()) == {"status", "us_market_open", "uk_market_open", "yahoo_ok", "system_ok"}
