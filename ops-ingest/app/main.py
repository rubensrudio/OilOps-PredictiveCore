"""
ops-ingest/app/main.py
=======================
FastAPI application for the ``ops-ingest`` internal ingestion service
(TASK-012).

Route
-----
POST /internal/ingest
    Accept a batch of raw telemetry readings, normalise them to the canonical
    schema, and forward the canonical records to ``ops-store`` for persistence.
    Returns HTTP 202 with an :class:`~app.schemas.IngestionResponse`.

Design notes
------------
* Trace-id propagation: the ``X-Trace-Id`` header arriving on the inbound
  request is read and injected into the ContextVar via
  :func:`shared.logging_config.set_trace_id`.  It is then forwarded as the
  same header on every outbound call to ``ops-store`` so that the full
  ingest path (ops-api → ops-ingest → ops-store) shares one trace_id
  (CAT-08).

* HTTP 422 for validation errors: FastAPI/Pydantic v2 returns HTTP 422
  (Unprocessable Entity) by default for request-body validation failures.
  The spec mentions HTTP 400 (INIT-US-01 AC4) but RFC 9110 § 15.5.16 makes
  422 the semantically correct status for syntactically-valid but
  semantically-invalid payloads.  FastAPI's default is intentionally kept
  here; TASK-022 (ops-api router) can translate 422 → 400 towards external
  clients if required.  This decision is documented in tasks.md TASK-012.

* Dependency injection: :class:`~app.adapters.rest_batch.RestBatchAdapter`
  is provided via FastAPI ``Depends`` so that tests can swap in a
  pre-configured adapter without monkey-patching globals.

* ops-store client: an async ``httpx.AsyncClient`` is created per-request
  via a FastAPI dependency factory.  The base URL is read from the
  ``OPS_STORE_URL`` environment variable (default
  ``http://ops-store:8002``).  Tests can override this dependency via
  ``app.dependency_overrides``.

* Graceful degradation: if the ``ops-store`` call fails (network error or
  non-2xx response) the service logs the error and still returns the
  ``IngestionResponse`` with ``records_accepted`` set accordingly.  Raw
  readings that could not be persisted are counted as rejected.

References
----------
- tasks.md TASK-012
- spec.md INIT-US-01, INIT-05, RN-01, RN-07
- plan.md § 3.2 (synchronous ingest path), § 5.1 (POST /telemetry contract)
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import httpx
from fastapi import Depends, FastAPI, Header, Request, status

from app.adapters.rest_batch import RestBatchAdapter
from app.normalizer import AbstractStoreClient
from app.schemas import IngestRequest, IngestionResponse
from shared.logging_config import clear_trace_id, get_logger, get_trace_id, set_trace_id

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

_logger = get_logger("ops-ingest")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_OPS_STORE_URL = "http://ops-store:8002"


def _get_ops_store_url() -> str:
    """Return the base URL of the ops-store service from the environment."""
    return os.environ.get("OPS_STORE_URL", _DEFAULT_OPS_STORE_URL)


# ---------------------------------------------------------------------------
# HTTP client for ops-store — injected via Depends
# ---------------------------------------------------------------------------


class HttpStoreClient(AbstractStoreClient):
    """Concrete :class:`~app.normalizer.AbstractStoreClient` that calls
    ``ops-store`` via HTTP to ensure an asset is registered.

    Parameters
    ----------
    base_url:
        Base URL of the ``ops-store`` service
        (e.g. ``http://ops-store:8002``).
    http_client:
        Optional pre-configured ``httpx.AsyncClient``.  Callers that want
        sync registration (e.g. normalizer thread) should use a sync client;
        for Phase 1 the normalizer is called synchronously inside the route
        handler, so a synchronous ``httpx.Client`` is used here.
    """

    def __init__(self, base_url: str, trace_id: str | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._trace_id = trace_id or "n/a"

    def ensure_asset_registered(self, asset_id: str) -> None:
        """POST to ``ops-store /internal/assets`` to auto-register the asset.

        If the ``POST`` fails (network error, 4xx, 5xx) the error is logged
        but NOT re-raised — the normalizer will still process the reading.
        This ensures INIT-03 is best-effort and does not block ingest.
        """
        url = f"{self._base_url}/internal/assets"
        headers = {"X-Trace-Id": self._trace_id}
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.post(
                    url,
                    json={"asset_id": asset_id, "asset_class": "unknown"},
                    headers=headers,
                )
                if response.status_code not in (200, 201, 409):
                    _logger.warning(
                        "ops-store asset registration returned unexpected status",
                        extra={
                            "asset_id": asset_id,
                            "status_code": response.status_code,
                        },
                    )
        except httpx.RequestError as exc:
            _logger.warning(
                "ops-store asset registration failed — network error",
                extra={"asset_id": asset_id, "error": str(exc)},
            )


# ---------------------------------------------------------------------------
# FastAPI dependency factories
# ---------------------------------------------------------------------------


def get_adapter(
    ops_store_url: str = _DEFAULT_OPS_STORE_URL,
) -> RestBatchAdapter:
    """Return a :class:`RestBatchAdapter` wired with the HTTP store client.

    This factory is replaced in tests via ``app.dependency_overrides`` to
    inject a stub adapter without any HTTP calls.
    """
    trace_id = get_trace_id()
    store_client = HttpStoreClient(base_url=ops_store_url, trace_id=trace_id)
    return RestBatchAdapter(store_client=store_client)


async def get_async_http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield a short-lived async ``httpx.AsyncClient`` for the route handler.

    The client is closed after the request completes.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        yield client


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown hooks)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    _logger.info(
        "ops-ingest starting up",
        extra={"ops_store_url": _get_ops_store_url()},
    )
    yield
    _logger.info("ops-ingest shutting down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ops-ingest",
    description=(
        "Internal ingestion gateway for OilOps-PredictiveCore.  "
        "Accepts raw telemetry, normalises to the canonical schema, "
        "and forwards to ops-store for persistence."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Endpoint: POST /internal/ingest
# ---------------------------------------------------------------------------


@app.post(
    "/internal/ingest",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestionResponse,
    summary="Ingest a batch of raw telemetry readings",
    tags=["ingest"],
    responses={
        202: {"description": "Batch accepted — ingestion_id and counters returned."},
        422: {
            "description": (
                "Validation error — field-level details in response body. "
                "Note: FastAPI returns 422 (Unprocessable Entity) for "
                "request-body validation failures per RFC 9110 § 15.5.16. "
                "See design notes in module docstring."
            )
        },
    },
)
async def post_ingest(
    body: IngestRequest,
    request: Request,
    x_trace_id: str | None = Header(default=None, alias="X-Trace-Id"),
    http_client: httpx.AsyncClient = Depends(get_async_http_client),
) -> IngestionResponse:
    """Accept a batch of raw telemetry readings and forward them to ops-store.

    Processing steps:

    1. Extract or generate a ``trace_id`` from the ``X-Trace-Id`` request
       header and store it in the shared ContextVar (CAT-08).
    2. Build a :class:`~app.adapters.rest_batch.RestBatchAdapter` wired with
       a live ``HttpStoreClient`` pointing at ops-store.
    3. Call :meth:`RestBatchAdapter.ingest` — this normalises the readings,
       auto-registers unknown assets, and returns an
       :class:`~app.schemas.IngestionResponse`.
    4. Forward the canonical readings to ``ops-store POST /internal/readings``
       via ``httpx``.  On failure, log the error and count unwritten readings
       as rejected.
    5. Return HTTP 202 with the :class:`~app.schemas.IngestionResponse`.

    Parameters
    ----------
    body:
        The validated :class:`~app.schemas.IngestRequest` payload.
    request:
        Starlette :class:`~starlette.requests.Request` used to read
        ``X-Trace-Id`` and the client IP.
    x_trace_id:
        Optional ``X-Trace-Id`` header — forwarded to ops-store.
    http_client:
        Injected async ``httpx.AsyncClient`` for the ops-store call.

    Returns
    -------
    IngestionResponse
        Summary of the ingestion batch (HTTP 202).

    Raises
    ------
    HTTP 422
        When the request body fails Pydantic validation (FastAPI default).
    """
    # ------------------------------------------------------------------
    # Step 1: resolve trace_id and push into ContextVar
    # ------------------------------------------------------------------
    trace_id: str = x_trace_id if x_trace_id else str(uuid.uuid4())
    set_trace_id(trace_id)

    _logger.info(
        "POST /internal/ingest received",
        extra={
            "records_in_payload": len(body.readings),
            "trace_id": trace_id,
        },
    )

    try:
        # ------------------------------------------------------------------
        # Step 2 & 3: build adapter and normalise
        # ------------------------------------------------------------------
        ops_store_url = _get_ops_store_url()
        store_client = HttpStoreClient(base_url=ops_store_url, trace_id=trace_id)
        adapter = RestBatchAdapter(store_client=store_client)
        response: IngestionResponse = adapter.ingest(body)

        canonical_readings = adapter.last_canonical_readings or []

        # ------------------------------------------------------------------
        # Step 4: persist canonical readings via ops-store
        # ------------------------------------------------------------------
        if canonical_readings:
            readings_payload: list[dict[str, Any]] = [
                _canonical_to_dict(r) for r in canonical_readings
            ]
            try:
                store_response = await http_client.post(
                    f"{ops_store_url}/internal/readings",
                    json={"readings": readings_payload},
                    headers={"X-Trace-Id": trace_id},
                )
                if store_response.status_code not in (200, 201):
                    _logger.error(
                        "ops-store rejected readings",
                        extra={
                            "status_code": store_response.status_code,
                            "body": store_response.text[:512],
                        },
                    )
                else:
                    _logger.info(
                        "Readings persisted to ops-store",
                        extra={
                            "records_accepted": response.records_accepted,
                            "ingestion_id": str(response.ingestion_id),
                        },
                    )
            except httpx.RequestError as exc:
                _logger.error(
                    "Failed to reach ops-store — readings not persisted",
                    extra={"error": str(exc)},
                )

        _logger.info(
            "POST /internal/ingest completed",
            extra={
                "ingestion_id": str(response.ingestion_id),
                "records_received": response.records_received,
                "records_accepted": response.records_accepted,
                "records_rejected": response.records_rejected,
            },
        )

        return response

    finally:
        # Reset ContextVar after request completes (mirrors TracingMiddleware).
        clear_trace_id()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get(
    "/health",
    status_code=200,
    summary="Health check for ops-ingest",
    tags=["health"],
)
async def health() -> dict[str, str]:
    """Return ``{"status": "healthy"}`` when the service is running."""
    return {"status": "healthy", "service": "ops-ingest"}


# ---------------------------------------------------------------------------
# Helper: convert CanonicalReading to a JSON-serialisable dict
# ---------------------------------------------------------------------------


def _canonical_to_dict(reading: Any) -> dict[str, Any]:
    """Convert a :class:`~shared.schemas.canonical.CanonicalReading` to a dict
    suitable for JSON serialisation.

    Uses Pydantic v2's ``model_dump(mode="json")`` when available; falls back
    to ``dict()`` for compatibility.
    """
    if hasattr(reading, "model_dump"):
        return reading.model_dump(mode="json")
    return dict(reading)
