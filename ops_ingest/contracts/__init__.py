"""
ops_ingest/contracts — domain event contracts for OilOps-PredictiveCore.

Public surface
--------------
IngestionCompletedEvent
    Emitted after a batch of telemetry readings has been normalised and
    persisted.  Consumed by ``ops-feature`` to trigger feature computation.

FeaturesComputedEvent
    Emitted after feature extraction completes for an asset window.
    Consumed by ``ops-models`` to trigger inference.

PredictionEmittedEvent
    Emitted after a prediction is persisted for an asset.  Consumed by
    ``ops-explain`` (async SHAP computation) and the ``ops-api``
    WebSocket manager (real-time broadcast to clients).

Design note
-----------
All three events carry a ``trace_id`` field so that a single distributed
trace can be correlated across all OilOps-PredictiveCore services —
satisfying INIT-US-08 AC4 and CAT-07/CAT-08 from the acceptance criteria.

These contracts are intentionally defined as Pydantic models (not plain
dataclasses) so that:

1. Serialisation to/from JSON is handled automatically when Phase 2
   migrates to Kafka message payloads (DA-04).
2. Field-level validation is enforced at the producer side, not just the
   consumer side — consistent with the OWASP input-validation guidance
   applied throughout the project.
"""

from ops_ingest.contracts.events import (
    FeaturesComputedEvent,
    IngestionCompletedEvent,
    PredictionEmittedEvent,
)

__all__: list[str] = [
    "IngestionCompletedEvent",
    "FeaturesComputedEvent",
    "PredictionEmittedEvent",
]
