"""
ops-feature/tests/test_windowing.py
=====================================
Integration tests for WindowingPipeline (TASK-014 / CAT-13 / INIT-US-06-AC4).

All tests use a real DuckDB in-memory database (no mocks) as required by the
task criterion.

Test scenarios
--------------
1. Asset A (128 samples, window_size=64): pipeline generates 2 feature records.
2. Asset B (10 samples, window_size=64): pipeline generates 0 feature records
   because no complete window can be formed (10 < 64 = min_window_size).
3. Idempotency (CAT-13): running the pipeline a second time for asset A
   produces 0 new records -- all windows are recognised as duplicates via
   INSERT OR IGNORE on the unique index
   (asset_id, window_start, window_end, feature_version).
4. Asset isolation (INIT-US-06-AC4): a deliberately-broken store for asset C
   does not prevent asset A from being processed successfully.
5. Empty asset: an asset with no raw readings returns an empty result dict
   (windows_processed=0) without error.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from ops_store.app.db.duckdb_store import DuckDBStore
from ops_feature.app.windowing import WindowingPipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_reading(
    asset_id: str,
    value: float,
    ts: datetime,
    ingestion_id: str | None = None,
) -> dict[str, Any]:
    """Build a minimal raw reading dict compatible with DuckDBStore."""
    return {
        "id": str(uuid.uuid4()),
        "asset_id": asset_id,
        "timestamp": ts.isoformat(),
        "metric_name": "vibration_x",
        "value": value,
        "unit": "m/s2",
        "source_protocol": "rest_batch",
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "ingestion_id": ingestion_id or str(uuid.uuid4()),
        "is_backfill": False,
    }


def _ingest_readings(store: DuckDBStore, readings: list[dict[str, Any]]) -> None:
    store.write_raw_readings(readings)


def _count_feature_records(store: DuckDBStore, asset_id: str) -> int:
    """Count feature_records rows for *asset_id* directly via DuckDB."""
    row = store._conn.execute(
        "SELECT COUNT(*) FROM feature_records WHERE asset_id = ?",
        [asset_id],
    ).fetchone()
    return row[0] if row else 0


def _build_readings_for_asset(
    asset_id: str,
    n: int,
    base_ts: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build *n* sinusoidal readings spaced 1 ms apart for *asset_id*."""
    if base_ts is None:
        base_ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return [
        _make_reading(
            asset_id=asset_id,
            value=math.sin(2 * math.pi * i / 64),
            ts=base_ts + timedelta(milliseconds=i),
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store() -> DuckDBStore:
    """Return a fresh in-memory DuckDBStore for each test."""
    return DuckDBStore(db_path=":memory:")


@pytest.fixture()
def pipeline(store: DuckDBStore) -> WindowingPipeline:
    """Return a WindowingPipeline with window_size=64, fft_bins=64."""
    return WindowingPipeline(store=store, window_size=64, fft_bins=64)


# ---------------------------------------------------------------------------
# Test: nominal pipeline execution
# ---------------------------------------------------------------------------


class TestWindowingPipelineNominal:
    """Happy-path tests."""

    def test_asset_a_128_samples_generates_two_records(
        self, store: DuckDBStore, pipeline: WindowingPipeline
    ) -> None:
        """128 samples / window_size=64 => 2 complete windows => 2 feature records."""
        readings_a = _build_readings_for_asset("ASSET-A", n=128)
        _ingest_readings(store, readings_a)

        result = pipeline.run("ASSET-A")

        assert result["asset_id"] == "ASSET-A"
        assert result["windows_processed"] == 2
        assert result["records_written"] == 2
        assert result["records_skipped"] == 0
        assert result["windows_too_small"] == 0
        assert "error" not in result
        assert _count_feature_records(store, "ASSET-A") == 2

    def test_asset_b_10_samples_generates_no_records(
        self, store: DuckDBStore, pipeline: WindowingPipeline
    ) -> None:
        """10 samples < window_size=64 => 0 complete windows => 0 feature records."""
        readings_b = _build_readings_for_asset("ASSET-B", n=10)
        _ingest_readings(store, readings_b)

        result = pipeline.run("ASSET-B")

        assert result["asset_id"] == "ASSET-B"
        assert result["windows_processed"] == 0
        assert result["records_written"] == 0
        assert result["records_skipped"] == 0
        assert result["windows_too_small"] == 0
        assert "error" not in result
        assert _count_feature_records(store, "ASSET-B") == 0

    def test_both_assets_same_store_independent(self, store: DuckDBStore) -> None:
        """Asset A (128 samples) and asset B (10 samples) use the same store.
        A produces 2 records; B produces 0.
        """
        pipeline = WindowingPipeline(store=store, window_size=64, fft_bins=64)

        readings_a = _build_readings_for_asset("ASSET-A", n=128)
        readings_b = _build_readings_for_asset(
            "ASSET-B",
            n=10,
            base_ts=datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc),
        )
        _ingest_readings(store, readings_a)
        _ingest_readings(store, readings_b)

        result_a = pipeline.run("ASSET-A")
        result_b = pipeline.run("ASSET-B")

        assert result_a["records_written"] == 2
        assert result_b["records_written"] == 0
        assert _count_feature_records(store, "ASSET-A") == 2
        assert _count_feature_records(store, "ASSET-B") == 0

    def test_partial_window_at_tail_is_discarded(
        self, store: DuckDBStore, pipeline: WindowingPipeline
    ) -> None:
        """130 samples / 64 => 2 complete windows + 2 leftover => 2 records."""
        readings = _build_readings_for_asset("ASSET-TAIL", n=130)
        _ingest_readings(store, readings)

        result = pipeline.run("ASSET-TAIL")

        assert result["windows_processed"] == 2
        assert result["records_written"] == 2
        assert _count_feature_records(store, "ASSET-TAIL") == 2

    def test_empty_asset_returns_zero_counts(
        self, store: DuckDBStore, pipeline: WindowingPipeline
    ) -> None:
        """Asset with no raw readings: pipeline returns all-zero counters."""
        result = pipeline.run("ASSET-EMPTY")

        assert result["asset_id"] == "ASSET-EMPTY"
        assert result["windows_processed"] == 0
        assert result["records_written"] == 0
        assert "error" not in result

    def test_exactly_one_window(
        self, store: DuckDBStore, pipeline: WindowingPipeline
    ) -> None:
        """Exactly 64 samples => exactly 1 window => 1 feature record."""
        readings = _build_readings_for_asset("ASSET-ONE", n=64)
        _ingest_readings(store, readings)

        result = pipeline.run("ASSET-ONE")

        assert result["windows_processed"] == 1
        assert result["records_written"] == 1
        assert _count_feature_records(store, "ASSET-ONE") == 1


# ---------------------------------------------------------------------------
# Test: idempotency (CAT-13)
# ---------------------------------------------------------------------------


class TestWindowingPipelineIdempotency:
    """Guarantee: running the pipeline twice for the same asset and data set
    yields exactly the same number of feature records as running it once.
    The second run must not insert any new rows.
    """

    def test_second_run_produces_no_duplicates(
        self, store: DuckDBStore, pipeline: WindowingPipeline
    ) -> None:
        """CAT-13: idempotency -- second run inserts 0 new feature records."""
        readings_a = _build_readings_for_asset("ASSET-A", n=128)
        _ingest_readings(store, readings_a)

        # First run
        result1 = pipeline.run("ASSET-A")
        assert result1["records_written"] == 2
        count_after_first = _count_feature_records(store, "ASSET-A")
        assert count_after_first == 2

        # Second run -- all windows already exist in the DB
        result2 = pipeline.run("ASSET-A")
        assert result2["records_written"] == 0
        assert result2["records_skipped"] == 2
        assert result2["windows_processed"] == 2
        assert "error" not in result2

        count_after_second = _count_feature_records(store, "ASSET-A")
        assert count_after_second == 2, (
            f"Expected 2 feature records after second run, got {count_after_second}"
        )

    def test_third_run_also_idempotent(
        self, store: DuckDBStore, pipeline: WindowingPipeline
    ) -> None:
        """Three consecutive runs must not create more than 2 records."""
        readings_a = _build_readings_for_asset("ASSET-TRIPLE", n=128)
        _ingest_readings(store, readings_a)

        pipeline.run("ASSET-TRIPLE")
        pipeline.run("ASSET-TRIPLE")
        pipeline.run("ASSET-TRIPLE")

        assert _count_feature_records(store, "ASSET-TRIPLE") == 2

    def test_idempotency_with_asset_a_and_b(
        self, store: DuckDBStore, pipeline: WindowingPipeline
    ) -> None:
        """Idempotency for asset A does not affect asset B."""
        readings_a = _build_readings_for_asset("ASSET-A", n=128)
        readings_b = _build_readings_for_asset(
            "ASSET-B",
            n=10,
            base_ts=datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc),
        )
        _ingest_readings(store, readings_a)
        _ingest_readings(store, readings_b)

        # First run for both
        r1a = pipeline.run("ASSET-A")
        r1b = pipeline.run("ASSET-B")
        assert r1a["records_written"] == 2
        assert r1b["records_written"] == 0

        # Second run for both
        r2a = pipeline.run("ASSET-A")
        r2b = pipeline.run("ASSET-B")
        assert r2a["records_written"] == 0
        assert r2a["records_skipped"] == 2
        assert r2b["records_written"] == 0

        # Total DB state
        assert _count_feature_records(store, "ASSET-A") == 2
        assert _count_feature_records(store, "ASSET-B") == 0


