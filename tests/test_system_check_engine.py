"""Tests for system_check_engine.run_system_checks and the /api/system/checks endpoint."""
import pytest
import database as _db_module
import system_check_engine as _sce


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
