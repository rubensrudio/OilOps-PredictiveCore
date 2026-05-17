"""
ops-ingest/tests/test_mqtt_stub.py
====================================
Unit tests for the MQTT adapter stub — TASK-011 verification criteria.

Verification criteria (from tasks.md TASK-011):
  Instanciar e chamar método principal de cada stub levanta NotImplementedError
  com mensagem não vazia.

Specifically for MQTTAdapter:
  1. Instantiation succeeds without raising any exception.
  2. Calling ``ingest(request)`` raises ``NotImplementedError``.
  3. The ``NotImplementedError`` message is non-empty.
  4. The message references "Fase 2" to make the deferral reason explicit.
  5. The method signature matches the RestBatchAdapter contract:
     ``ingest(request: IngestRequest) -> IngestionResponse``.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest

from ops_ingest.app.adapters.mqtt_stub import MQTTAdapter
from ops_ingest.app.schemas import IngestRequest, IngestReading


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(n: int = 1) -> IngestRequest:
    """Build a minimal valid :class:`IngestRequest` with *n* readings."""
    reading = IngestReading(
        asset_id="PUMP-001",
        timestamp=datetime.now(tz=timezone.utc),
        metric_name="vibration_x",
        value=0.0023,
        unit="m/s2",
        source_protocol="mqtt",
    )
    return IngestRequest(readings=[reading] * n)


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


class TestMQTTAdapterInstantiation:
    """MQTTAdapter must be instantiable without arguments."""

    def test_instantiation_succeeds(self):
        adapter = MQTTAdapter()
        assert adapter is not None

    def test_instance_is_mqtt_adapter(self):
        adapter = MQTTAdapter()
        assert isinstance(adapter, MQTTAdapter)


# ---------------------------------------------------------------------------
# Interface contract — method signature
# ---------------------------------------------------------------------------


class TestMQTTAdapterInterface:
    """MQTTAdapter.ingest must match the RestBatchAdapter interface contract."""

    def test_ingest_method_exists(self):
        adapter = MQTTAdapter()
        assert hasattr(adapter, "ingest"), "MQTTAdapter must expose an 'ingest' method"

    def test_ingest_is_callable(self):
        adapter = MQTTAdapter()
        assert callable(adapter.ingest)

    def test_ingest_signature_has_request_param(self):
        """ingest(request: IngestRequest) -> IngestionResponse — 'request' param must exist."""
        sig = inspect.signature(MQTTAdapter.ingest)
        assert "request" in sig.parameters, (
            "MQTTAdapter.ingest must accept a 'request' parameter"
        )


# ---------------------------------------------------------------------------
# Core criterion: NotImplementedError with non-empty message
# ---------------------------------------------------------------------------


class TestMQTTAdapterIngestRaisesNotImplemented:
    """
    TASK-011 primary criterion:
    Calling MQTTAdapter.ingest MUST raise NotImplementedError with a
    non-empty message.
    """

    def test_ingest_raises_not_implemented_error(self):
        """Core criterion 1: NotImplementedError is raised."""
        adapter = MQTTAdapter()
        with pytest.raises(NotImplementedError):
            adapter.ingest(_make_request())

    def test_ingest_error_message_is_non_empty(self):
        """Core criterion 2: the error message must not be empty."""
        adapter = MQTTAdapter()
        with pytest.raises(NotImplementedError) as exc_info:
            adapter.ingest(_make_request())
        assert str(exc_info.value), (
            "NotImplementedError message must be non-empty"
        )

    def test_ingest_error_message_references_fase_2(self):
        """Error message should mention 'Fase 2' to explain the deferral."""
        adapter = MQTTAdapter()
        with pytest.raises(NotImplementedError) as exc_info:
            adapter.ingest(_make_request())
        assert "Fase 2" in str(exc_info.value), (
            "NotImplementedError message should reference 'Fase 2'"
        )

    def test_ingest_raises_with_empty_request(self):
        """Stub must raise even when the request has zero readings."""
        adapter = MQTTAdapter()
        empty_request = IngestRequest(readings=[])
        with pytest.raises(NotImplementedError):
            adapter.ingest(empty_request)

    def test_ingest_raises_with_multi_reading_request(self):
        """Stub must raise regardless of the number of readings in the batch."""
        adapter = MQTTAdapter()
        with pytest.raises(NotImplementedError):
            adapter.ingest(_make_request(n=10))
