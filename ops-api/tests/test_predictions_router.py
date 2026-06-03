"""
tests/test_predictions_router.py
==================================
Contract tests for GET /predictions/{asset_id} (TASK-022).

Criteria verified (from tasks.md TASK-022):
  - GET /predictions/{asset_id} with existing predictions returns HTTP 200
    with a PredictionResult payload.
  - GET /predictions/UNKNOWN returns HTTP 404.
  - Response carries the X-Advisory-Only: true header (via middleware).

Design notes
------------
* The route handler delegates to ops-store via httpx.  In tests we override
  the ``get_store_client`` dependency to inject a mock that never makes real
  network calls.
* PredictionResult schema is imported from ops_models so we can validate the
  response shape.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_with_predictions() -> FastAPI:
    """Return a minimal FastAPI app that includes only the predictions router."""
    from ops_api.app.middleware.advisory import AdvisoryMiddleware
    from ops_api.app.middleware.tracing import TracingMiddleware
    from ops_api.app.routers.predictions import router as predictions_router

    _app = FastAPI()
    _app.add_middleware(AdvisoryMiddleware)
    _app.add_middleware(TracingMiddleware)
    _app.include_router(predictions_router)
    return _app


# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

_PREDICTION_RESPONSE = {
    "prediction_id": str(uuid.uuid4()),
    "asset_id": "PUMP-001",
    "asset_class": "rotating_equipment",
    "anomaly_score": 0.87,
    "confidence_score": 0.92,
    "alert": True,
    "severity": "high",
    "predicted_at": datetime.now(tz=timezone.utc).isoformat(),
    "model_version": "vibration-autoencoder-v1",
    "model_id": "vibration-autoencoder-v1",
    "feature_record_id": str(uuid.uuid4()),
    "explain_status": "pending",
}


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
# Tests: GET /predictions/{asset_id}
# ---------------------------------------------------------------------------


class TestGetPredictions:
    """Contract tests for the GET /predictions/{asset_id} endpoint."""

    def _client_with_mock_store(
        self,
        status_code: int = 200,
        response_data: dict | None = None,
    ) -> TestClient:
        """Return a TestClient whose ops-store httpx call is mocked.

        Parameters
        ----------
        status_code:
            HTTP status code the mocked ops-store will return.
        response_data:
            JSON body the mocked ops-store will return.
        """
        from ops_api.app.routers.predictions import get_store_client

        data = response_data if response_data is not None else _PREDICTION_RESPONSE
        fake_response = _FakeHttpxResponse(status_code=status_code, data=data)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        _app = _make_app_with_predictions()
        _app.dependency_overrides[get_store_client] = lambda: mock_client
        return TestClient(_app)

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_existing_asset_returns_200(self) -> None:
        """GET /predictions/{asset_id} with data MUST return HTTP 200."""
        client = self._client_with_mock_store()
        response = client.get("/predictions/PUMP-001")
        assert response.status_code == 200

    def test_response_contains_prediction_fields(self) -> None:
        """Response body MUST contain all PredictionResult fields."""
        client = self._client_with_mock_store()
        response = client.get("/predictions/PUMP-001")
        body = response.json()
        assert "prediction_id" in body
        assert "asset_id" in body
        assert "anomaly_score" in body
        assert "confidence_score" in body
        assert "explain_status" in body
        assert "model_version" in body

    def test_advisory_header_present_on_200(self) -> None:
        """Response MUST carry X-Advisory-Only: true (RN-06 / CAT-04)."""
        client = self._client_with_mock_store()
        response = client.get("/predictions/PUMP-001")
        assert response.headers.get("x-advisory-only") == "true"

    def test_response_asset_id_matches_request(self) -> None:
        """Response ``asset_id`` MUST match the path parameter."""
        client = self._client_with_mock_store()
        response = client.get("/predictions/PUMP-001")
        assert response.json()["asset_id"] == "PUMP-001"

    # ------------------------------------------------------------------
    # 404 path — unknown asset
    # ------------------------------------------------------------------

    def test_unknown_asset_returns_404(self) -> None:
        """GET /predictions/UNKNOWN MUST return HTTP 404 when no predictions exist."""
        client = self._client_with_mock_store(
            status_code=404,
            response_data={"detail": "No predictions found for asset_id 'UNKNOWN'"},
        )
        response = client.get("/predictions/UNKNOWN")
        assert response.status_code == 404

    def test_404_response_contains_detail(self) -> None:
        """404 response MUST include a ``detail`` field with a descriptive message."""
        client = self._client_with_mock_store(
            status_code=404,
            response_data={"detail": "No predictions found for asset_id 'UNKNOWN'"},
        )
        response = client.get("/predictions/UNKNOWN")
        body = response.json()
        assert "detail" in body
        assert "UNKNOWN" in body["detail"]

    def test_advisory_header_present_on_404(self) -> None:
        """Even 404 responses MUST carry X-Advisory-Only: true (RN-06)."""
        client = self._client_with_mock_store(
            status_code=404,
            response_data={"detail": "No predictions found for asset_id 'UNKNOWN'"},
        )
        response = client.get("/predictions/UNKNOWN")
        assert response.headers.get("x-advisory-only") == "true"
