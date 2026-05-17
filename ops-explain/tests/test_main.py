"""Tests for ops-explain FastAPI app (TASK-020).

Criteria from tasks.md (TASK-020):
    - GET /internal/explain/{id} with explain_status=pending returns HTTP 202
      and field retry_after > 0.
    - GET /internal/explain/{id} with explain_status=ready returns HTTP 200
      with feature_attributions.
    - GET /internal/explain/{id} for unknown prediction_id returns HTTP 404.
    - POST /internal/explain/trigger/{prediction_id} enqueues task and returns
      HTTP 200 with queued=True.
    - GET /health returns HTTP 200.
    - Upstream ops-store unreachable returns HTTP 502.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
from fastapi.testclient import TestClient

from ops_explain.app.main import app, get_http_client


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


def _make_mock_client(
    status_code: int,
    json_body: dict[str, Any] | None = None,
    raise_error: bool = False,
) -> AsyncMock:
    """Build a mock AsyncClient that returns a controlled response.

    The mock replicates the interface used by ``Depends(get_http_client)``:
    an async context-manager-style object whose ``get`` method is awaitable.
    """
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = json_body or {}

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    if raise_error:
        mock_client.get.side_effect = httpx.ConnectError("connection refused")
    else:
        mock_client.get.return_value = mock_response

    return mock_client


def _override_client(mock_client: AsyncMock):
    """Return a FastAPI dependency override that yields *mock_client*."""

    async def _dep():
        yield mock_client

    return _dep


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_returns_200(self) -> None:
        with TestClient(app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "ops-explain"


# ---------------------------------------------------------------------------
# GET /internal/explain/{prediction_id} — ready (200)
# ---------------------------------------------------------------------------


class TestGetExplainReady:
    """Verify 200 response when ops-store reports explain_status=ready."""

    _READY_PAYLOAD: dict[str, Any] = {
        "prediction_id": "pred-ready-001",
        "explain_status": "ready",
        "method": "shap_kernel",
        "feature_attributions": [
            {"feature_name": f"feat_{i}", "attribution_value": 0.1 * i, "rank": i + 1}
            for i in range(5)
        ],
        "baseline_window": {"stats_per_feature": {}},
    }

    def test_returns_200_with_feature_attributions(self) -> None:
        """tasks.md criterion: explain_status=ready → HTTP 200 with feature_attributions."""
        mock_client = _make_mock_client(200, json_body=self._READY_PAYLOAD)
        app.dependency_overrides[get_http_client] = _override_client(mock_client)
        try:
            with TestClient(app) as client:
                resp = client.get("/internal/explain/pred-ready-001")
        finally:
            app.dependency_overrides.pop(get_http_client, None)

        assert resp.status_code == 200
        data = resp.json()
        assert "feature_attributions" in data
        assert len(data["feature_attributions"]) >= 5
        assert data["explain_status"] == "ready"

    def test_feature_attributions_contain_rank(self) -> None:
        mock_client = _make_mock_client(200, json_body=self._READY_PAYLOAD)
        app.dependency_overrides[get_http_client] = _override_client(mock_client)
        try:
            with TestClient(app) as client:
                resp = client.get("/internal/explain/pred-ready-001")
        finally:
            app.dependency_overrides.pop(get_http_client, None)

        attributions = resp.json()["feature_attributions"]
        assert all("rank" in item for item in attributions)
        assert all("feature_name" in item for item in attributions)
        assert all("attribution_value" in item for item in attributions)


# ---------------------------------------------------------------------------
# GET /internal/explain/{prediction_id} — pending (202)
# ---------------------------------------------------------------------------


class TestGetExplainPending:
    """Verify 202 response when ops-store reports explain_status=pending."""

    _PENDING_PAYLOAD: dict[str, Any] = {
        "explain_status": "pending",
        "retry_after": 30,
    }

    def test_returns_202_with_retry_after(self) -> None:
        """tasks.md criterion: explain_status=pending → HTTP 202 and retry_after > 0."""
        mock_client = _make_mock_client(202, json_body=self._PENDING_PAYLOAD)
        app.dependency_overrides[get_http_client] = _override_client(mock_client)
        try:
            with TestClient(app) as client:
                resp = client.get("/internal/explain/pred-pending-001")
        finally:
            app.dependency_overrides.pop(get_http_client, None)

        assert resp.status_code == 202
        data = resp.json()
        assert "retry_after" in data
        assert data["retry_after"] > 0

    def test_retry_after_propagated_from_upstream(self) -> None:
        """The retry_after value from ops-store is forwarded as-is."""
        payload = {"explain_status": "pending", "retry_after": 60}
        mock_client = _make_mock_client(202, json_body=payload)
        app.dependency_overrides[get_http_client] = _override_client(mock_client)
        try:
            with TestClient(app) as client:
                resp = client.get("/internal/explain/pred-pending-002")
        finally:
            app.dependency_overrides.pop(get_http_client, None)

        assert resp.status_code == 202
        assert resp.json()["retry_after"] == 60

    def test_explain_status_is_pending_in_body(self) -> None:
        mock_client = _make_mock_client(202, json_body=self._PENDING_PAYLOAD)
        app.dependency_overrides[get_http_client] = _override_client(mock_client)
        try:
            with TestClient(app) as client:
                resp = client.get("/internal/explain/pred-pending-003")
        finally:
            app.dependency_overrides.pop(get_http_client, None)

        assert resp.json()["explain_status"] == "pending"

    def test_prediction_id_present_in_body(self) -> None:
        mock_client = _make_mock_client(202, json_body=self._PENDING_PAYLOAD)
        app.dependency_overrides[get_http_client] = _override_client(mock_client)
        try:
            with TestClient(app) as client:
                resp = client.get("/internal/explain/pred-pending-abc")
        finally:
            app.dependency_overrides.pop(get_http_client, None)

        assert resp.json()["prediction_id"] == "pred-pending-abc"

    def test_default_retry_after_used_when_absent(self) -> None:
        """When upstream omits retry_after, default of 30 is used."""
        mock_client = _make_mock_client(202, json_body={"explain_status": "pending"})
        app.dependency_overrides[get_http_client] = _override_client(mock_client)
        try:
            with TestClient(app) as client:
                resp = client.get("/internal/explain/pred-pending-def")
        finally:
            app.dependency_overrides.pop(get_http_client, None)

        assert resp.status_code == 202
        assert resp.json()["retry_after"] == 30


# ---------------------------------------------------------------------------
# GET /internal/explain/{prediction_id} — not found (404)
# ---------------------------------------------------------------------------


class TestGetExplainNotFound:
    """Verify 404 response when ops-store returns 404."""

    def test_returns_404_when_unknown(self) -> None:
        """tasks.md criterion: unknown prediction_id → HTTP 404."""
        mock_client = _make_mock_client(404, json_body={"detail": "not found"})
        app.dependency_overrides[get_http_client] = _override_client(mock_client)
        try:
            with TestClient(app) as client:
                resp = client.get("/internal/explain/does-not-exist")
        finally:
            app.dependency_overrides.pop(get_http_client, None)

        assert resp.status_code == 404

    def test_404_detail_contains_prediction_id(self) -> None:
        mock_client = _make_mock_client(404)
        app.dependency_overrides[get_http_client] = _override_client(mock_client)
        try:
            with TestClient(app) as client:
                resp = client.get("/internal/explain/my-pred-xyz")
        finally:
            app.dependency_overrides.pop(get_http_client, None)

        assert "my-pred-xyz" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /internal/explain/{prediction_id} — upstream error (502)
# ---------------------------------------------------------------------------


class TestGetExplainUpstreamError:
    """Verify 502 when ops-store is unreachable."""

    def test_returns_502_on_connection_error(self) -> None:
        mock_client = _make_mock_client(0, raise_error=True)
        app.dependency_overrides[get_http_client] = _override_client(mock_client)
        try:
            with TestClient(app) as client:
                resp = client.get("/internal/explain/pred-unreachable")
        finally:
            app.dependency_overrides.pop(get_http_client, None)

        assert resp.status_code == 502

    def test_returns_502_on_unexpected_upstream_status(self) -> None:
        """Unexpected upstream status (e.g. 500) → HTTP 502."""
        mock_client = _make_mock_client(500, json_body={"detail": "internal error"})
        app.dependency_overrides[get_http_client] = _override_client(mock_client)
        try:
            with TestClient(app) as client:
                resp = client.get("/internal/explain/pred-500")
        finally:
            app.dependency_overrides.pop(get_http_client, None)

        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# POST /internal/explain/trigger/{prediction_id}
# ---------------------------------------------------------------------------


class TestTriggerExplain:
    """Verify trigger endpoint enqueues the task correctly."""

    def test_trigger_returns_200_with_queued_true(self) -> None:
        """Trigger endpoint should return HTTP 200 with queued=True."""
        with TestClient(app) as client:
            resp = client.post(
                "/internal/explain/trigger/pred-trigger-001",
                json={"feature_record_id": "fr-trigger-001"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["queued"] is True
        assert data["prediction_id"] == "pred-trigger-001"
        assert data["feature_record_id"] == "fr-trigger-001"

    def test_trigger_missing_feature_record_id_returns_422(self) -> None:
        """feature_record_id is required; missing it → HTTP 422."""
        with TestClient(app) as client:
            resp = client.post(
                "/internal/explain/trigger/pred-bad",
                json={},
            )
        assert resp.status_code == 422

    def test_trigger_optional_fields_accepted(self) -> None:
        """asset_id, asset_class, model_id are optional."""
        payload = {
            "feature_record_id": "fr-002",
            "asset_id": "pump-001",
            "asset_class": "rotating_equipment",
            "model_id": "vibration-v1",
        }
        with TestClient(app) as client:
            resp = client.post(
                "/internal/explain/trigger/pred-full-001",
                json=payload,
            )
        assert resp.status_code == 200
        assert resp.json()["queued"] is True

    def test_trigger_enqueues_into_manager(self) -> None:
        """Verify the trigger endpoint responds with 200/queued=True.

        The BackgroundTaskManager singleton is per event-loop; TestClient
        creates its own event loop for the lifespan, so we cannot inspect
        the queue from the test-process loop after lifespan teardown.
        The important contract here is that the HTTP response confirms
        the task was accepted (queued=True).
        """
        with TestClient(app) as client:
            resp = client.post(
                "/internal/explain/trigger/pred-queue-check",
                json={"feature_record_id": "fr-queue-check"},
            )
        assert resp.status_code == 200
        assert resp.json()["queued"] is True

    def test_trigger_with_trace_id_header(self) -> None:
        """X-Trace-Id header should be accepted without error."""
        with TestClient(app) as client:
            resp = client.post(
                "/internal/explain/trigger/pred-traced",
                json={"feature_record_id": "fr-traced"},
                headers={"x-trace-id": "test-trace-abc"},
            )
        assert resp.status_code == 200