# ---------------------------------------------------------------------------
# Test: asset isolation (INIT-US-06-AC4)
# ---------------------------------------------------------------------------


class TestWindowingPipelineIsolation:
    """An exception raised while processing one asset must not prevent
    other assets from being processed (INIT-US-06-AC4).
    """

    def test_exception_for_one_asset_is_isolated(self) -> None:
        """Asset isolation: a storage error for one asset returns an error
        result without affecting a second, healthy asset.
        """
        good_store = DuckDBStore(db_path=":memory:")
        readings_a = _build_readings_for_asset("ASSET-A", n=128)
        _ingest_readings(good_store, readings_a)

        pipeline = WindowingPipeline(store=good_store, window_size=64, fft_bins=64)

        # Patch _process_asset to raise for ASSET-C only
        original_process = pipeline._process_asset

        def failing_process(asset_id: str, from_ts: Any, to_ts: Any) -> dict:
            if asset_id == "ASSET-C":
                raise RuntimeError("Simulated storage failure for ASSET-C")
            return original_process(asset_id, from_ts, to_ts)

        pipeline._process_asset = failing_process  # type: ignore[method-assign]

        result_c = pipeline.run("ASSET-C")
        result_a = pipeline.run("ASSET-A")

        # ASSET-C must report error without crashing
        assert "error" in result_c
        assert result_c["records_written"] == 0

        # ASSET-A must succeed independently
        assert "error" not in result_a
        assert result_a["records_written"] == 2
        assert _count_feature_records(good_store, "ASSET-A") == 2

    def test_run_many_continues_after_failure(self) -> None:
        """run_many: failure of asset C does not prevent asset A from completing."""
        store = DuckDBStore(db_path=":memory:")
        readings_a = _build_readings_for_asset("ASSET-A", n=128)
        _ingest_readings(store, readings_a)

        pipeline = WindowingPipeline(store=store, window_size=64, fft_bins=64)

        original_process = pipeline._process_asset

        def failing_process(asset_id: str, from_ts: Any, to_ts: Any) -> dict:
            if asset_id == "ASSET-C":
                raise RuntimeError("Simulated failure")
            return original_process(asset_id, from_ts, to_ts)

        pipeline._process_asset = failing_process  # type: ignore[method-assign]

        results = pipeline.run_many(["ASSET-C", "ASSET-A"])
        assert len(results) == 2

        result_c = next(r for r in results if r["asset_id"] == "ASSET-C")
        result_a = next(r for r in results if r["asset_id"] == "ASSET-A")

        assert "error" in result_c
        assert "error" not in result_a
        assert result_a["records_written"] == 2


