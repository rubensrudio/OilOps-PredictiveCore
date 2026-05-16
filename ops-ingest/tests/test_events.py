"""
ops-ingest/tests/test_events.py
================================
Unit tests for the domain event contracts defined in
``ops_ingest/contracts/events.py`` (TASK-004).

Coverage targets (QA: wave):
- All three event classes are importable without ImportError.
- All mandatory fields are present and validated.
- ``trace_id`` is required in every event (preparation for Kafka Phase 2 —
  DA-04 from plan.md).
- Field-level validation rejects blank/empty string identifiers.
- ``anomaly_score`` and ``confidence_score`` are bounded to [0.0, 1.0].
- UTC-aware ``occurred_at`` field is required (AwareDatetime).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ops_ingest.contracts.events import (
    FeaturesComputedEvent,
    IngestionCompletedEvent,
    PredictionEmittedEvent,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=timezone.utc)
_TRACE = "trace-abc-123"
_ASSET = "PUMP-001"
_UUID = uuid.uuid4()


# ---------------------------------------------------------------------------
# IngestionCompletedEvent
# ---------------------------------------------------------------------------

class TestIngestionCompletedEvent:
    """Validates IngestionCompletedEvent construction and field constraints."""

    def _valid_payload(self) -> dict:
        return {
            "trace_id": _TRACE,
            "ingestion_id": _UUID,
            "asset_ids": [_ASSET, "MOTOR-007"],
            "records_accepted": 10,
            "records_rejected": 2,
            "occurred_at": _NOW,
        }

    def test_import_no_error(self) -> None:
        """Import of IngestionCompletedEvent must not raise ImportError."""
        # If we reached this line the import at the top of the module succeeded.
        assert IngestionCompletedEvent is not None

    def test_valid_construction(self) -> None:
        """All mandatory fields produce a valid model instance."""
        event = IngestionCompletedEvent(**self._valid_payload())
        assert event.trace_id == _TRACE
        assert event.records_accepted == 10
        assert event.records_rejected == 2
        assert len(event.asset_ids) == 2

    def test_trace_id_required(self) -> None:
        """Omitting trace_id raises ValidationError."""
        payload = self._valid_payload()
        del payload["trace_id"]
        with pytest.raises(ValidationError):
            IngestionCompletedEvent(**payload)

    def test_trace_id_blank_rejected(self) -> None:
        """Blank trace_id (empty string) raises ValidationError (min_length=1)."""
        payload = self._valid_payload()
        payload["trace_id"] = ""
        with pytest.raises(ValidationError):
            IngestionCompletedEvent(**payload)

    def test_records_accepted_negative_rejected(self) -> None:
        """Negative records_accepted raises ValidationError (ge=0)."""
        payload = self._valid_payload()
        payload["records_accepted"] = -1
        with pytest.raises(ValidationError):
            IngestionCompletedEvent(**payload)

    def test_records_rejected_negative_rejected(self) -> None:
        """Negative records_rejected raises ValidationError (ge=0)."""
        payload = self._valid_payload()
        payload["records_rejected"] = -1
        with pytest.raises(ValidationError):
            IngestionCompletedEvent(**payload)

    def test_occurred_at_required(self) -> None:
        """Omitting occurred_at raises ValidationError."""
        payload = self._valid_payload()
        del payload["occurred_at"]
        with pytest.raises(ValidationError):
            IngestionCompletedEvent(**payload)

    def test_occurred_at_naive_datetime_rejected(self) -> None:
        """Naive datetime (no timezone) is rejected by AwareDatetime."""
        payload = self._valid_payload()
        payload["occurred_at"] = datetime(2026, 5, 16, 10, 0, 0)  # no tz
        with pytest.raises(ValidationError):
            IngestionCompletedEvent(**payload)

    def test_ingestion_id_uuid(self) -> None:
        """ingestion_id field accepts a UUID object."""
        payload = self._valid_payload()
        payload["ingestion_id"] = uuid.uuid4()
        event = IngestionCompletedEvent(**payload)
        assert isinstance(event.ingestion_id, uuid.UUID)

    def test_json_round_trip(self) -> None:
        """Event serialises to JSON and deserialises back without data loss."""
        event = IngestionCompletedEvent(**self._valid_payload())
        json_str = event.model_dump_json()
        restored = IngestionCompletedEvent.model_validate_json(json_str)
        assert restored.trace_id == event.trace_id
        assert restored.ingestion_id == event.ingestion_id
        assert restored.records_accepted == event.records_accepted


# ---------------------------------------------------------------------------
# FeaturesComputedEvent
# ---------------------------------------------------------------------------

class TestFeaturesComputedEvent:
    """Validates FeaturesComputedEvent construction and field constraints."""

    def _valid_payload(self) -> dict:
        return {
            "trace_id": _TRACE,
            "asset_id": _ASSET,
            "feature_record_id": _UUID,
            "feature_version": "1.0.0",
            "occurred_at": _NOW,
        }

    def test_import_no_error(self) -> None:
        assert FeaturesComputedEvent is not None

    def test_valid_construction(self) -> None:
        event = FeaturesComputedEvent(**self._valid_payload())
        assert event.trace_id == _TRACE
        assert event.asset_id == _ASSET
        assert event.feature_version == "1.0.0"

    def test_trace_id_required(self) -> None:
        payload = self._valid_payload()
        del payload["trace_id"]
        with pytest.raises(ValidationError):
            FeaturesComputedEvent(**payload)

    def test_asset_id_blank_rejected(self) -> None:
        payload = self._valid_payload()
        payload["asset_id"] = ""
        with pytest.raises(ValidationError):
            FeaturesComputedEvent(**payload)

    def test_feature_version_blank_rejected(self) -> None:
        payload = self._valid_payload()
        payload["feature_version"] = ""
        with pytest.raises(ValidationError):
            FeaturesComputedEvent(**payload)

    def test_feature_record_id_uuid(self) -> None:
        event = FeaturesComputedEvent(**self._valid_payload())
        assert isinstance(event.feature_record_id, uuid.UUID)

    def test_json_round_trip(self) -> None:
        event = FeaturesComputedEvent(**self._valid_payload())
        restored = FeaturesComputedEvent.model_validate_json(event.model_dump_json())
        assert restored.asset_id == event.asset_id
        assert restored.feature_record_id == event.feature_record_id


# ---------------------------------------------------------------------------
# PredictionEmittedEvent
# ---------------------------------------------------------------------------

class TestPredictionEmittedEvent:
    """Validates PredictionEmittedEvent construction and field constraints."""

    def _valid_payload(self) -> dict:
        return {
            "trace_id": _TRACE,
            "prediction_id": _UUID,
            "asset_id": _ASSET,
            "asset_class": "rotating_equipment",
            "model_version": "vibration-autoencoder-v1",
            "anomaly_score": 0.87,
            "confidence_score": 0.92,
            "alert": True,
            "occurred_at": _NOW,
        }

    def test_import_no_error(self) -> None:
        assert PredictionEmittedEvent is not None

    def test_valid_construction(self) -> None:
        event = PredictionEmittedEvent(**self._valid_payload())
        assert event.trace_id == _TRACE
        assert event.anomaly_score == pytest.approx(0.87)
        assert event.confidence_score == pytest.approx(0.92)
        assert event.alert is True

    def test_trace_id_required(self) -> None:
        payload = self._valid_payload()
        del payload["trace_id"]
        with pytest.raises(ValidationError):
            PredictionEmittedEvent(**payload)

    def test_anomaly_score_below_zero_rejected(self) -> None:
        """anomaly_score must be >= 0.0."""
        payload = self._valid_payload()
        payload["anomaly_score"] = -0.1
        with pytest.raises(ValidationError):
            PredictionEmittedEvent(**payload)

    def test_anomaly_score_above_one_rejected(self) -> None:
        """anomaly_score must be <= 1.0."""
        payload = self._valid_payload()
        payload["anomaly_score"] = 1.1
        with pytest.raises(ValidationError):
            PredictionEmittedEvent(**payload)

    def test_confidence_score_bounds(self) -> None:
        """confidence_score must be in [0.0, 1.0]."""
        payload = self._valid_payload()
        payload["confidence_score"] = 1.01
        with pytest.raises(ValidationError):
            PredictionEmittedEvent(**payload)

    def test_asset_class_blank_rejected(self) -> None:
        payload = self._valid_payload()
        payload["asset_class"] = ""
        with pytest.raises(ValidationError):
            PredictionEmittedEvent(**payload)

    def test_model_version_blank_rejected(self) -> None:
        payload = self._valid_payload()
        payload["model_version"] = ""
        with pytest.raises(ValidationError):
            PredictionEmittedEvent(**payload)

    def test_alert_false_valid(self) -> None:
        """alert=False with low scores is valid."""
        payload = self._valid_payload()
        payload["alert"] = False
        payload["anomaly_score"] = 0.2
        payload["confidence_score"] = 0.95
        event = PredictionEmittedEvent(**payload)
        assert event.alert is False

    def test_prediction_id_uuid(self) -> None:
        event = PredictionEmittedEvent(**self._valid_payload())
        assert isinstance(event.prediction_id, uuid.UUID)

    def test_json_round_trip(self) -> None:
        event = PredictionEmittedEvent(**self._valid_payload())
        restored = PredictionEmittedEvent.model_validate_json(event.model_dump_json())
        assert restored.prediction_id == event.prediction_id
        assert restored.anomaly_score == pytest.approx(event.anomaly_score)
        assert restored.alert == event.alert
