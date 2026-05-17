"""
ops-ingest/tests/test_kafka_stub.py
======================================
Unit tests for the Kafka adapter stub — TASK-011 verification criteria.

Verification criteria (from tasks.md TASK-011):
  Instanciar e chamar método principal de cada stub levanta NotImplementedError
  com mensagem não vazia.

Specifically for KafkaAdapter:
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

from ops_ingest.app.adapters.kafka_stub import KafkaAdapter
from ops_ingest.app.schemas import IngestRequest, IngestReading


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(n: int = 1) -> IngestRequest:
    """Build a minimal valid :class:`IngestRequest` with *n* readings."""
    reading = IngestReading(
        asset_id="COMPRESSOR-007",
        timestamp=datetime.now(tz=timezone.utc),
        metric_name="vibration_y",
        value=1.5432,
        unit="m/s2",
        source_protocol="kafka",
    )
    return IngestRequest(readings=[reading] * n)


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


class TestKafkaAdapterInstantiation:
    """KafkaAdapter must be instantiable without arguments."""

    def test_instantiation_succeeds(self):
        adapter = KafkaAdapter()
        assert adapter is not None

    def test_instance_is_kafka_adapter(self):
        adapter = KafkaAdapter()
        assert isinstance(adapter, KafkaAdapter)


# ---------------------------------------------------------------------------
# Interface contract — method signature
# ---------------------------------------------------------------------------


class TestKafkaAdapterInterface:
    """KafkaAdapter.ingest must match the RestBatchAdapter interface contract."""

    def test_ingest_method_exists(self):
        adapter = KafkaAdapter()
        assert hasattr(adapter, "ingest"), "KafkaAdapter must expose an 'ingest' method"

    def test_ingest_is_callable(self):
        adapter = KafkaAdapter()
        assert callable(adapter.ingest)

    def test_ingest_signature_has_request_param(self):
        """ingest(request: IngestRequest) -> IngestionResponse — 'request' param must exist."""
        sig = inspect.signature(KafkaAdapter.ingest)
        assert "request" in sig.parameters, (
            "KafkaAdapter.ingest must accept a 'request' parameter"
        )


# ---------------------------------------------------------------------------
# Core criterion: NotImplementedError with non-empty message
# ---------------------------------------------------------------------------


class TestKafkaAdapterIngestRaisesNotImplemented:
    """
    TASK-011 primary criterion:
    Calling KafkaAdapter.ingest MUST raise NotImplementedError with a
    non-empty message.
    """

    def test_ingest_raises_not_implemented_error(self):
        """Core criterion 1: NotImplementedError is raised."""
        adapter = KafkaAdapter()
        with pytest.raises(NotImplementedError):
            adapter.ingest(_make_request())

    def test_ingest_error_message_is_non_empty(self):
        """Core criterion 2: the error message must not be empty."""
        adapter = KafkaAdapter()
        with pytest.raises(NotImplementedError) as exc_info:
            adapter.ingest(_make_request())
        assert str(exc_info.value), (
            "NotImplementedError message must be non-empty"
        )

    def test_ingest_error_message_references_fase_2(self):
        """Error message should mention 'Fase 2' to explain the deferral."""
        adapter = KafkaAdapter()
        with pytest.raises(NotImplementedError) as exc_info:
            adapter.ingest(_make_request())
        assert "Fase 2" in str(exc_info.value), (
            "NotImplementedError message should reference 'Fase 2'"
        )

    def test_ingest_raises_with_empty_request(self):
        """Stub must raise even when the request has zero readings."""
        adapter = KafkaAdapter()
        empty_request = IngestRequest(readings=[])
        with pytest.raises(NotImplementedError):
            adapter.ingest(empty_request)

    def test_ingest_raises_with_multi_reading_request(self):
        """Stub must raise regardless of the number of readings in the batch."""
        adapter = KafkaAdapter()
        with pytest.raises(NotImplementedError):
            adapter.ingest(_make_request(n=10))
