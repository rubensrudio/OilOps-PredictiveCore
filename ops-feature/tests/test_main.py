"""
ops-feature/tests/test_main.py
================================
Tests for the ops-feature FastAPI application (TASK-015).

Test scenarios
--------------
1. POST /internal/compute/{asset_id} with raw readings available returns
   HTTP 202 with pipeline statistics (records_written > 0).
2. POST /internal/compute/{asset_id} for an asset without raw readings returns
   HTTP 200 with {"computed": 0}.
3. POST /internal/compute/{asset_id} with trace_id header propagates trace_id.
4. GET /health returns HTTP 200 with {"status": "healthy"}.
5. POLL_INTERVAL_SECONDS env var: _get_poll_interval() returns correct value.
6. _get_poll_interval() falls back to default on invalid env value.

Design notes
------------
* Tests inject a real WindowingPipeline backed by an in-memory DuckDBStore
  (no HTTP calls).  This is achieved via FastAPI's ``dependency_overrides``
  mechanism using the ``get_pipeline`` dependency factory in the app module.
* The lifespan (background polling task) is disabled in tests by providing a
  separate test app instance without the lifespan, or by overriding the
  app's lifespan.  We use ``TestClient`` which does not invoke the lifespan
  by default when used as a context manager.
* All tests run synchronously using ``starlette.testclient.TestClient``.
"""

from __future__ import annotations

import math
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ops_store.app.db.duckdb_store import DuckDBStore
from ops_feature.app.windowing import WindowingPipeline

# We must import app *after* conftest has registered the module aliases.
from ops_feature.app.main import app, get_pipeline  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_reading(
    asset_id: str,
    value: float,
    ts: datetime,
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
        "ingestion_id": str(uuid.uuid4()),
        "is_backfill": False,
    }


def _build_readings(asset_id: str, n: int = 128) -> list[dict[str, Any]]:
    """Build *n* sinusoidal readings spaced 1 ms apart."""
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
def in_memory_store() -> DuckDBStore:
    """Fresh in-memory DuckDBStore for each test."""
    return DuckDBStore(db_path=":memory:")


@pytest.fixture()
def client_with_data(in_memory_store: DuckDBStore):
    """TestClient with a pipeline backed by an in-memory store that has
    128 raw readings for ASSET-001.
    """
    readings = _build_readings("ASSET-001", n=128)
    in_memory_store.write_raw_readings(readings)

    def _get_pipeline_override() -> WindowingPipeline:
        return WindowingPipeline(  # type: ignore[arg-type]
            store=in_memory_store, window_size=64, fft_bins=64
        )

    app.dependency_overrides[get_pipeline] = _get_pipeline_override
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def client_empty(in_memory_store: DuckDBStore):
    """TestClient with a pipeline backed by an in-memory store with NO readings."""

    def _get_pipeline_override() -> WindowingPipeline:
        return WindowingPipeline(  # type: ignore[arg-type]
            store=in_memory_store, window_size=64, fft_bins=64
        )

    app.dependency_overrides[get_pipeline] = _get_pipeline_override
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests: POST /internal/compute/{asset_id}
# ---------------------------------------------------------------------------


