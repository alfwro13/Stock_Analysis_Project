"""
tests/test_07_api_routes_audit.py  ── api_routes.py Audit Regression Tests

Covers the bugs found and fixed in the June 2026 api_routes.py audit:

1. Anomaly datetime comparison — /api/system/metrics must not crash when a
   .joblib model file has a naive (no tzinfo) or missing 'trained_at' field.
2. Path traversal — /api/universe/import/server uses the resolved path
   throughout, not a re-created unresolved one.
3. Intraday chart placeholder HTML — /api/intraday-chart/<ticker> must NOT
   return inline style= attributes in its error/empty fallback responses.
4. AI Contagion status — GET /api/ai-contagion/status must parse both new-format
   (dict payload with tickers + severity_score) and legacy-format (bare list)
   snapshot rows without crashing, and must return the correct shape.
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

    def test_gap_notice_shown_when_ticker_persistently_gapped(self, client):
        with patch("api_routes.yahoo_engine.is_intraday_gap_alerted", return_value=True):
            html = self._get_chart_html(client)
        assert "box-warning" in html
        assert "no fresh intraday data" in html.lower()
        assert "style=" not in html

    def test_gap_notice_absent_when_not_gapped(self, client):
        with patch("api_routes.yahoo_engine.is_intraday_gap_alerted", return_value=False):
            html = self._get_chart_html(client)
        assert "box-warning" not in html


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


# ── 4. AI Contagion status — _parse_payload covers new and legacy formats ─────

class TestAiContagionStatusEndpoint:
    """
    GET /api/ai-contagion/status reads ai_contagion_snapshots rows and passes
    payload_json through an internal _parse_payload() helper that handles two
    formats:
      - New: JSON object {"tickers": [...], "severity_score": 0.5}
      - Legacy: bare JSON array  ["NVDA", "MSFT"]

    Tests seed rows directly into the in-memory test DB so the response can be
    verified end-to-end without mocking the DB layer.
    """

    def _seed(self, conn, payload_json: str, alert: int = 0):
        conn.execute(
            """INSERT INTO ai_contagion_snapshots
               (scan_ts, leader_count, etf_count, alert_fired, payload_json)
               VALUES ('2026-06-07 10:00:00', 2, 1, ?, ?)""",
            (alert, payload_json),
        )
        conn.commit()

    def _cleanup(self, conn):
        conn.execute("DELETE FROM ai_contagion_snapshots")
        conn.commit()

    def test_empty_snapshots_returns_empty_list(self, client):
        import database
        conn = database.get_connection()
        try:
            self._cleanup(conn)
            resp = client.get("/api/ai-contagion/status")
        finally:
            self._cleanup(conn)
            conn.close()
        assert resp.status_code == 200
        data = _json(resp)
        assert data["status"] == "success"
        assert data["snapshots"] == []

    def test_new_format_payload_parsed_correctly(self, client):
        import json
        import database
        conn = database.get_connection()
        try:
            self._cleanup(conn)
            payload = json.dumps({"tickers": ["NVDA", "AMD"], "severity_score": 0.75})
            self._seed(conn, payload, alert=1)
            resp = client.get("/api/ai-contagion/status")
        finally:
            self._cleanup(conn)
            conn.close()
        assert resp.status_code == 200
        snap = _json(resp)["snapshots"][0]
        assert snap["tickers"] == ["NVDA", "AMD"]
        assert snap["severity_score"] == pytest.approx(0.75)
        assert snap["alert_fired"] is True

    def test_legacy_list_payload_parsed_without_crash(self, client):
        import json
        import database
        conn = database.get_connection()
        try:
            self._cleanup(conn)
            payload = json.dumps(["NVDA", "AMD"])
            self._seed(conn, payload)
            resp = client.get("/api/ai-contagion/status")
        finally:
            self._cleanup(conn)
            conn.close()
        assert resp.status_code == 200
        snap = _json(resp)["snapshots"][0]
        assert snap["tickers"] == ["NVDA", "AMD"]
        assert snap["severity_score"] == 0.0

    def test_null_payload_json_does_not_crash(self, client):
        import database
        conn = database.get_connection()
        try:
            self._cleanup(conn)
            self._seed(conn, None)
            resp = client.get("/api/ai-contagion/status")
        finally:
            self._cleanup(conn)
            conn.close()
        assert resp.status_code == 200
        snap = _json(resp)["snapshots"][0]
        assert snap["tickers"] == []
        assert snap["severity_score"] == 0.0

    def test_response_shape_has_required_keys(self, client):
        import json
        import database
        conn = database.get_connection()
        try:
            self._cleanup(conn)
            payload = json.dumps({"tickers": ["SPY"], "severity_score": 0.1})
            self._seed(conn, payload)
            resp = client.get("/api/ai-contagion/status")
        finally:
            self._cleanup(conn)
            conn.close()
        assert resp.status_code == 200
        snap = _json(resp)["snapshots"][0]
        for key in ("scan_ts", "leader_count", "etf_count", "alert_fired", "tickers", "severity_score"):
            assert key in snap, f"Missing key in snapshot: {key}"


# ── 5. _normalise_constituents — ETF predictor weight normalisation ───────────

class TestNormaliseConstituents:
    """_normalise_constituents normalises ETF constituent weights to sum to 1.0."""

    @staticmethod
    def _item(ticker: str, weight: float):
        from api_routes_analysis import EtfConstituentItem
        return EtfConstituentItem(ticker=ticker, weight=weight)

    def test_weights_normalised_to_sum_one(self):
        from api_routes_analysis import _normalise_constituents
        items = [self._item("AAPL", 60.0), self._item("MSFT", 40.0)]
        result = _normalise_constituents(items)
        assert abs(sum(r["weight"] for r in result) - 1.0) < 1e-9

    def test_weight_proportions_preserved(self):
        from api_routes_analysis import _normalise_constituents
        items = [self._item("AAPL", 60.0), self._item("MSFT", 40.0)]
        result = _normalise_constituents(items)
        assert abs(result[0]["weight"] - 0.6) < 1e-9
        assert abs(result[1]["weight"] - 0.4) < 1e-9

    def test_zero_total_returns_empty(self):
        from api_routes_analysis import _normalise_constituents
        items = [self._item("AAPL", 0.0), self._item("MSFT", 0.0)]
        assert _normalise_constituents(items) == []

    def test_negative_total_returns_empty(self):
        from api_routes_analysis import _normalise_constituents
        items = [self._item("AAPL", -5.0), self._item("MSFT", -3.0)]
        assert _normalise_constituents(items) == []

    def test_ticker_uppercased_and_stripped(self):
        from api_routes_analysis import _normalise_constituents
        items = [self._item("  aapl  ", 1.0)]
        result = _normalise_constituents(items)
        assert result[0]["ticker"] == "AAPL"

    def test_already_normalised_passes_through(self):
        from api_routes_analysis import _normalise_constituents
        items = [self._item("SPY", 0.5), self._item("QQQ", 0.5)]
        result = _normalise_constituents(items)
        assert abs(sum(r["weight"] for r in result) - 1.0) < 1e-9
        assert abs(result[0]["weight"] - 0.5) < 1e-9
