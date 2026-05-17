"""
ops-feature/app/main.py
========================
FastAPI application for the ``ops-feature`` internal feature-engineering
service (TASK-015 / INIT-US-06).

Routes
------
POST /internal/compute/{asset_id}
    Trigger the :class:`~ops_feature.app.windowing.WindowingPipeline` for the
    given asset.  Returns HTTP 202 when raw readings were found and feature
    computation was started; HTTP 200 with ``{"computed": 0}`` when no raw
    readings exist for the asset.

GET /health
    Health check — returns ``{"status": "healthy", "service": "ops-feature"}``.

Background polling
------------------
A periodic background task (default interval: 30 s, configurable via the
``POLL_INTERVAL_SECONDS`` environment variable) runs inside the FastAPI
lifespan.  It queries ``ops-store`` for all distinct ``asset_id`` values that
have raw readings but no corresponding feature records, then dispatches the
:class:`~ops_feature.app.windowing.WindowingPipeline` for each of them.

Architecture decisions
----------------------
* The ``WindowingPipeline`` receives an HTTP-backed store adapter
  (``HttpDuckDBStoreAdapter``) so that it communicates with ``ops-store`` via
  HTTP, exactly like every other service in the stack.  This keeps
  ``ops-feature`` stateless: it holds no local database handle.
* For tests, the ``get_pipeline`` FastAPI dependency is overridable via
  ``app.dependency_overrides``, allowing tests to inject a pipeline backed by
  an in-memory DuckDB store without any HTTP calls.
* The polling task runs as ``asyncio.create_task`` inside the lifespan context
  manager (DA-04 / plan.md § 3.2).  It is cancelled gracefully on shutdown.
* ``trace_id`` is propagated: each HTTP trigger generates (or receives from the
  ``X-Trace-Id`` header) a trace_id that flows through all log records emitted
  during that request.  The poller uses a fresh UUID per poll cycle.

Advisory notice
---------------
Per RN-06, all service output (logs) includes the advisory notice that this
system is for informational purposes only and is NOT a safety-rated system.

Environment variables
---------------------
OPS_STORE_URL
    Base URL of the ``ops-store`` service (default: ``http://ops-store:8002``).
POLL_INTERVAL_SECONDS
    Seconds between automatic polling cycles (default: 30).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncGenerator

import httpx
from fastapi import Depends, FastAPI, Header, status
from pydantic import BaseModel

from ops_feature.app.windowing import WindowingPipeline
from shared.logging_config import clear_trace_id, get_logger, set_trace_id

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

_logger = get_logger("ops-feature")

# ---------------------------------------------------------------------------
# Advisory notice (RN-06)
# ---------------------------------------------------------------------------

_ADVISORY_NOTICE = (
    "ADVISORY ONLY: OilOps-PredictiveCore is an operational decision-support "
    "system. It does NOT replace safety-instrumented systems (SIS) or any "
    "safety-rated control function. All outputs are advisory."
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_OPS_STORE_URL = "http://ops-store:8002"
_DEFAULT_POLL_INTERVAL = 30


def _get_ops_store_url() -> str:
    """Return the base URL of the ops-store service from the environment."""
    return os.environ.get("OPS_STORE_URL", _DEFAULT_OPS_STORE_URL)


def _get_poll_interval() -> int:
    """Return the polling interval in seconds from the environment."""
    raw = os.environ.get("POLL_INTERVAL_SECONDS", str(_DEFAULT_POLL_INTERVAL))
    try:
        value = int(raw)
        return max(1, value)  # guard: minimum 1 second
    except ValueError:
        _logger.warning(
            "Invalid POLL_INTERVAL_SECONDS value; falling back to default",
            extra={"raw_value": raw, "default": _DEFAULT_POLL_INTERVAL},
        )
        return _DEFAULT_POLL_INTERVAL


# ---------------------------------------------------------------------------
# HTTP-backed StorageInterface adapter
# ---------------------------------------------------------------------------


class HttpDuckDBStoreAdapter:
    """Thin adapter that satisfies the StorageInterface contract used by
    :class:`~ops_feature.app.windowing.WindowingPipeline` by forwarding every
    call to the ``ops-store`` HTTP API.

    Only the methods required by ``WindowingPipeline`` are implemented here:
    - :meth:`get_raw_readings_by_asset`
    - :meth:`write_feature_record`

    All other ``StorageInterface`` methods raise :exc:`NotImplementedError`;
    they are never called by the pipeline.

    Parameters
    ----------
    base_url:
        Base URL of the ``ops-store`` service (e.g. ``http://ops-store:8002``).
    trace_id:
        Current request trace_id, forwarded to ``ops-store`` as the
        ``X-Trace-Id`` HTTP header.
    """

    def __init__(self, base_url: str, trace_id: str = "n/a") -> None:
        self._base_url = base_url.rstrip("/")
        self._trace_id = trace_id

    # ------------------------------------------------------------------
    # Raw readings (read-only from ops-feature perspective)
    # ------------------------------------------------------------------

    def get_raw_readings_by_asset(
        self,
        asset_id: str,
        from_ts: datetime,
        to_ts: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch raw readings from ops-store via HTTP GET.

        Returns an empty list when the call fails (graceful degradation).
        """
        url = f"{self._base_url}/internal/readings/{asset_id}"
        params = {
            "from_ts": from_ts.isoformat(),
            "to_ts": to_ts.isoformat(),
        }
        headers = {"X-Trace-Id": self._trace_id}
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(url, params=params, headers=headers)
            if response.status_code == 200:
                return response.json()
            _logger.warning(
                "ops-store GET /internal/readings returned unexpected status",
                extra={
                    "asset_id": asset_id,
                    "status_code": response.status_code,
                },
            )
            return []
        except httpx.RequestError as exc:
            _logger.error(
                "Failed to reach ops-store for readings — returning empty list",
                extra={"asset_id": asset_id, "error": str(exc)},
            )
            return []

    # ------------------------------------------------------------------
    # Feature records (write)
    # ------------------------------------------------------------------

    def write_feature_record(self, feature_record: dict[str, Any]) -> bool:
        """Persist a feature record via ops-store HTTP POST.

        Returns
        -------
        bool
            ``True`` when the record was newly inserted, ``False`` when it
            was a duplicate (idempotent — CAT-13).  Falls back to ``False``
            on network / HTTP error (graceful degradation).
        """
        url = f"{self._base_url}/internal/features"
        headers = {"X-Trace-Id": self._trace_id}
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    url,
                    json={"feature_record": feature_record},
                    headers=headers,
                )
            if response.status_code in (200, 201):
                data = response.json()
                return bool(data.get("inserted", False))
            _logger.warning(
                "ops-store POST /internal/features returned unexpected status",
                extra={
                    "asset_id": feature_record.get("asset_id"),
                    "status_code": response.status_code,
                },
            )
            return False
        except httpx.RequestError as exc:
            _logger.error(
                "Failed to reach ops-store for writing feature record",
                extra={
                    "asset_id": feature_record.get("asset_id"),
                    "error": str(exc),
                },
            )
            return False

    # ------------------------------------------------------------------
    # Unused StorageInterface methods (raise to expose programming errors)
    # ------------------------------------------------------------------

    def write_raw_readings(self, readings: list[dict[str, Any]]) -> int:
        raise NotImplementedError("ops-feature does not write raw readings")

    def get_feature_records_by_asset(
        self,
        asset_id: str,
        from_ts: datetime,
        to_ts: datetime,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "ops-feature does not read feature records via this adapter"
        )

    def write_prediction(
        self,
        prediction: dict[str, Any],
        audit_event: dict[str, Any],
    ) -> str:
        raise NotImplementedError("ops-feature does not write predictions")

    def get_latest_prediction(self, asset_id: str) -> dict[str, Any] | None:
        raise NotImplementedError("ops-feature does not read predictions")

    def write_audit_event(self, audit_event: dict[str, Any]) -> str:
        raise NotImplementedError("ops-feature does not write audit events")

    def get_audit_log(self, **_kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError("ops-feature does not read audit log")


# ---------------------------------------------------------------------------
# Dependency: pipeline factory
# ---------------------------------------------------------------------------


def get_pipeline() -> WindowingPipeline:
    """Return a :class:`~ops_feature.app.windowing.WindowingPipeline` backed
    by the HTTP adapter pointing at ``ops-store``.

    This function is the FastAPI dependency.  Tests override it via
    ``app.dependency_overrides`` to inject a pipeline backed by an in-memory
    DuckDB store.

    The current ``trace_id`` is read from the shared ContextVar (set earlier
    in the request handler) so that every HTTP call the pipeline makes to
    ``ops-store`` carries the same trace context.
    """
    from shared.logging_config import get_trace_id as _get_trace_id

    trace_id = _get_trace_id()
    store = HttpDuckDBStoreAdapter(
        base_url=_get_ops_store_url(),
        trace_id=trace_id,
    )
    return WindowingPipeline(store=store)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Background polling coroutine
# ---------------------------------------------------------------------------


async def _poll_pending_assets(poll_interval: int, ops_store_url: str) -> None:
    """Periodically query ops-store for assets that have raw readings but no
    feature records, then trigger the WindowingPipeline for each of them.

    The loop runs indefinitely until the enclosing task is cancelled
    (typically on application shutdown).

    Strategy
    --------
    The poll queries ``GET /internal/readings/`` (all assets endpoint) to
    discover any ``asset_id`` with raw readings, then queries
    ``GET /internal/features/{asset_id}`` to check if feature records already
    exist.  Assets without feature records are dispatched to the pipeline.

    Note: in Phase 1, ``ops-store`` exposes ``GET /internal/readings/{asset_id}``
    but not a global "list all assets" endpoint.  The poll therefore uses
    ``GET /internal/assets`` (introduced by TASK-007 / TASK-008) to obtain
    the list of registered assets.

    Parameters
    ----------
    poll_interval:
        Seconds to sleep between cycles.
    ops_store_url:
        Base URL of the ``ops-store`` service.
    """
    _logger.info(
        "Background polling started",
        extra={
            "poll_interval_seconds": poll_interval,
            "ops_store_url": ops_store_url,
            "advisory": _ADVISORY_NOTICE,
        },
    )

    while True:
        await asyncio.sleep(poll_interval)

        poll_trace_id = str(uuid.uuid4())
        set_trace_id(poll_trace_id)

        _logger.info(
            "Poll cycle started",
            extra={"poll_trace_id": poll_trace_id},
        )

        try:
            asset_ids = await _fetch_asset_ids(ops_store_url, poll_trace_id)
            if not asset_ids:
                _logger.debug(
                    "Poll cycle: no registered assets found",
                    extra={"poll_trace_id": poll_trace_id},
                )
            else:
                pending = await _filter_pending_assets(
                    asset_ids, ops_store_url, poll_trace_id
                )
                _logger.info(
                    "Poll cycle: assets pending feature computation",
                    extra={
                        "total_assets": len(asset_ids),
                        "pending_assets": len(pending),
                        "poll_trace_id": poll_trace_id,
                    },
                )
                for asset_id in pending:
                    _dispatch_pipeline(asset_id, ops_store_url, poll_trace_id)

        except asyncio.CancelledError:
            _logger.info(
                "Background polling cancelled — shutting down",
                extra={"poll_trace_id": poll_trace_id},
            )
            raise
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "Unhandled exception in poll cycle — will retry next interval",
                extra={"error": str(exc), "poll_trace_id": poll_trace_id},
                exc_info=True,
            )
        finally:
            clear_trace_id()


