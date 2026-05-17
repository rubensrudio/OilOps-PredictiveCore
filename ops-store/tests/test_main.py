"""
ops-store/tests/test_main.py
==============================
Tests for the FastAPI app defined in ops-store/app/main.py (TASK-008).

Criteria verified (from tasks.md TASK-008)
-------------------------------------------
1. POST /internal/readings with a valid payload returns HTTP 201.
2. POST /internal/readings with an empty ``readings`` list returns HTTP 422
   (Pydantic min_length=1 constraint).
3. GET  /internal/readings/{asset_id} returns the readings previously posted.
4. POST /internal/features with a valid feature_record returns HTTP 201.
5. GET  /internal/features/{asset_id} returns feature records in window.
6. POST /internal/predictions with valid prediction + audit_event returns HTTP 201.
7. GET  /internal/predictions/{asset_id}/latest returns HTTP 404 for unknown asset.
8. GET  /internal/predictions/{asset_id}/latest returns HTTP 200 with prediction data
   after a successful POST.
9. Dependency override: stores are injected via FastAPI Depends so that tests
   use isolated in-memory instances.

Design
------
* ``TestClient`` (synchronous WSGI wrapper from ``starlette.testclient``) is
  used to avoid ``pytest-asyncio`` setup for what are simple HTTP-level tests.
* The DuckDBStore and SQLiteStore dependencies are overridden at the module
  level via ``app.dependency_overrides``.  Each test function receives fresh
  store instances via module-level fixtures to guarantee isolation.
* All stores use ``:memory:`` so no filesystem artefacts are produced.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ops_store.app.db.duckdb_store import DuckDBStore
from ops_store.app.db.sqlite_store import SQLiteStore
from ops_store.app.main import app, get_duckdb_store, get_sqlite_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(*args: int) -> datetime:
    """Construct an aware UTC datetime.  Args forwarded to datetime()."""
    return datetime(*args, tzinfo=timezone.utc)


def _reading(
    asset_id: str = "PUMP-001",
    ts: datetime | None = None,
    value: float = 0.0023,
) -> dict:
    """Return a minimal canonical reading dict."""
    return {
        "id": str(uuid.uuid4()),
        "asset_id": asset_id,
        "timestamp": (ts or _utc(2026, 5, 16, 10)).isoformat(),
        "metric_name": "vibration_x",
        "value": value,
        "unit": "m/s2",
        "source_protocol": "rest_batch",
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "ingestion_id": str(uuid.uuid4()),
        "is_backfill": False,
    }


def _feature(
    asset_id: str = "PUMP-001",
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    feature_version: str = "v1",
) -> dict:
    """Return a minimal feature record dict."""
    ws = window_start or _utc(2026, 5, 16, 10)
    we = window_end or _utc(2026, 5, 16, 11)
    return {
        "id": str(uuid.uuid4()),
        "asset_id": asset_id,
        "window_start": ws.isoformat(),
        "window_end": we.isoformat(),
        "raw_record_ids": [],
        "feature_version": feature_version,
        "rms": 0.0023,
        "variance": 0.0001,
        "kurtosis": 3.1,
        "skewness": 0.05,
        "fft_bins": [0.001] * 64,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def _prediction_and_audit(asset_id: str = "PUMP-001") -> tuple[dict, dict]:
    """Return a minimal (prediction, audit_event) pair."""
    pred_id = str(uuid.uuid4())
    audit_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    prediction = {
        "id": pred_id,
        "asset_id": asset_id,
        "asset_class": "rotating_equipment",
        "anomaly_score": 0.87,
        "confidence_score": 0.92,
        "alert": True,
        "severity": "high",
        "model_id": "vibration-autoencoder-v1",
        "model_version": "1.0.0",
        "feature_record_id": str(uuid.uuid4()),
        "predicted_at": now,
        "explain_status": "pending",
    }
    audit_event = {
        "id": audit_id,
        "event_type": "prediction_emitted",
        "prediction_id": pred_id,
        "asset_id": asset_id,
        "model_version": "1.0.0",
        "triggered_at": now,
        "confidence_score": 0.92,
        "trace_id": str(uuid.uuid4()),
        "details": None,
    }
    return prediction, audit_event


# ---------------------------------------------------------------------------
# Fixtures: shared in-memory store instances + TestClient
# ---------------------------------------------------------------------------


@pytest.fixture()
def duckdb_store() -> DuckDBStore:
    """Fresh DuckDB in-memory store for each test."""
    return DuckDBStore(db_path=":memory:")


@pytest.fixture()
def sqlite_store() -> SQLiteStore:
    """Fresh SQLite in-memory store for each test."""
    return SQLiteStore(db_path=":memory:")


@pytest.fixture()
def client(duckdb_store: DuckDBStore, sqlite_store: SQLiteStore) -> TestClient:
    """TestClient with dependency overrides pointing to in-memory stores.

    The overrides are removed after each test to avoid leaking state between
    test functions.
    """
    app.dependency_overrides[get_duckdb_store] = lambda: duckdb_store
    app.dependency_overrides[get_sqlite_store] = lambda: sqlite_store

    with TestClient(app) as tc:
        yield tc

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests: POST /internal/readings
# ---------------------------------------------------------------------------


class TestPostReadings:
    """Verify POST /internal/readings behaviour."""

    def test_valid_payload_returns_201(self, client: TestClient) -> None:
        """TASK-008 criterion: POST /internal/readings with valid payload -> HTTP 201."""
        payload = {"readings": [_reading()]}
        response = client.post("/internal/readings", json=payload)
        assert response.status_code == 201
        body = response.json()
        assert "inserted" in body
        assert body["inserted"] == 1

    def test_multiple_readings_returns_count(self, client: TestClient) -> None:
        """Posting multiple readings returns the count submitted."""
        payload = {"readings": [_reading(), _reading(asset_id="PUMP-002")]}
        response = client.post("/internal/readings", json=payload)
        assert response.status_code == 201
        assert response.json()["inserted"] == 2

    def test_empty_readings_list_returns_422(self, client: TestClient) -> None:
        """Empty ``readings`` list fails Pydantic min_length=1 -> HTTP 422."""
        response = client.post("/internal/readings", json={"readings": []})
        assert response.status_code == 422

    def test_missing_readings_field_returns_422(self, client: TestClient) -> None:
        """Missing ``readings`` key -> HTTP 422 from Pydantic."""
        response = client.post("/internal/readings", json={})
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Tests: GET /internal/readings/{asset_id}
# ---------------------------------------------------------------------------


class TestGetReadings:
    """Verify GET /internal/readings/{asset_id} behaviour."""

    def test_returns_posted_readings(
        self, client: TestClient, duckdb_store: DuckDBStore
    ) -> None:
        """Readings posted via the store are retrievable via the GET endpoint."""
        r = _reading(asset_id="EQUIP-01", ts=_utc(2026, 5, 16, 10))
        duckdb_store.write_raw_readings([r])

        response = client.get(
            "/internal/readings/EQUIP-01",
            params={
                "from_ts": "2026-05-16T09:00:00+00:00",
                "to_ts": "2026-05-16T11:00:00+00:00",
            },
        )
        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == 1
        assert rows[0]["asset_id"] == "EQUIP-01"

    def test_returns_empty_for_unknown_asset(self, client: TestClient) -> None:
        """Unknown asset returns empty list (not 404)."""
        response = client.get("/internal/readings/UNKNOWN-ASSET")
        assert response.status_code == 200
        assert response.json() == []

    def test_window_filtering(
        self, client: TestClient, duckdb_store: DuckDBStore
    ) -> None:
        """Readings outside the requested window are not returned."""
        inside = _reading(asset_id="A1", ts=_utc(2026, 5, 16, 10))
        outside = _reading(asset_id="A1", ts=_utc(2026, 5, 15, 5))
        duckdb_store.write_raw_readings([inside, outside])

        response = client.get(
            "/internal/readings/A1",
            params={
                "from_ts": "2026-05-16T00:00:00+00:00",
                "to_ts": "2026-05-17T00:00:00+00:00",
            },
        )
        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Tests: POST /internal/features
# ---------------------------------------------------------------------------


class TestPostFeatures:
    """Verify POST /internal/features behaviour."""

    def test_valid_feature_record_returns_201(self, client: TestClient) -> None:
        """Valid feature record persisted -> HTTP 201 with inserted=True."""
        payload = {"feature_record": _feature()}
        response = client.post("/internal/features", json=payload)
        assert response.status_code == 201
        body = response.json()
        assert body["inserted"] is True

    def test_duplicate_feature_record_returns_201_inserted_false(
        self, client: TestClient
    ) -> None:
        """Duplicate (same asset+window+version) -> HTTP 201, inserted=False."""
        fr = _feature()
        payload = {"feature_record": fr}
        r1 = client.post("/internal/features", json=payload)
        assert r1.status_code == 201
        assert r1.json()["inserted"] is True

        r2 = client.post("/internal/features", json=payload)
        assert r2.status_code == 201
        assert r2.json()["inserted"] is False

    def test_missing_feature_record_field_returns_422(
        self, client: TestClient
    ) -> None:
        """Missing ``feature_record`` key -> HTTP 422."""
        response = client.post("/internal/features", json={})
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Tests: GET /internal/features/{asset_id}
# ---------------------------------------------------------------------------


class TestGetFeatures:
    """Verify GET /internal/features/{asset_id} behaviour."""

    def test_returns_posted_feature_records(
        self, client: TestClient, duckdb_store: DuckDBStore
    ) -> None:
        """Feature records written directly to the store are retrievable."""
        fr = _feature(asset_id="ROTOR-01")
        duckdb_store.write_feature_record(fr)

        response = client.get(
            "/internal/features/ROTOR-01",
            params={
                "from_ts": "2026-05-16T09:00:00+00:00",
                "to_ts": "2026-05-16T12:00:00+00:00",
            },
        )
        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == 1
        assert rows[0]["asset_id"] == "ROTOR-01"

    def test_returns_empty_for_unknown_asset(self, client: TestClient) -> None:
        """Unknown asset returns empty list."""
        response = client.get("/internal/features/UNKNOWN-ASSET")
        assert response.status_code == 200
        assert response.json() == []


# ---------------------------------------------------------------------------
# Tests: POST /internal/predictions
# ---------------------------------------------------------------------------


class TestPostPredictions:
    """Verify POST /internal/predictions behaviour."""

    def test_valid_prediction_returns_201(self, client: TestClient) -> None:
        """Valid prediction + audit_event -> HTTP 201 with prediction_id."""
        prediction, audit_event = _prediction_and_audit()

        # Asset must be pre-registered to satisfy FK constraint.
        # Retrieve the sqlite store from the override (side-effect call not needed here)
        sqlite: SQLiteStore = app.dependency_overrides[get_sqlite_store]()
        sqlite.register_asset(
            asset_id=prediction["asset_id"],
            asset_class="rotating_equipment",
            registered_at=datetime.now(timezone.utc).isoformat(),
            metadata=None,
        )
        # model_version must also be registered for FK on model_id.
        sqlite.register_model_version(
            model_id=prediction["model_id"],
            version=prediction["model_version"],
            asset_class="rotating_equipment",
            artifact_path="/data/models/v1.onnx",
            artifact_format="onnx",
            deployed_at=datetime.now(timezone.utc).isoformat(),
            anomaly_threshold=0.5,
            severity_thresholds='{"low":0.5,"medium":0.75,"high":0.9}',
        )

        payload = {"prediction": prediction, "audit_event": audit_event}
        response = client.post("/internal/predictions", json=payload)
        assert response.status_code == 201
        body = response.json()
        assert "prediction_id" in body
        assert body["prediction_id"] == prediction["id"]

    def test_missing_prediction_field_returns_422(
        self, client: TestClient
    ) -> None:
        """Missing ``prediction`` key -> HTTP 422."""
        _, audit_event = _prediction_and_audit()
        response = client.post(
            "/internal/predictions", json={"audit_event": audit_event}
        )
        assert response.status_code == 422

    def test_missing_audit_event_field_returns_422(
        self, client: TestClient
    ) -> None:
        """Missing ``audit_event`` key -> HTTP 422."""
        prediction, _ = _prediction_and_audit()
        response = client.post(
            "/internal/predictions", json={"prediction": prediction}
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Tests: GET /internal/predictions/{asset_id}/latest
# ---------------------------------------------------------------------------


class TestGetLatestPrediction:
    """Verify GET /internal/predictions/{asset_id}/latest behaviour."""

    def test_unknown_asset_returns_404(self, client: TestClient) -> None:
        """TASK-008 primary criterion: unknown asset -> HTTP 404.

        This is the canonical verification criterion stated in tasks.md:
        ``GET /internal/predictions/UNKNOWN/latest`` returns HTTP 404.
        """
        response = client.get("/internal/predictions/UNKNOWN/latest")
        assert response.status_code == 404
        body = response.json()
        assert "detail" in body
        assert "UNKNOWN" in body["detail"]

    def test_returns_latest_prediction_after_post(
        self, client: TestClient, sqlite_store: SQLiteStore
    ) -> None:
        """After writing a prediction directly to the store, GET returns it."""
        prediction, audit_event = _prediction_and_audit(asset_id="ROTOR-ALPHA")

        # Pre-register asset + model so FK constraints are satisfied.
        sqlite_store.register_asset(
            asset_id="ROTOR-ALPHA",
            asset_class="rotating_equipment",
            registered_at=datetime.now(timezone.utc).isoformat(),
            metadata=None,
        )
        sqlite_store.register_model_version(
            model_id=prediction["model_id"],
            version=prediction["model_version"],
            asset_class="rotating_equipment",
            artifact_path="/data/models/v1.onnx",
            artifact_format="onnx",
            deployed_at=datetime.now(timezone.utc).isoformat(),
            anomaly_threshold=0.5,
            severity_thresholds='{"low":0.5,"medium":0.75,"high":0.9}',
        )

        # Write via store directly to ensure it is persisted in the fixture's
        # in-memory instance (which the TestClient already uses via override).
        sqlite_store.write_prediction(prediction, audit_event)

        response = client.get("/internal/predictions/ROTOR-ALPHA/latest")
        assert response.status_code == 200
        body = response.json()
        assert body["asset_id"] == "ROTOR-ALPHA"
        assert body["id"] == prediction["id"]

    def test_detail_message_contains_asset_id(self, client: TestClient) -> None:
        """The 404 detail message includes the requested asset_id."""
        response = client.get("/internal/predictions/PUMP-NOT-FOUND/latest")
        assert response.status_code == 404
        assert "PUMP-NOT-FOUND" in response.json()["detail"]
