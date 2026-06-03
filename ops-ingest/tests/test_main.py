"""
ops-ingest/tests/test_main.py
================================
Integration tests for ``ops-ingest/app/main.py`` — TASK-012 verification
criteria.

Verification criteria (from tasks.md TASK-012):
  1. ``POST /internal/ingest`` with valid payload → HTTP 202 with fields
     ``ingestion_id``, ``records_received``, ``records_accepted``,
     ``records_rejected``.
  2. Malformed payload → HTTP 422 with per-field error details (FastAPI
     default; see design note in main.py about 400 vs 422).

Additional coverage:
  - Empty readings list → HTTP 202, all counters 0.
  - X-Trace-Id header is forwarded to ops-store calls.
  - ops-store unavailable → still returns HTTP 202 (graceful degradation).
  - /health endpoint returns HTTP 200 with status healthy.
  - X-Trace-Id header absent → service generates one internally.
  - Dependency injection: adapter can be replaced via dependency_overrides.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
from fastapi.testclient import TestClient

# conftest.py ensures sys.path contains the project root and service root
from app.adapters.rest_batch import RestBatchAdapter
from app.main import app, get_adapter, get_async_http_client
from app.normalizer import StubStoreClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_reading(
    asset_id: str = "PUMP-001",
    metric_name: str = "vibration_x",
    value: float = 0.0023,
    unit: str = "m/s2",
    source_protocol: str = "rest_batch",
) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "metric_name": metric_name,
        "value": value,
        "unit": unit,
        "source_protocol": source_protocol,
    }


def _make_stub_adapter() -> RestBatchAdapter:
    """Return a RestBatchAdapter backed by a StubStoreClient (no HTTP calls)."""
    return RestBatchAdapter(store_client=StubStoreClient())


def _make_noop_http_client() -> AsyncMock:
    """Return an async mock httpx client that responds 201 to all POSTs."""
    client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.status_code = 201
    client.post = AsyncMock(return_value=mock_response)
    return client


# ---------------------------------------------------------------------------
# Test client factory (overrides dependencies for isolation)
# ---------------------------------------------------------------------------


def _build_test_client() -> TestClient:
    """Build a TestClient with dependency overrides that avoid real HTTP calls.

    - get_adapter → stub adapter (no ops-store auto-registration HTTP calls)
    - get_async_http_client → async mock (no ops-store persistence HTTP calls)
    """
    app.dependency_overrides[get_adapter] = _make_stub_adapter
    app.dependency_overrides[get_async_http_client] = _make_noop_http_client
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# TASK-012 criterion 1: valid payload → HTTP 202 with required fields
# ---------------------------------------------------------------------------


class TestPostIngestValidPayload:
    """POST /internal/ingest with a valid payload MUST return HTTP 202."""

    def setup_method(self):
        self.client = _build_test_client()

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_single_reading_returns_202(self):
        """Core criterion 1a: single reading → HTTP 202."""
        payload = {"readings": [_valid_reading()]}
        response = self.client.post("/internal/ingest", json=payload)
        assert response.status_code == 202

    def test_response_contains_ingestion_id(self):
        """Core criterion 1b: response body has ingestion_id."""
        payload = {"readings": [_valid_reading()]}
        response = self.client.post("/internal/ingest", json=payload)
        data = response.json()
        assert "ingestion_id" in data
        # Must be a valid UUID string
        uuid.UUID(data["ingestion_id"])

    def test_response_contains_records_received(self):
        """Core criterion 1c: response body has records_received."""
        payload = {"readings": [_valid_reading(), _valid_reading()]}
        response = self.client.post("/internal/ingest", json=payload)
        data = response.json()
        assert "records_received" in data
        assert data["records_received"] == 2

    def test_response_contains_records_accepted(self):
        """Core criterion 1d: response body has records_accepted."""
        payload = {"readings": [_valid_reading()]}
        response = self.client.post("/internal/ingest", json=payload)
        data = response.json()
        assert "records_accepted" in data
        assert data["records_accepted"] == 1

    def test_response_contains_records_rejected(self):
        """Core criterion 1e: response body has records_rejected."""
        payload = {"readings": [_valid_reading()]}
        response = self.client.post("/internal/ingest", json=payload)
        data = response.json()
        assert "records_rejected" in data
        assert data["records_rejected"] == 0

    def test_multiple_readings_counters_correct(self):
        """Batch of 5 valid readings → records_received=5, accepted=5, rejected=0."""
        payload = {"readings": [_valid_reading() for _ in range(5)]}
        response = self.client.post("/internal/ingest", json=payload)
        data = response.json()
        assert data["records_received"] == 5
        assert data["records_accepted"] == 5
        assert data["records_rejected"] == 0

    def test_empty_readings_list_returns_202(self):
        """Empty batch is a valid no-op → HTTP 202, all counters 0."""
        payload = {"readings": []}
        response = self.client.post("/internal/ingest", json=payload)
        assert response.status_code == 202
        data = response.json()
        assert data["records_received"] == 0
        assert data["records_accepted"] == 0
        assert data["records_rejected"] == 0

    def test_all_required_fields_present_in_response(self):
        """All four required response fields are present simultaneously."""
        payload = {"readings": [_valid_reading()]}
        response = self.client.post("/internal/ingest", json=payload)
        data = response.json()
        for field in ("ingestion_id", "records_received", "records_accepted", "records_rejected"):
            assert field in data, f"Missing required field: {field}"


# ---------------------------------------------------------------------------
# TASK-012 criterion 2: malformed payload → HTTP 422
# ---------------------------------------------------------------------------


class TestPostIngestMalformedPayload:
    """POST /internal/ingest with malformed payload MUST return HTTP 422.

    Note (from tasks.md TASK-012 design decision):
        FastAPI/Pydantic v2 returns HTTP 422 (Unprocessable Entity) for
        request-body validation failures.  The spec mentions HTTP 400 but
        RFC 9110 § 15.5.16 makes 422 semantically correct.  This is
        intentional — ops-api (TASK-022) translates to 400 for external
        clients if needed.
    """

    def setup_method(self):
        self.client = _build_test_client()

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_missing_readings_field_returns_422(self):
        """Payload missing the 'readings' key → HTTP 422."""
        response = self.client.post("/internal/ingest", json={})
        assert response.status_code == 422

    def test_invalid_value_type_returns_422(self):
        """Reading with value='not-a-number' → HTTP 422."""
        reading = _valid_reading()
        reading["value"] = "not-a-number"
        response = self.client.post("/internal/ingest", json={"readings": [reading]})
        assert response.status_code == 422

    def test_missing_asset_id_returns_422(self):
        """Reading without asset_id → HTTP 422."""
        reading = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "metric_name": "vibration_x",
            "value": 0.001,
            "unit": "m/s2",
            "source_protocol": "rest_batch",
        }
        response = self.client.post("/internal/ingest", json={"readings": [reading]})
        assert response.status_code == 422

    def test_missing_timestamp_returns_422(self):
        """Reading without timestamp → HTTP 422."""
        reading = {
            "asset_id": "PUMP-001",
            "metric_name": "vibration_x",
            "value": 0.001,
            "unit": "m/s2",
            "source_protocol": "rest_batch",
        }
        response = self.client.post("/internal/ingest", json={"readings": [reading]})
        assert response.status_code == 422

    def test_naive_timestamp_returns_422(self):
        """Naive datetime (no timezone) → HTTP 422 (AwareDatetime required)."""
        reading = _valid_reading()
        reading["timestamp"] = "2026-05-17T10:00:00"  # no timezone offset
        response = self.client.post("/internal/ingest", json={"readings": [reading]})
        assert response.status_code == 422

    def test_422_response_has_detail_field(self):
        """HTTP 422 response body must contain a 'detail' field with errors."""
        reading = _valid_reading()
        reading["value"] = "bad-value"
        response = self.client.post("/internal/ingest", json={"readings": [reading]})
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
        assert isinstance(data["detail"], list)
        assert len(data["detail"]) > 0

    def test_completely_wrong_body_type_returns_422(self):
        """Sending a list instead of an object → HTTP 422."""
        response = self.client.post("/internal/ingest", json=[_valid_reading()])
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """GET /health must return HTTP 200 with service status."""

    def setup_method(self):
        self.client = _build_test_client()

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_health_returns_200(self):
        """GET /health → HTTP 200."""
        response = self.client.get("/health")
        assert response.status_code == 200

    def test_health_returns_healthy_status(self):
        """GET /health → body contains status=healthy."""
        response = self.client.get("/health")
        data = response.json()
        assert data["status"] == "healthy"

    def test_health_returns_service_name(self):
        """GET /health → body contains service=ops-ingest."""
        response = self.client.get("/health")
        data = response.json()
        assert data["service"] == "ops-ingest"


# ---------------------------------------------------------------------------
# Trace-id propagation
# ---------------------------------------------------------------------------


class TestTraceIdPropagation:
    """X-Trace-Id from inbound request MUST be propagated to ops-store."""

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_inbound_trace_id_forwarded_to_ops_store(self):
        """X-Trace-Id on request → forwarded in ops-store POST header."""
        captured_headers: list[dict] = []

        async def mock_http_client():  # noqa: ANN201
            client = AsyncMock(spec=httpx.AsyncClient)

            async def capture_post(url, json=None, headers=None, **kwargs):  # noqa: ANN001, ANN201
                if headers:
                    captured_headers.append(dict(headers))
                mock_resp = MagicMock()
                mock_resp.status_code = 201
                return mock_resp

            client.post = capture_post
            yield client

        app.dependency_overrides[get_adapter] = _make_stub_adapter
        app.dependency_overrides[get_async_http_client] = mock_http_client

        client = TestClient(app)
        trace_id = "test-trace-abc-123"
        payload = {"readings": [_valid_reading()]}
        client.post(
            "/internal/ingest",
            json=payload,
            headers={"X-Trace-Id": trace_id},
        )

        # At least one outbound call must carry the trace_id header.
        assert any(
            h.get("X-Trace-Id") == trace_id for h in captured_headers
        ), f"Expected X-Trace-Id={trace_id!r} in outbound headers; got: {captured_headers}"

    def test_missing_trace_id_does_not_crash(self):
        """No X-Trace-Id header → service generates one, returns 202."""
        app.dependency_overrides[get_adapter] = _make_stub_adapter
        app.dependency_overrides[get_async_http_client] = _make_noop_http_client

        client = TestClient(app)
        payload = {"readings": [_valid_reading()]}
        response = client.post("/internal/ingest", json=payload)
        assert response.status_code == 202


# ---------------------------------------------------------------------------
# Graceful degradation: ops-store unreachable
# ---------------------------------------------------------------------------


class TestOpsStoreGracefulDegradation:
    """If ops-store is unreachable, ops-ingest MUST still return HTTP 202."""

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_ops_store_network_error_still_returns_202(self):
        """ops-store raises RequestError → HTTP 202 returned (degraded mode)."""

        async def failing_http_client():  # noqa: ANN201
            client = AsyncMock(spec=httpx.AsyncClient)
            client.post = AsyncMock(
                side_effect=httpx.RequestError("connection refused")
            )
            yield client

        app.dependency_overrides[get_adapter] = _make_stub_adapter
        app.dependency_overrides[get_async_http_client] = failing_http_client

        test_client = TestClient(app, raise_server_exceptions=False)
        payload = {"readings": [_valid_reading()]}
        response = test_client.post("/internal/ingest", json=payload)
        assert response.status_code == 202

    def test_ops_store_5xx_still_returns_202(self):
        """ops-store returns 500 → HTTP 202 still returned (degraded mode)."""

        async def error_http_client():  # noqa: ANN201
            client = AsyncMock(spec=httpx.AsyncClient)
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_resp.text = "Internal Server Error"
            client.post = AsyncMock(return_value=mock_resp)
            yield client

        app.dependency_overrides[get_adapter] = _make_stub_adapter
        app.dependency_overrides[get_async_http_client] = error_http_client

        test_client = TestClient(app, raise_server_exceptions=False)
        payload = {"readings": [_valid_reading()]}
        response = test_client.post("/internal/ingest", json=payload)
        assert response.status_code == 202
