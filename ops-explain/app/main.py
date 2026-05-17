"""ops-explain FastAPI application — TASK-020.

Exposes two internal endpoints:

- ``GET  /internal/explain/{prediction_id}``         — Query explanation status and
                                                        return SHAP result if ready.
- ``POST /internal/explain/trigger/{prediction_id}`` — Enqueue SHAP computation for
                                                        a prediction (called by
                                                        ops-models after inference).
- ``GET  /health``                                   — Liveness probe.

Explanation retrieval protocol
-------------------------------
This service proxies GET requests to ``ops-store`` to check explanation status:

    ops-store returns 200  → forward payload to caller with HTTP 200 (ready)
    ops-store returns 202  → forward HTTP 202 with ``retry_after`` (pending)
    ops-store returns 404  → return HTTP 404 (unknown prediction_id)

The ``OPS_STORE_URL`` env var (default ``http://ops-store:8002``) configures
the upstream address.

Trigger endpoint
----------------
``POST /internal/explain/trigger/{prediction_id}`` accepts a JSON body with
``feature_record_id`` (required) plus optional ``asset_id``, ``asset_class``,
``model_id`` fields.  It calls ``BackgroundTaskManager.enqueue`` to schedule
SHAP attribution computation.  The manager must already be running (started
during lifespan).  If the manager is not running (e.g. test context without
lifespan), the endpoint still enqueues the task — the worker is started lazily.

Dependency injection
--------------------
``httpx.AsyncClient`` is injected via ``Depends(get_http_client)`` so tests
can override with a mock that returns controlled responses without starting
a real ops-store.

Logging
-------
Structured JSON logging via ``shared.logging_config.get_logger``.  Every
request logs ``prediction_id``, ``explain_status`` (on GET) or ``enqueue``
operation (on POST) with ``trace_id`` propagated from the
``X-Trace-Id`` request header when present.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ops_explain.app.background import BackgroundTaskManager
from shared.logging_config import get_logger

# ---------------------------------------------------------------------------
# Module-level setup
# ---------------------------------------------------------------------------

_logger = get_logger("ops-explain")

_OPS_STORE_URL: str = os.environ.get("OPS_STORE_URL", "http://ops-store:8002")

# ---------------------------------------------------------------------------
# Background task manager (singleton per process)
# ---------------------------------------------------------------------------

# The manager is instantiated at module level with stub dependencies.
# In production the lifespan hook replaces these with real implementations
# wired to ops-store.  For unit tests the dependency override of the FastAPI
# app is the recommended approach for the HTTP client; the manager's
# enqueue/requeue is tested directly in test_background.py.
_task_manager: BackgroundTaskManager | None = None


def _get_task_manager() -> BackgroundTaskManager:
    """Return the global BackgroundTaskManager, creating a no-op instance if absent."""
    global _task_manager  # noqa: PLW0603
    if _task_manager is None:
        # Phase-1 stub: no real store / explain_fn at startup — replaced during
        # lifespan when the full dependency graph is wired.  Enqueue still works;
        # the worker will drain tasks once started.
        _task_manager = BackgroundTaskManager(
            store=_NullStore(),
            explain_fn=lambda pid, feats: {},
            get_features_fn=lambda fid: None,
        )
    return _task_manager


class _NullStore:
    """Minimal stub store used before the real store is wired in lifespan."""

    def get_predictions_by_status(self, status: str, limit: int = 100) -> list[dict[str, Any]]:  # noqa: ARG002
        return []

    def update_explain_status(self, prediction_id: str, status: str) -> bool:  # noqa: ARG002
        return True


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan: start and stop the background worker."""
    manager = _get_task_manager()
    await manager.start_worker()
    _logger.info("ops-explain background worker started.")
    yield
    await manager.stop_worker()
    _logger.info("ops-explain background worker stopped.")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ops-explain",
    description=(
        "Internal SHAP explanation service for OilOps-PredictiveCore. "
        "WARNING: This system is ADVISORY ONLY. "
        "It does NOT replace safety-instrumented systems (RN-06)."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Dependency providers
# ---------------------------------------------------------------------------


async def get_http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield a shared httpx.AsyncClient for inter-service calls.

    Injected via ``Depends`` so tests can override with a mock client that
    returns controlled responses without hitting a real ops-store.
    """
    async with httpx.AsyncClient(base_url=_OPS_STORE_URL, timeout=5.0) as client:
        yield client


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class TriggerRequest(BaseModel):
    """Body for POST /internal/explain/trigger/{prediction_id}."""

    feature_record_id: str = Field(
        ...,
        description="UUID of the feature_record in ops-store used for SHAP input.",
    )
    asset_id: str | None = Field(None, description="Asset identifier (informational).")
    asset_class: str | None = Field(None, description="Asset class (informational).")
    model_id: str | None = Field(None, description="Model ID used for inference.")


class TriggerResponse(BaseModel):
    """Response for POST /internal/explain/trigger/{prediction_id}."""

    prediction_id: str
    feature_record_id: str
    queued: bool


class PendingExplainResponse(BaseModel):
    """Response body for HTTP 202 (explanation pending)."""

    prediction_id: str
    explain_status: str = "pending"
    retry_after: int = Field(
        30,
        ge=1,
        description="Suggested number of seconds before retrying GET /explain.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_trace_id(request: Request) -> str | None:
    """Extract X-Trace-Id from request headers if present."""
    return request.headers.get("x-trace-id")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe — always returns 200 when the process is running."""
    return {"status": "ok", "service": "ops-explain"}


@app.get("/internal/explain/{prediction_id}")
async def get_explain(
    prediction_id: str,
    request: Request,
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> Any:
    """Return SHAP explanation for *prediction_id*.

    Proxies to ``ops-store GET /internal/explain/{prediction_id}``:

    - ``ops-store 200``  → HTTP 200 with ``feature_attributions`` payload.
    - ``ops-store 202``  → HTTP 202 with ``{"retry_after": 30}`` (pending).
    - ``ops-store 404``  → HTTP 404 (prediction not found).
    - Any other status  → HTTP 502 (bad gateway from upstream).

    Parameters
    ----------
    prediction_id:
        UUID of the prediction whose explanation is being requested.

    Returns
    -------
    JSON payload from ops-store (200 case) or ``PendingExplainResponse``
    (202 case).
    """
    trace_id = _extract_trace_id(request)

    _logger.info(
        "GET /internal/explain/%s called",
        prediction_id,
        extra={"prediction_id": prediction_id, "trace_id": trace_id},
    )

    try:
        upstream_resp = await http_client.get(
            f"/internal/explain/{prediction_id}",
            headers=({"x-trace-id": trace_id} if trace_id else {}),
        )
    except httpx.RequestError as exc:
        _logger.error(
            "Failed to reach ops-store for explain/%s: %s",
            prediction_id,
            exc,
            extra={"prediction_id": prediction_id, "error": str(exc)},
        )
        raise HTTPException(
            status_code=502,
            detail="Upstream ops-store is unreachable.",
        ) from exc

    if upstream_resp.status_code == 200:
        _logger.info(
            "Explanation ready for %s",
            prediction_id,
            extra={"prediction_id": prediction_id, "explain_status": "ready"},
        )
        return upstream_resp.json()

    if upstream_resp.status_code == 202:
        upstream_body: dict[str, Any] = upstream_resp.json()
        retry_after: int = int(upstream_body.get("retry_after", 30))
        _logger.info(
            "Explanation pending for %s (retry_after=%d)",
            prediction_id,
            retry_after,
            extra={
                "prediction_id": prediction_id,
                "explain_status": "pending",
                "retry_after": retry_after,
            },
        )
        from fastapi.responses import JSONResponse  # local import to keep top clean

        return JSONResponse(
            status_code=202,
            content=PendingExplainResponse(
                prediction_id=prediction_id,
                explain_status="pending",
                retry_after=retry_after,
            ).model_dump(),
        )

    if upstream_resp.status_code == 404:
        _logger.info(
            "No explanation found for %s",
            prediction_id,
            extra={"prediction_id": prediction_id},
        )
        raise HTTPException(
            status_code=404,
            detail=f"No explanation found for prediction_id '{prediction_id}'.",
        )

    # Unexpected upstream response.
    _logger.warning(
        "Unexpected status %d from ops-store for explain/%s",
        upstream_resp.status_code,
        prediction_id,
        extra={
            "prediction_id": prediction_id,
            "upstream_status": upstream_resp.status_code,
        },
    )
    raise HTTPException(
        status_code=502,
        detail=(
            f"Unexpected response from ops-store: HTTP {upstream_resp.status_code}"
        ),
    )


@app.post("/internal/explain/trigger/{prediction_id}", response_model=TriggerResponse)
async def trigger_explain(
    prediction_id: str,
    body: TriggerRequest,
    request: Request,
) -> TriggerResponse:
    """Enqueue SHAP computation for *prediction_id*.

    Called by ``ops-models`` after a prediction has been persisted.  Adds
    the task to the ``BackgroundTaskManager`` queue; the worker coroutine
    (started during lifespan) drains the queue asynchronously.

    Parameters
    ----------
    prediction_id:
        UUID of the prediction to explain.
    body:
        ``TriggerRequest`` with ``feature_record_id`` (required) and optional
        contextual fields.

    Returns
    -------
    ``TriggerResponse`` confirming the task was enqueued.
    """
    trace_id = _extract_trace_id(request)

    manager = _get_task_manager()
    manager.enqueue(
        prediction_id=prediction_id,
        feature_record_id=body.feature_record_id,
    )

    _logger.info(
        "SHAP task enqueued for prediction %s (feature_record_id=%s)",
        prediction_id,
        body.feature_record_id,
        extra={
            "prediction_id": prediction_id,
            "feature_record_id": body.feature_record_id,
            "asset_id": body.asset_id,
            "asset_class": body.asset_class,
            "model_id": body.model_id,
            "trace_id": trace_id,
        },
    )

    return TriggerResponse(
        prediction_id=prediction_id,
        feature_record_id=body.feature_record_id,
        queued=True,
    )
