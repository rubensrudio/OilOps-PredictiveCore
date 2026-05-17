"""
ops-store/tests/test_duckdb_store.py
======================================
Unit tests for DuckDBStore (TASK-006).

All tests use DuckDB in-memory databases so that no filesystem artefact is
produced or leaked between runs.

Criteria verified (from tasks.md TASK-006)
-------------------------------------------
1. ``DuckDBStore`` is importable from ``ops_store.app.db.duckdb_store``.
2. ``DuckDBStore`` is a concrete implementation of ``StorageInterface``
   (instantiation does NOT raise ``TypeError``).
3. ``write_raw_readings(readings)`` inserts rows and returns the count of
   rows actually inserted.
4. ``get_raw_readings_by_asset(asset_id, from_ts, to_ts)`` returns exactly
   the rows that belong to the queried asset and fall within the time window.
5. ``write_feature_record(feature_record)`` returns ``True`` when a new row
   is inserted.
6. ``write_feature_record(feature_record)`` returns ``False`` when a
   duplicate ``(asset_id, window_start, window_end, feature_version)`` is
   detected — INSERT OR IGNORE semantics.  The database still contains
   exactly one record (idempotency).
7. ``get_feature_records_by_asset(asset_id, from_ts, to_ts)`` returns exactly
   the records that belong to the queried asset and fall within the window.
8. Methods belonging to the SQLite backend (``write_prediction``,
   ``get_latest_prediction``, ``write_audit_event``, ``get_audit_log``) raise
   ``NotImplementedError``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from ops_store.app.db.duckdb_store import DuckDBStore
from ops_store.app.storage_interface import StorageInterface


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    """Helper: create an aware UTC datetime."""
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _raw_reading(
    asset_id: str = "PUMP-001",
    ts: datetime | None = None,
    metric_name: str = "vibration_x",
    value: float = 0.0023,
    ingestion_id: str | None = None,
) -> dict:
    """Return a canonical reading dict compatible with raw_readings schema."""
    return {
        "id": str(uuid.uuid4()),
        "asset_id": asset_id,
        "timestamp": (ts or _utc(2026, 5, 16, 10)).isoformat(),
        "metric_name": metric_name,
        "value": value,
        "unit": "m/s2",
        "source_protocol": "rest_batch",
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "ingestion_id": ingestion_id or str(uuid.uuid4()),
        "is_backfill": False,
    }


def _feature_record(
    asset_id: str = "PUMP-001",
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    feature_version: str = "v1",
) -> dict:
    """Return a feature record dict compatible with feature_records schema."""
    ws = window_start or _utc(2026, 5, 16, 10)
    we = window_end or _utc(2026, 5, 16, 11)
    return {
        "id": str(uuid.uuid4()),
        "asset_id": asset_id,
        "window_start": ws.isoformat(),
        "window_end": we.isoformat(),
        "raw_record_ids": json.dumps([str(uuid.uuid4())]),
        "feature_version": feature_version,
        "rms": 0.0123,
        "variance": 0.0001,
        "kurtosis": 3.1,
        "skewness": 0.2,
        "fft_bins": json.dumps([0.1] * 64),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture()
def store() -> DuckDBStore:
    """Provide a fresh in-memory DuckDBStore for each test."""
    return DuckDBStore(db_path=":memory:")


# ---------------------------------------------------------------------------
# Tests -- importability and interface compliance
# ---------------------------------------------------------------------------


class TestDuckDBStoreImport:
    """DuckDBStore is importable and complies with StorageInterface."""

    def test_class_is_importable(self) -> None:
        assert DuckDBStore is not None

    def test_is_concrete_subclass_of_storage_interface(self) -> None:
        assert issubclass(DuckDBStore, StorageInterface)

    def test_instantiation_does_not_raise(self) -> None:
        """DuckDBStore must be concrete: no TypeError from ABC."""
        s = DuckDBStore(db_path=":memory:")
        assert s is not None


# ---------------------------------------------------------------------------
# Tests -- write_raw_readings
# ---------------------------------------------------------------------------


class TestWriteRawReadings:
    """write_raw_readings persists rows and returns the insert count."""

    def test_empty_list_returns_zero(self, store: DuckDBStore) -> None:
        result = store.write_raw_readings([])
        assert result == 0

    def test_single_reading_returns_one(self, store: DuckDBStore) -> None:
        result = store.write_raw_readings([_raw_reading()])
        assert result == 1

    def test_two_readings_returns_two(self, store: DuckDBStore) -> None:
        readings = [
            _raw_reading(asset_id="PUMP-001", ts=_utc(2026, 5, 16, 10)),
            _raw_reading(asset_id="PUMP-001", ts=_utc(2026, 5, 16, 11)),
        ]
        result = store.write_raw_readings(readings)
        assert result == 2

    def test_readings_for_different_assets(self, store: DuckDBStore) -> None:
        readings = [
            _raw_reading(asset_id="PUMP-001"),
            _raw_reading(asset_id="PUMP-002"),
        ]
        result = store.write_raw_readings(readings)
        assert result == 2


# ---------------------------------------------------------------------------
# Tests -- get_raw_readings_by_asset (TASK-006 primary criterion)
# ---------------------------------------------------------------------------


class TestGetRawReadingsByAsset:
    """get_raw_readings_by_asset returns exactly the matching rows."""

    def test_returns_two_readings_for_asset(self, store: DuckDBStore) -> None:
        """Write 2 raw_readings, read back by asset_id+window — returns exactly 2."""
        readings = [
            _raw_reading(asset_id="PUMP-001", ts=_utc(2026, 5, 16, 10)),
            _raw_reading(asset_id="PUMP-001", ts=_utc(2026, 5, 16, 11)),
        ]
        store.write_raw_readings(readings)

        result = store.get_raw_readings_by_asset(
            asset_id="PUMP-001",
            from_ts=_utc(2026, 5, 16, 9),
            to_ts=_utc(2026, 5, 16, 12),
        )
        assert len(result) == 2

    def test_returns_empty_for_unknown_asset(self, store: DuckDBStore) -> None:
        store.write_raw_readings([_raw_reading(asset_id="PUMP-001")])
        result = store.get_raw_readings_by_asset(
            asset_id="UNKNOWN",
            from_ts=_utc(2026, 5, 1),
            to_ts=_utc(2026, 5, 31),
        )
        assert result == []

    def test_time_window_filters_out_of_range(self, store: DuckDBStore) -> None:
        """Reading at 10:00 is outside window [11:00, 12:00) — not returned."""
        store.write_raw_readings([
            _raw_reading(asset_id="PUMP-001", ts=_utc(2026, 5, 16, 10)),
        ])
        result = store.get_raw_readings_by_asset(
            asset_id="PUMP-001",
            from_ts=_utc(2026, 5, 16, 11),
            to_ts=_utc(2026, 5, 16, 12),
        )
        assert result == []

    def test_does_not_return_other_asset_readings(self, store: DuckDBStore) -> None:
        """Readings from PUMP-002 must not appear when querying PUMP-001."""
        store.write_raw_readings([
            _raw_reading(asset_id="PUMP-001", ts=_utc(2026, 5, 16, 10)),
            _raw_reading(asset_id="PUMP-002", ts=_utc(2026, 5, 16, 10)),
        ])
        result = store.get_raw_readings_by_asset(
            asset_id="PUMP-001",
            from_ts=_utc(2026, 5, 16, 9),
            to_ts=_utc(2026, 5, 16, 12),
        )
        assert len(result) == 1
        assert all(r["asset_id"] == "PUMP-001" for r in result)

    def test_result_is_list_of_dicts(self, store: DuckDBStore) -> None:
        store.write_raw_readings([_raw_reading()])
        result = store.get_raw_readings_by_asset(
            asset_id="PUMP-001",
            from_ts=_utc(2026, 5, 16, 9),
            to_ts=_utc(2026, 5, 16, 12),
        )
        assert isinstance(result, list)
        assert isinstance(result[0], dict)

    def test_from_ts_inclusive(self, store: DuckDBStore) -> None:
        """from_ts boundary is inclusive."""
        ts = _utc(2026, 5, 16, 10)
        store.write_raw_readings([_raw_reading(asset_id="PUMP-001", ts=ts)])
        result = store.get_raw_readings_by_asset(
            asset_id="PUMP-001",
            from_ts=ts,
            to_ts=_utc(2026, 5, 16, 12),
        )
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Tests -- write_feature_record (TASK-006 idempotency criterion)
# ---------------------------------------------------------------------------


class TestWriteFeatureRecord:
    """write_feature_record returns True for new rows, False for duplicates."""

    def test_new_record_returns_true(self, store: DuckDBStore) -> None:
        result = store.write_feature_record(_feature_record())
        assert result is True

    def test_duplicate_record_returns_false(self, store: DuckDBStore) -> None:
        """
        Write the same (asset_id, window_start, window_end, feature_version)
        twice — second call returns False (INSERT OR IGNORE).
        """
        rec = _feature_record(
            asset_id="PUMP-001",
            window_start=_utc(2026, 5, 16, 10),
            window_end=_utc(2026, 5, 16, 11),
            feature_version="v1",
        )
        first = store.write_feature_record(rec)
        # Build a second record with a NEW id but same unique key
        dup = dict(rec)
        dup["id"] = str(uuid.uuid4())
        second = store.write_feature_record(dup)

        assert first is True
        assert second is False

    def test_idempotency_database_has_one_record(self, store: DuckDBStore) -> None:
        """After writing the same window twice, the DB contains exactly 1 record."""
        rec = _feature_record(
            asset_id="PUMP-001",
            window_start=_utc(2026, 5, 16, 10),
            window_end=_utc(2026, 5, 16, 11),
            feature_version="v1",
        )
        store.write_feature_record(rec)
        dup = dict(rec)
        dup["id"] = str(uuid.uuid4())
        store.write_feature_record(dup)

        result = store.get_feature_records_by_asset(
            asset_id="PUMP-001",
            from_ts=_utc(2026, 5, 16, 9),
            to_ts=_utc(2026, 5, 16, 12),
        )
        assert len(result) == 1

    def test_different_version_creates_new_record(self, store: DuckDBStore) -> None:
        """Same asset+window with different feature_version => two rows."""
        rec_v1 = _feature_record(feature_version="v1")
        rec_v2 = _feature_record(feature_version="v2")
        r1 = store.write_feature_record(rec_v1)
        r2 = store.write_feature_record(rec_v2)
        assert r1 is True
        assert r2 is True


# ---------------------------------------------------------------------------
# Tests -- get_feature_records_by_asset
# ---------------------------------------------------------------------------


class TestGetFeatureRecordsByAsset:
    """get_feature_records_by_asset returns exactly the matching rows."""

    def test_returns_matching_records(self, store: DuckDBStore) -> None:
        rec = _feature_record(
            asset_id="PUMP-001",
            window_start=_utc(2026, 5, 16, 10),
            window_end=_utc(2026, 5, 16, 11),
        )
        store.write_feature_record(rec)

        result = store.get_feature_records_by_asset(
            asset_id="PUMP-001",
            from_ts=_utc(2026, 5, 16, 9),
            to_ts=_utc(2026, 5, 16, 12),
        )
        assert len(result) == 1
        assert result[0]["asset_id"] == "PUMP-001"

    def test_returns_empty_for_unknown_asset(self, store: DuckDBStore) -> None:
        store.write_feature_record(_feature_record(asset_id="PUMP-001"))
        result = store.get_feature_records_by_asset(
            asset_id="UNKNOWN",
            from_ts=_utc(2026, 5, 1),
            to_ts=_utc(2026, 5, 31),
        )
        assert result == []

    def test_does_not_return_other_asset(self, store: DuckDBStore) -> None:
        store.write_feature_record(_feature_record(asset_id="PUMP-001"))
        store.write_feature_record(_feature_record(asset_id="PUMP-002"))
        result = store.get_feature_records_by_asset(
            asset_id="PUMP-001",
            from_ts=_utc(2026, 5, 16, 9),
            to_ts=_utc(2026, 5, 16, 12),
        )
        assert len(result) == 1
        assert result[0]["asset_id"] == "PUMP-001"

    def test_time_window_filter(self, store: DuckDBStore) -> None:
        """Record with window_start outside query window is not returned."""
        store.write_feature_record(_feature_record(
            window_start=_utc(2026, 5, 16, 10),
            window_end=_utc(2026, 5, 16, 11),
        ))
        result = store.get_feature_records_by_asset(
            asset_id="PUMP-001",
            from_ts=_utc(2026, 5, 16, 12),
            to_ts=_utc(2026, 5, 16, 14),
        )
        assert result == []

    def test_result_is_list_of_dicts(self, store: DuckDBStore) -> None:
        store.write_feature_record(_feature_record())
        result = store.get_feature_records_by_asset(
            asset_id="PUMP-001",
            from_ts=_utc(2026, 5, 16, 9),
            to_ts=_utc(2026, 5, 16, 12),
        )
        assert isinstance(result, list)
        assert isinstance(result[0], dict)


# ---------------------------------------------------------------------------
# Tests -- SQLite-only methods raise NotImplementedError
# ---------------------------------------------------------------------------


class TestNotImplementedMethods:
    """write_prediction, get_latest_prediction, write_audit_event, get_audit_log
    are delegated to SQLiteStore (TASK-007) and must raise NotImplementedError."""

    def test_write_prediction_raises(self, store: DuckDBStore) -> None:
        with pytest.raises(NotImplementedError):
            store.write_prediction({}, {})

    def test_get_latest_prediction_raises(self, store: DuckDBStore) -> None:
        with pytest.raises(NotImplementedError):
            store.get_latest_prediction("PUMP-001")

    def test_write_audit_event_raises(self, store: DuckDBStore) -> None:
        with pytest.raises(NotImplementedError):
            store.write_audit_event({})

    def test_get_audit_log_raises(self, store: DuckDBStore) -> None:
        with pytest.raises(NotImplementedError):
            store.get_audit_log()
