"""
tests/test_telemetry_router.py
================================
Contract tests for POST /telemetry (TASK-022).

Criteria verified (from tasks.md TASK-022):
  - POST /telemetry with valid payload returns HTTP 202 with ``ingestion_id``.
  - POST /telemetry with malformed payload (missing required field) returns
    HTTP 400 (translated from the upstream 422 via exception handler).
  - The response carries the X-Advisory-Only: true header (via middleware).

Design notes
------------
* The route handler delegates to ops-ingest via httpx.  In tests we override
  the ``get_ingest_client`` dependency to inject a mock that never makes a
  real network call.
* The ``ops_api.app.routers.telemetry`` module is registered in conftest.py
  so that the import path resolves without sys.path hacks.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers: build a minimal app with the telemetry router for isolated tests
# ---------------------------------------------------------------------------


def _make_app_with_telemetry() -> FastAPI:
    """Return a minimal FastAPI app that includes only the telemetry router.

    Registers the same exception handlers as the production main.py so that
    422 RequestValidationError is translated to 400 (INIT-US-01 AC4).
    """
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    from ops_api.app.middleware.advisory import AdvisoryMiddleware
    from ops_api.app.middleware.tracing import TracingMiddleware
    from ops_api.app.routers.telemetry import router as telemetry_router

    _app = FastAPI()
    _app.add_middleware(AdvisoryMiddleware)
    _app.add_middleware(TracingMiddleware)

    @_app.exception_handler(RequestValidationError)
    async def _validation_handler(req: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"detail": exc.errors()},
            headers={"X-Advisory-Only": "true"},
        )

    _app.include_router(telemetry_router)
    return _app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_READING = {
    "asset_id": "PUMP-001",
    "timestamp": "2026-05-16T10:00:00Z",
    "metric_name": "vibration_x",
    "value": 0.0023,
    "unit": "m/s2",
    "source_protocol": "rest_batch",
}

_VALID_PAYLOAD = {"readings": [_VALID_READING]}

_INGESTION_RESPONSE = {
    "ingestion_id": str(uuid.uuid4()),
    "records_received": 1,
    "records_accepted": 1,
    "records_rejected": 0,
    "rejection_details": [],
}


class _FakeHttpxResponse:
    """Minimal stand-in for httpx.Response returned by the mocked client."""

    def __init__(self, status_code: int, data: dict) -> None:
        self.status_code = status_code
        self._data = data

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
# Tests: POST /telemetry
# ---------------------------------------------------------------------------


class TestPostTelemetry:
    """Contract tests for the POST /telemetry endpoint."""

    def _client_with_mock_ingest(
        self,
        status_code: int = 202,
        response_data: dict | None = None,
    ) -> TestClient:
        """Return a TestClient whose ops-ingest httpx call is mocked.

        Parameters
        ----------
        status_code:
            Status code the mocked ops-ingest will return.
        response_data:
            JSON body the mocked ops-ingest will return.
        """
        from ops_api.app.routers.telemetry import get_ingest_client

        data = response_data if response_data is not None else _INGESTION_RESPONSE
        fake_response = _FakeHttpxResponse(status_code=status_code, data=data)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=fake_response)
        # Support async context manager usage: ``async with get_ingest_client() as client``
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        _app = _make_app_with_telemetry()
        _app.dependency_overrides[get_ingest_client] = lambda: mock_client
        return TestClient(_app)

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_valid_payload_returns_202(self) -> None:
        """POST /telemetry with valid payload MUST return HTTP 202."""
        client = self._client_with_mock_ingest()
        response = client.post("/telemetry", json=_VALID_PAYLOAD)
        assert response.status_code == 202

    def test_valid_payload_response_contains_ingestion_id(self) -> None:
        """Response body MUST contain ``ingestion_id``."""
        client = self._client_with_mock_ingest()
        response = client.post("/telemetry", json=_VALID_PAYLOAD)
        body = response.json()
        assert "ingestion_id" in body

    def test_valid_payload_response_contains_counters(self) -> None:
        """Response body MUST contain ``records_received``, ``records_accepted``,
        ``records_rejected``."""
        client = self._client_with_mock_ingest()
        response = client.post("/telemetry", json=_VALID_PAYLOAD)
        body = response.json()
        assert "records_received" in body
        assert "records_accepted" in body
        assert "records_rejected" in body

    def test_advisory_header_present_on_success(self) -> None:
        """Response MUST carry X-Advisory-Only: true (RN-06 / CAT-04)."""
        client = self._client_with_mock_ingest()
        response = client.post("/telemetry", json=_VALID_PAYLOAD)
        assert response.headers.get("x-advisory-only") == "true"

    def test_empty_readings_list_returns_202(self) -> None:
        """An empty ``readings`` list is a valid, idempotent no-op batch."""
        empty_response = {
            **_INGESTION_RESPONSE,
            "records_received": 0,
            "records_accepted": 0,
        }
        client = self._client_with_mock_ingest(response_data=empty_response)
        response = client.post("/telemetry", json={"readings": []})
        assert response.status_code == 202

    # ------------------------------------------------------------------
    # Malformed payload — HTTP 400 (translated from upstream 422)
    # ------------------------------------------------------------------

    def test_missing_readings_field_returns_400(self) -> None:
        """Payload missing ``readings`` key MUST return HTTP 400."""
        client = self._client_with_mock_ingest()
        response = client.post("/telemetry", json={})
        assert response.status_code == 400

    def test_invalid_field_type_returns_400(self) -> None:
        """Payload with ``readings`` as a string (not a list) MUST return HTTP 400."""
        client = self._client_with_mock_ingest()
        response = client.post("/telemetry", json={"readings": "not-a-list"})
        assert response.status_code == 400

    def test_advisory_header_present_on_400(self) -> None:
        """Error responses MUST also carry X-Advisory-Only: true.

        The header may appear once (middleware only) or have value ``true, true``
        (middleware + explicit handler) depending on TestClient/Starlette version.
        Either way the value must *contain* ``true``.
        """
        client = self._client_with_mock_ingest()
        response = client.post("/telemetry", json={})
        header_value = response.headers.get("x-advisory-only", "")
        assert "true" in header_value
