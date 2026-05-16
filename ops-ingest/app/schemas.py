"""
ops-ingest/app/schemas.py
============================
Pydantic schemas for the ops-ingest service (TASK-009).

These schemas define the *external-facing* ingestion contract — the payload
format that external systems (SCADA historians, REST batch clients, etc.) use
when publishing telemetry to OilOps-PredictiveCore.  They are intentionally
kept separate from the canonical internal schema (``shared/schemas/canonical.py``)
so that the external API surface can evolve independently of the internal
storage contract (RN-07).

Models
------
- :class:`IngestReading`     — A single telemetry data point from an external
                               system.  Corresponds to one row that will be
                               normalised to :class:`~shared.schemas.canonical.
                               CanonicalReading` by the normalizer layer.
- :class:`IngestRequest`     — Batch ingestion payload: a list of
                               :class:`IngestReading` objects.  An empty list
                               is deliberately allowed (idempotent, no-op
                               batch) — TASK-009 verification criterion 2.
- :class:`IngestionResponse` — Response returned by ``POST /internal/ingest``
                               summarising the outcome of the batch.  Contains
                               counters and per-record rejection details to
                               satisfy INIT-05 (records_received,
                               records_accepted, records_rejected).

Design notes
------------
- ``value`` is declared as ``float``.  Pydantic v2 will attempt numeric
  coercion; passing a non-numeric string such as ``"string"`` raises
  :class:`pydantic.ValidationError` — TASK-009 verification criterion 1.
- All string fields use ``min_length=1`` to reject blank values early,
  consistent with OWASP input-validation guidance and INIT-04.
- ``timestamp`` is declared as :class:`pydantic.AwareDatetime` to enforce
  timezone-awareness (UTC contract of RN-01).  Naive datetimes are rejected
  with a :class:`pydantic.ValidationError`.
- ``rejection_details`` defaults to an empty list so callers that do not
  track per-record rejection reasons can omit the field.

Usage
-----
>>> from app.schemas import IngestReading, IngestRequest, IngestionResponse
>>> import uuid
>>> from datetime import datetime, timezone
>>>
>>> reading = IngestReading(
...     asset_id="PUMP-001",
...     timestamp=datetime.now(tz=timezone.utc),
...     metric_name="vibration_x",
...     value=0.0023,
...     unit="m/s2",
...     source_protocol="rest_batch",
... )
>>> batch = IngestRequest(readings=[reading])
>>> response = IngestionResponse(
...     ingestion_id=uuid.uuid4(),
...     records_received=1,
...     records_accepted=1,
...     records_rejected=0,
...     rejection_details=[],
... )
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, Field


class IngestReading(BaseModel):
    """A single telemetry data point received from an external system.

    This is the *external* ingestion contract.  Once validated, an
    :class:`IngestReading` will be transformed by the normalizer layer into a
    :class:`~shared.schemas.canonical.CanonicalReading` with enriched fields
    (``id``, ``ingested_at``, ``ingestion_id``, ``is_backfill``).

    Attributes
    ----------
    asset_id:
        Identifier of the originating asset (e.g. ``"PUMP-001"``).
        Must be a non-empty string of at most 64 characters.
    timestamp:
        Moment at which the measurement was taken, **in UTC**.  Declared as
        :class:`pydantic.AwareDatetime` so that naive datetimes (without
        timezone info) are rejected with a :class:`pydantic.ValidationError`.
        Pydantic parses ISO 8601 strings with a timezone offset automatically.
    metric_name:
        Name of the measured quantity (e.g. ``"vibration_x"``).
        Must be a non-empty string of at most 128 characters.
    value:
        Numeric measurement value as a Python :class:`float`.  Non-numeric
        strings such as ``"string"`` are rejected with a
        :class:`pydantic.ValidationError` (TASK-009 criterion 1).
    unit:
        Engineering unit of the measurement (e.g. ``"m/s2"``).
        Must be a non-empty string of at most 32 characters.
    source_protocol:
        Adapter that originated this reading.  Expected values:
        ``"rest_batch"``, ``"mqtt"``, ``"kafka"``, ``"opcua"``.
        Must be a non-empty string of at most 32 characters.
    """

    model_config = {"populate_by_name": True}

    asset_id: Annotated[str, Field(min_length=1, max_length=64)] = Field(
        description="Canonical identifier of the originating asset (e.g. 'PUMP-001').",
    )
    timestamp: AwareDatetime = Field(
        description=(
            "Moment at which the measurement was taken (UTC). "
            "Must be timezone-aware; naive datetimes are rejected."
        ),
    )
    metric_name: Annotated[str, Field(min_length=1, max_length=128)] = Field(
        description="Name of the measured quantity (e.g. 'vibration_x').",
    )
    value: float = Field(
        description=(
            "Numeric measurement value. "
            "Non-numeric strings are rejected with a ValidationError."
        ),
    )
    unit: Annotated[str, Field(min_length=1, max_length=32)] = Field(
        description="Engineering unit of the measurement (e.g. 'm/s2').",
    )
    source_protocol: Annotated[str, Field(min_length=1, max_length=32)] = Field(
        description=(
            "Adapter that originated this reading "
            "(rest_batch | mqtt | kafka | opcua)."
        ),
    )


class IngestRequest(BaseModel):
    """Batch ingestion payload: a list of :class:`IngestReading` objects.

    An empty list is explicitly allowed — a batch with zero readings is a
    valid, idempotent no-op call.  This satisfies TASK-009 verification
    criterion 2.

    Attributes
    ----------
    readings:
        List of :class:`IngestReading` objects to be ingested.  May be empty.
    """

    model_config = {"populate_by_name": True}

    readings: list[IngestReading] = Field(
        description=(
            "List of telemetry readings to ingest. "
            "An empty list is valid and results in a no-op batch."
        ),
    )


class IngestionResponse(BaseModel):
    """Response returned after processing an :class:`IngestRequest` batch.

    Carries the outcome summary of the ingestion operation, satisfying
    INIT-05 (response with ``ingestion_id``, ``records_received``,
    ``records_accepted``, ``records_rejected``).

    Attributes
    ----------
    ingestion_id:
        UUID identifying this ingestion batch.  Links to the
        ``ingestion_batches`` table in SQLite via ``ops-store``.
    records_received:
        Total number of :class:`IngestReading` objects present in the batch
        payload (regardless of outcome).
    records_accepted:
        Number of readings that passed validation and were forwarded for
        persistence.
    records_rejected:
        Number of readings that failed validation and were not persisted.
        Should equal ``records_received - records_accepted``.
    rejection_details:
        List of dictionaries describing per-record rejection reasons.  Each
        entry typically contains ``index`` (position in the original list),
        ``field`` (offending field name), and ``reason`` (human-readable
        description).  Defaults to an empty list when there are no
        rejections.
    """

    model_config = {"populate_by_name": True}

    ingestion_id: uuid.UUID = Field(
        description="UUID identifying this ingestion batch.",
    )
    records_received: int = Field(
        ge=0,
        description="Total number of readings present in the batch payload.",
    )
    records_accepted: int = Field(
        ge=0,
        description="Number of readings that passed validation and were persisted.",
    )
    records_rejected: int = Field(
        ge=0,
        description="Number of readings that failed validation and were not persisted.",
    )
    rejection_details: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Per-record rejection reasons. "
            "Each entry contains 'index', 'field', and 'reason' keys. "
            "Defaults to an empty list."
        ),
    )
