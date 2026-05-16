"""
ops-feature/app/schemas.py
============================
Pydantic schemas for the ops-feature service (TASK-013).

Models
------
- :class:`FeatureRecord`   — Feature vector computed for one time window of a
                             vibration signal.  Matches the ``feature_records``
                             DuckDB table defined in the plan (section 4.2).
- :class:`FeatureRequest`  — Input payload to trigger feature computation for
                             a given asset window.

Design notes
------------
- All timestamps are ``datetime`` objects with timezone info (UTC).
- ``fft_bins`` is stored as ``list[float]``; the length is not enforced here
  to keep the schema flexible (configured at the extractor level).
- ``raw_record_ids`` is a list of UUID strings; keeping them as ``str`` rather
  than ``uuid.UUID`` avoids JSON-serialisation friction with DuckDB JSON columns.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field


class FeatureRecord(BaseModel):
    """
    Feature vector persisted in ``feature_records`` (DuckDB).

    All nullable fields (rms, variance, …) are ``None`` when the window was
    skipped due to insufficient samples — though in practice the pipeline
    only writes records for successful extractions.
    """

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this feature record (UUID).",
    )
    asset_id: str = Field(
        ...,
        description="Identifier of the asset whose signal produced this record.",
    )
    window_start: datetime = Field(
        ...,
        description="UTC timestamp of the first sample in the window.",
    )
    window_end: datetime = Field(
        ...,
        description="UTC timestamp of the last sample in the window.",
    )
    raw_record_ids: List[str] = Field(
        default_factory=list,
        description=(
            "List of raw_reading UUIDs whose values were included in this "
            "feature window."
        ),
    )
    feature_version: str = Field(
        default="1.0.0",
        description=(
            "Version string of the feature engineering pipeline that produced "
            "this record.  Allows idempotency checks in the unique index "
            "(asset_id, window_start, window_end, feature_version)."
        ),
    )
    # ------------------------------------------------------------------
    # Computed features (nullable — None means window was skipped)
    # ------------------------------------------------------------------
    rms: Optional[float] = Field(
        default=None,
        description="Root Mean Square of the signal amplitude.",
    )
    variance: Optional[float] = Field(
        default=None,
        description="Population variance (ddof=0) of the signal.",
    )
    kurtosis: Optional[float] = Field(
        default=None,
        description="Fisher (excess) kurtosis of the signal (normal ≈ 0).",
    )
    skewness: Optional[float] = Field(
        default=None,
        description="Fisher skewness of the signal (symmetric ≈ 0).",
    )
    fft_bins: Optional[List[float]] = Field(
        default=None,
        description=(
            "One-sided FFT magnitude spectrum normalised by N.  "
            "Length is determined by OILOPS_FFT_BINS (default: 64)."
        ),
    )
    computed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when this feature record was computed.",
    )


class FeatureRequest(BaseModel):
    """
    Input payload to request feature computation for a specific asset window.

    Sent by the WindoingPipeline (or an HTTP trigger) to the feature extractor.
    """

    asset_id: str = Field(
        ...,
        description="Asset for which features should be computed.",
    )
    window_start: datetime = Field(
        ...,
        description="UTC start of the window to process.",
    )
    window_end: datetime = Field(
        ...,
        description="UTC end of the window to process.",
    )
    feature_version: str = Field(
        default="1.0.0",
        description="Version of the feature pipeline to use.",
    )
