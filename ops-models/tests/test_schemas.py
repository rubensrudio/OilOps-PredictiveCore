"""
ops-models/tests/test_schemas.py
==================================
Unit tests for ops-models Pydantic schemas (TASK-017).

Coverage
--------
- PredictionRequest: valid instantiation, required fields.
- PredictionResult: valid instantiation; severity enum constraint
  (only low | medium | high | None); anomaly_score and confidence_score
  float range validators [0.0, 1.0].
- ModelDeployRequest: valid instantiation.
- ModelDeployResponse: valid instantiation.

Verification criteria (from tasks.md TASK-017)
-----------------------------------------------
- Schemas importable and instantiable with valid data.
- ``severity`` accepts only ``low | medium | high | None``.
- ``anomaly_score`` and ``confidence_score`` are float in [0.0, 1.0] (validators).

Namespace isolation note
------------------------
Imports use the qualified ``ops_models.app.schemas`` path registered by
conftest.py to avoid collisions with ops-ingest's ``app`` package when both
services are tested in the same pytest session (integration wave QA fix).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ops_models.app.schemas import (
    ModelDeployRequest,
    ModelDeployResponse,
    PredictionRequest,
    PredictionResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=timezone.utc)
_UUID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# PredictionRequest
# ---------------------------------------------------------------------------


class TestPredictionRequest:
    def test_valid_instantiation(self) -> None:
        req = PredictionRequest(
            feature_record_id=_UUID,
            asset_id="PUMP-001",
            asset_class="rotating_equipment",
        )
        assert req.asset_id == "PUMP-001"
        assert req.asset_class == "rotating_equipment"
        assert req.feature_record_id == _UUID

    def test_missing_feature_record_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            PredictionRequest(  # type: ignore[call-arg]
                asset_id="PUMP-001",
                asset_class="rotating_equipment",
            )

    def test_missing_asset_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            PredictionRequest(  # type: ignore[call-arg]
                feature_record_id=_UUID,
                asset_class="rotating_equipment",
            )

    def test_missing_asset_class_raises(self) -> None:
        with pytest.raises(ValidationError):
            PredictionRequest(  # type: ignore[call-arg]
                feature_record_id=_UUID,
                asset_id="PUMP-001",
            )


# ---------------------------------------------------------------------------
# PredictionResult
# ---------------------------------------------------------------------------


class TestPredictionResult:
    def _valid_payload(self, **overrides: object) -> dict:  # type: ignore[return]
        base: dict = {
            "prediction_id": _UUID,
            "asset_id": "PUMP-001",
            "asset_class": "rotating_equipment",
            "anomaly_score": 0.75,
            "confidence_score": 0.90,
            "alert": True,
            "severity": "high",
            "predicted_at": _NOW,
            "model_version": "vibration-autoencoder-v1",
            "model_id": "rotating_equipment-v1.0.0",
            "feature_record_id": str(uuid.uuid4()),
            "explain_status": "pending",
        }
        base.update(overrides)
        return base

    def test_valid_instantiation(self) -> None:
        result = PredictionResult(**self._valid_payload())
        assert result.alert is True
        assert result.severity == "high"

    def test_severity_none_is_valid(self) -> None:
        result = PredictionResult(**self._valid_payload(severity=None, alert=False))
        assert result.severity is None

    def test_severity_low_is_valid(self) -> None:
        result = PredictionResult(**self._valid_payload(severity="low"))
        assert result.severity == "low"

    def test_severity_medium_is_valid(self) -> None:
        result = PredictionResult(**self._valid_payload(severity="medium"))
        assert result.severity == "medium"

    def test_severity_invalid_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PredictionResult(**self._valid_payload(severity="critical"))
        # Confirm the error references the severity field
        errors = exc_info.value.errors()
        assert any("severity" in str(e) for e in errors)

    def test_anomaly_score_at_zero_is_valid(self) -> None:
        result = PredictionResult(**self._valid_payload(anomaly_score=0.0))
        assert result.anomaly_score == 0.0

    def test_anomaly_score_at_one_is_valid(self) -> None:
        result = PredictionResult(**self._valid_payload(anomaly_score=1.0))
        assert result.anomaly_score == 1.0

    def test_anomaly_score_below_zero_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PredictionResult(**self._valid_payload(anomaly_score=-0.01))
        errors = exc_info.value.errors()
        assert any("anomaly_score" in str(e) for e in errors)

    def test_anomaly_score_above_one_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PredictionResult(**self._valid_payload(anomaly_score=1.01))
        errors = exc_info.value.errors()
        assert any("anomaly_score" in str(e) for e in errors)

    def test_confidence_score_at_zero_is_valid(self) -> None:
        result = PredictionResult(**self._valid_payload(confidence_score=0.0))
        assert result.confidence_score == 0.0

    def test_confidence_score_at_one_is_valid(self) -> None:
        result = PredictionResult(**self._valid_payload(confidence_score=1.0))
        assert result.confidence_score == 1.0

    def test_confidence_score_below_zero_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PredictionResult(**self._valid_payload(confidence_score=-0.01))
        errors = exc_info.value.errors()
        assert any("confidence_score" in str(e) for e in errors)

    def test_confidence_score_above_one_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PredictionResult(**self._valid_payload(confidence_score=1.001))
        errors = exc_info.value.errors()
        assert any("confidence_score" in str(e) for e in errors)

    def test_explain_status_default_is_pending(self) -> None:
        """explain_status defaults to 'pending' when not provided."""
        payload = self._valid_payload()
        payload.pop("explain_status")
        result = PredictionResult(**payload)
        assert result.explain_status == "pending"

    def test_missing_required_field_raises(self) -> None:
        payload = self._valid_payload()
        payload.pop("prediction_id")
        with pytest.raises(ValidationError):
            PredictionResult(**payload)


# ---------------------------------------------------------------------------
# ModelDeployRequest
# ---------------------------------------------------------------------------


class TestModelDeployRequest:
    def test_valid_instantiation(self) -> None:
        req = ModelDeployRequest(
            asset_class="rotating_equipment",
            version="2.0.0",
            anomaly_threshold=0.6,
            severity_thresholds={"low": 0.6, "medium": 0.75, "high": 0.9},
        )
        assert req.version == "2.0.0"
        assert req.anomaly_threshold == 0.6

    def test_missing_asset_class_raises(self) -> None:
        with pytest.raises(ValidationError):
            ModelDeployRequest(  # type: ignore[call-arg]
                version="1.0.0",
                anomaly_threshold=0.5,
                severity_thresholds={"low": 0.5, "medium": 0.7, "high": 0.9},
            )

    def test_missing_version_raises(self) -> None:
        with pytest.raises(ValidationError):
            ModelDeployRequest(  # type: ignore[call-arg]
                asset_class="rotating_equipment",
                anomaly_threshold=0.5,
                severity_thresholds={"low": 0.5, "medium": 0.7, "high": 0.9},
            )


# ---------------------------------------------------------------------------
# ModelDeployResponse
# ---------------------------------------------------------------------------


class TestModelDeployResponse:
    def test_valid_instantiation(self) -> None:
        resp = ModelDeployResponse(
            model_id="vibration-autoencoder-v2.0.0",
            version="2.0.0",
            asset_class="rotating_equipment",
            deployed_at=_NOW,
            is_active=True,
        )
        assert resp.is_active is True
        assert resp.model_id == "vibration-autoencoder-v2.0.0"

    def test_is_active_defaults_to_true(self) -> None:
        resp = ModelDeployResponse(
            model_id="vibration-autoencoder-v1",
            version="1.0.0",
            asset_class="rotating_equipment",
            deployed_at=_NOW,
        )
        assert resp.is_active is True

    def test_missing_model_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            ModelDeployResponse(  # type: ignore[call-arg]
                version="1.0.0",
                asset_class="rotating_equipment",
                deployed_at=_NOW,
            )
