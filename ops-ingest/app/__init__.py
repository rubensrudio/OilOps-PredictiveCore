"""
ops-ingest/app/__init__.py
============================
Package initialiser for the ops-ingest application.

This package implements the ingestion gateway for OilOps-PredictiveCore.
It receives telemetry from external systems (REST batch, MQTT stub, Kafka stub),
normalises it to the canonical schema, and forwards it to ops-store for
persistence.

Modules
-------
- :mod:`app.schemas`  — Pydantic models for ingestion request/response contracts
                        (TASK-009).
"""
