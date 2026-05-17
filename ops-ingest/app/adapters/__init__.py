"""
ops-ingest/app/adapters/__init__.py
=====================================
Adapter package for ops-ingest.

Each adapter is responsible for receiving telemetry from a specific external
protocol (REST batch, MQTT, Kafka, OPC UA) and converting it to the canonical
ingestion interface defined by this package.

Adapters available
------------------
- :mod:`rest_batch`  — HTTP REST batch ingestion (Fase 1 — implemented,
                        see TASK-010 / TASK-012).
- :mod:`mqtt_stub`   — MQTT adapter stub (Fase 2 — raises NotImplementedError).
- :mod:`kafka_stub`  — Kafka adapter stub (Fase 2 — raises NotImplementedError).

All adapters expose the same interface contract:
``ingest(request: IngestRequest) -> IngestionResponse``
"""
