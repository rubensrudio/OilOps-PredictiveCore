"""
tests/test_explain_router.py
==============================
Contract tests for GET /explain/{prediction_id} (TASK-023).

Criteria verified (from tasks.md TASK-023):
  - GET /explain/{id} with ``explain_status=ready`` returns HTTP 200 with
    ``feature_attributions`` containing >= 5 items (INIT-10).
  - GET /explain/{id} with ``explain_status=pending`` returns HTTP 202 with
    ``retry_after`` field (INIT-11).
  - GET /explain/{id} for unknown prediction_id returns HTTP 404.
  - Response carries the X-Advisory-Only: true header (RN-06 / CAT-04).
  - X-Trace-Id is propagated to the upstream ops-explain call (CAT-08).
  - ops-explain unreachable returns HTTP 502.

Design notes
------------
* The route handler delegates to ops-explain via httpx.  In tests we
  override the ``get_explain_client`` dependency to inject a mock that never
  makes real network calls.
* A minimal FastAPI app is built for each test class to isolate the explain
  router without loading unrelated routers.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_with_explain() -> FastAPI:
    """Return a minimal FastAPI app with only the explain router + middlewares."""
    from ops_api.app.middleware.advisory import AdvisoryMiddleware
    from ops_api.app.middleware.tracing import TracingMiddleware
    from ops_api.app.routers.explain import router as explain_router

    _app = FastAPI()
    _app.add_middleware(AdvisoryMiddleware)
    _app.add_middleware(TracingMiddleware)
    _app.include_router(explain_router)
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


# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

_PREDICTION_ID = str(uuid.uuid4())

_READY_RESPONSE: dict = {
    "prediction_id": _PREDICTION_ID,
    "method": "shap",
    "feature_attributions": [
        {"feature_name": "fft_bin_12", "attribution_value": 0.43, "rank": 1},
        {"feature_name": "rms", "attribution_value": 0.31, "rank": 2},
        {"feature_name": "kurtosis", "attribution_value": 0.18, "rank": 3},
        {"feature_name": "fft_bin_28", "attribution_value": 0.14, "rank": 4},
        {"feature_name": "variance", "attribution_value": 0.09, "rank": 5},
    ],
    "baseline_window": {
        "start": "2026-05-09T10:00:00Z",
        "end": "2026-05-16T10:00:00Z",
        "stats_per_feature": {
            "rms": {"mean": 0.0012, "std": 0.0003, "p5": 0.0008, "p95": 0.0018}
        },
    },
}

_PENDING_RESPONSE: dict = {
    "prediction_id": _PREDICTION_ID,
    "explain_status": "pending",
    "retry_after": 30,
}


# ---------------------------------------------------------------------------
# Tests: GET /explain/{prediction_id} — ready (HTTP 200)
# ---------------------------------------------------------------------------


class TestGetExplainReady:
    """Tests for the explain_status=ready path (HTTP 200)."""

    def _client_with_mock_explain(
        self,
        status_code: int = 200,
        response_data: dict | None = None,
    ) -> TestClient:
        """Return a TestClient whose ops-explain httpx call is mocked."""
        from ops_api.app.routers.explain import get_explain_client

        data = response_data if response_data is not None else _READY_RESPONSE
        fake_response = _FakeHttpxResponse(status_code=status_code, data=data)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        _app = _make_app_with_explain()
        _app.dependency_overrides[get_explain_client] = lambda: mock_client
        return TestClient(_app)

    def test_ready_returns_200(self) -> None:
        """GET /explain/{id} with explain_status=ready MUST return HTTP 200."""
        client = self._client_with_mock_explain()
        response = client.get(f"/explain/{_PREDICTION_ID}")
        assert response.status_code == 200

    def test_ready_response_contains_feature_attributions(self) -> None:
        """Response MUST include ``feature_attributions`` key."""
        client = self._client_with_mock_explain()
        body = client.get(f"/explain/{_PREDICTION_ID}").json()
        assert "feature_attributions" in body

    def test_ready_feature_attributions_has_at_least_five_items(self) -> None:
        """``feature_attributions`` MUST contain >= 5 items (INIT-10 / CAT-05)."""
        client = self._client_with_mock_explain()
        body = client.get(f"/explain/{_PREDICTION_ID}").json()
        assert len(body["feature_attributions"]) >= 5

    def test_ready_each_attribution_has_required_fields(self) -> None:
        """Each item in ``feature_attributions`` MUST have feature_name,
        attribution_value, and rank."""
        client = self._client_with_mock_explain()
        body = client.get(f"/explain/{_PREDICTION_ID}").json()
        for item in body["feature_attributions"]:
            assert "feature_name" in item
            assert "attribution_value" in item
            assert "rank" in item

    def test_ready_advisory_header_present(self) -> None:
        """HTTP 200 response MUST carry X-Advisory-Only: true (RN-06)."""
        client = self._client_with_mock_explain()
        response = client.get(f"/explain/{_PREDICTION_ID}")
        assert response.headers.get("x-advisory-only") == "true"

    def test_ready_contains_prediction_id(self) -> None:
        """Response body MUST contain the requested prediction_id."""
        client = self._client_with_mock_explain()
        body = client.get(f"/explain/{_PREDICTION_ID}").json()
        assert body["prediction_id"] == _PREDICTION_ID


# ---------------------------------------------------------------------------
# Tests: GET /explain/{prediction_id} — pending (HTTP 202)
# ---------------------------------------------------------------------------


class TestGetExplainPending:
    """Tests for the explain_status=pending path (HTTP 202)."""

    def _client_with_mock_explain_pending(self) -> TestClient:
        """Return a TestClient that always returns HTTP 202 (pending)."""
        from ops_api.app.routers.explain import get_explain_client

        fake_response = _FakeHttpxResponse(
            status_code=202, data=_PENDING_RESPONSE
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        _app = _make_app_with_explain()
        _app.dependency_overrides[get_explain_client] = lambda: mock_client
        return TestClient(_app)

    def test_pending_returns_202(self) -> None:
        """GET /explain/{id} with explain_status=pending MUST return HTTP 202."""
        client = self._client_with_mock_explain_pending()
        response = client.get(f"/explain/{_PREDICTION_ID}")
        assert response.status_code == 202

    def test_pending_response_contains_retry_after(self) -> None:
        """HTTP 202 body MUST contain ``retry_after`` (INIT-11)."""
        client = self._client_with_mock_explain_pending()
        body = client.get(f"/explain/{_PREDICTION_ID}").json()
        assert "retry_after" in body

    def test_pending_retry_after_is_positive(self) -> None:
        """``retry_after`` MUST be a positive integer."""
        client = self._client_with_mock_explain_pending()
        body = client.get(f"/explain/{_PREDICTION_ID}").json()
        assert isinstance(body["retry_after"], int)
        assert body["retry_after"] > 0

    def test_pending_advisory_header_present(self) -> None:
        """HTTP 202 response MUST carry X-Advisory-Only: true (RN-06)."""
        client = self._client_with_mock_explain_pending()
        response = client.get(f"/explain/{_PREDICTION_ID}")
        assert response.headers.get("x-advisory-only") == "true"

    def test_pending_response_contains_explain_status(self) -> None:
        """HTTP 202 body MUST contain ``explain_status`` field."""
        client = self._client_with_mock_explain_pending()
        body = client.get(f"/explain/{_PREDICTION_ID}").json()
        assert "explain_status" in body
        assert body["explain_status"] == "pending"


# ---------------------------------------------------------------------------
# Tests: GET /explain/{prediction_id} — not found (HTTP 404)
# ---------------------------------------------------------------------------


class TestGetExplainNotFound:
    """Tests for the not-found path (HTTP 404)."""

    def _client_with_mock_explain_404(self) -> TestClient:
        """Return a TestClient that always returns HTTP 404."""
        from ops_api.app.routers.explain import get_explain_client

        fake_response = _FakeHttpxResponse(
            status_code=404,
            data={"detail": f"No explanation found for prediction_id '{_PREDICTION_ID}'."},
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        _app = _make_app_with_explain()
        _app.dependency_overrides[get_explain_client] = lambda: mock_client
        return TestClient(_app)

    def test_unknown_prediction_returns_404(self) -> None:
        """GET /explain/{unknown_id} MUST return HTTP 404."""
        client = self._client_with_mock_explain_404()
        response = client.get(f"/explain/{_PREDICTION_ID}")
        assert response.status_code == 404

    def test_404_response_contains_detail(self) -> None:
        """404 response MUST include a ``detail`` field."""
        client = self._client_with_mock_explain_404()
        body = client.get(f"/explain/{_PREDICTION_ID}").json()
        assert "detail" in body

    def test_advisory_header_present_on_404(self) -> None:
        """HTTP 404 response MUST carry X-Advisory-Only: true (RN-06)."""
        client = self._client_with_mock_explain_404()
        response = client.get(f"/explain/{_PREDICTION_ID}")
        assert response.headers.get("x-advisory-only") == "true"


# ---------------------------------------------------------------------------
# Tests: upstream failures (HTTP 502)
# ---------------------------------------------------------------------------


class TestGetExplainUpstreamFailure:
    """Tests for upstream error / unreachable scenarios (HTTP 502)."""

    def _client_with_network_error(self) -> TestClient:
        """Return a TestClient whose httpx call raises a RequestError."""
        from ops_api.app.routers.explain import get_explain_client

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        _app = _make_app_with_explain()
        _app.dependency_overrides[get_explain_client] = lambda: mock_client
        return TestClient(_app)

    def _client_with_upstream_500(self) -> TestClient:
        """Return a TestClient whose ops-explain returns HTTP 500."""
        from ops_api.app.routers.explain import get_explain_client

        fake_response = _FakeHttpxResponse(
            status_code=500,
            data={"detail": "Internal server error"},
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        _app = _make_app_with_explain()
        _app.dependency_overrides[get_explain_client] = lambda: mock_client
        return TestClient(_app)

    def test_network_error_returns_502(self) -> None:
        """ops-explain unreachable MUST return HTTP 502."""
        client = self._client_with_network_error()
        response = client.get(f"/explain/{_PREDICTION_ID}")
        assert response.status_code == 502

    def test_upstream_500_returns_502(self) -> None:
        """Unexpected upstream status MUST be translated to HTTP 502."""
        client = self._client_with_upstream_500()
        response = client.get(f"/explain/{_PREDICTION_ID}")
        assert response.status_code == 502

    def test_advisory_header_present_on_502(self) -> None:
        """HTTP 502 response MUST carry X-Advisory-Only: true (RN-06)."""
        client = self._client_with_upstream_500()
        response = client.get(f"/explain/{_PREDICTION_ID}")
        assert response.headers.get("x-advisory-only") == "true"


# ---------------------------------------------------------------------------
# Tests: trace_id propagation (CAT-08)
# ---------------------------------------------------------------------------


class TestGetExplainTracePropagation:
    """Tests that X-Trace-Id is propagated to the upstream ops-explain call."""

    def test_trace_id_forwarded_to_upstream(self) -> None:
        """X-Trace-Id from the request MUST be forwarded to ops-explain."""
        from ops_api.app.routers.explain import get_explain_client

        custom_trace = "test-trace-abc-123"
        fake_response = _FakeHttpxResponse(status_code=200, data=_READY_RESPONSE)

        captured_headers: dict[str, str] = {}

        async def _capturing_get(url: str, headers: dict | None = None, **kwargs: object) -> _FakeHttpxResponse:
            if headers:
                captured_headers.update(headers)
            return fake_response

        mock_client = AsyncMock()
        mock_client.get = _capturing_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        _app = _make_app_with_explain()
        _app.dependency_overrides[get_explain_client] = lambda: mock_client

        with TestClient(_app) as tc:
            tc.get(
                f"/explain/{_PREDICTION_ID}",
                headers={"X-Trace-Id": custom_trace},
            )

        assert captured_headers.get("X-Trace-Id") == custom_trace

    def test_no_trace_id_sends_no_header(self) -> None:
        """When X-Trace-Id is absent in request, no X-Trace-Id header is
        forwarded to ops-explain (the middleware may add it to the response,
        but the upstream call headers dict stays empty)."""
        from ops_api.app.routers.explain import get_explain_client

        fake_response = _FakeHttpxResponse(status_code=200, data=_READY_RESPONSE)

        captured_headers: dict[str, str] = {}

        async def _capturing_get(url: str, headers: dict | None = None, **kwargs: object) -> _FakeHttpxResponse:
            if headers:
                captured_headers.update(headers)
            return fake_response

        mock_client = AsyncMock()
        mock_client.get = _capturing_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        _app = _make_app_with_explain()
        _app.dependency_overrides[get_explain_client] = lambda: mock_client

        with TestClient(_app) as tc:
            tc.get(f"/explain/{_PREDICTION_ID}")

        # Without an explicit X-Trace-Id request header the router must NOT
        # inject a spurious value into the upstream headers dict.
        assert "X-Trace-Id" not in captured_headers


# ---------------------------------------------------------------------------
# Tests: main app integration (explain router registered in main.py)
# ---------------------------------------------------------------------------


class TestExplainRegisteredInMainApp:
    """Smoke test: explain router should be reachable via the production app."""

    def test_main_app_explain_endpoint_exists(self) -> None:
        """GET /explain/{id} via the full production app MUST NOT return 404/405."""
        from ops_api.app.routers.explain import get_explain_client

        fake_response = _FakeHttpxResponse(status_code=200, data=_READY_RESPONSE)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # Import the production app (main.py must have included explain router)
        import ops_api.app.main as main_module

        main_module.app.dependency_overrides[get_explain_client] = lambda: mock_client
        try:
            with TestClient(main_module.app) as tc:
                response = tc.get(f"/explain/{_PREDICTION_ID}")
            # The route must exist and return something other than 404 (route not found)
            # or 405 (method not allowed).
            assert response.status_code not in (404, 405)
        finally:
            main_module.app.dependency_overrides.pop(get_explain_client, None)

    @pytest.mark.parametrize("missing_route", ["/explain"])
    def test_explain_root_without_id_returns_404(self, missing_route: str) -> None:
        """GET /explain without a prediction_id path param MUST return 404."""
        from ops_api.app.routers.explain import get_explain_client

        fake_response = _FakeHttpxResponse(status_code=200, data=_READY_RESPONSE)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        import ops_api.app.main as main_module

        main_module.app.dependency_overrides[get_explain_client] = lambda: mock_client
        try:
            with TestClient(main_module.app) as tc:
                response = tc.get(missing_route)
            assert response.status_code == 404
        finally:
            main_module.app.dependency_overrides.pop(get_explain_client, None)
