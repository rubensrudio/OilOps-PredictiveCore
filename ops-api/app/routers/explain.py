"""
ops-api/app/routers/explain.py
================================
Router for ``GET /explain/{prediction_id}``.

This module implements the public-facing explanation query endpoint.
ops-api acts as a gateway and delegates to ``ops-explain`` via an async
httpx call, propagating the trace context for distributed tracing (CAT-08).

Route
-----
GET /explain/{prediction_id}
    Return the SHAP explanation for *prediction_id*.

    Response matrix (INIT-US-03):
    - HTTP 200  — ``explain_status = ready``; body contains
      ``feature_attributions`` with at least the top-5 features (INIT-10).
    - HTTP 202  — ``explain_status = pending``; body contains ``retry_after``
      in seconds (INIT-11).
    - HTTP 404  — ``prediction_id`` does not exist in ops-explain / ops-store.
    - HTTP 502  — ``ops-explain`` is unreachable or returned an unexpected
      upstream status.

Design notes
------------
* Dependency injection: ``get_explain_client()`` yields a short-lived
  ``httpx.AsyncClient`` and is overridden in tests via
  ``app.dependency_overrides``.

* Header propagation: ``X-Trace-Id`` from the incoming request is forwarded
  to ``ops-explain`` so the trace spans both services (CAT-08).

* X-Advisory-Only: true is injected automatically by
  :class:`~ops_api.app.middleware.advisory.AdvisoryMiddleware` — this router
  does NOT set it manually (RN-06).

* The 202 response must be returned as a ``JSONResponse`` with explicit
  ``status_code=202`` because FastAPI's default response model handling would
  promote it to 200.

References
----------
- tasks.md TASK-023
- spec.md INIT-US-03, RN-06
- plan.md § 3.2, § 5.3
"""

from __future__ import annotations

import os
from typing import Any, AsyncGenerator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["explain"])

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_OPS_EXPLAIN_URL = "http://ops-explain:8005"


def _get_ops_explain_url() -> str:
    """Return the base URL of the ops-explain service from the environment."""
    return os.environ.get("OPS_EXPLAIN_URL", _DEFAULT_OPS_EXPLAIN_URL)


# ---------------------------------------------------------------------------
# HTTP client dependency — injected via Depends
# ---------------------------------------------------------------------------


async def get_explain_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield a short-lived async ``httpx.AsyncClient`` for the explain call.

    This dependency is overridden in tests via ``app.dependency_overrides``
    to inject a mock that never makes real network calls.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        yield client


# ---------------------------------------------------------------------------
# Endpoint: GET /explain/{prediction_id}
# ---------------------------------------------------------------------------


@router.get(
    "/explain/{prediction_id}",
    summary="Return SHAP explanation for a prediction",
    responses={
        200: {
            "description": (
                "Explanation ready — feature_attributions list with "
                "at least 5 items ranked by attribution magnitude."
            )
        },
        202: {
            "description": (
                "Explanation still being computed (explain_status = pending). "
                "Retry after ``retry_after`` seconds."
            )
        },
        404: {
            "description": "No prediction found for the given prediction_id."
        },
    },
)
async def get_explain(
    prediction_id: str,
    request: Request,
    http_client: httpx.AsyncClient = Depends(get_explain_client),
) -> Any:
    """Return the SHAP explanation for *prediction_id*.

    Queries ``ops-explain GET /internal/explain/{prediction_id}`` and
    propagates the response to the caller:

    - ``ops-explain 200``  → HTTP 200 with ``feature_attributions`` body.
    - ``ops-explain 202``  → HTTP 202 with ``retry_after`` body (pending).
    - ``ops-explain 404``  → HTTP 404 (prediction not found).
    - Any other status    → HTTP 502 (bad gateway from upstream).

    Parameters
    ----------
    prediction_id:
        UUID of the prediction whose SHAP explanation is requested.
    request:
        Starlette :class:`~starlette.requests.Request` — used to read the
        ``X-Trace-Id`` header for propagation.
    http_client:
        Injected async ``httpx.AsyncClient``.

    Returns
    -------
    JSON payload from ops-explain (200 or 202 case).

    Raises
    ------
    HTTP 404
        When the prediction_id is unknown in ops-explain.
    HTTP 502
        When ``ops-explain`` is unreachable or returns an unexpected status.
    """
    trace_id = request.headers.get("X-Trace-Id")
    explain_url = _get_ops_explain_url()

    headers: dict[str, str] = {}
    if trace_id:
        headers["X-Trace-Id"] = trace_id

    try:
        response = await http_client.get(
            f"{explain_url}/internal/explain/{prediction_id}",
            headers=headers,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ops-explain unreachable: {exc}",
        ) from exc

    if response.status_code == 200:
        return response.json()

    if response.status_code == 202:
        # Re-emit 202 explicitly — FastAPI would otherwise promote to 200.
        return JSONResponse(status_code=202, content=response.json())

    if response.status_code == 404:
        detail = response.json().get(
            "detail",
            f"No explanation found for prediction_id '{prediction_id}'",
        )
        raise HTTPException(status_code=404, detail=detail)

    raise HTTPException(
        status_code=502,
        detail=f"ops-explain returned unexpected status {response.status_code}",
    )
