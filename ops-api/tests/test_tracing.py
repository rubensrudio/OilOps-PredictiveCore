"""
tests/test_tracing.py
======================
Integration tests for TracingMiddleware (TASK-021).

Criteria verified (from tasks.md TASK-021):
  - Request without ``X-Trace-Id`` → response contains a header with a
    generated UUID.
  - Request with ``X-Trace-Id: abc`` → response re-propagates the same value.
  - The trace_id is available via ``shared.logging_config.get_trace_id()``
    inside the request context (verified via a test endpoint).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ops_api.app.middleware.tracing import TracingMiddleware


# ---------------------------------------------------------------------------
# Fixture: minimal FastAPI app with TracingMiddleware registered
# ---------------------------------------------------------------------------


@pytest.fixture()
def tracing_app() -> FastAPI:
    """Return a minimal FastAPI app that only has TracingMiddleware."""
    _app = FastAPI()
    _app.add_middleware(TracingMiddleware)

    @_app.get("/echo-trace")
    async def _echo_trace() -> dict[str, str]:
        """Return the current trace_id visible inside the request context."""
        from shared.logging_config import get_trace_id

        return {"trace_id": get_trace_id()}

    @_app.get("/ok")
    async def _ok() -> dict[str, str]:
        return {"status": "ok"}

    return _app


@pytest.fixture()
def client(tracing_app: FastAPI) -> TestClient:
    return TestClient(tracing_app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTracingMiddleware:
    """Verify trace_id generation and propagation behaviour."""

    def test_response_contains_trace_id_header_when_absent_in_request(
        self, client: TestClient
    ) -> None:
        """When request has no X-Trace-Id, response must include a generated UUID."""
        response = client.get("/ok")
        assert response.status_code == 200
        trace_id = response.headers.get("x-trace-id")
        assert trace_id is not None
        # Must be a valid UUID4
        parsed = uuid.UUID(trace_id)
        assert parsed.version == 4

    def test_response_propagates_existing_trace_id(
        self, client: TestClient
    ) -> None:
        """When request carries X-Trace-Id, the same value must appear in response."""
        custom_trace = "abc-custom-trace-123"
        response = client.get("/ok", headers={"X-Trace-Id": custom_trace})
        assert response.status_code == 200
        assert response.headers.get("x-trace-id") == custom_trace

    def test_generated_trace_ids_are_unique_per_request(
        self, client: TestClient
    ) -> None:
        """Each request without X-Trace-Id must receive a distinct UUID."""
        trace_1 = client.get("/ok").headers.get("x-trace-id")
        trace_2 = client.get("/ok").headers.get("x-trace-id")
        assert trace_1 is not None
        assert trace_2 is not None
        assert trace_1 != trace_2

    def test_trace_id_propagated_to_contextvar(
        self, client: TestClient
    ) -> None:
        """trace_id must be available via get_trace_id() inside the handler."""
        custom_trace = "ctx-prop-test-456"
        response = client.get(
            "/echo-trace", headers={"X-Trace-Id": custom_trace}
        )
        assert response.status_code == 200
        assert response.json()["trace_id"] == custom_trace

    def test_generated_trace_id_propagated_to_contextvar(
        self, client: TestClient
    ) -> None:
        """Auto-generated trace_id must also be visible in the handler context."""
        response = client.get("/echo-trace")
        assert response.status_code == 200
        body_trace = response.json()["trace_id"]
        header_trace = response.headers.get("x-trace-id")
        assert body_trace == header_trace
        # Must be a valid UUID
        uuid.UUID(body_trace)

    def test_main_app_injects_trace_id_header(self) -> None:
        """The production ops-api app must include X-Trace-Id in responses."""
        from ops_api.app.main import app as prod_app

        with TestClient(prod_app) as prod_client:
            response = prod_client.get("/")
            assert "x-trace-id" in response.headers

    def test_main_app_propagates_custom_trace_id(self) -> None:
        """Production app must echo back a custom X-Trace-Id header."""
        from ops_api.app.main import app as prod_app

        with TestClient(prod_app) as prod_client:
            response = prod_client.get(
                "/", headers={"X-Trace-Id": "custom-prod-trace"}
            )
            assert response.headers.get("x-trace-id") == "custom-prod-trace"
