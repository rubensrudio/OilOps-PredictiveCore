"""
tests/test_audit_router.py
============================
Contract tests for GET /audit (TASK-027).

Criteria verified (from tasks.md TASK-027):
  - GET /audit?asset_id=PUMP-001&page=1&page_size=10 returns HTTP 200
    with JSON body containing ``events``, ``total``, ``page``, ``page_size``
    (INIT-US-08-AC2).
  - GET /audit without query params returns HTTP 200 with the same four fields.
  - When ops-store returns HTTP 502, the endpoint returns HTTP 502.
  - Response carries the X-Advisory-Only: true header (RN-06).

Design notes
------------
* The audit router delegates to ``ops-store`` via httpx.  In tests the
  ``get_audit_client`` dependency is overridden to inject a mock that never
  makes real network calls.

* A minimal FastAPI app is built in each helper so the audit router runs in
  isolation from unrelated routers.

* ``_FakeHttpxResponse`` mirrors the shape used in sibling test modules
  (test_health_router.py, test_explain_router.py) to keep the test layer
  consistent.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_with_audit() -> FastAPI:
    """Return a minimal FastAPI app containing only the audit router and middlewares."""
    from ops_api.app.middleware.advisory import AdvisoryMiddleware
    from ops_api.app.middleware.tracing import TracingMiddleware
    from ops_api.app.routers.audit import router as audit_router

    _app = FastAPI()
    _app.add_middleware(AdvisoryMiddleware)
    _app.add_middleware(TracingMiddleware)
    _app.include_router(audit_router)
    return _app


class _FakeHttpxResponse:
    """Minimal stand-in for httpx.Response returned by the mocked client."""

    def __init__(self, status_code: int, data: dict | None = None) -> None:
        self.status_code = status_code
        self._data = data or {}

    def json(self) -> dict:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=MagicMock(),
                response=MagicMock(status_code=self.status_code),
            )


# Canonical sample audit response returned by a healthy ops-store.
_SAMPLE_AUDIT_RESPONSE: dict = {
    "events": [
        {
            "prediction_id": "11111111-1111-1111-1111-111111111111",
            "asset_id": "PUMP-001",
            "model_version": "vibration-autoencoder-v1",
            "triggered_at": "2026-05-16T10:01:30Z",
            "confidence_score": 0.92,
            "trace_id": "abc123",
        }
    ],
    "total": 1,
    "page": 1,
    "page_size": 10,
}

_EMPTY_AUDIT_RESPONSE: dict = {
    "events": [],
    "total": 0,
    "page": 1,
    "page_size": 10,
}


def _make_mock_client(
    status_code: int = 200,
    response_data: dict | None = None,
) -> AsyncMock:
    """Build a mock httpx.AsyncClient that returns the configured response."""
    data = response_data if response_data is not None else _SAMPLE_AUDIT_RESPONSE
    fake_response = _FakeHttpxResponse(status_code=status_code, data=data)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=fake_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ---------------------------------------------------------------------------
# Tests: GET /audit with query params
# ---------------------------------------------------------------------------


class TestGetAuditWithParams:
    """Tests for GET /audit?asset_id=PUMP-001&page=1&page_size=10."""

    def _client_with_mock(
        self,
        status_code: int = 200,
        response_data: dict | None = None,
    ) -> TestClient:
        from ops_api.app.routers.audit import get_audit_client

        mock_client = _make_mock_client(status_code, response_data)
        _app = _make_app_with_audit()
        _app.dependency_overrides[get_audit_client] = lambda: mock_client
        return TestClient(_app)

    def test_with_params_returns_200(self) -> None:
        """GET /audit with asset_id + page + page_size MUST return HTTP 200."""
        client = self._client_with_mock()
        response = client.get("/audit?asset_id=PUMP-001&page=1&page_size=10")
        assert response.status_code == 200

    def test_with_params_body_has_events(self) -> None:
        """Response body MUST contain an ``events`` key."""
        client = self._client_with_mock()
        body = client.get("/audit?asset_id=PUMP-001&page=1&page_size=10").json()
        assert "events" in body

    def test_with_params_body_has_total(self) -> None:
        """Response body MUST contain a ``total`` key."""
        client = self._client_with_mock()
        body = client.get("/audit?asset_id=PUMP-001&page=1&page_size=10").json()
        assert "total" in body

    def test_with_params_body_has_page(self) -> None:
        """Response body MUST contain a ``page`` key."""
        client = self._client_with_mock()
        body = client.get("/audit?asset_id=PUMP-001&page=1&page_size=10").json()
        assert "page" in body

    def test_with_params_body_has_page_size(self) -> None:
        """Response body MUST contain a ``page_size`` key."""
        client = self._client_with_mock()
        body = client.get("/audit?asset_id=PUMP-001&page=1&page_size=10").json()
        assert "page_size" in body

    def test_with_params_all_required_fields_present(self) -> None:
        """Single aggregated check: events, total, page, page_size all present."""
        client = self._client_with_mock()
        body = client.get("/audit?asset_id=PUMP-001&page=1&page_size=10").json()
        for field in ("events", "total", "page", "page_size"):
            assert field in body, f"Required field '{field}' missing from /audit response."

    def test_with_params_events_is_list(self) -> None:
        """``events`` MUST be a list."""
        client = self._client_with_mock()
        body = client.get("/audit?asset_id=PUMP-001&page=1&page_size=10").json()
        assert isinstance(body["events"], list)

    def test_advisory_header_present_with_params(self) -> None:
        """GET /audit MUST carry X-Advisory-Only: true (RN-06)."""
        client = self._client_with_mock()
        response = client.get("/audit?asset_id=PUMP-001&page=1&page_size=10")
        assert response.headers.get("x-advisory-only") == "true"


# ---------------------------------------------------------------------------
# Tests: GET /audit without query params (defaults)
# ---------------------------------------------------------------------------


class TestGetAuditWithoutParams:
    """Tests for GET /audit with no query parameters (all defaults)."""

    def _client_with_mock(self) -> TestClient:
        from ops_api.app.routers.audit import get_audit_client

        mock_client = _make_mock_client(200, _EMPTY_AUDIT_RESPONSE)
        _app = _make_app_with_audit()
        _app.dependency_overrides[get_audit_client] = lambda: mock_client
        return TestClient(_app)

    def test_without_params_returns_200(self) -> None:
        """GET /audit without params MUST return HTTP 200."""
        client = self._client_with_mock()
        response = client.get("/audit")
        assert response.status_code == 200

    def test_without_params_all_required_fields_present(self) -> None:
        """Response body MUST contain events, total, page, page_size."""
        client = self._client_with_mock()
        body = client.get("/audit").json()
        for field in ("events", "total", "page", "page_size"):
            assert field in body, f"Required field '{field}' missing from /audit response."

    def test_without_params_events_is_list(self) -> None:
        """``events`` MUST be a list (empty when ops-store returns empty)."""
        client = self._client_with_mock()
        body = client.get("/audit").json()
        assert isinstance(body["events"], list)

    def test_advisory_header_present_without_params(self) -> None:
        """X-Advisory-Only: true MUST be present even when no params are supplied."""
        client = self._client_with_mock()
        response = client.get("/audit")
        assert response.headers.get("x-advisory-only") == "true"


# ---------------------------------------------------------------------------
# Tests: ops-store error propagation — HTTP 502
# ---------------------------------------------------------------------------


class TestGetAuditUpstreamError:
    """Tests for error scenarios when ops-store is unreachable or returns an error."""

    def _client_with_status(self, status_code: int) -> TestClient:
        """Return a TestClient whose ops-store mock returns the given status code."""
        from ops_api.app.routers.audit import get_audit_client

        mock_client = _make_mock_client(status_code, {"detail": "error"})
        _app = _make_app_with_audit()
        _app.dependency_overrides[get_audit_client] = lambda: mock_client
        return TestClient(_app)

    def _client_with_network_error(self) -> TestClient:
        """Return a TestClient whose httpx call raises a RequestError."""
        from ops_api.app.routers.audit import get_audit_client

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        _app = _make_app_with_audit()
        _app.dependency_overrides[get_audit_client] = lambda: mock_client
        return TestClient(_app)

    def test_ops_store_502_returns_502(self) -> None:
        """ops-store returning 502 MUST propagate as HTTP 502 to caller."""
        client = self._client_with_status(502)
        response = client.get("/audit")
        assert response.status_code == 502

    def test_ops_store_500_returns_502(self) -> None:
        """Any non-200 ops-store response MUST result in HTTP 502."""
        client = self._client_with_status(500)
        response = client.get("/audit")
        assert response.status_code == 502

    def test_ops_store_503_returns_502(self) -> None:
        """ops-store 503 (starting / unavailable) MUST result in HTTP 502."""
        client = self._client_with_status(503)
        response = client.get("/audit")
        assert response.status_code == 502

    def test_network_error_returns_502(self) -> None:
        """ops-store unreachable (network error) MUST result in HTTP 502."""
        client = self._client_with_network_error()
        response = client.get("/audit")
        assert response.status_code == 502

    def test_advisory_header_present_on_502(self) -> None:
        """HTTP 502 response MUST also carry X-Advisory-Only: true (RN-06)."""
        client = self._client_with_status(500)
        response = client.get("/audit")
        assert response.headers.get("x-advisory-only") == "true"

    def test_502_response_has_detail(self) -> None:
        """HTTP 502 response MUST include a ``detail`` field."""
        client = self._client_with_status(500)
        body = client.get("/audit").json()
        assert "detail" in body


# ---------------------------------------------------------------------------
# Tests: query parameter forwarding
# ---------------------------------------------------------------------------


class TestGetAuditParamForwarding:
    """Tests that query parameters are forwarded to ops-store correctly."""

    def test_asset_id_forwarded_to_upstream(self) -> None:
        """``asset_id`` query param MUST be forwarded to ops-store."""
        from ops_api.app.routers.audit import get_audit_client

        captured_params: dict = {}

        async def _capturing_get(
            url: str,
            params: dict | None = None,
            headers: dict | None = None,
            **kwargs: object,
        ) -> _FakeHttpxResponse:
            if params:
                captured_params.update(params)
            return _FakeHttpxResponse(200, _SAMPLE_AUDIT_RESPONSE)

        mock_client = AsyncMock()
        mock_client.get = _capturing_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        _app = _make_app_with_audit()
        _app.dependency_overrides[get_audit_client] = lambda: mock_client

        with TestClient(_app) as tc:
            tc.get("/audit?asset_id=PUMP-001")

        assert captured_params.get("asset_id") == "PUMP-001", (
            f"Expected asset_id='PUMP-001' in upstream params, got: {captured_params}"
        )

    def test_page_and_page_size_forwarded_to_upstream(self) -> None:
        """``page`` and ``page_size`` MUST be forwarded to ops-store."""
        from ops_api.app.routers.audit import get_audit_client

        captured_params: dict = {}

        async def _capturing_get(
            url: str,
            params: dict | None = None,
            headers: dict | None = None,
            **kwargs: object,
        ) -> _FakeHttpxResponse:
            if params:
                captured_params.update(params)
            return _FakeHttpxResponse(200, _EMPTY_AUDIT_RESPONSE)

        mock_client = AsyncMock()
        mock_client.get = _capturing_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        _app = _make_app_with_audit()
        _app.dependency_overrides[get_audit_client] = lambda: mock_client

        with TestClient(_app) as tc:
            tc.get("/audit?page=3&page_size=25")

        assert captured_params.get("page") == 3
        assert captured_params.get("page_size") == 25