async def _fetch_asset_ids(ops_store_url: str, trace_id: str) -> list[str]:
    """Return the list of distinct asset_ids from ops-store.

    Calls ``GET /internal/assets`` which returns a JSON array of string
    asset identifiers (introduced in TASK-015 fix / ops-store TASK-008).

    Returns an empty list on network error or unexpected response shape
    (graceful degradation — poller continues running).
    """
    url = f"{ops_store_url.rstrip('/')}/internal/assets"
    headers = {"X-Trace-Id": trace_id}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            # ops-store returns a plain list[str] of asset_ids.
            if isinstance(data, list):
                return [item for item in data if isinstance(item, str)]
        _logger.warning(
            "ops-store GET /internal/assets returned unexpected status",
            extra={"status_code": response.status_code},
        )
    except httpx.RequestError as exc:
        _logger.warning(
            "Failed to reach ops-store for asset list",
            extra={"error": str(exc)},
        )
    return []


async def _filter_pending_assets(
    asset_ids: list[str],
    ops_store_url: str,
    trace_id: str,
) -> list[str]:
    """Return those asset_ids that have raw readings but no feature records.

    An asset is considered 'pending' if ``GET /internal/readings/{asset_id}``
    returns at least one row and ``GET /internal/features/{asset_id}`` returns
    an empty list.
    """
    pending: list[str] = []
    base = ops_store_url.rstrip("/")
    headers = {"X-Trace-Id": trace_id}

    async with httpx.AsyncClient(timeout=15.0) as client:
        for asset_id in asset_ids:
            try:
                feat_resp = await client.get(
                    f"{base}/internal/features/{asset_id}",
                    headers=headers,
                )
                if feat_resp.status_code == 200:
                    features = feat_resp.json()
                    if not features:
                        # No feature records — check if there are raw readings
                        read_resp = await client.get(
                            f"{base}/internal/readings/{asset_id}",
                            headers=headers,
                        )
                        if read_resp.status_code == 200 and read_resp.json():
                            pending.append(asset_id)
            except httpx.RequestError as exc:
                _logger.warning(
                    "Error checking pending status for asset",
                    extra={"asset_id": asset_id, "error": str(exc)},
                )

    return pending