class TestPostCompute:
    """Tests for POST /internal/compute/{asset_id}."""

    def test_returns_202_when_raw_readings_available(
        self, client_with_data: TestClient
    ) -> None:
        """TASK-015 criterion 1: asset with raw readings returns HTTP 202."""
        response = client_with_data.post("/internal/compute/ASSET-001")

        assert response.status_code == 202, (
            f"Expected 202, got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert body["asset_id"] == "ASSET-001"
        assert body["computed"] >= 1, "Expected at least 1 feature record computed"
        assert body["records_written"] >= 1
        assert "windows_processed" in body
        assert "records_skipped" in body
        assert "windows_too_small" in body

    def test_returns_200_computed_zero_when_no_readings(
        self, client_empty: TestClient
    ) -> None:
        """TASK-015 criterion 2: asset without raw readings returns HTTP 200
        with {"computed": 0}.
        """
        response = client_empty.post("/internal/compute/ASSET-NO-DATA")

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert body == {"computed": 0}, (
            f"Expected {{'computed': 0}}, got {body}"
        )

    def test_trace_id_header_is_accepted(
        self, client_with_data: TestClient
    ) -> None:
        """Supplying X-Trace-Id header does not cause errors."""
        custom_trace_id = str(uuid.uuid4())
        response = client_with_data.post(
            "/internal/compute/ASSET-001",
            headers={"X-Trace-Id": custom_trace_id},
        )
        assert response.status_code == 202

    def test_second_compute_is_idempotent(
        self, client_with_data: TestClient
    ) -> None:
        """Running compute twice for the same asset: second call returns 202
        but records_written=0 (all windows already computed — idempotency).
        """
        # First call computes features
        resp1 = client_with_data.post("/internal/compute/ASSET-001")
        assert resp1.status_code == 202
        assert resp1.json()["records_written"] >= 1

        # Second call: all windows already in DB — idempotent, still 202
        # but records_written should be 0 (all skipped)
        resp2 = client_with_data.post("/internal/compute/ASSET-001")
        assert resp2.status_code == 202
        body2 = resp2.json()
        assert body2["records_written"] == 0
        assert body2["records_skipped"] >= 1

    def test_unknown_asset_returns_200_computed_zero(
        self, client_with_data: TestClient
    ) -> None:
        """An asset_id that has no readings in the store returns 200 / computed=0."""
        response = client_with_data.post("/internal/compute/UNKNOWN-ASSET-999")
        assert response.status_code == 200
        assert response.json() == {"computed": 0}

    def test_pipeline_error_returns_500(
        self, in_memory_store: DuckDBStore
    ) -> None:
        """MAJOR-2 fix: when WindowingPipeline.run() returns a dict with 'error',
        the endpoint must return HTTP 500 (not HTTP 202) with the error detail.
        """
        # Build a mock pipeline whose run() always returns an error dict.
        failing_pipeline = MagicMock(spec=WindowingPipeline)
        failing_pipeline.run.return_value = {
            "asset_id": "ASSET-ERR",
            "windows_processed": 0,
            "records_written": 0,
            "records_skipped": 0,
            "windows_too_small": 0,
            "error": "Simulated pipeline failure",
        }

        def _get_failing_pipeline() -> WindowingPipeline:
            return failing_pipeline  # type: ignore[return-value]

        app.dependency_overrides[get_pipeline] = _get_failing_pipeline
        try:
            with TestClient(app, raise_server_exceptions=True) as client:
                response = client.post("/internal/compute/ASSET-ERR")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 500, (
            f"Expected 500 when pipeline returns error, got {response.status_code}: "
            f"{response.text}"
        )
        body = response.json()
        assert body["asset_id"] == "ASSET-ERR"
        assert "error" in body
        assert body["error"] == "Simulated pipeline failure"


# ---------------------------------------------------------------------------
# Tests: GET /health
# ---------------------------------------------------------------------------


class TestHealth:
    """Tests for GET /health."""

    def test_health_returns_200(self, client_empty: TestClient) -> None:
        """GET /health returns HTTP 200."""
        response = client_empty.get("/health")
        assert response.status_code == 200

    def test_health_body_contains_status_healthy(
        self, client_empty: TestClient
    ) -> None:
        """GET /health body contains status=healthy and service=ops-feature."""
        response = client_empty.get("/health")
        body = response.json()
        assert body["status"] == "healthy"
        assert body["service"] == "ops-feature"


# ---------------------------------------------------------------------------
# Tests: configuration helpers
# ---------------------------------------------------------------------------


class TestConfiguration:
    """Tests for _get_poll_interval() configuration helper."""

    def test_default_poll_interval(self) -> None:
        """_get_poll_interval returns 30 when POLL_INTERVAL_SECONDS is unset."""
        from ops_feature.app.main import _get_poll_interval

        with patch.dict(os.environ, {}, clear=False):
            # Remove the key if it exists
            os.environ.pop("POLL_INTERVAL_SECONDS", None)
            result = _get_poll_interval()
        assert result == 30

    def test_custom_poll_interval_from_env(self) -> None:
        """_get_poll_interval returns the value set in POLL_INTERVAL_SECONDS."""
        from ops_feature.app.main import _get_poll_interval

        with patch.dict(os.environ, {"POLL_INTERVAL_SECONDS": "60"}):
            result = _get_poll_interval()
        assert result == 60

    def test_invalid_poll_interval_falls_back_to_default(self) -> None:
        """_get_poll_interval falls back to 30 on a non-integer value."""
        from ops_feature.app.main import _get_poll_interval

        with patch.dict(os.environ, {"POLL_INTERVAL_SECONDS": "not_a_number"}):
            result = _get_poll_interval()
        assert result == 30

    def test_poll_interval_minimum_is_one(self) -> None:
        """_get_poll_interval returns at least 1 even if 0 is passed."""
        from ops_feature.app.main import _get_poll_interval

        with patch.dict(os.environ, {"POLL_INTERVAL_SECONDS": "0"}):
            result = _get_poll_interval()
        assert result >= 1
