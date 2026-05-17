"""Tests for ops-models FastAPI application (TASK-018).

Test matrix
-----------
1. ``POST /internal/predict`` with active model mocked → 200 + PredictionResult
   with ``explain_status="pending"``.
2. ``POST /internal/predict`` without active model → 503.
3. ``POST /internal/models/deploy`` with valid ``.onnx`` file → 200 +
   ModelDeployResponse.
4. ``POST /internal/models/deploy`` with ``.pkl`` file → 422.
5. ``GET /internal/models/{model_id}`` with existing id → 200 + model dict.
6. ``GET /internal/models/{model_id}`` with unknown id → 404.
7. ``GET /health`` → 200 ``{"status": "ok", "service": "ops-models"}``.

All tests use ``TestClient`` (sync) from ``starlette``.  ``OnnxRunner`` and
``ModelRegistry`` are mocked via ``unittest.mock`` so that no ONNX artefact
or SQLite file is needed on disk during CI.

Strategy
--------
- ``ModelRegistry`` dependency is overridden via FastAPI's
  ``app.dependency_overrides`` mechanism.
- ``OnnxRunner`` is patched via ``unittest.mock.patch`` on its import path
  inside ``ops_models.app.main``.
- For the deploy endpoint we do NOT mock the file-system write, but we do
  mock ``ModelRegistry.register_model`` and ``activate_version`` so that no
  persistent DB is needed.
"""

from __future__ import annotations

