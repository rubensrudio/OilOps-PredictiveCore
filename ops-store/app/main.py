"""
ops-store/app/main.py
======================
FastAPI application for the ``ops-store`` internal service.

This module exposes six internal HTTP endpoints used by sibling services
(``ops-ingest``, ``ops-feature``, ``ops-models``, ``ops-api``) to read and
write telemetry data, feature records, and predictions.  It is NOT intended
to be exposed to external clients — all routes live under the ``/internal``
prefix.

Routes
------
POST   /internal/readings                        Persist a batch of raw readings (DuckDB)
GET    /internal/readings/{asset_id}             Read raw readings by asset + time window
POST   /internal/features                        Persist a feature record (DuckDB, idempotent)
GET    /internal/features/{asset_id}             Read feature records by asset + time window
POST   /internal/predictions                     Persist prediction + audit event atomically (SQLite)
GET    /internal/predictions/{asset_id}/latest   Return latest prediction or HTTP 404

Dependency injection
--------------------
``DuckDBStore`` and ``SQLiteStore`` are provided via FastAPI ``Depends``.
The factory callables ``get_duckdb_store`` and ``get_sqlite_store`` read the
database paths from environment variables ``OILOPS_DUCKDB_PATH`` (default
``":memory:"``) and ``OILOPS_SQLITE_PATH`` (default ``":memory:"``).

In tests a ``TestClient`` overrides these dependencies to inject in-memory
stores so the tests remain isolated from any filesystem state.

Design notes
------------
* HTTP 201 is used for all successful POSTs (resource created).
* HTTP 404 is used when a GET returns no data (e.g. unknown asset).
* Pydantic models for request/response validation are defined inline in this
  module to keep the surface small (TASK-008 only touches main.py).
* The ``from_ts`` / ``to_ts`` query parameters accept ISO 8601 strings and
  are parsed via ``datetime.fromisoformat``.  FastAPI's built-in ``datetime``
  type is used where possible; the fallback ``from_ts`` / ``to_ts`` default
  to a very wide window when omitted (beginning of epoch … far future) so
  callers that omit the window still get results.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from ops_store.app.db.duckdb_store import DuckDBStore
from ops_store.app.db.sqlite_store import SQLiteStore

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ops-store",
    description="Internal storage service for OilOps-PredictiveCore",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Dependency factories
# ---------------------------------------------------------------------------

_EPOCH_START = datetime(1970, 1, 1, tzinfo=timezone.utc)
_FAR_FUTURE = datetime(9999, 12, 31, tzinfo=timezone.utc)


def get_duckdb_store() -> DuckDBStore:
    """Return a DuckDBStore instance using the configured database path.

    The path is read from the ``OILOPS_DUCKDB_PATH`` environment variable;
    defaults to ``":memory:"`` (in-process ephemeral database) when the
    variable is unset.  Tests override this dependency to inject a shared
    in-memory instance.
    """
    db_path = os.environ.get("OILOPS_DUCKDB_PATH", ":memory:")
    return DuckDBStore(db_path=db_path)


def get_sqlite_store() -> SQLiteStore:
    """Return a SQLiteStore instance using the configured database path.

    The path is read from the ``OILOPS_SQLITE_PATH`` environment variable;
    defaults to ``":memory:"`` when unset.  Tests override this dependency.
    """
    db_path = os.environ.get("OILOPS_SQLITE_PATH", ":memory:")
    return SQLiteStore(db_path=db_path)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class WriteReadingsRequest(BaseModel):
    """Payload for POST /internal/readings."""

    readings: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        description="List of canonical reading dicts (CanonicalReading schema).",
    )


class WriteReadingsResponse(BaseModel):
    """Response for POST /internal/readings."""

    inserted: int = Field(..., description="Number of readings submitted for insertion.")


class WriteFeatureRequest(BaseModel):
    """Payload for POST /internal/features."""

    feature_record: dict[str, Any] = Field(
        ...,
        description="Feature record dict (FeatureRecord schema).",
    )


class WriteFeatureResponse(BaseModel):
    """Response for POST /internal/features."""

    inserted: bool = Field(
        ...,
        description="True when a new record was persisted; False when a duplicate was ignored.",
    )


class WritePredictionRequest(BaseModel):
    """Payload for POST /internal/predictions."""

    prediction: dict[str, Any] = Field(
        ...,
        description="Prediction dict (fields matching the predictions table).",
    )
    audit_event: dict[str, Any] = Field(
        ...,
        description="Audit event dict (fields matching the audit_log table).",
    )


class WritePredictionResponse(BaseModel):
    """Response for POST /internal/predictions."""

    prediction_id: str = Field(..., description="UUID of the newly persisted prediction.")


# ---------------------------------------------------------------------------
# Helper: parse optional ISO 8601 timestamp query params
# ---------------------------------------------------------------------------


def _parse_ts(value: str | None, default: datetime) -> datetime:
    """Parse an ISO 8601 string into an aware datetime, or return *default*.

    If the parsed datetime has no timezone info, UTC is assumed.
    """
    if value is None:
        return default
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Endpoint: POST /internal/readings
# ---------------------------------------------------------------------------


@app.post(
    "/internal/readings",
    status_code=201,
    response_model=WriteReadingsResponse,
    summary="Persist a batch of raw telemetry readings",
    tags=["readings"],
)
def post_readings(
    body: WriteReadingsRequest,
    store: DuckDBStore = Depends(get_duckdb_store),
) -> WriteReadingsResponse:
    """Persist a list of canonical reading dicts into DuckDB ``raw_readings``.

    Returns HTTP 201 with the count of rows submitted.  Duplicate rows (same
    ``id`` UUID) are silently ignored by the store's ``INSERT OR IGNORE``
    logic (RN-02 — immutable after first write).

    Raises
    ------
    HTTP 422
        When the request body fails Pydantic validation (empty list or wrong
        type for ``readings``).
    """
    inserted = store.write_raw_readings(body.readings)
    return WriteReadingsResponse(inserted=inserted)


# ---------------------------------------------------------------------------
# Endpoint: GET /internal/readings/{asset_id}
# ---------------------------------------------------------------------------


@app.get(
    "/internal/readings/{asset_id}",
    status_code=200,
    summary="Read raw readings for an asset within a time window",
    tags=["readings"],
)
def get_readings(
    asset_id: str,
    from_ts: str | None = Query(default=None, description="ISO 8601 UTC start (inclusive)"),
    to_ts: str | None = Query(default=None, description="ISO 8601 UTC end (exclusive)"),
    store: DuckDBStore = Depends(get_duckdb_store),
) -> list[dict[str, Any]]:
    """Return all raw readings for *asset_id* within ``[from_ts, to_ts)``.

    When *from_ts* or *to_ts* are omitted, the widest possible window is used
    (epoch start and far future, respectively), effectively returning all
    readings for the asset.

    Returns an empty list (not 404) when no readings exist for the given
    asset and window.
    """
    start = _parse_ts(from_ts, _EPOCH_START)
    end = _parse_ts(to_ts, _FAR_FUTURE)
    return store.get_raw_readings_by_asset(asset_id, start, end)


# ---------------------------------------------------------------------------
# Endpoint: POST /internal/features
# ---------------------------------------------------------------------------


@app.post(
    "/internal/features",
    status_code=201,
    response_model=WriteFeatureResponse,
    summary="Persist a computed feature record (idempotent)",
    tags=["features"],
)
def post_feature(
    body: WriteFeatureRequest,
    store: DuckDBStore = Depends(get_duckdb_store),
) -> WriteFeatureResponse:
    """Persist *feature_record* into DuckDB ``feature_records``.

    Idempotent: if a record with the same
    ``(asset_id, window_start, window_end, feature_version)`` already exists,
    the duplicate is silently ignored and ``inserted=False`` is returned.

    Returns HTTP 201 in both cases (resource exists either way); callers can
    inspect ``inserted`` to detect duplicates.
    """
    inserted = store.write_feature_record(body.feature_record)
    return WriteFeatureResponse(inserted=inserted)


# ---------------------------------------------------------------------------
# Endpoint: GET /internal/features/{asset_id}
# ---------------------------------------------------------------------------


@app.get(
    "/internal/features/{asset_id}",
    status_code=200,
    summary="Read feature records for an asset within a time window",
    tags=["features"],
)
def get_features(
    asset_id: str,
    from_ts: str | None = Query(default=None, description="ISO 8601 UTC start (inclusive)"),
    to_ts: str | None = Query(default=None, description="ISO 8601 UTC end (exclusive)"),
    store: DuckDBStore = Depends(get_duckdb_store),
) -> list[dict[str, Any]]:
    """Return feature records for *asset_id* within ``[from_ts, to_ts)``.

    Window is matched against ``window_start``.  Returns an empty list when no
    records match.
    """
    start = _parse_ts(from_ts, _EPOCH_START)
    end = _parse_ts(to_ts, _FAR_FUTURE)
    return store.get_feature_records_by_asset(asset_id, start, end)


# ---------------------------------------------------------------------------
# Endpoint: POST /internal/predictions
# ---------------------------------------------------------------------------


@app.post(
    "/internal/predictions",
    status_code=201,
    response_model=WritePredictionResponse,
    summary="Persist a prediction and its audit event atomically",
    tags=["predictions"],
)
def post_prediction(
    body: WritePredictionRequest,
    store: SQLiteStore = Depends(get_sqlite_store),
) -> WritePredictionResponse:
    """Persist *prediction* and *audit_event* in a single SQLite transaction.

    Both rows are written atomically (RN-03 / INIT-US-08-AC3): if the
    ``audit_log`` INSERT fails the entire transaction is rolled back and no
    partial state is left in the database.

    Returns HTTP 201 with the ``prediction_id`` on success.

    Raises
    ------
    HTTP 422
        When the request body fails Pydantic validation.
    HTTP 500
        When the underlying SQLite transaction fails (propagated from the
        store).
    """
    try:
        prediction_id = store.write_prediction(body.prediction, body.audit_event)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return WritePredictionResponse(prediction_id=prediction_id)


# ---------------------------------------------------------------------------
# Endpoint: GET /internal/predictions/{asset_id}/latest
# ---------------------------------------------------------------------------


@app.get(
    "/internal/predictions/{asset_id}/latest",
    status_code=200,
    summary="Return the latest prediction for an asset",
    tags=["predictions"],
)
def get_latest_prediction(
    asset_id: str,
    store: SQLiteStore = Depends(get_sqlite_store),
) -> dict[str, Any]:
    """Return the most recent prediction for *asset_id*.

    Raises
    ------
    HTTP 404
        When no predictions exist for *asset_id*.
    """
    result = store.get_latest_prediction(asset_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No predictions found for asset_id '{asset_id}'",
        )
    return result
