"""
ops-ingest/app/adapters/kafka_stub.py
========================================
Stub for the Kafka ingestion adapter (Fase 2).

This module provides a documented placeholder that satisfies the same interface
contract as ``RestBatchAdapter`` (TASK-010 / TASK-012).  All public methods
raise :class:`NotImplementedError` with a message that clearly communicates the
implementation is deferred to Fase 2 of the project.

Fase 2 Implementation Contract
--------------------------------
When this stub is promoted to a real adapter it MUST:

1. **Expose** ``ingest(request: IngestRequest) -> IngestionResponse`` — the same
   signature used by ``RestBatchAdapter`` so that the service factory in
   ``ops-ingest/app/main.py`` can select an adapter at runtime without changes
   to the calling code (strategy pattern).

2. **Consume** messages from one or more Kafka topics (configurable via
   ``OILOPS_KAFKA_BOOTSTRAP_SERVERS`` and ``OILOPS_KAFKA_TOPIC_FILTER``
   environment variables) using an async Kafka consumer (e.g. ``aiokafka``).
   Consumer group ID MUST be configurable (``OILOPS_KAFKA_CONSUMER_GROUP``,
   default ``"oilops-ingest"``).

3. **Deserialize** Kafka record values into :class:`~app.schemas.IngestRequest`
   objects.  Both JSON and Avro/Schema-Registry formats SHOULD be supported in
   Fase 2; JSON is the mandatory minimum.  The ``source_protocol`` field MUST be
   set to ``"kafka"`` on every produced :class:`~app.schemas.IngestReading`.

4. **Forward** the parsed :class:`~app.schemas.IngestRequest` to the normalizer
   layer (``ops-ingest/app/normalizer.py``) exactly as ``RestBatchAdapter``
   does, ensuring the canonical schema (RN-01) is produced regardless of the
   originating protocol (RN-07).

5. **Commit** Kafka offsets only after the normalizer layer has successfully
   persisted the batch to ``ops-store``, providing at-least-once delivery
   guarantees.

6. **Propagate** ``trace_id`` from Kafka record headers when present, or generate
   a fresh UUID if the header is absent, keeping the distributed tracing contract
   intact (see ``ops-api/app/middleware/tracing.py``).

7. **Satisfy** all existing unit and integration tests in
   ``ops-ingest/tests/test_kafka_stub.py`` without modification.

8. **Replace** the HTTP-based polling approach (Fase 1 DA-04) with Kafka-native
   event streaming for internal service communication (``IngestionCompletedEvent``,
   ``FeaturesComputedEvent``, ``PredictionEmittedEvent`` — see
   ``ops-ingest/contracts/events.py``).

References
----------
- spec.md INIT-US-01 (SCADA/Historian as external actor)
- plan.md DA-04 (HTTP síncrono Fase 1; Kafka Fase 2 — contracts already defined)
- plan.md section 8 (Áreas Sensíveis — sem credenciais embutidas; usar env vars)
- ops-ingest/contracts/events.py (domain event contracts prepared for Kafka migration)
"""

from __future__ import annotations

from ops_ingest.app.schemas import IngestRequest, IngestionResponse


class KafkaAdapter:
    """Kafka ingestion adapter stub — implementation deferred to Fase 2.

    This class exposes the same public interface as ``RestBatchAdapter``
    (method ``ingest``) so that it can be used interchangeably at the service
    layer once fully implemented.

    All methods raise :class:`NotImplementedError` until Fase 2 is started.
    See the module docstring for the full implementation contract.
    """

    def ingest(self, request: IngestRequest) -> IngestionResponse:
        """Ingest a batch of telemetry readings received via Kafka.

        Parameters
        ----------
        request:
            Batch ingestion payload validated against :class:`~app.schemas.IngestRequest`.

        Returns
        -------
        IngestionResponse
            Summary of the ingestion outcome (records received, accepted, rejected).

        Raises
        ------
        NotImplementedError
            Always.  Full Kafka integration is Fase 2 of OilOps-PredictiveCore.
            See module-level docstring for the complete implementation contract.
        """
        raise NotImplementedError(
            "KafkaAdapter.ingest is not implemented. "
            "Full Kafka ingestion support is planned for Fase 2 of OilOps-PredictiveCore. "
            "See ops-ingest/app/adapters/kafka_stub.py for the implementation contract."
        )
