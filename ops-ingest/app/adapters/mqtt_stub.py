"""
ops-ingest/app/adapters/mqtt_stub.py
=======================================
Stub for the MQTT ingestion adapter (Fase 2).

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

2. **Subscribe** to one or more MQTT topics (configurable via
   ``OILOPS_MQTT_BROKER_URL`` and ``OILOPS_MQTT_TOPIC_FILTER`` environment
   variables) using an async MQTT client (e.g. ``aiomqtt`` or ``gmqtt``).

3. **Parse** incoming MQTT payloads into :class:`~app.schemas.IngestRequest`
   objects, mapping MQTT message fields to the :class:`~app.schemas.IngestReading`
   schema.  The ``source_protocol`` field MUST be set to ``"mqtt"`` on every
   produced :class:`~app.schemas.IngestReading`.

4. **Forward** the parsed :class:`~app.schemas.IngestRequest` to the normalizer
   layer (``ops-ingest/app/normalizer.py``) exactly as ``RestBatchAdapter``
   does, ensuring the canonical schema (RN-01) is produced regardless of the
   originating protocol (RN-07).

5. **Handle** QoS levels 0, 1, and 2.  At-least-once delivery (QoS 1) is the
   recommended minimum for industrial telemetry to avoid data loss.

6. **Propagate** ``trace_id`` from MQTT user properties (MQTT 5.0) or generate a
   fresh UUID when the incoming message does not carry one, keeping the
   distributed tracing contract intact (see ``ops-api/app/middleware/tracing.py``).

7. **Satisfy** all existing unit and integration tests in
   ``ops-ingest/tests/test_mqtt_stub.py`` without modification.

References
----------
- spec.md INIT-US-01 (SCADA/Historian as external actor)
- plan.md DA-04 (HTTP síncrono Fase 1 / Kafka / MQTT preparação Fase 2)
- plan.md section 8 (Áreas Sensíveis — sem credenciais embutidas; usar env vars)
"""

from __future__ import annotations

from ops_ingest.app.schemas import IngestRequest, IngestionResponse


class MQTTAdapter:
    """MQTT ingestion adapter stub — implementation deferred to Fase 2.

    This class exposes the same public interface as ``RestBatchAdapter``
    (method ``ingest``) so that it can be used interchangeably at the service
    layer once fully implemented.

    All methods raise :class:`NotImplementedError` until Fase 2 is started.
    See the module docstring for the full implementation contract.
    """

    def ingest(self, request: IngestRequest) -> IngestionResponse:
        """Ingest a batch of telemetry readings received via MQTT.

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
            Always.  Full MQTT integration is Fase 2 of OilOps-PredictiveCore.
            See module-level docstring for the complete implementation contract.
        """
        raise NotImplementedError(
            "MQTTAdapter.ingest is not implemented. "
            "Full MQTT ingestion support is planned for Fase 2 of OilOps-PredictiveCore. "
            "See ops-ingest/app/adapters/mqtt_stub.py for the implementation contract."
        )
