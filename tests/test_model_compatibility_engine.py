from pathlib import Path
from unittest.mock import patch

import pytest
from sklearn.exceptions import InconsistentVersionWarning

import model_compatibility_engine as compatibility


@pytest.fixture(autouse=True)
def reset_compatibility_state():
    compatibility._callback = None
    compatibility._reported_jobs.clear()
    compatibility._pending_reports.clear()
    yield
    compatibility._callback = None
    compatibility._reported_jobs.clear()
    compatibility._pending_reports.clear()


def test_version_mismatch_rejects_artifact_and_reports_training_job():
    warning = InconsistentVersionWarning(
        estimator_name="ExtraTreeRegressor",
        current_sklearn_version="1.9.1",
        original_sklearn_version="1.9.0",
    )
    reports = []
    compatibility.set_retraining_callback(
        lambda job_id, path, version: reports.append((job_id, path, version))
    )

    with patch("model_compatibility_engine.joblib.load", side_effect=warning):
        with pytest.raises(compatibility.ModelVersionMismatch):
            compatibility.load_sklearn_artifact(
                Path("/tmp/AAPL.joblib"),
                "anomaly_training_job",
            )

    assert reports == [
        ("anomaly_training_job", Path("/tmp/AAPL.joblib"), "1.9.0")
    ]


def test_repeated_family_mismatch_is_reported_once():
    warning = InconsistentVersionWarning(
        estimator_name="ExtraTreeRegressor",
        current_sklearn_version="1.9.1",
        original_sklearn_version="1.9.0",
    )
    reports = []
    compatibility.set_retraining_callback(
        lambda job_id, path, version: reports.append((job_id, path, version))
    )

    with patch("model_compatibility_engine.joblib.load", side_effect=warning):
        for ticker in ("AAPL", "MSFT"):
            with pytest.raises(compatibility.ModelVersionMismatch):
                compatibility.load_sklearn_artifact(
                    Path(f"/tmp/{ticker}.joblib"),
                    "anomaly_training_job",
                )

    assert len(reports) == 1


def test_pending_mismatch_flushes_when_scheduler_registers():
    compatibility._report_mismatch(
        "ml_training_job",
        Path("/tmp/ml_ensemble.joblib"),
        "1.9.0",
    )
    reports = []

    compatibility.set_retraining_callback(
        lambda job_id, path, version: reports.append((job_id, path, version))
    )

    assert reports == [
        ("ml_training_job", Path("/tmp/ml_ensemble.joblib"), "1.9.0")
    ]


def test_sidecar_mismatch_rejects_before_unpickling(tmp_path):
    artifact_path = tmp_path / "model.joblib"
    compatibility._version_path(artifact_path).write_text("0.0.0")
    reports = []
    compatibility.set_retraining_callback(
        lambda job_id, path, version: reports.append((job_id, path, version))
    )

    with patch("model_compatibility_engine.joblib.load") as load:
        with pytest.raises(compatibility.ModelVersionMismatch):
            compatibility.load_sklearn_artifact(artifact_path, "ml_training_job")

    load.assert_not_called()
    assert reports == [("ml_training_job", artifact_path, "0.0.0")]


def test_dump_writes_current_version_sidecar(tmp_path):
    artifact_path = tmp_path / "model.joblib"

    compatibility.dump_sklearn_artifact({"model": "test"}, artifact_path)

    assert compatibility._version_path(artifact_path).read_text() == compatibility.sklearn_version