def _dispatch_pipeline(
    asset_id: str,
    ops_store_url: str,
    trace_id: str,
) -> None:
    """Synchronously run the WindowingPipeline for *asset_id*.

    This is called from the async polling loop; the pipeline itself is
    synchronous (DuckDB and httpx sync clients).  Wrapping it here avoids
    blocking the event loop for extended periods — for Phase 1 ingest volumes,
    the synchronous call is acceptable.  A future improvement (Phase 2) would
    dispatch via a thread-pool executor.
    """
    store = HttpDuckDBStoreAdapter(base_url=ops_store_url, trace_id=trace_id)
    pipeline = WindowingPipeline(store=store)  # type: ignore[arg-type]
    try:
        result = pipeline.run(asset_id)
        _logger.info(
            "Poll: WindowingPipeline completed",
            extra={**result, "triggered_by": "poller"},
        )
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "Poll: WindowingPipeline raised an unhandled exception",
            extra={"asset_id": asset_id, "error": str(exc)},
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown hooks
# ---------------------------------------------------------------------------

_poll_task: asyncio.Task[None] | None = None


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan context manager.

    On startup:
    - Logs service info (including advisory notice per RN-06).
    - Starts the background polling task via ``asyncio.create_task``.

    On shutdown:
    - Cancels the polling task and awaits its completion.
    """
    global _poll_task  # noqa: PLW0603

    poll_interval = _get_poll_interval()
    ops_store_url = _get_ops_store_url()

    _logger.info(
        "ops-feature starting up",
        extra={
            "ops_store_url": ops_store_url,
            "poll_interval_seconds": poll_interval,
            "advisory": _ADVISORY_NOTICE,
        },
    )

    _poll_task = asyncio.create_task(
        _poll_pending_assets(poll_interval, ops_store_url),
        name="ops-feature-poller",
    )

    try:
        yield
    finally:
        _logger.info("ops-feature shutting down — cancelling poller")
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass
        _logger.info("ops-feature shutdown complete")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ops-feature",
    description=(
        "Internal feature-engineering service for OilOps-PredictiveCore. "
        "Triggers WindowingPipeline to compute vibration features from raw "
        "telemetry stored in ops-store. "
        f"{_ADVISORY_NOTICE}"
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ComputeResponse(BaseModel):
    """Response body for POST /internal/compute/{asset_id}."""

    asset_id: str
    computed: int
    windows_processed: int
    records_written: int
    records_skipped: int
    windows_too_small: int


class ComputeNoneResponse(BaseModel):
    """Response body when no raw readings exist for the asset."""

    computed: int = 0


# ---------------------------------------------------------------------------
# Endpoint: POST /internal/compute/{asset_id}
# ---------------------------------------------------------------------------


@app.post(
    "/internal/compute/{asset_id}",
    summary="Trigger WindowingPipeline for a specific asset",
    tags=["compute"],
    responses={
        202: {
            "description": (
                "Feature computation accepted — raw readings exist and the "
                "pipeline was dispatched.  Response includes pipeline statistics."
            ),
            "model": ComputeResponse,
        },
        200: {
            "description": (
                "No raw readings found for the asset — nothing to compute."
            ),
            "model": ComputeNoneResponse,
        },
    },
)
async def post_compute(
    asset_id: str,
    pipeline: WindowingPipeline = Depends(get_pipeline),
    x_trace_id: str | None = Header(default=None, alias="X-Trace-Id"),
) -> Any:
    """Trigger the :class:`~ops_feature.app.windowing.WindowingPipeline` for
    the given *asset_id*.

    Processing steps:

    1. Extract or generate a ``trace_id`` from the ``X-Trace-Id`` request
       header and set it in the shared ContextVar.
    2. The injected ``pipeline`` (via ``Depends``) is already wired to the
       correct store adapter (HTTP-backed in production, in-memory in tests).
    3. Run the ``WindowingPipeline`` for the asset.
    4. Return HTTP 202 with pipeline statistics when raw readings existed;
       HTTP 200 with ``{"computed": 0}`` when no readings were found.

    Parameters
    ----------
    asset_id:
        Canonical asset identifier (path parameter).
    pipeline:
        Injected :class:`~ops_feature.app.windowing.WindowingPipeline`
        (FastAPI dependency — overridable in tests).
    x_trace_id:
        Optional ``X-Trace-Id`` header for distributed tracing (CAT-08).

    Returns
    -------
    HTTP 202
        When raw readings were present (pipeline dispatched).
    HTTP 200
        When no raw readings exist for the asset (nothing to compute).
    """
    trace_id: str = x_trace_id if x_trace_id else str(uuid.uuid4())
    set_trace_id(trace_id)

    _logger.info(
        "POST /internal/compute received",
        extra={
            "asset_id": asset_id,
            "trace_id": trace_id,
            "advisory": _ADVISORY_NOTICE,
        },
    )

    try:
        result = pipeline.run(asset_id)

        # MAJOR-2 fix: check for pipeline error BEFORE checking no_readings.
        # When WindowingPipeline.run() returns a dict with an "error" key it
        # means the pipeline raised an unrecoverable exception for this asset.
        # Returning HTTP 202 in that case would silently discard the error and
        # mislead the caller into believing the computation succeeded.
        if "error" in result:
            error_detail = result["error"]
            _logger.error(
                "POST /internal/compute: pipeline returned an error",
                extra={
                    "asset_id": asset_id,
                    "error": error_detail,
                    "trace_id": trace_id,
                },
            )
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "asset_id": asset_id,
                    "error": error_detail,
                },
            )

        # If the pipeline found no readings (all counters == 0 and no error),
        # return HTTP 200 with computed=0
        no_readings = (
            result.get("windows_processed", 0) == 0
            and result.get("records_written", 0) == 0
        )

        if no_readings:
            _logger.info(
                "POST /internal/compute: no raw readings found",
                extra={"asset_id": asset_id, "trace_id": trace_id},
            )
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"computed": 0},
            )

        _logger.info(
            "POST /internal/compute completed",
            extra={
                "asset_id": asset_id,
                "records_written": result.get("records_written", 0),
                "trace_id": trace_id,
            },
        )

        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "asset_id": result["asset_id"],
                "computed": result.get("records_written", 0),
                "windows_processed": result.get("windows_processed", 0),
                "records_written": result.get("records_written", 0),
                "records_skipped": result.get("records_skipped", 0),
                "windows_too_small": result.get("windows_too_small", 0),
            },
        )

    finally:
        clear_trace_id()


# ---------------------------------------------------------------------------
# Endpoint: GET /health
# ---------------------------------------------------------------------------


@app.get(
    "/health",
    status_code=200,
    summary="Health check for ops-feature",
    tags=["health"],
)
async def health() -> dict[str, str]:
    """Return ``{"status": "healthy"}`` when the service is running."""
    return {"status": "healthy", "service": "ops-feature"}
