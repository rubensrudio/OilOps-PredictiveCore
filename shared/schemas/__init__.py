"""
shared/schemas — Pydantic schemas shared across all OilOps-PredictiveCore
Python services.

Public surface
--------------
CanonicalReading
    The central internal contract for telemetry data after normalisation by
    ``ops-ingest`` (RN-01).  All services that read from or write to
    ``ops-store`` use this schema.
"""

from shared.schemas.canonical import CanonicalReading

__all__: list[str] = ["CanonicalReading"]
