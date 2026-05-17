"""
tests/test_health_router.py
============================
Contract tests for GET /health (TASK-026).

Criteria verified (from tasks.md TASK-026):
  - GET /health with all services mocked as healthy MUST return HTTP 200
    with ``status: healthy`` and each service status ``healthy`` in
    ``services`` dict.
  - GET /health with ``ops-models`` mocked as returning HTTP 503 MUST
    return HTTP 503 with ``status: starting`` and
    ``services["ops-models"] == "starting"``.
  - Response body MUST include ``checked_at`` ISO-8601 timestamp.
  - Response carries the X-Advisory-Only: true header (via middleware).

Design notes
------------
* The route handler makes parallel httpx calls (asyncio.gather) to each
  dependent service's ``GET /health`` endpoint.  In tests we override the
  ``get_health_client`` dependency to inject a mock whose ``get`` coroutine
  returns pre-configured fake responses — one per downstream service URL.
* The mock uses a lookup table keyed by URL so individual services can be
  made to return different statuses independently.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_with_health() -> FastAPI:
    """Return a minimal FastAPI app that includes only the health router."""
    from ops_api.app.middleware.advisory import AdvisoryMiddleware
    from ops_api.app.middleware.tracing import TracingMiddleware
    from ops_api.app.routers.health import router as health_router

    _app = FastAPI()
    _app.add_middleware(AdvisoryMiddleware)
    _app.add_middleware(TracingMiddleware)
    _app.include_router(health_router)
    return _app


_ALL_SERVICES = [
    "ops-ingest",
    "ops-store",
    "ops-feature",
    "ops-models",
    "ops-explain",
]

_SERVICE_DEFAULT_PORTS = {
    "ops-ingest": 8001,
    "ops-store": 8002,
    "ops-feature": 8003,
    "ops-models": 8004,
    "ops-explain": 8005,
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


def _make_mock_client(service_statuses: dict[str, int]) -> AsyncMock:
    """Build a mock httpx.AsyncClient where each service's /health returns
    the configured status code.

    Parameters
    ----------
    service_statuses:
        Mapping of service name → HTTP status code to return for that
        service's ``GET /health`` call.  Services not listed default to 200.
    """
    # Build a lookup from full URL to status code.
    url_to_status: dict[str, int] = {}
    for svc, port in _SERVICE_DEFAULT_PORTS.items():
        code = service_statuses.get(svc, 200)
        url = f"http://{svc}:{port}/health"
        url_to_status[url] = code

    async def _fake_get(url: str, **kwargs: object) -> _FakeHttpxResponse:
        code = url_to_status.get(url, 200)
        if code == 200:
            return _FakeHttpxResponse(200, {"status": "healthy"})
        return _FakeHttpxResponse(code, {"status": "starting"})

    mock_client = AsyncMock()
    mock_client.get = _fake_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ---------------------------------------------------------------------------
# Tests: GET /health
# ---------------------------------------------------------------------------


class TestGetHealth:
    """Contract tests for the GET /health endpoint (TASK-026)."""

    def _client_with_mock_services(
        self,
        service_statuses: dict[str, int] | None = None,
    ) -> TestClient:
        """Return a TestClient whose per-service httpx calls are mocked.

        Parameters
        ----------
        service_statuses:
            Mapping of service name → HTTP status code.  Defaults to all
            services returning 200 (healthy).
        """
        from ops_api.app.routers.health import get_health_client

        statuses = service_statuses or {}
        mock_client = _make_mock_client(statuses)

        _app = _make_app_with_health()
        _app.dependency_overrides[get_health_client] = lambda: mock_client
        return TestClient(_app)

    # ------------------------------------------------------------------
    # Happy path — all services healthy
    # ------------------------------------------------------------------

    def test_all_healthy_returns_200(self) -> None:
        """GET /health with all services healthy MUST return HTTP 200."""
        client = self._client_with_mock_services()
        response = client.get("/health")
        assert response.status_code == 200

    def test_all_healthy_status_is_healthy(self) -> None:
        """Response body ``status`` MUST be ``healthy`` when all services are up."""
        client = self._client_with_mock_services()
        body = client.get("/health").json()
        assert body["status"] == "healthy"

    def test_all_healthy_services_dict_contains_all_services(self) -> None:
        """Response body ``services`` dict MUST contain all five service keys."""
        client = self._client_with_mock_services()
        body = client.get("/health").json()
        for svc in _ALL_SERVICES:
            assert svc in body["services"], f"Service '{svc}' missing from services dict"

    def test_all_healthy_each_service_status_is_healthy(self) -> None:
        """Each service entry in ``services`` MUST be ``healthy``."""
        client = self._client_with_mock_services()
        body = client.get("/health").json()
        for svc in _ALL_SERVICES:
            assert body["services"][svc] == "healthy", (
                f"Service '{svc}' expected 'healthy', got '{body['services'][svc]}'"
            )

    def test_all_healthy_response_contains_checked_at(self) -> None:
        """Response body MUST include a ``checked_at`` ISO-8601 timestamp."""
        client = self._client_with_mock_services()
        body = client.get("/health").json()
        assert "checked_at" in body
        # Verify it parses as a valid ISO-8601 datetime.
        parsed = datetime.fromisoformat(body["checked_at"])
        assert parsed.tzinfo is not None  # Must be timezone-aware (UTC).

    def test_advisory_header_present_on_200(self) -> None:
        """HTTP 200 response MUST carry X-Advisory-Only: true (RN-06)."""
        client = self._client_with_mock_services()
        response = client.get("/health")
        assert response.headers.get("x-advisory-only") == "true"

    # ------------------------------------------------------------------
    # Edge case — one service returning 503 (starting)
    # ------------------------------------------------------------------

    def test_ops_models_503_returns_http_503(self) -> None:
        """When ops-models returns 503, GET /health MUST return HTTP 503."""
        client = self._client_with_mock_services({"ops-models": 503})
        response = client.get("/health")
        assert response.status_code == 503

    def test_ops_models_503_overall_status_is_starting(self) -> None:
        """When ops-models returns 503, overall ``status`` MUST be ``starting``."""
        client = self._client_with_mock_services({"ops-models": 503})
        body = client.get("/health").json()
        assert body["status"] == "starting"

    def test_ops_models_503_models_service_is_starting(self) -> None:
        """When ops-models returns 503, ``services['ops-models']`` MUST be ``starting``."""
        client = self._client_with_mock_services({"ops-models": 503})
        body = client.get("/health").json()
        assert body["services"]["ops-models"] == "starting"

    def test_ops_models_503_other_services_remain_healthy(self) -> None:
        """When only ops-models is unhealthy, other services MUST still show ``healthy``."""
        client = self._client_with_mock_services({"ops-models": 503})
        body = client.get("/health").json()
        for svc in _ALL_SERVICES:
            if svc == "ops-models":
                continue
            assert body["services"][svc] == "healthy", (
                f"Service '{svc}' expected 'healthy', got '{body['services'][svc]}'"
            )

    def test_503_response_contains_checked_at(self) -> None:
        """503 response body MUST also include ``checked_at`` timestamp."""
        client = self._client_with_mock_services({"ops-models": 503})
        body = client.get("/health").json()
        assert "checked_at" in body

    def test_advisory_header_present_on_503(self) -> None:
        """HTTP 503 response MUST also carry X-Advisory-Only: true (RN-06)."""
        client = self._client_with_mock_services({"ops-models": 503})
        response = client.get("/health")
        assert response.headers.get("x-advisory-only") == "true"

    # ------------------------------------------------------------------
    # Edge case — connection failure acts like "starting"
    # ------------------------------------------------------------------

    def test_connection_error_service_treated_as_starting(self) -> None:
        """When a service raises httpx.RequestError, it MUST be treated as ``starting``."""
        import httpx

        from ops_api.app.routers.health import get_health_client

        # Override _fake_get to raise RequestError for ops-ingest.
        async def _failing_get(url: str, **kwargs: object) -> _FakeHttpxResponse:
            if "ops-ingest" in url:
                raise httpx.ConnectError("connection refused", request=MagicMock())
            return _FakeHttpxResponse(200, {"status": "healthy"})

        mock_client = AsyncMock()
        mock_client.get = _failing_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        _app = _make_app_with_health()
        _app.dependency_overrides[get_health_client] = lambda: mock_client
        tc = TestClient(_app)

        response = tc.get("/health")
        assert response.status_code == 503
        body = response.json()
        assert body["services"]["ops-ingest"] == "starting"
        assert body["status"] == "starting"

    # ------------------------------------------------------------------
    # All services unhealthy
    # ------------------------------------------------------------------

    def test_all_services_503_returns_503(self) -> None:
        """When all services return 503, GET /health MUST return HTTP 503."""
        all_down = {svc: 503 for svc in _ALL_SERVICES}
        client = self._client_with_mock_services(all_down)
        response = client.get("/health")
        assert response.status_code == 503

    def test_all_services_503_all_entries_are_starting(self) -> None:
        """When all services are down, every entry in ``services`` MUST be ``starting``."""
        all_down = {svc: 503 for svc in _ALL_SERVICES}
        client = self._client_with_mock_services(all_down)
        body = client.get("/health").json()
        for svc in _ALL_SERVICES:
            assert body["services"][svc] == "starting"
