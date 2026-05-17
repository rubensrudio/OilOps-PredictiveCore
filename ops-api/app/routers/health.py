"""
ops-api/app/routers/health.py
==============================
Router for ``GET /health``.

This module implements the aggregated health check endpoint for the
ops-api public gateway.  It performs parallel health checks against all
five dependent microservices using ``asyncio.gather`` and returns a
consolidated status.

Route
-----
GET /health
    Perform parallel ``GET /health`` calls to ``ops-ingest``,
    ``ops-store``, ``ops-feature``, ``ops-models``, and ``ops-explain``.

    * HTTP 200 ``{"status": "healthy",  "services": {...}, "checked_at": "..."}``
      when **all** dependent services respond with HTTP 200.
    * HTTP 503 ``{"status": "starting", "services": {...}, "checked_at": "..."}``
      when **any** dependent service responds with a non-200 status, fails
      to connect (``httpx.RequestError``), or raises any exception.

Design notes
------------
* ``asyncio.gather(..., return_exceptions=True)`` is used so a single
  service failure does not abort the fan-out.  Exceptions are treated the
  same as a non-200 response (plan.md edge case spec).

* A service is "starting" when:
  - Its ``GET /health`` returns any status code other than 200.
  - The connection raises ``httpx.RequestError`` (refused, timeout, etc.).

* Service URLs are read from environment variables with Docker Compose
  defaults (plan.md § 3.1, tasks.md TASK-026 design context).

* ``checked_at`` is always included in the response body regardless of
  overall health (both 200 and 503).

* X-Advisory-Only: true is injected by :class:`~ops_api.app.middleware.
  advisory.AdvisoryMiddleware` — no manual handling needed here (RN-06).

Service URL environment variables
----------------------------------
- ``OPS_INGEST_URL``  → ``http://ops-ingest:8001``
- ``OPS_STORE_URL``   → ``http://ops-store:8002``
- ``OPS_FEATURE_URL`` → ``http://ops-feature:8003``
- ``OPS_MODELS_URL``  → ``http://ops-models:8004``
- ``OPS_EXPLAIN_URL`` → ``http://ops-explain:8005``

References
----------
- tasks.md TASK-026
- spec.md INIT-US-05 AC2, edge case "GET /health during initialisation"
- plan.md § 3.1, § 5.6
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

router = APIRouter(tags=["ops"])

# ---------------------------------------------------------------------------
# Configuration — service URLs
# ---------------------------------------------------------------------------

_SERVICE_URL_DEFAULTS: dict[str, str] = {
    "ops-ingest": "http://ops-ingest:8001",
    "ops-store": "http://ops-store:8002",
    "ops-feature": "http://ops-feature:8003",
    "ops-models": "http://ops-models:8004",
    "ops-explain": "http://ops-explain:8005",
}

_SERVICE_URL_ENV: dict[str, str] = {
    "ops-ingest": "OPS_INGEST_URL",
    "ops-store": "OPS_STORE_URL",
    "ops-feature": "OPS_FEATURE_URL",
    "ops-models": "OPS_MODELS_URL",
    "ops-explain": "OPS_EXPLAIN_URL",
}


def _get_service_url(service: str) -> str:
    """Return the base URL for *service* from the environment or the default.

    Parameters
    ----------
    service:
        Canonical service name (e.g. ``"ops-models"``).
    """
    env_var = _SERVICE_URL_ENV[service]
    default = _SERVICE_URL_DEFAULTS[service]
    return os.environ.get(env_var, default)


# ---------------------------------------------------------------------------
# HTTP client dependency — injected via Depends
# ---------------------------------------------------------------------------


async def get_health_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield a short-lived async ``httpx.AsyncClient`` for health checks.

    Connection timeout is kept short (5 s) because a slow response is
    functionally equivalent to an unhealthy service during start-up.

    This dependency is overridden in tests via ``app.dependency_overrides``
    to inject a mock that never makes real network calls.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        yield client


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_HEALTHY = "healthy"
_STARTING = "starting"


async def _check_service(
    client: httpx.AsyncClient,
    service: str,
    url: str,
) -> str:
    """Perform a single ``GET /health`` call and return the service status string.

    Parameters
    ----------
    client:
        Async HTTP client (injected by the endpoint dependency).
    service:
        Service name — used only for logging context; not used in the
        HTTP call itself.
    url:
        Full URL of the service's health endpoint
        (e.g. ``"http://ops-models:8004/health"``).

    Returns
    -------
    str
        ``"healthy"`` when the service responds with HTTP 200;
        ``"starting"`` for any other status or connection error.
    """
    try:
        response = await client.get(url)
        if response.status_code == 200:
            return _HEALTHY
        return _STARTING
    except httpx.RequestError:
        return _STARTING


# ---------------------------------------------------------------------------
# Endpoint: GET /health
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    status_code=200,
    summary="Aggregated health check of all dependent services",
    responses={
        200: {
            "description": (
                "All dependent services are healthy.  "
                "``status`` is ``healthy`` and each ``services`` entry is ``healthy``."
            )
        },
        503: {
            "description": (
                "One or more dependent services are not yet ready.  "
                "``status`` is ``starting``; affected service entries are "
                "``starting``."
            )
        },
    },
)
async def health_check(
    http_client: httpx.AsyncClient = Depends(get_health_client),
) -> JSONResponse:
    """Return the aggregated health status of the ops-api and its dependencies.

    Performs a parallel fan-out ``GET /health`` to each dependent service
    using ``asyncio.gather(return_exceptions=True)``.  A service that does
    not respond with HTTP 200 — or fails to connect — is reported as
    ``"starting"``.

    Parameters
    ----------
    http_client:
        Injected async ``httpx.AsyncClient`` (overridable in tests).

    Returns
    -------
    JSONResponse
        HTTP 200 with ``{"status": "healthy", "services": {...}, "checked_at": "..."}``
        when all five services are up.

        HTTP 503 with ``{"status": "starting", "services": {...}, "checked_at": "..."}``
        when any service is unavailable or still initialising.
    """
    services = list(_SERVICE_URL_DEFAULTS.keys())
    urls = [_get_service_url(svc) + "/health" for svc in services]

    raw_results = await asyncio.gather(
        *[_check_service(http_client, svc, url) for svc, url in zip(services, urls)],
        return_exceptions=True,
    )

    service_status: dict[str, str] = {}
    overall_healthy = True

    for svc, result in zip(services, raw_results):
        if isinstance(result, Exception) or result != _HEALTHY:
            service_status[svc] = _STARTING
            overall_healthy = False
        else:
            service_status[svc] = _HEALTHY

    status_code = 200 if overall_healthy else 503
    status_label = _HEALTHY if overall_healthy else _STARTING

    return JSONResponse(
        status_code=status_code,
        content={
            "status": status_label,
            "services": service_status,
            "checked_at": datetime.now(tz=timezone.utc).isoformat(),
        },
    )
