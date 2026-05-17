"""
ops-api/app/routers/audit.py
==============================
Router for ``GET /audit``.

Queries the audit log via the ``ops-store`` internal service and returns a
paginated JSON result set.

Route
-----
GET /audit
    Query the ``audit_log`` table via ``ops-store``.

    Query parameters:
    - ``asset_id``   (str, optional)       — filter by asset
    - ``from``       (datetime, optional)  — filter events after this UTC time
    - ``to``         (datetime, optional)  — filter events before this UTC time
    - ``page``       (int, default=1)      — 1-based page number
    - ``page_size``  (int, default=10, max=100) — records per page

    Response (HTTP 200):
    ```json
    {
      "events": [...],
      "total": 42,
      "page": 1,
      "page_size": 10
    }
    ```

    HTTP 502 is returned when ``ops-store`` is unreachable or returns an
    error status.

Design notes
------------
* ``ops-store`` base URL is read from the ``OPS_STORE_URL`` environment
  variable (default: ``http://ops-store:8002``).

* The endpoint forwards all query parameters to ops-store as-is; the
  store service owns the query logic (filtering, pagination).

* When ops-store returns any non-200 status, this endpoint propagates
  HTTP 502 (Bad Gateway) to the caller (INIT-US-08-AC2, tasks.md TASK-027).

* X-Advisory-Only: true is injected automatically by
  :class:`~ops_api.app.middleware.advisory.AdvisoryMiddleware` — not set
  here manually (RN-06).

* HTTP client is injected via FastAPI ``Depends`` so tests can override it
  without making real network calls.

Service URL environment variable
---------------------------------
- ``OPS_STORE_URL`` → ``http://ops-store:8002``

References
----------
- tasks.md TASK-027
- spec.md INIT-US-08, INIT-US-08-AC2
- plan.md § 4.5, § 5.8
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, AsyncGenerator, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

router = APIRouter(tags=["observability"])

# ---------------------------------------------------------------------------
# Configuration — ops-store URL
# ---------------------------------------------------------------------------

_DEFAULT_OPS_STORE_URL = "http://ops-store:8002"


def _get_ops_store_url() -> str:
    """Return the base URL of the ops-store service from the environment."""
    return os.environ.get("OPS_STORE_URL", _DEFAULT_OPS_STORE_URL)


# ---------------------------------------------------------------------------
# HTTP client dependency — injected via Depends
# ---------------------------------------------------------------------------


async def get_audit_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield a short-lived async ``httpx.AsyncClient`` for the audit query.

    This dependency is overridden in tests via ``app.dependency_overrides``
    to inject a mock that never makes real network calls.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        yield client


# ---------------------------------------------------------------------------
# Endpoint: GET /audit
# ---------------------------------------------------------------------------


@router.get(
    "/audit",
    summary="Query the audit log of prediction events",
    responses={
        200: {
            "description": (
                "Paginated list of audit events with ``events``, ``total``, "
                "``page``, and ``page_size`` fields."
            )
        },
        502: {
            "description": "ops-store is unreachable or returned an error."
        },
    },
)
async def get_audit(
    request: Request,
    asset_id: Optional[str] = Query(
        default=None,
        description="Filter events by asset identifier.",
    ),
    from_: Optional[datetime] = Query(
        default=None,
        alias="from",
        description="Return events triggered at or after this UTC datetime (ISO 8601).",
    ),
    to: Optional[datetime] = Query(
        default=None,
        description="Return events triggered at or before this UTC datetime (ISO 8601).",
    ),
    page: int = Query(
        default=1,
        ge=1,
        description="1-based page number.",
    ),
    page_size: int = Query(
        default=10,
        ge=1,
        le=100,
        description="Number of records per page (maximum 100).",
    ),
    http_client: httpx.AsyncClient = Depends(get_audit_client),
) -> Any:
    """Return a paginated list of audit log events from ``ops-store``.

    Delegates to ``ops-store GET /internal/audit`` forwarding all query
    parameters.  If ops-store is unreachable or returns a non-200 status,
    this endpoint responds with HTTP 502.

    Parameters
    ----------
    request:
        Starlette request — used to forward the ``X-Trace-Id`` header.
    asset_id:
        Optional filter by asset identifier.
    from_:
        Optional lower-bound filter on ``triggered_at`` (inclusive, UTC).
    to:
        Optional upper-bound filter on ``triggered_at`` (inclusive, UTC).
    page:
        1-based page index (default: 1).
    page_size:
        Records per page; maximum 100 (default: 10).
    http_client:
        Injected async ``httpx.AsyncClient``.

    Returns
    -------
    dict
        ``{"events": [...], "total": N, "page": P, "page_size": S}``

    Raises
    ------
    HTTPException
        HTTP 502 when ops-store is unreachable or returns a non-200 status.
    """
    store_url = _get_ops_store_url()

    # Build query params for the upstream call.
    params: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
    }
    if asset_id is not None:
        params["asset_id"] = asset_id
    if from_ is not None:
        params["from"] = from_.isoformat()
    if to is not None:
        params["to"] = to.isoformat()

    # Forward trace context.
    headers: dict[str, str] = {}
    trace_id = request.headers.get("X-Trace-Id")
    if trace_id:
        headers["X-Trace-Id"] = trace_id

    try:
        response = await http_client.get(
            f"{store_url}/internal/audit",
            params=params,
            headers=headers,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ops-store unreachable: {exc}",
        ) from exc

    if response.status_code == 200:
        return response.json()

    raise HTTPException(
        status_code=502,
        detail=f"ops-store returned unexpected status {response.status_code}",
    )
