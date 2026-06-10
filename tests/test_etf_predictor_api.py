"""Tests for ETF predictor API endpoints."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _json(resp) -> dict:
    try:
        return resp.json()
    except Exception as exc:
        raise AssertionError(
            f"Response is not valid JSON.\nStatus: {resp.status_code}\nBody: {resp.text[:500]}"
        ) from exc


_SAMPLE_BODY = {
    "name": "API Test Predictor",
    "etf_ticker": "TST.L",
    "constituents": [
        {"ticker": "AAPL", "weight": 50.0},
        {"ticker": "MSFT", "weight": 30.0},
        {"ticker": "NVDA", "weight": 20.0},
    ],
    "enabled": True,
    "auto_schedule": False,
    "pre_run_time": "13:30",
    "post_run_time": "22:00",
}


@pytest.mark.api
def test_list_etf_predictors_returns_200(client):
    resp = client.get("/api/etf-predictors")
    assert resp.status_code == 200
    data = _json(resp)
    assert data.get("status") == "success"
    assert "configs" in data
    assert isinstance(data["configs"], list)


@pytest.mark.api
def test_create_etf_predictor(client):
    resp = client.post("/api/etf-predictors", json=_SAMPLE_BODY)
    assert resp.status_code == 200
    data = _json(resp)
    assert data.get("status") == "success"
    assert "id" in data


@pytest.mark.api
def test_create_etf_predictor_normalises_weights(client):
    """Weights should be normalised to sum = 1.0 regardless of input values."""
    resp = client.post("/api/etf-predictors", json={
        **_SAMPLE_BODY,
        "name": "Normalise Test",
        "constituents": [
            {"ticker": "AAA", "weight": 200.0},
            {"ticker": "BBB", "weight": 100.0},
            {"ticker": "CCC", "weight": 100.0},
        ],
    })
    assert resp.status_code == 200
    config_id = _json(resp)["id"]

    import database as _db
    cfg = _db.get_etf_predictor_config(config_id)
    total = sum(h["weight"] for h in cfg["constituents"])
    assert abs(total - 1.0) < 1e-6, f"Weights did not normalise to 1.0: total={total}"
    _db.soft_delete_etf_predictor_config(config_id)


@pytest.mark.api
def test_create_rejects_empty_constituents(client):
    resp = client.post("/api/etf-predictors", json={**_SAMPLE_BODY, "constituents": []})
    assert resp.status_code == 422


@pytest.mark.api
def test_create_rejects_zero_weight_constituents(client):
    resp = client.post("/api/etf-predictors", json={
        **_SAMPLE_BODY,
        "constituents": [{"ticker": "AAPL", "weight": 0.0}],
    })
    assert resp.status_code == 422


@pytest.mark.api
def test_update_etf_predictor(client):
    create_resp = client.post("/api/etf-predictors", json=_SAMPLE_BODY)
    config_id = _json(create_resp)["id"]

    updated = {**_SAMPLE_BODY, "name": "Updated Name"}
    resp = client.put(f"/api/etf-predictors/{config_id}", json=updated)
    assert resp.status_code == 200
    assert _json(resp).get("status") == "success"

    import database as _db
    cfg = _db.get_etf_predictor_config(config_id)
    assert cfg["name"] == "Updated Name"
    _db.soft_delete_etf_predictor_config(config_id)


@pytest.mark.api
def test_update_nonexistent_returns_404(client):
    resp = client.put("/api/etf-predictors/999999", json=_SAMPLE_BODY)
    assert resp.status_code == 404


@pytest.mark.api
def test_delete_etf_predictor(client):
    create_resp = client.post("/api/etf-predictors", json=_SAMPLE_BODY)
    config_id = _json(create_resp)["id"]

    resp = client.delete(f"/api/etf-predictors/{config_id}")
    assert resp.status_code == 200
    assert _json(resp).get("status") == "success"

    import database as _db
    assert _db.get_etf_predictor_config(config_id) is None


@pytest.mark.api
def test_delete_nonexistent_returns_404(client):
    resp = client.delete("/api/etf-predictors/999998")
    assert resp.status_code == 404


@pytest.mark.api
def test_run_now_returns_success(client):
    create_resp = client.post("/api/etf-predictors", json=_SAMPLE_BODY)
    config_id = _json(create_resp)["id"]

    resp = client.post(f"/api/etf-predictors/{config_id}/run")
    assert resp.status_code == 200
    assert _json(resp).get("status") == "success"

    import database as _db
    _db.soft_delete_etf_predictor_config(config_id)


@pytest.mark.api
def test_run_nonexistent_returns_404(client):
    resp = client.post("/api/etf-predictors/999997/run")
    assert resp.status_code == 404


@pytest.mark.api
def test_predictions_returns_accuracy_shape(client):
    create_resp = client.post("/api/etf-predictors", json=_SAMPLE_BODY)
    config_id = _json(create_resp)["id"]

    resp = client.get(f"/api/etf-predictors/{config_id}/predictions")
    assert resp.status_code == 200
    data = _json(resp)
    assert data.get("status") == "success"
    assert "next_open" in data
    assert "us_open_impact" in data

    import database as _db
    _db.soft_delete_etf_predictor_config(config_id)


@pytest.mark.api
def test_predictions_nonexistent_returns_404(client):
    resp = client.get("/api/etf-predictors/999996/predictions")
    assert resp.status_code == 404