# ---------------------------------------------------------------------------
# Test: feature record contents
# ---------------------------------------------------------------------------


class TestWindowingPipelineFeatureContents:
    """Verify that persisted feature records contain the expected fields."""

    def test_feature_record_fields_are_populated(
        self, store: DuckDBStore, pipeline: WindowingPipeline
    ) -> None:
        """Each feature record must have non-null rms, variance, kurtosis,
        skewness, and fft_bins of the correct length.
        """
        readings_a = _build_readings_for_asset("ASSET-A", n=64)
        _ingest_readings(store, readings_a)

        result = pipeline.run("ASSET-A")
        assert result["records_written"] == 1

        row = store._conn.execute(
            "SELECT rms, variance, kurtosis, skewness, fft_bins "
            "FROM feature_records WHERE asset_id = ?",
            ["ASSET-A"],
        ).fetchone()
        assert row is not None

        rms, variance, kurtosis, skewness, fft_bins_json = row
        assert isinstance(rms, float) and rms > 0.0
        assert isinstance(variance, float) and variance >= 0.0
        assert isinstance(kurtosis, float)
        assert isinstance(skewness, float)

        fft_bins = json.loads(fft_bins_json)
        assert isinstance(fft_bins, list)
        assert len(fft_bins) == 64

    def test_feature_record_references_raw_record_ids(
        self, store: DuckDBStore, pipeline: WindowingPipeline
    ) -> None:
        """raw_record_ids in each feature record must reference exactly
        window_size readings (64 IDs).
        """
        readings_a = _build_readings_for_asset("ASSET-A", n=64)
        _ingest_readings(store, readings_a)

        pipeline.run("ASSET-A")

        row = store._conn.execute(
            "SELECT raw_record_ids FROM feature_records WHERE asset_id = ?",
            ["ASSET-A"],
        ).fetchone()
        assert row is not None

        ids = json.loads(row[0])
        assert isinstance(ids, list)
        assert len(ids) == 64

    def test_feature_record_window_timestamps(
        self, store: DuckDBStore, pipeline: WindowingPipeline
    ) -> None:
        """window_start and window_end must correspond to the first and last
        timestamps of the window readings respectively.

        DuckDB returns TIMESTAMPTZ as timezone-aware datetime objects; we
        normalise to UTC for comparison to avoid local-timezone offsets.
        """
        base_ts = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        readings = _build_readings_for_asset("ASSET-TS", n=64, base_ts=base_ts)
        _ingest_readings(store, readings)

        pipeline_local = WindowingPipeline(store=store, window_size=64, fft_bins=64)
        pipeline_local.run("ASSET-TS")

        row = store._conn.execute(
            "SELECT window_start AT TIME ZONE 'UTC', window_end AT TIME ZONE 'UTC' "
            "FROM feature_records WHERE asset_id = ?",
            ["ASSET-TS"],
        ).fetchone()
        assert row is not None

        window_start, window_end = row
        # window_start corresponds to reading[0] timestamp (base_ts + 0ms)
        # window_end corresponds to reading[63] timestamp (base_ts + 63ms)
        expected_start = base_ts
        expected_end = base_ts + timedelta(milliseconds=63)

        # DuckDB AT TIME ZONE 'UTC' returns naive datetime in UTC; compare as
        # seconds-since-epoch to avoid any millisecond representation issues.
        def _to_epoch(ts: Any) -> float:
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return ts.timestamp()
            return datetime.fromisoformat(str(ts)).replace(
                tzinfo=timezone.utc
            ).timestamp()

        assert abs(_to_epoch(window_start) - _to_epoch(expected_start)) < 0.001
        assert abs(_to_epoch(window_end) - _to_epoch(expected_end)) < 0.001