import io
import json
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Conftest registers ops_models.* in sys.modules before this import.
from ops_models.app.main import app, get_registry


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_mock_registry(
    active_model: dict[str, Any] | None = None,
    registered_id: str = "rotating_equipment-v1.0.0",
    list_models: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Return a MagicMock that mimics ModelRegistry.

    Parameters
    ----------
    active_model:
        Value returned by ``get_active_model``.  ``None`` simulates no active
        model.
    registered_id:
        Value returned by ``register_model``.
    list_models:
        Value returned by ``list_models()``.  Defaults to ``[]``.
    """
    registry = MagicMock()
    registry.get_active_model.return_value = active_model
    registry.register_model.return_value = registered_id
    registry.activate_version.return_value = None
    registry.list_models.return_value = list_models or []
    registry.close.return_value = None
    return registry


def _override_registry(mock: MagicMock):
    """Install *mock* as the ``get_registry`` dependency override."""

    def _dep() -> Generator[MagicMock, None, None]:
        yield mock

    app.dependency_overrides[get_registry] = _dep


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    """Provide a fresh TestClient for each test."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    _clear_overrides()


# ---------------------------------------------------------------------------
# Shared model metadata for predict tests
# ---------------------------------------------------------------------------

_ACTIVE_MODEL: dict[str, Any] = {
    "model_id": "rotating_equipment-v1.0.0",
    "asset_class": "rotating_equipment",
    "version": "1.0.0",
    "artifact_path": "/data/models/rotating_equipment-v1.0.0.onnx",
    "artifact_format": "onnx",
    "anomaly_threshold": 0.5,
    "severity_thresholds": json.dumps({"low": 0.5, "medium": 0.75, "high": 0.9}),
    "is_active": 1,
    "deployed_at": "2026-05-17T00:00:00+00:00",
}

_PREDICT_PAYLOAD: dict[str, Any] = {
    "feature_record_id": "frec-001",
    "asset_id": "PUMP-001",
    "asset_class": "rotating_equipment",
    "features": [0.1, 0.2, 0.3, 0.4],
}


# ---------------------------------------------------------------------------
# Test 1 — POST /internal/predict — active model — 200
# ---------------------------------------------------------------------------


def test_predict_with_active_model_returns_200(client: TestClient) -> None:
    """Inference succeeds when registry returns an active model."""
    mock_registry = _make_mock_registry(active_model=_ACTIVE_MODEL)
    _override_registry(mock_registry)

    # Mock OnnxRunner so no disk artefact is needed.
    mock_runner_instance = MagicMock()
    mock_runner_instance.run.return_value = {
        "anomaly_score": 0.8,
        "confidence_score": 0.9,
    }

    with patch("ops_models.app.main.OnnxRunner", return_value=mock_runner_instance):
        resp = client.post("/internal/predict", json=_PREDICT_PAYLOAD)

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Mandatory PredictionResult fields.
    assert body["explain_status"] == "pending"
    assert body["asset_id"] == "PUMP-001"
    assert body["asset_class"] == "rotating_equipment"
    assert 0.0 <= body["anomaly_score"] <= 1.0
    assert 0.0 <= body["confidence_score"] <= 1.0
    assert isinstance(body["alert"], bool)
    assert "prediction_id" in body
    assert "model_version" in body

    # OnnxRunner was initialised with the correct artefact path.
    with patch("ops_models.app.main.OnnxRunner") as mock_runner_cls:
        mock_runner_cls.return_value = mock_runner_instance
        client.post("/internal/predict", json=_PREDICT_PAYLOAD)
        mock_runner_cls.assert_called_once_with(_ACTIVE_MODEL["artifact_path"])


# ---------------------------------------------------------------------------
# Test 2 — POST /internal/predict — no active model — 503
# ---------------------------------------------------------------------------


def test_predict_no_active_model_returns_503(client: TestClient) -> None:
    """When no model is active for the requested asset_class, return 503."""
    mock_registry = _make_mock_registry(active_model=None)
    _override_registry(mock_registry)

    resp = client.post("/internal/predict", json=_PREDICT_PAYLOAD)

    assert resp.status_code == 503, resp.text
    assert "No active model" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Test 3 — POST /internal/models/deploy — .onnx file — 200
# ---------------------------------------------------------------------------


def test_deploy_onnx_returns_200(client: TestClient, tmp_path) -> None:  # noqa: ANN001
    """Valid .onnx upload should return 200 with ModelDeployResponse fields."""
    mock_registry = _make_mock_registry(registered_id="rotating_equipment-v2.0.0")
    _override_registry(mock_registry)

    metadata_payload = {
        "asset_class": "rotating_equipment",
        "version": "2.0.0",
        "anomaly_threshold": 0.6,
        "severity_thresholds": {"low": 0.6, "medium": 0.75, "high": 0.9},
        "is_active": True,
    }

    fake_onnx = io.BytesIO(b"ONNX_FAKE_BYTES")

    # Patch _MODEL_DATA_DIR to a temp directory to avoid touching real FS.
    with patch("ops_models.app.main._MODEL_DATA_DIR", tmp_path):
        resp = client.post(
            "/internal/models/deploy",
            files={"artifact": ("my_model.onnx", fake_onnx, "application/octet-stream")},
            data={"metadata": json.dumps(metadata_payload)},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_id"] == "rotating_equipment-v2.0.0"
    assert body["version"] == "2.0.0"
    assert body["asset_class"] == "rotating_equipment"
    assert body["is_active"] is True
    assert "deployed_at" in body

    # Registry methods were called.
    mock_registry.register_model.assert_called_once()
    mock_registry.activate_version.assert_called_once_with(
        "rotating_equipment-v2.0.0"
    )


# ---------------------------------------------------------------------------
# Test 4 — POST /internal/models/deploy — .pkl file — 422
# ---------------------------------------------------------------------------


def test_deploy_pkl_returns_422(client: TestClient) -> None:
    """Non-.onnx artefact extension should be rejected with HTTP 422."""
    mock_registry = _make_mock_registry()
    _override_registry(mock_registry)

    fake_pkl = io.BytesIO(b"FAKE_PICKLE_DATA")

    resp = client.post(
        "/internal/models/deploy",
        files={"artifact": ("model.pkl", fake_pkl, "application/octet-stream")},
        data={"metadata": json.dumps({
            "asset_class": "rotating_equipment",
            "version": "1.0.0",
            "anomaly_threshold": 0.5,
            "severity_thresholds": {"low": 0.5, "medium": 0.75, "high": 0.9},
        })},
    )

    assert resp.status_code == 422, resp.text
    detail: str = resp.json()["detail"]
    assert ".pkl" in detail or "pkl" in detail.lower()


# ---------------------------------------------------------------------------
# Test 5 — GET /internal/models/{model_id} — existing — 200
# ---------------------------------------------------------------------------


def test_get_model_existing_returns_200(client: TestClient) -> None:
    """Should return the model dict when model_id exists in the registry."""
    existing_model = {**_ACTIVE_MODEL}
    mock_registry = _make_mock_registry(list_models=[existing_model])
    _override_registry(mock_registry)

    resp = client.get(f"/internal/models/{existing_model['model_id']}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_id"] == existing_model["model_id"]
    assert body["asset_class"] == existing_model["asset_class"]


# ---------------------------------------------------------------------------
# Test 6 — GET /internal/models/{model_id} — not found — 404
# ---------------------------------------------------------------------------


def test_get_model_not_found_returns_404(client: TestClient) -> None:
    """Should return 404 when the requested model_id does not exist."""
    mock_registry = _make_mock_registry(list_models=[])
    _override_registry(mock_registry)

    resp = client.get("/internal/models/nonexistent-model-id")

    assert resp.status_code == 404, resp.text
    assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Test 7 — GET /health — 200
# ---------------------------------------------------------------------------


def test_health_returns_200(client: TestClient) -> None:
    """Health endpoint always returns 200 with expected body."""
    resp = client.get("/health")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "ops-models"
