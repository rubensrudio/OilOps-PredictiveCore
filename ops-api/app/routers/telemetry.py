"""
ops-api/app/routers/telemetry.py
==================================
Router for ``POST /telemetry``.

This module implements the public-facing telemetry ingestion endpoint.
ops-api acts purely as an API gateway: it validates the incoming payload
shape and delegates 100 % of the processing to ``ops-ingest`` via an
async httpx call.  No normalisation or domain logic lives here (DA-04,
plan.md § 3.2).

Route
-----
POST /telemetry
    Accept a batch of raw telemetry readings, forward them to the
    ``ops-ingest`` internal service, and return HTTP 202 with an
    :class:`IngestionResponse`.

Design notes
------------
* Payload validation: FastAPI/Pydantic v2 validates the request body
  against :class:`~ops_ingest.app.schemas.IngestRequest`.  For payloads
  that fail Pydantic validation the framework raises a
  ``RequestValidationError`` (HTTP 422).  An exception handler registered
  on the app translates 422 → 400 at the ops-api boundary so that external
  callers receive the HTTP status documented in the spec (INIT-US-01 AC4).
  This translation happens **after** Pydantic validation, so the field-level
  detail list from the ``ValidationError`` is preserved in the response body.

* Upstream delegation: if ``ops-ingest`` itself returns 422 (its own
  Pydantic validation) that status is also mapped to 400 before being
  forwarded to the external caller.

* Header propagation: the ``X-Trace-Id`` header from the incoming request
  is forwarded to ``ops-ingest`` so the trace spans both services (CAT-08).

* X-Advisory-Only: true is injected by :class:`~ops_api.app.middleware.
  advisory.AdvisoryMiddleware` and requires no manual handling in this
  router (RN-06).

References
----------
- tasks.md TASK-022
- spec.md INIT-US-01, INIT-05, RN-06, RN-07
- plan.md § 3.2, § 5.1
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

# Import IngestRequest to validate the payload at the ops-api boundary.
# The schema lives in ops-ingest; we import it by qualified path so the
# conftest.py alias resolution works in tests.
from ops_ingest.app.schemas import IngestRequest, IngestionResponse

router = APIRouter(tags=["telemetry"])

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_OPS_INGEST_URL = "http://ops-ingest:8001"


def _get_ops_ingest_url() -> str:
    """Return the base URL of the ops-ingest service from the environment."""
    return os.environ.get("OPS_INGEST_URL", _DEFAULT_OPS_INGEST_URL)


# ---------------------------------------------------------------------------
# HTTP client dependency — injected via Depends
# ---------------------------------------------------------------------------


async def get_ingest_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield a short-lived async ``httpx.AsyncClient`` for the ingest call.

    This dependency is overridden in tests via ``app.dependency_overrides``
    to inject a mock that never makes real network calls.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


# ---------------------------------------------------------------------------
# Endpoint: POST /telemetry
# ---------------------------------------------------------------------------


@router.post(
    "/telemetry",
    status_code=202,
    response_model=IngestionResponse,
    summary="Ingest a batch of raw telemetry readings",
    responses={
        202: {"description": "Batch accepted — ingestion_id and counters returned."},
        400: {
            "description": (
                "Malformed payload — required field missing or invalid type. "
                "Field-level validation errors are included in the response body."
            )
        },
    },
)
async def post_telemetry(
    body: IngestRequest,
    request: Request,
    http_client: httpx.AsyncClient = Depends(get_ingest_client),
) -> IngestionResponse:
    """Accept a batch of telemetry readings and delegate to ops-ingest.

    Processing steps:

    1. FastAPI/Pydantic validates the request body against
       :class:`~ops_ingest.app.schemas.IngestRequest`.  Invalid payloads
       raise a ``RequestValidationError`` which is translated to HTTP 400
       by the exception handler registered on the main app.
    2. The validated body is serialised back to JSON and forwarded to
       ``ops-ingest POST /internal/ingest`` via httpx.
    3. ``trace_id`` from the ``X-Trace-Id`` header is propagated so the
       trace spans the full ingest path (CAT-08).
    4. The :class:`~ops_ingest.app.schemas.IngestionResponse` returned by
       ops-ingest is deserialised and returned to the caller with HTTP 202.

    Parameters
    ----------
    body:
        Validated :class:`~ops_ingest.app.schemas.IngestRequest` payload.
    request:
        Starlette :class:`~starlette.requests.Request` — used to read the
        ``X-Trace-Id`` header for propagation.
    http_client:
        Injected async ``httpx.AsyncClient``.

    Returns
    -------
    IngestionResponse
        Summary of the ingestion batch (HTTP 202).

    Raises
    ------
    HTTP 400
        When the request body fails Pydantic validation (translated from
        the 422 ``RequestValidationError`` by the app-level exception
        handler; see ``ops_api/app/main.py``).
    HTTP 502
        When ``ops-ingest`` is unreachable or returns an unexpected error.
    """
    trace_id = request.headers.get("X-Trace-Id")
    ingest_url = _get_ops_ingest_url()

    headers: dict[str, str] = {}
    if trace_id:
        headers["X-Trace-Id"] = trace_id

    # Serialise using Pydantic v2 model_dump so datetime fields are ISO 8601.
    payload = body.model_dump(mode="json")

    try:
        response = await http_client.post(
            f"{ingest_url}/internal/ingest",
            json=payload,
            headers=headers,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ops-ingest unreachable: {exc}",
        ) from exc

    # Translate ops-ingest 422 → 400 at the gateway boundary (INIT-US-01 AC4).
    if response.status_code == 422:
        raise HTTPException(status_code=400, detail=response.json())

    if response.status_code != 202:
        raise HTTPException(
            status_code=502,
            detail=f"ops-ingest returned unexpected status {response.status_code}",
        )

    return IngestionResponse(**response.json())
