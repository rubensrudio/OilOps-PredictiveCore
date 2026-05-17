"""
ops-api/app/main.py
====================
FastAPI application entry-point for the ops-api public gateway service.

This module wires together:
  - AdvisoryMiddleware  — injects ``X-Advisory-Only: true`` in every response
  - TracingMiddleware   — generates / propagates ``trace_id`` via ContextVar
  - Routers registered for TASK-022:
    - telemetry router   — POST /telemetry
    - predictions router — GET /predictions/{asset_id}
  - Router registered for TASK-023:
    - explain router — GET /explain/{prediction_id}
  - Router registered for TASK-024:
    - stream router — WS /predictions/stream
  - Router registered for TASK-025:
    - models router — POST /models/deploy
  - Router registered for TASK-026:
    - health router — GET /health (fan-out to 5 dependent services)
  - Routers registered for TASK-027:
    - metrics router — GET /metrics (Prometheus text format)
    - audit router   — GET /audit (paginated prediction audit log)

All eight routers are active: telemetry, predictions, explain, stream,
models, health, metrics, audit.

IMPORTANT — RN-06 advisory notice
----------------------------------
This system is purely advisory.  Predictions issued by the motor are NOT
certified for safety-instrumented functions (SIF) or any safety-rated
application (SIL classification).  Operators MUST NOT use predictions as the
sole basis for safety-critical decisions.  Always consult qualified engineering
personnel and certified safety systems.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ops_api.app.middleware.advisory import AdvisoryMiddleware
from ops_api.app.middleware.tracing import TracingMiddleware

_ADVISORY_HEADER = "X-Advisory-Only"
_ADVISORY_VALUE = "true"

# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="OilOps-PredictiveCore — Public Gateway API",
    description=(
        "Advisory-only predictive maintenance API.  "
        "Header X-Advisory-Only: true is present on all responses (RN-06)."
    ),
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Translate Pydantic/FastAPI 422 validation errors to HTTP 400.

    External clients receive HTTP 400 (Bad Request) with the field-level
    error details from Pydantic so they can understand which fields were
    invalid (INIT-US-01 AC4, tasks.md TASK-022 design decision).

    The X-Advisory-Only header is included so the middleware guarantee
    (RN-06) holds on error responses too.
    """
    return JSONResponse(
        status_code=400,
        content={"detail": exc.errors()},
        headers={_ADVISORY_HEADER: _ADVISORY_VALUE},
    )


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler — ensures X-Advisory-Only is present even on 500s.

    The Starlette ServerErrorMiddleware bypasses ASGI middleware on unhandled
    exceptions; adding an explicit handler here closes that gap.
    """
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers={_ADVISORY_HEADER: _ADVISORY_VALUE},
    )


# ---------------------------------------------------------------------------
# Middleware registration (MUST happen before routers are included)
# Order matters: add_middleware() calls are applied in reverse, so the LAST
# add_middleware() becomes the OUTERMOST wrapper.
# We want TracingMiddleware outermost (sets trace_id first), then Advisory.
# ---------------------------------------------------------------------------

app.add_middleware(AdvisoryMiddleware)
app.add_middleware(TracingMiddleware)

# ---------------------------------------------------------------------------
# Router registration
# TASK-022: telemetry + predictions
# TASK-023: explain
# TASK-024: stream
# TASK-025: models
# TASK-026: health
# TASK-027: metrics + audit
# ---------------------------------------------------------------------------

from ops_api.app.routers.telemetry import router as _telemetry_router  # noqa: E402
from ops_api.app.routers.predictions import router as _predictions_router  # noqa: E402
from ops_api.app.routers.explain import router as _explain_router  # noqa: E402
from ops_api.app.routers.stream import router as _stream_router  # noqa: E402
from ops_api.app.routers.models import router as _models_router  # noqa: E402
from ops_api.app.routers.health import router as _health_router  # noqa: E402
from ops_api.app.routers.metrics import router as _metrics_router  # noqa: E402
from ops_api.app.routers.audit import router as _audit_router  # noqa: E402

app.include_router(_telemetry_router)
app.include_router(_predictions_router)
app.include_router(_explain_router)
app.include_router(_stream_router)
app.include_router(_models_router)
app.include_router(_health_router)
app.include_router(_metrics_router)
app.include_router(_audit_router)

# ---------------------------------------------------------------------------
# Root endpoint — advisory notice (RN-06)
# ---------------------------------------------------------------------------


@app.get("/")
async def root() -> dict[str, str]:
    """Return an advisory notice confirming the system is operational."""
    return {
        "service": "ops-api",
        "status": "operational",
        "advisory": (
            "WARNING: This system is advisory only.  Predictions are NOT "
            "certified for safety-instrumented functions or safety-rated "
            "applications.  Do not use as the sole basis for safety-critical "
            "decisions."
        ),
    }
