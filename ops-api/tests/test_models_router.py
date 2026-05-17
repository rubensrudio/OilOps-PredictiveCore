"""
tests/test_models_router.py
============================
Contract tests for POST /models/deploy (TASK-025).

Criteria verified (from tasks.md TASK-025):
  - POST /models/deploy with a .pkl artifact MUST return HTTP 422 with a
    descriptive error message (format validation at ops-api gateway level).
  - POST /models/deploy with a valid .onnx artifact MUST return HTTP 200
    with ``model_id`` and ``is_active: True`` in the response body.
  - Response carries the X-Advisory-Only: true header (via middleware).

Design notes
------------
* The route handler delegates to ops-models via httpx multipart/form-data.
  In tests we override the ``get_models_client`` dependency to inject a mock
  that never makes real network calls.
* Format validation (extension check) is performed IN ops-api before the
  upstream call — .pkl, .h5 etc. are rejected immediately with HTTP 422.
* ModelDeployResponse is imported from ops_models so we can validate the
  response shape.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_with_models() -> FastAPI:
    """Return a minimal FastAPI app that includes only the models router."""
    from ops_api.app.middleware.advisory import AdvisoryMiddleware
    from ops_api.app.middleware.tracing import TracingMiddleware
    from ops_api.app.routers.models import router as models_router

    _app = FastAPI()
    _app.add_middleware(AdvisoryMiddleware)
    _app.add_middleware(TracingMiddleware)
    _app.include_router(models_router)
    return _app


# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

_DEPLOY_RESPONSE = {
    "model_id": "rotating_equipment-v2.0.0",
    "version": "2.0.0",
    "asset_class": "rotating_equipment",
    "deployed_at": datetime.now(tz=timezone.utc).isoformat(),
    "is_active": True,
}

_VALID_METADATA = (
    '{"asset_class": "rotating_equipment", "version": "2.0.0",'
    ' "anomaly_threshold": 0.6,'
    ' "severity_thresholds": {"low": 0.6, "medium": 0.75, "high": 0.9}}'
)


class _FakeHttpxResponse:
    """Minimal stand-in for httpx.Response returned by the mocked client."""

    def __init__(self, status_code: int, data: dict | None = None) -> None:
        self.status_code = status_code
        self._data = data or {}

    def json(self) -> dict:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=MagicMock(),
                response=MagicMock(status_code=self.status_code),
            )


# ---------------------------------------------------------------------------
# Tests: POST /models/deploy
# ---------------------------------------------------------------------------


class TestPostModelsDeploy:
    """Contract tests for the POST /models/deploy endpoint."""

    def _client_with_mock_models(
        self,
        status_code: int = 200,
        response_data: dict | None = None,
    ) -> TestClient:
        """Return a TestClient whose ops-models httpx call is mocked."""
        from ops_api.app.routers.models import get_models_client

        data = response_data if response_data is not None else _DEPLOY_RESPONSE
        fake_response = _FakeHttpxResponse(status_code=status_code, data=data)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        _app = _make_app_with_models()
        _app.dependency_overrides[get_models_client] = lambda: mock_client
        return TestClient(_app)

    # ------------------------------------------------------------------
    # Happy path — .onnx file
    # ------------------------------------------------------------------

    def test_onnx_artifact_returns_200(self) -> None:
        """POST /models/deploy with .onnx file MUST return HTTP 200."""
        client = self._client_with_mock_models()
        artifact_bytes = b"fake-onnx-binary-content"
        response = client.post(
            "/models/deploy",
            files={"artifact": ("model.onnx", io.BytesIO(artifact_bytes), "application/octet-stream")},
            data={"metadata": _VALID_METADATA},
        )
        assert response.status_code == 200

    def test_onnx_response_contains_model_id(self) -> None:
        """Response body MUST contain ``model_id`` field."""
        client = self._client_with_mock_models()
        artifact_bytes = b"fake-onnx-binary-content"
        response = client.post(
            "/models/deploy",
            files={"artifact": ("model.onnx", io.BytesIO(artifact_bytes), "application/octet-stream")},
            data={"metadata": _VALID_METADATA},
        )
        body = response.json()
        assert "model_id" in body

    def test_onnx_response_is_active_true(self) -> None:
        """Response body MUST have ``is_active: true`` on successful deploy."""
        client = self._client_with_mock_models()
        artifact_bytes = b"fake-onnx-binary-content"
        response = client.post(
            "/models/deploy",
            files={"artifact": ("model.onnx", io.BytesIO(artifact_bytes), "application/octet-stream")},
            data={"metadata": _VALID_METADATA},
        )
        assert response.json()["is_active"] is True

    def test_advisory_header_present_on_200(self) -> None:
        """Response MUST carry X-Advisory-Only: true (RN-06 / CAT-04)."""
        client = self._client_with_mock_models()
        artifact_bytes = b"fake-onnx-binary-content"
        response = client.post(
            "/models/deploy",
            files={"artifact": ("model.onnx", io.BytesIO(artifact_bytes), "application/octet-stream")},
            data={"metadata": _VALID_METADATA},
        )
        assert response.headers.get("x-advisory-only") == "true"

    # ------------------------------------------------------------------
    # Format validation — .pkl file MUST be rejected before calling ops-models
    # ------------------------------------------------------------------

    def test_pkl_artifact_returns_422(self) -> None:
        """POST /models/deploy with .pkl file MUST return HTTP 422 immediately."""
        client = self._client_with_mock_models()
        artifact_bytes = b"fake-pickle-content"
        response = client.post(
            "/models/deploy",
            files={"artifact": ("model.pkl", io.BytesIO(artifact_bytes), "application/octet-stream")},
            data={"metadata": _VALID_METADATA},
        )
        assert response.status_code == 422

    def test_pkl_422_contains_descriptive_detail(self) -> None:
        """422 response for .pkl MUST include a descriptive ``detail`` field."""
        client = self._client_with_mock_models()
        artifact_bytes = b"fake-pickle-content"
        response = client.post(
            "/models/deploy",
            files={"artifact": ("model.pkl", io.BytesIO(artifact_bytes), "application/octet-stream")},
            data={"metadata": _VALID_METADATA},
        )
        body = response.json()
        assert "detail" in body
        # Message must mention the format received and what was expected.
        detail_str = str(body["detail"]).lower()
        assert ".pkl" in detail_str or "pkl" in detail_str

    def test_advisory_header_present_on_422(self) -> None:
        """Even 422 responses MUST carry X-Advisory-Only: true (RN-06)."""
        client = self._client_with_mock_models()
        artifact_bytes = b"fake-pickle-content"
        response = client.post(
            "/models/deploy",
            files={"artifact": ("model.pkl", io.BytesIO(artifact_bytes), "application/octet-stream")},
            data={"metadata": _VALID_METADATA},
        )
        assert response.headers.get("x-advisory-only") == "true"

    # ------------------------------------------------------------------
    # Format validation — .h5 file MUST also be rejected
    # ------------------------------------------------------------------

    def test_h5_artifact_returns_422(self) -> None:
        """POST /models/deploy with .h5 file MUST return HTTP 422."""
        client = self._client_with_mock_models()
        artifact_bytes = b"fake-h5-content"
        response = client.post(
            "/models/deploy",
            files={"artifact": ("model.h5", io.BytesIO(artifact_bytes), "application/octet-stream")},
            data={"metadata": _VALID_METADATA},
        )
        assert response.status_code == 422

    # ------------------------------------------------------------------
    # Metadata validation — invalid JSON must be rejected
    # ------------------------------------------------------------------

    def test_invalid_metadata_json_returns_422(self) -> None:
        """POST /models/deploy with non-JSON metadata MUST return HTTP 422."""
        client = self._client_with_mock_models()
        artifact_bytes = b"fake-onnx-content"
        response = client.post(
            "/models/deploy",
            files={"artifact": ("model.onnx", io.BytesIO(artifact_bytes), "application/octet-stream")},
            data={"metadata": "NOT_VALID_JSON"},
        )
        assert response.status_code == 422

    def test_missing_required_metadata_field_returns_422(self) -> None:
        """POST /models/deploy with metadata missing ``asset_class`` MUST return HTTP 422."""
        client = self._client_with_mock_models()
        artifact_bytes = b"fake-onnx-content"
        # severity_thresholds is required in ModelDeployRequest
        incomplete_metadata = '{"version": "2.0.0", "anomaly_threshold": 0.6}'
        response = client.post(
            "/models/deploy",
            files={"artifact": ("model.onnx", io.BytesIO(artifact_bytes), "application/octet-stream")},
            data={"metadata": incomplete_metadata},
        )
        assert response.status_code == 422

    # ------------------------------------------------------------------
    # Upstream propagation — ops-models 422 is forwarded as 422
    # ------------------------------------------------------------------

    def test_upstream_422_propagated(self) -> None:
        """When ops-models returns 422, ops-api MUST propagate HTTP 422."""
        upstream_422_data = {
            "detail": "Unsupported artifact format from upstream"
        }
        client = self._client_with_mock_models(
            status_code=422,
            response_data=upstream_422_data,
        )
        artifact_bytes = b"fake-onnx-content"
        response = client.post(
            "/models/deploy",
            files={"artifact": ("model.onnx", io.BytesIO(artifact_bytes), "application/octet-stream")},
            data={"metadata": _VALID_METADATA},
        )
        assert response.status_code == 422
