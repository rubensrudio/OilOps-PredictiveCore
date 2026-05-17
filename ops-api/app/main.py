"""
ops-api/app/main.py
====================
FastAPI application entry-point for the ops-api public gateway service.

This module wires together:
  - AdvisoryMiddleware  — injects ``X-Advisory-Only: true`` in every response
  - TracingMiddleware   — generates / propagates ``trace_id`` via ContextVar

Routers (TASK-022 through TASK-027) are registered here via
``app.include_router()`` once they are implemented.  The stubs below show
where they will be added so that downstream tasks can merge without conflicts.

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
# Global exception handler — ensures X-Advisory-Only is present even on 500s
# (The Starlette ServerErrorMiddleware bypasses ASGI middleware on unhandled
# exceptions; adding an explicit handler here closes that gap.)
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
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
