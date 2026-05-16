"""
shared/schemas/canonical.py
============================
Pydantic model representing the **canonical telemetry reading** — the single
internal contract that every OilOps-PredictiveCore service uses once raw
telemetry has been normalised by ``ops-ingest``.

This schema maps directly to the ``raw_readings`` table defined in
``plan.md`` § 4.1 and fulfils **RN-01** (every reading persisted in the
system, regardless of its source protocol, must conform to this schema).

All fields that are strings enforce ``min_length=1`` so that blank values are
rejected at the boundary — consistent with OWASP input-validation guidance and
the spec's INIT-04 (validation with descriptive messages).

Usage
-----
>>> from shared.schemas.canonical import CanonicalReading
>>> import uuid
>>> from datetime import datetime, timezone
>>> r = CanonicalReading(
...     id=uuid.uuid4(),
...     asset_id="PUMP-001",
...     timestamp=datetime.now(tz=timezone.utc),
...     metric_name="vibration_x",
...     value=0.0023,
...     unit="m/s2",
...     source_protocol="rest_batch",
...     ingested_at=datetime.now(tz=timezone.utc),
...     ingestion_id=uuid.uuid4(),
... )
>>> r.is_backfill
False
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class CanonicalReading(BaseModel):
    """Schema canônico de telemetria (RN-01).

    This is the central contract shared by all Python services within the
    OilOps-PredictiveCore stack.  Every reading that enters the system — via
    REST batch, MQTT stub, or Kafka stub — **must** be normalised to this
    schema before it is persisted in ``ops-store``.

    Attributes
    ----------
    id:
        Immutable UUID that uniquely identifies this reading record.
    asset_id:
        Canonical identifier of the originating asset (e.g. ``"PUMP-001"``).
        Must be a non-empty string; auto-registration in ``ops-store`` is
        triggered by ``ops-ingest`` when the asset is unknown (INIT-03).
    timestamp:
        Moment at which the measurement was taken, **in UTC**.  Pydantic
        parses ISO 8601 strings automatically.
    metric_name:
        Name of the measured quantity (e.g. ``"vibration_x"``).
    value:
        Numeric measurement value as a Python ``float``.
    unit:
        Engineering unit of the measurement (e.g. ``"m/s2"``).
    source_protocol:
        Adapter that originated this reading.  Expected values:
        ``"rest_batch"``, ``"mqtt"``, ``"kafka"``, ``"opcua"``.
    ingested_at:
        Timestamp at which ``ops-ingest`` created this canonical record,
        **in UTC**.
    ingestion_id:
        UUID of the ingestion batch that produced this record.  Links back
        to ``ingestion_batches`` in SQLite.
    is_backfill:
        ``True`` when ``timestamp`` is older than
        ``OILOPS_MAX_BACKFILL_DAYS`` (default: 30 days).  Defaults to
        ``False`` for all real-time readings.
    """

    model_config = {"populate_by_name": True}

    id: uuid.UUID = Field(
        description="Immutable UUID that uniquely identifies this reading record.",
    )
    asset_id: Annotated[str, Field(min_length=1, max_length=64)] = Field(
        description="Canonical identifier of the originating asset.",
    )
    timestamp: datetime = Field(
        description="Moment at which the measurement was taken (UTC).",
    )
    metric_name: Annotated[str, Field(min_length=1, max_length=128)] = Field(
        description="Name of the measured quantity.",
    )
    value: float = Field(
        description="Numeric measurement value.",
    )
    unit: Annotated[str, Field(min_length=1, max_length=32)] = Field(
        description="Engineering unit of the measurement.",
    )
    source_protocol: Annotated[str, Field(min_length=1, max_length=32)] = Field(
        description=(
            "Adapter that originated this reading "
            "(rest_batch | mqtt | kafka | opcua)."
        ),
    )
    ingested_at: datetime = Field(
        description="Timestamp at which ops-ingest created this canonical record (UTC).",
    )
    ingestion_id: uuid.UUID = Field(
        description="UUID of the ingestion batch that produced this record.",
    )
    is_backfill: bool = Field(
        default=False,
        description=(
            "True when timestamp is older than OILOPS_MAX_BACKFILL_DAYS "
            "(default: 30 days). Defaults to False."
        ),
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("timestamp", "ingested_at", mode="before")
    @classmethod
    def _require_non_empty_datetime_string(cls, v: object) -> object:
        """Reject empty strings before Pydantic attempts datetime parsing.

        Pydantic v2 coerces an empty string to ``None`` in some edge cases;
        making this explicit keeps error messages consistent.
        """
        if isinstance(v, str) and not v.strip():
            raise ValueError("datetime field must not be empty")
        return v
