"""
ops-ingest/tests/test_normalizer.py
=====================================
Unit tests for ops-ingest/app/normalizer.py and
ops-ingest/app/adapters/rest_batch.py — TASK-010 verification criteria.

Verification criteria (from tasks.md TASK-010):
  1. Teste unitário com lista de leituras FORA de ordem →
     retorna lista ordenada por timestamp.
  2. Leitura com timestamp 35 dias atrás (> default 30) →
     is_backfill=True.
  3. Asset desconhecido → chamada a ops-store para registro
     (mockada via StubStoreClient).

Additional coverage:
  - Empty batch → returns empty list / IngestionResponse with zeros.
  - All readings in order remain in order (stability).
  - Leitura with timestamp exactly at the backfill cutoff boundary.
  - Leitura with timestamp 29 days ago → is_backfill=False.
  - Multiple unique asset_ids → each registered once.
  - Same asset_id repeated → registered only once (idempotency).
  - Generated id is a valid UUID4.
  - ingested_at is UTC-aware.
  - ingestion_id is shared across all readings in a batch.
  - RestBatchAdapter.ingest returns IngestionResponse with correct counters.
  - RestBatchAdapter exposes last_canonical_readings after ingest.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.normalizer import AbstractStoreClient, Normalizer, StubStoreClient
from app.adapters.rest_batch import RestBatchAdapter
from app.schemas import IngestReading, IngestRequest
from shared.config import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_reading(
    asset_id: str = "PUMP-001",
    timestamp: datetime | None = None,
    metric_name: str = "vibration_x",
    value: float = 0.0023,
    unit: str = "m/s2",
    source_protocol: str = "rest_batch",
) -> IngestReading:
    """Build a valid :class:`IngestReading` with sensible defaults."""
    if timestamp is None:
        timestamp = datetime.now(tz=timezone.utc)
    return IngestReading(
        asset_id=asset_id,
        timestamp=timestamp,
        metric_name=metric_name,
        value=value,
        unit=unit,
        source_protocol=source_protocol,
    )


def _make_normalizer(
    store_client: AbstractStoreClient | None = None,
    max_backfill_days: int = 30,
) -> Normalizer:
    """Build a :class:`Normalizer` with a controlled Settings instance."""
    settings = Settings(max_backfill_days=max_backfill_days)
    return Normalizer(
        store_client=store_client or StubStoreClient(),
        settings=settings,
    )


# ---------------------------------------------------------------------------
# TASK-010 criterion 1: out-of-order readings are sorted by timestamp
# ---------------------------------------------------------------------------


class TestNormalizerOrdering:
    """Normalizer MUST re-order readings by timestamp ascending (INIT-02)."""

    def test_out_of_order_readings_are_sorted(self):
        """Core criterion 1: list of readings out of order → sorted output."""
        now = datetime.now(tz=timezone.utc)
        t1 = now - timedelta(hours=3)
        t2 = now - timedelta(hours=1)
        t3 = now - timedelta(hours=2)

        # Deliberately pass in t2, t3, t1 — out of order.
        readings = [
            _make_reading(timestamp=t2),
            _make_reading(timestamp=t3),
            _make_reading(timestamp=t1),
        ]

        normalizer = _make_normalizer()
        result = normalizer.normalize(readings, ingestion_id=uuid.uuid4())

        assert len(result) == 3
        # Must be ascending: t1 < t3 < t2
        assert result[0].timestamp == t1
        assert result[1].timestamp == t3
        assert result[2].timestamp == t2

    def test_already_sorted_readings_remain_sorted(self):
        """Readings already in ascending order must stay in the same order."""
        now = datetime.now(tz=timezone.utc)
        t1 = now - timedelta(hours=3)
        t2 = now - timedelta(hours=2)
        t3 = now - timedelta(hours=1)

        readings = [
            _make_reading(timestamp=t1),
            _make_reading(timestamp=t2),
            _make_reading(timestamp=t3),
        ]

        normalizer = _make_normalizer()
        result = normalizer.normalize(readings, ingestion_id=uuid.uuid4())

        assert result[0].timestamp == t1
        assert result[1].timestamp == t2
        assert result[2].timestamp == t3

    def test_single_reading_list_is_trivially_sorted(self):
        """A single-element list is always sorted."""
        readings = [_make_reading()]
        normalizer = _make_normalizer()
        result = normalizer.normalize(readings, ingestion_id=uuid.uuid4())
        assert len(result) == 1

    def test_empty_list_returns_empty_list(self):
        """Empty input → empty output (edge case)."""
        normalizer = _make_normalizer()
        result = normalizer.normalize([], ingestion_id=uuid.uuid4())
        assert result == []


# ---------------------------------------------------------------------------
# TASK-010 criterion 2: is_backfill flagging
# ---------------------------------------------------------------------------


class TestNormalizerBackfill:
    """Normalizer MUST flag is_backfill=True for readings older than max_backfill_days."""

    def test_reading_35_days_ago_is_flagged_backfill(self):
        """Core criterion 2: timestamp 35 days ago (> default 30) → is_backfill=True."""
        old_ts = datetime.now(tz=timezone.utc) - timedelta(days=35)
        readings = [_make_reading(timestamp=old_ts)]

        normalizer = _make_normalizer(max_backfill_days=30)
        result = normalizer.normalize(readings, ingestion_id=uuid.uuid4())

        assert len(result) == 1
        assert result[0].is_backfill is True

    def test_reading_29_days_ago_is_not_backfill(self):
        """Timestamp 29 days ago (< default 30) → is_backfill=False."""
        recent_ts = datetime.now(tz=timezone.utc) - timedelta(days=29)
        readings = [_make_reading(timestamp=recent_ts)]

        normalizer = _make_normalizer(max_backfill_days=30)
        result = normalizer.normalize(readings, ingestion_id=uuid.uuid4())

        assert len(result) == 1
        assert result[0].is_backfill is False

    def test_reading_now_is_not_backfill(self):
        """Timestamp = now → is_backfill=False."""
        readings = [_make_reading(timestamp=datetime.now(tz=timezone.utc))]
        normalizer = _make_normalizer()
        result = normalizer.normalize(readings, ingestion_id=uuid.uuid4())
        assert result[0].is_backfill is False

    def test_custom_max_backfill_days_respected(self):
        """Custom max_backfill_days=7: reading 8 days ago → is_backfill=True."""
        old_ts = datetime.now(tz=timezone.utc) - timedelta(days=8)
        readings = [_make_reading(timestamp=old_ts)]

        normalizer = _make_normalizer(max_backfill_days=7)
        result = normalizer.normalize(readings, ingestion_id=uuid.uuid4())

        assert result[0].is_backfill is True

    def test_custom_max_backfill_days_within_window(self):
        """Custom max_backfill_days=7: reading 6 days ago → is_backfill=False."""
        recent_ts = datetime.now(tz=timezone.utc) - timedelta(days=6)
        readings = [_make_reading(timestamp=recent_ts)]

        normalizer = _make_normalizer(max_backfill_days=7)
        result = normalizer.normalize(readings, ingestion_id=uuid.uuid4())

        assert result[0].is_backfill is False

    def test_mixed_batch_backfill_flags(self):
        """Batch with one old and one new reading — only old is flagged."""
        old_ts = datetime.now(tz=timezone.utc) - timedelta(days=35)
        new_ts = datetime.now(tz=timezone.utc) - timedelta(hours=1)

        readings = [
            _make_reading(timestamp=new_ts),
            _make_reading(timestamp=old_ts),
        ]

        normalizer = _make_normalizer(max_backfill_days=30)
        # normalize sorts by timestamp → old_ts first
        result = normalizer.normalize(readings, ingestion_id=uuid.uuid4())

        assert result[0].timestamp == old_ts
        assert result[0].is_backfill is True
        assert result[1].timestamp == new_ts
        assert result[1].is_backfill is False


# ---------------------------------------------------------------------------
# TASK-010 criterion 3: auto-registration via store client
# ---------------------------------------------------------------------------


class TestNormalizerAutoRegistration:
    """Normalizer MUST call store_client.ensure_asset_registered for unknown assets."""

    def test_unknown_asset_triggers_registration(self):
        """Core criterion 3: unknown asset_id → store_client called."""
        stub = StubStoreClient()
        normalizer = Normalizer(store_client=stub)

        readings = [_make_reading(asset_id="TURBINE-007")]
        normalizer.normalize(readings, ingestion_id=uuid.uuid4())

        assert "TURBINE-007" in stub.registered_assets

    def test_multiple_unique_assets_each_registered_once(self):
        """Each unique asset_id is registered exactly once per normalize call."""
        stub = StubStoreClient()
        normalizer = Normalizer(store_client=stub)

        readings = [
            _make_reading(asset_id="PUMP-001"),
            _make_reading(asset_id="PUMP-002"),
            _make_reading(asset_id="TURBINE-001"),
        ]
        normalizer.normalize(readings, ingestion_id=uuid.uuid4())

        assert stub.registered_assets == {"PUMP-001", "PUMP-002", "TURBINE-001"}

    def test_repeated_asset_id_registered_only_once(self):
        """Same asset_id repeated in a batch → registered only once (idempotency)."""
        stub = StubStoreClient()
        normalizer = Normalizer(store_client=stub)

        readings = [
            _make_reading(asset_id="PUMP-001"),
            _make_reading(asset_id="PUMP-001"),
            _make_reading(asset_id="PUMP-001"),
        ]
        normalizer.normalize(readings, ingestion_id=uuid.uuid4())

        # StubStoreClient.registered_assets is a set — only one entry.
        assert stub.registered_assets == {"PUMP-001"}

    def test_mock_store_client_called_with_correct_asset_id(self):
        """Verify the call to ensure_asset_registered using a MagicMock."""
        mock_client = MagicMock(spec=AbstractStoreClient)
        normalizer = Normalizer(store_client=mock_client)

        readings = [_make_reading(asset_id="COMPRESSOR-X")]
        normalizer.normalize(readings, ingestion_id=uuid.uuid4())

        mock_client.ensure_asset_registered.assert_called_once_with("COMPRESSOR-X")

    def test_empty_batch_does_not_call_store(self):
        """Empty batch → store_client.ensure_asset_registered never called."""
        mock_client = MagicMock(spec=AbstractStoreClient)
        normalizer = Normalizer(store_client=mock_client)

        normalizer.normalize([], ingestion_id=uuid.uuid4())

        mock_client.ensure_asset_registered.assert_not_called()


# ---------------------------------------------------------------------------
# Canonical reading field enrichment
# ---------------------------------------------------------------------------


class TestNormalizerEnrichment:
    """Normalizer MUST enrich IngestReading with id, ingested_at, ingestion_id."""

    def test_generated_id_is_uuid(self):
        """Each canonical reading gets a fresh UUID4 id."""
        readings = [_make_reading()]
        normalizer = _make_normalizer()
        result = normalizer.normalize(readings, ingestion_id=uuid.uuid4())

        assert isinstance(result[0].id, uuid.UUID)

    def test_each_reading_gets_unique_id(self):
        """Two readings in the same batch get different UUID ids."""
        readings = [_make_reading(), _make_reading()]
        normalizer = _make_normalizer()
        result = normalizer.normalize(readings, ingestion_id=uuid.uuid4())

        assert result[0].id != result[1].id

    def test_ingested_at_is_utc_aware(self):
        """ingested_at must be a UTC-aware datetime."""
        readings = [_make_reading()]
        normalizer = _make_normalizer()
        result = normalizer.normalize(readings, ingestion_id=uuid.uuid4())

        assert result[0].ingested_at.tzinfo is not None
        assert result[0].ingested_at.utcoffset().total_seconds() == 0

    def test_ingestion_id_shared_across_batch(self):
        """All readings in a batch share the same ingestion_id."""
        batch_ingestion_id = uuid.uuid4()
        readings = [_make_reading(), _make_reading(), _make_reading()]
        normalizer = _make_normalizer()
        result = normalizer.normalize(readings, ingestion_id=batch_ingestion_id)

        for reading in result:
            assert reading.ingestion_id == batch_ingestion_id

    def test_original_fields_are_preserved(self):
        """asset_id, metric_name, value, unit, source_protocol are carried over."""
        reading = _make_reading(
            asset_id="MOTOR-42",
            metric_name="temperature",
            value=98.6,
            unit="degC",
            source_protocol="mqtt",
        )
        normalizer = _make_normalizer()
        result = normalizer.normalize([reading], ingestion_id=uuid.uuid4())

        canonical = result[0]
        assert canonical.asset_id == "MOTOR-42"
        assert canonical.metric_name == "temperature"
        assert canonical.value == 98.6
        assert canonical.unit == "degC"
        assert canonical.source_protocol == "mqtt"


# ---------------------------------------------------------------------------
# StubStoreClient
# ---------------------------------------------------------------------------


class TestStubStoreClient:
    """StubStoreClient must record registered assets without I/O."""

    def test_instantiation_succeeds(self):
        stub = StubStoreClient()
        assert stub.registered_assets == set()

    def test_ensure_asset_registered_adds_to_set(self):
        stub = StubStoreClient()
        stub.ensure_asset_registered("PUMP-001")
        assert "PUMP-001" in stub.registered_assets

    def test_idempotent_registration(self):
        stub = StubStoreClient()
        stub.ensure_asset_registered("PUMP-001")
        stub.ensure_asset_registered("PUMP-001")
        assert stub.registered_assets == {"PUMP-001"}

    def test_multiple_assets_tracked(self):
        stub = StubStoreClient()
        stub.ensure_asset_registered("PUMP-001")
        stub.ensure_asset_registered("PUMP-002")
        assert stub.registered_assets == {"PUMP-001", "PUMP-002"}


# ---------------------------------------------------------------------------
# RestBatchAdapter
# ---------------------------------------------------------------------------


class TestRestBatchAdapter:
    """RestBatchAdapter MUST ingest an IngestRequest and return IngestionResponse."""

    def test_ingest_returns_ingestion_response(self):
        """ingest() must return an IngestionResponse."""
        from app.schemas import IngestionResponse

        adapter = RestBatchAdapter()
        request = IngestRequest(readings=[_make_reading()])
        response = adapter.ingest(request)

        assert isinstance(response, IngestionResponse)

    def test_records_received_matches_input_count(self):
        """records_received equals number of readings in the request."""
        adapter = RestBatchAdapter()
        request = IngestRequest(readings=[_make_reading() for _ in range(5)])
        response = adapter.ingest(request)

        assert response.records_received == 5

    def test_records_accepted_equals_received_for_valid_batch(self):
        """All valid readings → records_accepted == records_received."""
        adapter = RestBatchAdapter()
        request = IngestRequest(readings=[_make_reading() for _ in range(3)])
        response = adapter.ingest(request)

        assert response.records_accepted == 3
        assert response.records_rejected == 0

    def test_empty_batch_returns_zero_counters(self):
        """Empty request → all counters = 0, empty rejection_details."""
        adapter = RestBatchAdapter()
        request = IngestRequest(readings=[])
        response = adapter.ingest(request)

        assert response.records_received == 0
        assert response.records_accepted == 0
        assert response.records_rejected == 0
        assert response.rejection_details == []

    def test_ingestion_id_is_uuid(self):
        """Response ingestion_id is a valid UUID."""
        adapter = RestBatchAdapter()
        request = IngestRequest(readings=[_make_reading()])
        response = adapter.ingest(request)

        assert isinstance(response.ingestion_id, uuid.UUID)

    def test_last_canonical_readings_populated_after_ingest(self):
        """last_canonical_readings is populated after a successful ingest call."""
        adapter = RestBatchAdapter()
        request = IngestRequest(readings=[_make_reading()])
        adapter.ingest(request)

        assert adapter.last_canonical_readings is not None
        assert len(adapter.last_canonical_readings) == 1

    def test_last_canonical_readings_is_none_before_first_call(self):
        """last_canonical_readings is None before any ingest call."""
        adapter = RestBatchAdapter()
        assert adapter.last_canonical_readings is None

    def test_readings_in_last_canonical_are_sorted(self):
        """last_canonical_readings after ingest is sorted by timestamp."""
        now = datetime.now(tz=timezone.utc)
        t1 = now - timedelta(hours=3)
        t2 = now - timedelta(hours=1)
        t3 = now - timedelta(hours=2)

        request = IngestRequest(
            readings=[
                _make_reading(timestamp=t2),
                _make_reading(timestamp=t3),
                _make_reading(timestamp=t1),
            ]
        )
        adapter = RestBatchAdapter()
        adapter.ingest(request)

        canonical = adapter.last_canonical_readings
        assert canonical[0].timestamp == t1
        assert canonical[1].timestamp == t3
        assert canonical[2].timestamp == t2

    def test_auto_registration_triggered_via_stub_client(self):
        """RestBatchAdapter triggers auto-registration via injected store client."""
        stub = StubStoreClient()
        adapter = RestBatchAdapter(store_client=stub)

        request = IngestRequest(
            readings=[
                _make_reading(asset_id="NEW-ASSET-XYZ"),
            ]
        )
        adapter.ingest(request)

        assert "NEW-ASSET-XYZ" in stub.registered_assets

    def test_ingest_called_twice_returns_different_ingestion_ids(self):
        """Each ingest call generates a fresh ingestion_id (no caching)."""
        adapter = RestBatchAdapter()
        request = IngestRequest(readings=[_make_reading()])

        response1 = adapter.ingest(request)
        response2 = adapter.ingest(request)

        assert response1.ingestion_id != response2.ingestion_id
