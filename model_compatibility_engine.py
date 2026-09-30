from __future__ import annotations

import logging
import threading
import warnings
from pathlib import Path
from typing import Callable, TypeVar

import joblib
from sklearn import __version__ as sklearn_version
from sklearn.exceptions import InconsistentVersionWarning

from config import ANOMALY_MODELS_DIR, BASE_DIR, HISTORICAL_DIR

logger = logging.getLogger(__name__)

T = TypeVar("T")
RetrainingCallback = Callable[[str, Path, str], None]

_callback: RetrainingCallback | None = None
_reported_jobs: set[str] = set()
_pending_reports: dict[str, tuple[Path, str]] = {}
_lock = threading.Lock()


class ModelVersionMismatch(RuntimeError):
    """Raised when a persisted estimator was trained by another scikit-learn version."""


def set_retraining_callback(callback: RetrainingCallback) -> None:
    """Register the scheduler callback and flush mismatches found before scheduler startup."""
    global _callback
    with _lock:
        _callback = callback
        pending = list(_pending_reports.items())
        _pending_reports.clear()
    for job_id, (path, original_version) in pending:
        callback(job_id, path, original_version)


def _report_mismatch(job_id: str, path: Path, original_version: str) -> None:
    with _lock:
        if job_id in _reported_jobs:
            return
        _reported_jobs.add(job_id)
        callback = _callback
        if callback is None:
            _pending_reports[job_id] = (path, original_version)
            return
    callback(job_id, path, original_version)


def _version_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.sklearn-version")


def dump_sklearn_artifact(artifact: T, path: Path) -> None:
    """Persist an estimator and its scikit-learn version for pre-load compatibility checks."""
    joblib.dump(artifact, path)
    _version_path(path).write_text(sklearn_version)


def load_sklearn_artifact(path: Path, retraining_job_id: str) -> T:
    """Load a trusted local artifact and reject unsupported cross-version estimators."""
    version_path = _version_path(path) if isinstance(path, Path) else None
    if version_path is not None and version_path.exists():
        original_version = version_path.read_text().strip()
        if original_version != sklearn_version:
            _report_mismatch(retraining_job_id, path, original_version)
            raise ModelVersionMismatch(
                f"{path} was trained with scikit-learn {original_version}"
            )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", InconsistentVersionWarning)
            artifact = joblib.load(path)
    except InconsistentVersionWarning as warning:
        original_version = warning.original_sklearn_version
        _report_mismatch(retraining_job_id, path, original_version)
        logger.warning(
            "Rejected %s trained with scikit-learn %s; queued %s for retraining",
            path,
            original_version,
            retraining_job_id,
        )
        raise ModelVersionMismatch(
            f"{path} was trained with scikit-learn {original_version}"
        ) from warning

    if version_path is not None and not version_path.exists():
        version_path.write_text(sklearn_version)
    return artifact


def scan_model_compatibility() -> None:
    """Probe one artifact per training family so upgrades trigger retraining at startup."""
    models_dir = BASE_DIR / "models"
    families: list[tuple[str, list[Path]]] = [
        (
            "ml_training_job",
            [
                models_dir / "ml_ensemble.joblib",
                models_dir / "quantile_q10.joblib",
                models_dir / "quantile_q90.joblib",
            ],
        ),
        (
            "alert_referee_training_job",
            [models_dir / "alert_referee_trapmonitor.joblib"],
        ),
        (
            "confluence_referee_training_job",
            [models_dir / "alert_referee_confluence.joblib"],
        ),
        (
            "macro_model_training_job",
            [
                models_dir / "macro_hmm.joblib",
                models_dir / "macro_rf.joblib",
                models_dir / "macro_xgb.joblib",
            ],
        ),
        (
            "quant_analysis_job",
            [HISTORICAL_DIR / "market_stress_if.joblib"],
        ),
    ]
    anomaly_model = next(ANOMALY_MODELS_DIR.glob("*.joblib"), None)
    if anomaly_model is not None:
        families.append(("anomaly_training_job", [anomaly_model]))

    for job_id, paths in families:
        for path in paths:
            if not path.exists():
                continue
            try:
                load_sklearn_artifact(path, job_id)
            except ModelVersionMismatch:
                break
            except Exception as error:
                logger.warning("Could not inspect model artifact %s: %s", path, error)
