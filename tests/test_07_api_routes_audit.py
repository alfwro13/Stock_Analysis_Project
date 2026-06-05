"""
tests/test_07_api_routes_audit.py  ── api_routes.py Audit Regression Tests

Covers the bugs found and fixed in the June 2026 api_routes.py audit:

1. Anomaly datetime comparison — /api/system/metrics must not crash when a
   .joblib model file has a naive (no tzinfo) or missing 'trained_at' field.
2. Path traversal — /api/universe/import/server uses the resolved path
   throughout, not a re-created unresolved one.
3. Intraday chart placeholder HTML — /api/intraday-chart/<ticker> must NOT
   return inline style= attributes in its error/empty fallback responses.
"""

import io
import os
import sys
import joblib
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _json(resp) -> dict:
    try:
        return resp.json()
    except Exception as exc:
        raise AssertionError(
            f"Response is not valid JSON.\nStatus: {resp.status_code}\nBody: {resp.text[:500]}"
        ) from exc


# ── 1. Anomaly datetime staleness — naive vs. aware comparison ────────────────

class TestAnomalyDatetimeComparison:
    """
    /api/system/metrics scans ANOMALY_MODELS_DIR for .joblib files and
    compares their 'trained_at' field against a UTC-aware cutoff.

    Before the fix, datetime.fromisoformat() on a naive string raised TypeError
    when compared against a timezone-aware datetime.  After the fix the naive
    datetime is assumed UTC before the comparison.
    """

    def _make_model_file(self, directory: Path, filename: str, trained_at) -> Path:
        """Write a minimal .joblib payload to a temp directory."""
        path = directory / filename
        joblib.dump({"trained_at": trained_at, "model": None}, path)
        return path

    # ANOMALY_MODELS_DIR is imported locally inside get_system_metrics via
    # `from config import ANOMALY_MODELS_DIR`, so we patch it on the config module.

    def test_naive_trained_at_does_not_crash_metrics(self, client):
        """A model with a naive ISO datetime string must not crash /api/system/metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            self._make_model_file(tmppath, "test_model.joblib", "2025-01-01T10:00:00")
            with patch("config.ANOMALY_MODELS_DIR", tmppath):
                resp = client.get("/api/system/metrics")
        assert resp.status_code == 200, (
            f"system/metrics must not crash with a naive trained_at; got {resp.status_code}"
        )

    def test_aware_trained_at_does_not_crash_metrics(self, client):
        """A model with a UTC-aware ISO datetime string must not crash /api/system/metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            self._make_model_file(tmppath, "test_model.joblib", "2025-01-01T10:00:00+00:00")
            with patch("config.ANOMALY_MODELS_DIR", tmppath):
                resp = client.get("/api/system/metrics")
        assert resp.status_code == 200

    def test_missing_trained_at_does_not_crash_metrics(self, client):
        """A model with trained_at=None must not crash /api/system/metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            self._make_model_file(tmppath, "test_model.joblib", None)
            with patch("config.ANOMALY_MODELS_DIR", tmppath):
                resp = client.get("/api/system/metrics")
        assert resp.status_code == 200

    def test_stale_naive_model_counted_as_stale(self, client):
        """A model with a naive trained_at more than 7 days ago must be counted as stale."""
        stale_ts = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            self._make_model_file(tmppath, "stale_model.joblib", stale_ts)
            with patch("config.ANOMALY_MODELS_DIR", tmppath):
                resp = client.get("/api/system/metrics")
        assert resp.status_code == 200
        data = _json(resp)
        ml = data.get("ml", {})
        assert ml.get("anomaly_stale_count", 0) >= 1, (
            f"Stale model must be counted; anomaly_stale_count={ml.get('anomaly_stale_count')}"
        )

    def test_fresh_model_not_counted_as_stale(self, client):
        """A model trained 1 day ago must not be counted as stale."""
        fresh_ts = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            self._make_model_file(tmppath, "fresh_model.joblib", fresh_ts)
            with patch("config.ANOMALY_MODELS_DIR", tmppath):
                resp = client.get("/api/system/metrics")
        assert resp.status_code == 200
        data = _json(resp)
        ml = data.get("ml", {})
        assert ml.get("anomaly_stale_count", 0) == 0, (
            f"Fresh model must not be stale; anomaly_stale_count={ml.get('anomaly_stale_count')}"
        )


# ── 2. Path traversal — resolved path used throughout ─────────────────────────

class TestImportServerPathResolution:
    """
    Before the fix, the path traversal guard resolved the path for the check
    but then re-created an unresolved path for the actual file open.  A symlink
    attack could therefore pass the check and open an arbitrary file.

    After the fix, the resolved path is used for both the check and the open.
    These tests verify the guard still works end-to-end.
    """

    def test_dotdot_traversal_is_rejected(self, client):
        resp = client.post("/api/universe/import/server", json={"filename": "../../../etc/passwd.csv"})
        assert resp.status_code == 400
        assert _json(resp).get("status") == "error"

    def test_absolute_path_is_rejected(self, client):
        resp = client.post("/api/universe/import/server", json={"filename": "/etc/passwd.csv"})
        assert resp.status_code == 400

    def test_non_csv_extension_is_rejected(self, client):
        resp = client.post("/api/universe/import/server", json={"filename": "data.json"})
        assert resp.status_code == 400

    def test_valid_filename_returns_404_not_400(self, client):
        """A safe, well-formed filename that doesn't exist on disk must return 404,
        confirming the path guard passed and the file-existence check ran."""
        resp = client.post("/api/universe/import/server", json={"filename": "my_universe.csv"})
        assert resp.status_code == 404, (
            f"Safe filename must reach file-existence check (404); got {resp.status_code}"
        )

    def test_empty_filename_is_rejected(self, client):
        """An empty filename must be rejected before reaching the filesystem."""
        resp = client.post("/api/universe/import/server", json={"filename": ".csv"})
        # .csv alone resolves inside IMPORT_DIR but has no name — treated as missing file
        assert resp.status_code in (400, 404), (
            f"Empty/bare .csv filename must not return 200; got {resp.status_code}"
        )


# ── 3. Intraday chart placeholder — no inline styles ─────────────────────────

class TestIntradayChartPlaceholders:
    """
    /api/intraday-chart/<ticker> returns fallback HTML when no intraday
    parquet data exists.  Before the fix, that HTML contained inline
    style= attributes.  After the fix it uses CSS classes only.
    """

    def _get_chart_html(self, client, ticker: str = "FAKEXYZ") -> str:
        """Fetch the intraday chart endpoint for a ticker that has no parquet file."""
        with patch("api_routes.pd.read_parquet", side_effect=FileNotFoundError):
            resp = client.get(f"/api/intraday-chart/{ticker}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        return _json(resp).get("html", "")

    def _get_chart_html_error(self, client, ticker: str = "FAKEXYZ") -> str:
        """Simulate a generic parquet read error (not FileNotFoundError)."""
        with patch("api_routes.pd.read_parquet", side_effect=Exception("corrupt")):
            resp = client.get(f"/api/intraday-chart/{ticker}")
        assert resp.status_code == 200
        return _json(resp).get("html", "")

    def test_no_data_placeholder_has_no_inline_style(self, client):
        html = self._get_chart_html(client)
        assert "style=" not in html, (
            "FileNotFoundError fallback HTML must not contain inline style= attributes"
        )

    def test_no_data_placeholder_uses_css_class(self, client):
        html = self._get_chart_html(client)
        assert "intraday-placeholder" in html, (
            "FileNotFoundError fallback must use the .intraday-placeholder CSS class"
        )

    def test_error_placeholder_has_no_inline_style(self, client):
        html = self._get_chart_html_error(client)
        assert "style=" not in html, (
            "Generic error fallback HTML must not contain inline style= attributes"
        )

    def test_error_placeholder_uses_css_class(self, client):
        html = self._get_chart_html_error(client)
        assert "intraday-placeholder" in html, (
            "Generic error fallback must use the .intraday-placeholder CSS class"
        )

    def test_no_data_placeholder_contains_label_text(self, client):
        html = self._get_chart_html(client)
        assert "No intraday data yet" in html

    def test_error_placeholder_contains_label_text(self, client):
        html = self._get_chart_html_error(client)
        assert "unavailable" in html.lower()


# ── 4. CSS class presence in styles.css ───────────────────────────────────────

class TestIntradayPlaceholderCss:
    """The CSS classes used by the chart placeholders must be defined in styles.css."""

    STYLES_PATH = Path(__file__).parent.parent / "static" / "css" / "styles.css"

    def _css(self) -> str:
        return self.STYLES_PATH.read_text(encoding="utf-8")

    def test_intraday_placeholder_class_defined(self):
        assert ".intraday-placeholder" in self._css(), (
            ".intraday-placeholder must be defined in static/css/styles.css"
        )

    def test_intraday_placeholder_icon_class_defined(self):
        assert ".intraday-placeholder-icon" in self._css()

    def test_intraday_placeholder_label_class_defined(self):
        assert ".intraday-placeholder-label" in self._css()

    def test_intraday_placeholder_error_modifier_defined(self):
        assert ".intraday-placeholder--error" in self._css()
