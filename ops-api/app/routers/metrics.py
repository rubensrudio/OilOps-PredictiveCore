"""
ops-api/app/routers/metrics.py
================================
Router for ``GET /metrics``.

Exposes operational metrics in Prometheus text format (version 0.0.4).

Metrics exposed (INIT-US-08-AC1, CAT-12)
------------------------------------------
- ``predictions_total``                 Counter   label: asset_class
- ``predictions_latency_seconds``       Histogram label: model
- ``ingestion_records_total``           Counter   label: status
- ``model_inference_latency_seconds``   Histogram label: model

Design notes
------------
* Metrics are created as module-level variables — they are registered once
  in the default Prometheus registry at import time.  Re-creating them on
  every request would raise ``ValueError: Duplicated timeseries`` from
  prometheus_client.

* ``generate_latest()`` from prometheus_client serialises the registry to
  Prometheus text format (content-type ``text/plain; version=0.0.4``).

* The endpoint returns a ``Response`` with the correct Content-Type rather
  than a JSON body.

* X-Advisory-Only: true is injected automatically by
  :class:`~ops_api.app.middleware.advisory.AdvisoryMiddleware` — this router
  does NOT set it manually (RN-06).

References
----------
- tasks.md TASK-027
- spec.md INIT-US-08, INIT-US-08-AC1
- plan.md § 5.7, CAT-12
"""

from __future__ import annotations

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Histogram,
        generate_latest,
    )

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover — only hit if library is not installed
    _PROMETHEUS_AVAILABLE = False

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(tags=["observability"])

# ---------------------------------------------------------------------------
# Metric definitions (module-level — registered once at import time)
# ---------------------------------------------------------------------------

if _PROMETHEUS_AVAILABLE:
    predictions_total = Counter(
        "predictions_total",
        "Total predictions emitted by the ops-api gateway",
        labelnames=["asset_class"],
    )

    predictions_latency_seconds = Histogram(
        "predictions_latency_seconds",
        "Prediction end-to-end latency in seconds",
        labelnames=["model"],
    )

    ingestion_records_total = Counter(
        "ingestion_records_total",
        "Total telemetry records processed during ingestion",
        labelnames=["status"],
    )

    model_inference_latency_seconds = Histogram(
        "model_inference_latency_seconds",
        "Model inference latency in seconds",
        labelnames=["model"],
    )

else:  # pragma: no cover
    # Minimal stubs so the rest of the module remains importable in environments
    # where prometheus_client is absent.  Not reachable in test runs because the
    # library is listed in requirements.txt.
    class _FakeMetric:  # type: ignore[no-redef]
        def labels(self, **kwargs: object) -> "_FakeMetric":
            return self

        def inc(self, amount: float = 1) -> None:
            pass

        def observe(self, amount: float) -> None:
            pass

    predictions_total = _FakeMetric()  # type: ignore[assignment]
    predictions_latency_seconds = _FakeMetric()  # type: ignore[assignment]
    ingestion_records_total = _FakeMetric()  # type: ignore[assignment]
    model_inference_latency_seconds = _FakeMetric()  # type: ignore[assignment]

    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    def generate_latest() -> bytes:  # type: ignore[misc]
        """Return a minimal stub output listing the 4 required metric names."""
        return (
            b"# HELP predictions_total Total predictions emitted\n"
            b"# TYPE predictions_total counter\n"
            b"predictions_total 0\n"
            b"# HELP predictions_latency_seconds Prediction latency\n"
            b"# TYPE predictions_latency_seconds histogram\n"
            b"# HELP ingestion_records_total Total records ingested\n"
            b"# TYPE ingestion_records_total counter\n"
            b"ingestion_records_total 0\n"
            b"# HELP model_inference_latency_seconds Inference latency\n"
            b"# TYPE model_inference_latency_seconds histogram\n"
        )


# ---------------------------------------------------------------------------
# Endpoint: GET /metrics
# ---------------------------------------------------------------------------


@router.get(
    "/metrics",
    summary="Expose Prometheus-format operational metrics",
    response_class=Response,
    responses={
        200: {
            "content": {"text/plain": {}},
            "description": (
                "Prometheus text format (version 0.0.4) containing the four "
                "required metrics: predictions_total, predictions_latency_seconds, "
                "ingestion_records_total, model_inference_latency_seconds."
            ),
        }
    },
)
async def get_metrics() -> Response:
    """Return operational metrics in Prometheus text format.

    Serialises the default Prometheus registry using ``generate_latest()``
    and returns the output with the canonical ``Content-Type`` header
    ``text/plain; version=0.0.4; charset=utf-8``.

    The four metrics exposed are:

    - ``predictions_total{asset_class=...}``           — Counter
    - ``predictions_latency_seconds{model=...}``       — Histogram
    - ``ingestion_records_total{status=...}``          — Counter
    - ``model_inference_latency_seconds{model=...}``   — Histogram

    Returns
    -------
    Response
        HTTP 200 with ``Content-Type: text/plain; version=0.0.4; charset=utf-8``
        and Prometheus-formatted body.
    """
    metrics_output = generate_latest()
    return Response(
        content=metrics_output,
        media_type=CONTENT_TYPE_LATEST,
    )
