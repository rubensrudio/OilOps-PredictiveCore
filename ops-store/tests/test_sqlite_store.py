"""
ops-store/tests/test_sqlite_store.py
======================================
Unit tests for SQLiteStore (TASK-007).

All tests use an in-memory SQLite database (:memory:) so they are fully
isolated — no files on disk, no shared state between test functions.

Criteria verified (from tasks.md TASK-007)
------------------------------------------
1. write_prediction + write_audit_event in a single transaction:
   after commit both rows are present in predictions and audit_log.
2. Atomicity / rollback guarantee (RN-03):
   simulating a failure in the audit_event write causes the entire
   transaction to roll back — the prediction row is NOT persisted.
3. get_latest_prediction for an asset with no predictions returns None
   (not an exception).
4. write_audit_event standalone persists a row and returns the event id.
5. get_audit_log returns the expected pagination envelope.
6. Mapping: prediction dict may carry "prediction_id" OR "id" — both are
   accepted and stored under the "id" column.
7. write_raw_readings raises NotImplementedError (DuckDB responsibility).
8. get_raw_readings_by_asset raises NotImplementedError.
9. write_feature_record raises NotImplementedError.
10. get_feature_records_by_asset raises NotImplementedError.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from ops_store.app.db.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_prediction(*, asset_id: str = "PUMP-001", use_prediction_id: bool = False) -> dict:
    """Return a minimal valid prediction dict."""
    pred_id = str(uuid.uuid4())
    key = "prediction_id" if use_prediction_id else "id"
    return {
        key: pred_id,
        "asset_id": asset_id,
        "asset_class": "rotating_equipment",
        "anomaly_score": 0.75,
        "confidence_score": 0.90,
        "alert": 1,
        "severity": "high",
        "model_id": "vibration-autoencoder-v1",
        "model_version": "1.0.0",
        "feature_record_id": str(uuid.uuid4()),
        "predicted_at": _now_iso(),
        "explain_status": "pending",
    }


def _make_audit_event(*, prediction_id: str, asset_id: str = "PUMP-001") -> dict:
    """Return a minimal valid audit_log dict."""
    return {
        "id": str(uuid.uuid4()),
        "event_type": "prediction_emitted",
        "prediction_id": prediction_id,
        "asset_id": asset_id,
        "model_version": "1.0.0",
        "triggered_at": _now_iso(),
        "confidence_score": 0.90,
        "trace_id": str(uuid.uuid4()),
        "details": json.dumps({"source": "test"}),
    }


def _make_standalone_audit(event_type: str = "model_deployed") -> dict:
    """Return an audit event not tied to a prediction."""
    return {
        "id": str(uuid.uuid4()),
        "event_type": event_type,
        "prediction_id": None,
        "asset_id": None,
        "model_version": "1.0.0",
        "triggered_at": _now_iso(),
        "confidence_score": None,
        "trace_id": None,
        "details": None,
    }


@pytest.fixture()
def store() -> SQLiteStore:
    """Provide an in-memory SQLiteStore with schema initialised."""
    return SQLiteStore(db_path=":memory:")


# ---------------------------------------------------------------------------
# Prerequisite: asset must exist for predictions (FK). Helper to register one.
# ---------------------------------------------------------------------------

def _register_asset(store: SQLiteStore, asset_id: str = "PUMP-001") -> None:
    """Insert a minimal asset row to satisfy FK constraint in predictions."""
    store.register_asset(
        asset_id=asset_id,
        asset_class="rotating_equipment",
        registered_at=_now_iso(),
        metadata=None,
    )


def _register_model(store: SQLiteStore, model_id: str = "vibration-autoencoder-v1") -> None:
    """Insert a minimal model_versions row to satisfy FK constraint."""
    store.register_model_version(
        model_id=model_id,
        version="1.0.0",
        asset_class="rotating_equipment",
        artifact_path="/models/v1.onnx",
        artifact_format="onnx",
        deployed_at=_now_iso(),
        anomaly_threshold=0.5,
        severity_thresholds=json.dumps({"low": 0.5, "medium": 0.75, "high": 0.9}),
    )


# ---------------------------------------------------------------------------
# Test 1: write_prediction inserts both prediction and audit_event
# ---------------------------------------------------------------------------

class TestWritePredictionAtomicity:
    """Verify the happy path: both rows land in the DB after write_prediction."""

    def test_both_rows_present_after_commit(self, store: SQLiteStore) -> None:
        _register_asset(store)
        _register_model(store)

        prediction = _make_prediction()
        audit_event = _make_audit_event(prediction_id=prediction["id"])

        returned_id = store.write_prediction(prediction, audit_event)

        assert returned_id == prediction["id"]

        # Verify prediction row
        with store._connect() as conn:
            row = conn.execute(
                "SELECT id, asset_id, anomaly_score FROM predictions WHERE id = ?",
                (prediction["id"],),
            ).fetchone()
        assert row is not None
        assert row["id"] == prediction["id"]
        assert row["asset_id"] == "PUMP-001"
        assert abs(row["anomaly_score"] - 0.75) < 1e-9

        # Verify audit_log row
        with store._connect() as conn:
            audit_row = conn.execute(
                "SELECT id, event_type, prediction_id FROM audit_log WHERE id = ?",
                (audit_event["id"],),
            ).fetchone()
        assert audit_row is not None
        assert audit_row["event_type"] == "prediction_emitted"
        assert audit_row["prediction_id"] == prediction["id"]

    def test_returns_prediction_id_string(self, store: SQLiteStore) -> None:
        _register_asset(store)
        _register_model(store)

        prediction = _make_prediction()
        audit_event = _make_audit_event(prediction_id=prediction["id"])

        result = store.write_prediction(prediction, audit_event)
        assert isinstance(result, str)
        assert result == prediction["id"]


# ---------------------------------------------------------------------------
# Test 2: prediction dict with "prediction_id" key (not "id") is accepted
# ---------------------------------------------------------------------------

class TestWritePredictionKeyMapping:
    """Verify that both "id" and "prediction_id" dict keys are accepted."""

    def test_prediction_id_key_accepted(self, store: SQLiteStore) -> None:
        _register_asset(store)
        _register_model(store)

        prediction = _make_prediction(use_prediction_id=True)
        # The dict has "prediction_id" not "id"
        assert "prediction_id" in prediction
        assert "id" not in prediction

        pred_id = prediction["prediction_id"]
        audit_event = _make_audit_event(prediction_id=pred_id)
        audit_event["prediction_id"] = pred_id

        returned_id = store.write_prediction(prediction, audit_event)
        assert returned_id == pred_id

        with store._connect() as conn:
            row = conn.execute(
                "SELECT id FROM predictions WHERE id = ?", (pred_id,)
            ).fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# Test 3: rollback when audit_event write fails
# ---------------------------------------------------------------------------

class TestWritePredictionRollback:
    """Verify RN-03: if audit_event insert fails, prediction is NOT persisted."""

    def test_prediction_absent_after_audit_failure(self, store: SQLiteStore) -> None:
        _register_asset(store)
        _register_model(store)

        prediction = _make_prediction()
        # Provide a malformed audit_event that will cause a constraint violation.
        # event_type is NOT NULL so passing None should trigger SQLITE constraint.
        bad_audit = {
            "id": str(uuid.uuid4()),
            "event_type": None,      # NOT NULL violation — will cause INSERT failure
            "prediction_id": prediction["id"],
            "asset_id": "PUMP-001",
            "model_version": None,
            "triggered_at": _now_iso(),
            "confidence_score": None,
            "trace_id": None,
            "details": None,
        }

        with pytest.raises(Exception):
            store.write_prediction(prediction, bad_audit)

        # After the rollback the prediction row must NOT exist
        with store._connect() as conn:
            row = conn.execute(
                "SELECT id FROM predictions WHERE id = ?",
                (prediction["id"],),
            ).fetchone()
        assert row is None, "prediction must NOT be persisted after audit_event failure"

    def test_audit_absent_after_audit_failure(self, store: SQLiteStore) -> None:
        """Confirm the audit row is also absent (not half-committed)."""
        _register_asset(store)
        _register_model(store)

        prediction = _make_prediction()
        bad_audit_id = str(uuid.uuid4())
        bad_audit = {
            "id": bad_audit_id,
            "event_type": None,  # triggers constraint failure
            "prediction_id": prediction["id"],
            "asset_id": "PUMP-001",
            "model_version": None,
            "triggered_at": _now_iso(),
            "confidence_score": None,
            "trace_id": None,
            "details": None,
        }

        with pytest.raises(Exception):
            store.write_prediction(prediction, bad_audit)

        with store._connect() as conn:
            audit_row = conn.execute(
                "SELECT id FROM audit_log WHERE id = ?", (bad_audit_id,)
            ).fetchone()
        assert audit_row is None


# ---------------------------------------------------------------------------
# Test 4: get_latest_prediction returns None for asset with no predictions
# ---------------------------------------------------------------------------

class TestGetLatestPrediction:
    """get_latest_prediction must return None, never raise, when no rows exist."""

    def test_returns_none_for_unknown_asset(self, store: SQLiteStore) -> None:
        result = store.get_latest_prediction("NONEXISTENT-ASSET-9999")
        assert result is None

    def test_returns_most_recent_prediction(self, store: SQLiteStore) -> None:
        _register_asset(store)
        _register_model(store)

        # Insert two predictions with different timestamps
        older = _make_prediction()
        older["predicted_at"] = "2026-01-01T00:00:00+00:00"
        store.write_prediction(older, _make_audit_event(prediction_id=older["id"]))

        newer = _make_prediction()
        newer["predicted_at"] = "2026-06-01T00:00:00+00:00"
        store.write_prediction(newer, _make_audit_event(prediction_id=newer["id"]))

        result = store.get_latest_prediction("PUMP-001")
        assert result is not None
        assert result["id"] == newer["id"]

    def test_returns_dict_with_expected_keys(self, store: SQLiteStore) -> None:
        _register_asset(store)
        _register_model(store)

        prediction = _make_prediction()
        store.write_prediction(prediction, _make_audit_event(prediction_id=prediction["id"]))

        result = store.get_latest_prediction("PUMP-001")
        assert result is not None
        for key in ("id", "asset_id", "anomaly_score", "confidence_score",
                    "alert", "explain_status", "predicted_at"):
            assert key in result, f"Expected key '{key}' missing from result"


# ---------------------------------------------------------------------------
# Test 5: write_audit_event standalone
# ---------------------------------------------------------------------------

class TestWriteAuditEvent:
    """write_audit_event used outside a prediction transaction."""

    def test_persists_row_and_returns_id(self, store: SQLiteStore) -> None:
        audit = _make_standalone_audit("model_deployed")
        returned_id = store.write_audit_event(audit)

        assert returned_id == audit["id"]

        with store._connect() as conn:
            row = conn.execute(
                "SELECT id, event_type FROM audit_log WHERE id = ?",
                (audit["id"],),
            ).fetchone()
        assert row is not None
        assert row["event_type"] == "model_deployed"

    def test_returns_string(self, store: SQLiteStore) -> None:
        audit = _make_standalone_audit()
        result = store.write_audit_event(audit)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Test 6: get_audit_log pagination and filtering
# ---------------------------------------------------------------------------

class TestGetAuditLog:
    """Verify get_audit_log returns the expected pagination envelope."""

    def _insert_audit_events(self, store: SQLiteStore, count: int) -> list[str]:
        ids: list[str] = []
        for i in range(count):
            ev = _make_standalone_audit("model_deployed")
            ev["asset_id"] = "PUMP-001"
            store.write_audit_event(ev)
            ids.append(ev["id"])
        return ids

    def test_returns_pagination_envelope(self, store: SQLiteStore) -> None:
        result = store.get_audit_log()
        assert "events" in result
        assert "total" in result
        assert "page" in result
        assert "page_size" in result

    def test_empty_log_returns_zero_total(self, store: SQLiteStore) -> None:
        result = store.get_audit_log()
        assert result["total"] == 0
        assert result["events"] == []

    def test_total_reflects_inserted_rows(self, store: SQLiteStore) -> None:
        self._insert_audit_events(store, 5)
        result = store.get_audit_log()
        assert result["total"] == 5
        assert len(result["events"]) == 5

    def test_page_size_limits_events(self, store: SQLiteStore) -> None:
        self._insert_audit_events(store, 10)
        result = store.get_audit_log(page=1, page_size=3)
        assert result["page_size"] == 3
        assert len(result["events"]) == 3
        assert result["total"] == 10

    def test_asset_id_filter(self, store: SQLiteStore) -> None:
        # Insert 3 events for PUMP-001, 2 for MOTOR-002
        for _ in range(3):
            ev = _make_standalone_audit()
            ev["asset_id"] = "PUMP-001"
            store.write_audit_event(ev)
        for _ in range(2):
            ev = _make_standalone_audit()
            ev["asset_id"] = "MOTOR-002"
            store.write_audit_event(ev)

        result = store.get_audit_log(asset_id="PUMP-001")
        assert result["total"] == 3
        for ev in result["events"]:
            assert ev["asset_id"] == "PUMP-001"

    def test_from_ts_filter(self, store: SQLiteStore) -> None:
        """Events before from_ts should be excluded."""
        past_ev = _make_standalone_audit()
        past_ev["triggered_at"] = "2025-01-01T00:00:00+00:00"
        store.write_audit_event(past_ev)

        future_ev = _make_standalone_audit()
        future_ev["triggered_at"] = "2027-01-01T00:00:00+00:00"
        store.write_audit_event(future_ev)

        from_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = store.get_audit_log(from_ts=from_ts)
        assert result["total"] == 1
        assert result["events"][0]["id"] == future_ev["id"]

    def test_page_and_page_number_reflected(self, store: SQLiteStore) -> None:
        result = store.get_audit_log(page=3, page_size=20)
        assert result["page"] == 3
        assert result["page_size"] == 20


# ---------------------------------------------------------------------------
# Test 7-10: NotImplementedError for DuckDB-owned methods
# ---------------------------------------------------------------------------

class TestNotImplementedMethods:
    """DuckDB-owned methods must raise NotImplementedError on SQLiteStore."""

    def test_write_raw_readings_raises(self, store: SQLiteStore) -> None:
        with pytest.raises(NotImplementedError):
            store.write_raw_readings([])

    def test_get_raw_readings_by_asset_raises(self, store: SQLiteStore) -> None:
        from_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        to_ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with pytest.raises(NotImplementedError):
            store.get_raw_readings_by_asset("PUMP-001", from_ts, to_ts)

    def test_write_feature_record_raises(self, store: SQLiteStore) -> None:
        with pytest.raises(NotImplementedError):
            store.write_feature_record({})

    def test_get_feature_records_by_asset_raises(self, store: SQLiteStore) -> None:
        from_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        to_ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with pytest.raises(NotImplementedError):
            store.get_feature_records_by_asset("PUMP-001", from_ts, to_ts)
