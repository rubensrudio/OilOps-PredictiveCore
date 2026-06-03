"""
ops-api/app/routers/predictions.py
=====================================
Router for ``GET /predictions/{asset_id}``.

This module implements the public-facing prediction query endpoint.
ops-api consults ``ops-store`` (the source of truth for persisted
predictions, plan.md § 3.2 step 10) via an async httpx call and returns
the latest :class:`~ops_models.app.schemas.PredictionResult` for the
requested asset.

Route
-----
GET /predictions/{asset_id}
    Return the most recent prediction for *asset_id*.
    HTTP 200 with :class:`~ops_models.app.schemas.PredictionResult` when
    predictions exist; HTTP 404 with a descriptive message when they do not
    (INIT-US-02 AC2).

Design notes
------------
* Source of truth: ops-api queries ops-store, not ops-models, because
  ops-store persists completed predictions and is the durable record
  (DA-04, plan.md § 3.2).  ops-models would only be queried for a live,
  uncached inference.

* X-Advisory-Only: true is injected by AdvisoryMiddleware and requires no
  manual handling in this router (RN-06, INIT-US-02 AC4).

* Header propagation: the ``X-Trace-Id`` header is forwarded to ops-store
  so the trace spans both services (CAT-08).

References
----------
- tasks.md TASK-022
- spec.md INIT-US-02, RN-06
- plan.md § 3.2, § 5.2
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from ops_models.app.schemas import PredictionResult

router = APIRouter(tags=["predictions"])

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_OPS_STORE_URL = "http://ops-store:8002"


def _get_ops_store_url() -> str:
    """Return the base URL of the ops-store service from the environment."""
    return os.environ.get("OPS_STORE_URL", _DEFAULT_OPS_STORE_URL)


# ---------------------------------------------------------------------------
# HTTP client dependency — injected via Depends
# ---------------------------------------------------------------------------


async def get_store_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield a short-lived async ``httpx.AsyncClient`` for the store call.

    This dependency is overridden in tests via ``app.dependency_overrides``
    to inject a mock that never makes real network calls.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        yield client


# ---------------------------------------------------------------------------
# Endpoint: GET /predictions/{asset_id}
# ---------------------------------------------------------------------------


@router.get(
    "/predictions/{asset_id}",
    status_code=200,
    response_model=PredictionResult,
    summary="Return the latest prediction for an asset",
    responses={
        200: {
            "description": (
                "Most recent prediction for the asset — includes "
                "anomaly_score, confidence_score, alert, severity, "
                "explain_status."
            )
        },
        404: {
            "description": (
                "No predictions found for the requested asset_id. "
                "The asset may not yet have been evaluated by the model."
            )
        },
    },
)
async def get_predictions(
    asset_id: str,
    request: Request,
    http_client: httpx.AsyncClient = Depends(get_store_client),
) -> PredictionResult:
    """Return the most recent prediction for *asset_id*.

    Queries ``ops-store GET /internal/predictions/{asset_id}/latest`` which
    is the durable source of truth for persisted predictions (plan.md § 3.2).

    Parameters
    ----------
    asset_id:
        Canonical identifier of the asset whose latest prediction is
        requested (e.g. ``"PUMP-001"``).
    request:
        Starlette :class:`~starlette.requests.Request` — used to read the
        ``X-Trace-Id`` header for propagation.
    http_client:
        Injected async ``httpx.AsyncClient``.

    Returns
    -------
    PredictionResult
        Most recent :class:`~ops_models.app.schemas.PredictionResult`
        (HTTP 200).

    Raises
    ------
    HTTP 404
        When no predictions exist for *asset_id* in ops-store.
    HTTP 502
        When ``ops-store`` is unreachable or returns an unexpected status.
    """
    trace_id = request.headers.get("X-Trace-Id")
    store_url = _get_ops_store_url()

    headers: dict[str, str] = {}
    if trace_id:
        headers["X-Trace-Id"] = trace_id

    try:
        response = await http_client.get(
            f"{store_url}/internal/predictions/{asset_id}/latest",
            headers=headers,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ops-store unreachable: {exc}",
        ) from exc

    if response.status_code == 404:
        # Propagate the descriptive message from ops-store (INIT-US-02 AC2).
        detail = response.json().get(
            "detail",
            f"No predictions found for asset_id '{asset_id}'",
        )
        raise HTTPException(status_code=404, detail=detail)

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"ops-store returned unexpected status {response.status_code}",
        )

    return PredictionResult(**response.json())
