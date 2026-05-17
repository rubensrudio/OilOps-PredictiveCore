"""
tests/test_metrics_router.py
==============================
Contract tests for GET /metrics (TASK-027).

Criteria verified (from tasks.md TASK-027):
  - GET /metrics returns HTTP 200 (INIT-US-08-AC1, CAT-12).
  - Response Content-Type contains ``text/plain`` (Prometheus text format).
  - Response body contains the names of all four required metrics:
      predictions_total
      predictions_latency_seconds
      ingestion_records_total
      model_inference_latency_seconds
  - Response carries the X-Advisory-Only: true header (RN-06).

Design notes
------------
* The metrics router exposes a Prometheus text/plain endpoint — no httpx
  delegation to a downstream service.  The test uses a minimal FastAPI app
  that includes only the metrics router plus the two middleware layers.

* Metrics are module-level singletons registered in the default Prometheus
  registry.  Re-importing the module would raise a duplicate-registration
  error, so all tests share the same module instance via ``ops_api.*``
  bootstrap in conftest.py.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_with_metrics() -> FastAPI:
    """Return a minimal FastAPI app containing only the metrics router and middlewares."""
    from ops_api.app.middleware.advisory import AdvisoryMiddleware
    from ops_api.app.middleware.tracing import TracingMiddleware
    from ops_api.app.routers.metrics import router as metrics_router

    _app = FastAPI()
    _app.add_middleware(AdvisoryMiddleware)
    _app.add_middleware(TracingMiddleware)
    _app.include_router(metrics_router)
    return _app


# The four metric names that MUST appear in the Prometheus output (CAT-12).
_REQUIRED_METRIC_NAMES = [
    "predictions_total",
    "predictions_latency_seconds",
    "ingestion_records_total",
    "model_inference_latency_seconds",
]


# ---------------------------------------------------------------------------
# Tests: GET /metrics
# ---------------------------------------------------------------------------


class TestGetMetrics:
    """Contract tests for the GET /metrics endpoint (TASK-027)."""

    def setup_method(self) -> None:
        """Create a fresh TestClient for each test method."""
        self._app = _make_app_with_metrics()
        self._client = TestClient(self._app)

    # ------------------------------------------------------------------
    # HTTP status and content-type
    # ------------------------------------------------------------------

    def test_returns_200(self) -> None:
        """GET /metrics MUST return HTTP 200."""
        response = self._client.get("/metrics")
        assert response.status_code == 200

    def test_content_type_is_text_plain(self) -> None:
        """Response Content-Type MUST contain ``text/plain`` (Prometheus format)."""
        response = self._client.get("/metrics")
        content_type = response.headers.get("content-type", "")
        assert "text/plain" in content_type, (
            f"Expected Content-Type to contain 'text/plain', got: '{content_type}'"
        )

    def test_content_type_contains_prometheus_version(self) -> None:
        """Response Content-Type MUST include a ``version=`` parameter.

        The prometheus_client library sets this to ``version=0.0.4`` in older
        releases and ``version=1.0.0`` in newer ones (>= 0.20).  The test
        verifies that the ``version=`` marker is present rather than asserting a
        specific version string so it does not break across library upgrades.
        """
        response = self._client.get("/metrics")
        content_type = response.headers.get("content-type", "")
        assert "version=" in content_type, (
            f"Expected Content-Type to contain 'version=', got: '{content_type}'"
        )

    # ------------------------------------------------------------------
    # Body contents — required metric names (CAT-12)
    # ------------------------------------------------------------------

    def test_body_contains_predictions_total(self) -> None:
        """Response body MUST contain the ``predictions_total`` metric name."""
        body = self._client.get("/metrics").text
        assert "predictions_total" in body

    def test_body_contains_predictions_latency_seconds(self) -> None:
        """Response body MUST contain ``predictions_latency_seconds``."""
        body = self._client.get("/metrics").text
        assert "predictions_latency_seconds" in body

    def test_body_contains_ingestion_records_total(self) -> None:
        """Response body MUST contain ``ingestion_records_total``."""
        body = self._client.get("/metrics").text
        assert "ingestion_records_total" in body

    def test_body_contains_model_inference_latency_seconds(self) -> None:
        """Response body MUST contain ``model_inference_latency_seconds``."""
        body = self._client.get("/metrics").text
        assert "model_inference_latency_seconds" in body

    def test_body_contains_all_four_required_metrics(self) -> None:
        """Single aggregated check: all four required metric names MUST be present."""
        body = self._client.get("/metrics").text
        for metric_name in _REQUIRED_METRIC_NAMES:
            assert metric_name in body, (
                f"Required metric '{metric_name}' not found in /metrics output."
            )

    # ------------------------------------------------------------------
    # Advisory header (RN-06)
    # ------------------------------------------------------------------

    def test_advisory_header_present(self) -> None:
        """GET /metrics MUST carry X-Advisory-Only: true (RN-06)."""
        response = self._client.get("/metrics")
        assert response.headers.get("x-advisory-only") == "true", (
            "X-Advisory-Only: true header missing from GET /metrics response."
        )

    # ------------------------------------------------------------------
    # Response body is non-empty
    # ------------------------------------------------------------------

    def test_response_body_is_non_empty(self) -> None:
        """GET /metrics MUST return a non-empty body."""
        response = self._client.get("/metrics")
        assert len(response.content) > 0
