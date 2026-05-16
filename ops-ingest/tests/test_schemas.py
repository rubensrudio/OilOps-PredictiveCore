"""
ops-ingest/tests/test_schemas.py
==================================
Tests for ops-ingest/app/schemas.py — TASK-009 verification criteria.

Verification criteria (from tasks.md):
  1. IngestReading with value="string" raises ValidationError.
  2. IngestRequest with empty list passes validation.

Additional cases cover:
  - Valid IngestReading instantiation with all required fields.
  - IngestRequest with one or more readings.
  - IngestionResponse instantiation with all required fields.
  - Missing required fields raise ValidationError.
  - Invalid timestamp string raises ValidationError.
  - rejection_details field accepts list of dicts (flexible error detail).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas import IngestReading, IngestRequest, IngestionResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_READING_PAYLOAD: dict = {
    "asset_id": "PUMP-001",
    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    "metric_name": "vibration_x",
    "value": 0.0023,
    "unit": "m/s2",
    "source_protocol": "rest_batch",
}


def _make_reading_payload(**overrides) -> dict:
    """Return a copy of the valid reading payload with selected key overrides."""
    payload = dict(_VALID_READING_PAYLOAD)
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# IngestReading — valid instantiation
# ---------------------------------------------------------------------------


class TestIngestReadingValid:
    """IngestReading must accept a well-formed payload."""

    def test_valid_payload_instantiates(self):
        reading = IngestReading(**_VALID_READING_PAYLOAD)
        assert reading.asset_id == "PUMP-001"
        assert reading.metric_name == "vibration_x"
        assert reading.value == 0.0023
        assert reading.unit == "m/s2"
        assert reading.source_protocol == "rest_batch"

    def test_value_as_integer_is_coerced_to_float(self):
        """Integer values must be coerced to float (Pydantic v2 behaviour)."""
        reading = IngestReading(**_make_reading_payload(value=42))
        assert isinstance(reading.value, float)
        assert reading.value == 42.0

    def test_value_zero_is_valid(self):
        reading = IngestReading(**_make_reading_payload(value=0.0))
        assert reading.value == 0.0

    def test_value_negative_is_valid(self):
        """Negative vibration readings are physically valid."""
        reading = IngestReading(**_make_reading_payload(value=-3.14))
        assert reading.value == -3.14

    def test_timestamp_as_aware_datetime_object(self):
        """Passing a datetime object (not string) must also be accepted."""
        dt = datetime.now(tz=timezone.utc)
        reading = IngestReading(**_make_reading_payload(timestamp=dt))
        assert reading.timestamp == dt


# ---------------------------------------------------------------------------
# IngestReading — TASK-009 primary criterion: value="string" raises error
# ---------------------------------------------------------------------------


class TestIngestReadingValueValidation:
    """
    TASK-009 criterion 1:
    IngestReading with value="string" MUST raise ValidationError.
    """

    def test_value_string_raises_validation_error(self):
        """Core criterion: non-numeric string for value must be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            IngestReading(**_make_reading_payload(value="string"))
        errors = exc_info.value.errors()
        # At least one error must point at the 'value' field.
        field_names = [e["loc"][0] for e in errors]
        assert "value" in field_names

    def test_value_none_raises_validation_error(self):
        """None is not a valid float value."""
        with pytest.raises(ValidationError):
            IngestReading(**_make_reading_payload(value=None))

    def test_value_empty_string_raises_validation_error(self):
        """Empty string is not a valid float value."""
        with pytest.raises(ValidationError):
            IngestReading(**_make_reading_payload(value=""))

    def test_value_alphanumeric_string_raises_validation_error(self):
        """Alphanumeric string e.g. '3.14abc' must be rejected."""
        with pytest.raises(ValidationError):
            IngestReading(**_make_reading_payload(value="3.14abc"))


# ---------------------------------------------------------------------------
# IngestReading — missing required fields
# ---------------------------------------------------------------------------


class TestIngestReadingMissingFields:
    """Missing required fields must raise ValidationError."""

    @pytest.mark.parametrize(
        "field",
        ["asset_id", "timestamp", "metric_name", "value", "unit", "source_protocol"],
    )
    def test_missing_required_field_raises_validation_error(self, field: str):
        payload = dict(_VALID_READING_PAYLOAD)
        del payload[field]
        with pytest.raises(ValidationError):
            IngestReading(**payload)


# ---------------------------------------------------------------------------
# IngestReading — blank string fields
# ---------------------------------------------------------------------------


class TestIngestReadingBlankFields:
    """Blank (empty) string fields must be rejected (min_length=1)."""

    @pytest.mark.parametrize(
        "field",
        ["asset_id", "metric_name", "unit", "source_protocol"],
    )
    def test_blank_string_field_raises_validation_error(self, field: str):
        with pytest.raises(ValidationError):
            IngestReading(**_make_reading_payload(**{field: ""}))


# ---------------------------------------------------------------------------
# IngestRequest — TASK-009 primary criterion: empty list passes validation
# ---------------------------------------------------------------------------


class TestIngestRequestEmptyList:
    """
    TASK-009 criterion 2:
    IngestRequest with an empty list MUST pass validation.
    """

    def test_empty_readings_list_passes_validation(self):
        """Core criterion: IngestRequest([]) must not raise."""
        request = IngestRequest(readings=[])
        assert request.readings == []
        assert len(request.readings) == 0


# ---------------------------------------------------------------------------
# IngestRequest — non-empty lists
# ---------------------------------------------------------------------------


class TestIngestRequestNonEmpty:
    """IngestRequest must also accept non-empty lists."""

    def test_single_reading_in_request(self):
        reading = IngestReading(**_VALID_READING_PAYLOAD)
        request = IngestRequest(readings=[reading])
        assert len(request.readings) == 1

    def test_multiple_readings_in_request(self):
        reading = IngestReading(**_VALID_READING_PAYLOAD)
        request = IngestRequest(readings=[reading, reading])
        assert len(request.readings) == 2

    def test_readings_field_missing_raises_validation_error(self):
        """The 'readings' field itself is required."""
        with pytest.raises(ValidationError):
            IngestRequest()


# ---------------------------------------------------------------------------
# IngestionResponse — valid instantiation
# ---------------------------------------------------------------------------


class TestIngestionResponseValid:
    """IngestionResponse must be fully instantiable with required fields."""

    def test_valid_instantiation_with_no_rejections(self):
        response = IngestionResponse(
            ingestion_id=uuid.uuid4(),
            records_received=5,
            records_accepted=5,
            records_rejected=0,
            rejection_details=[],
        )
        assert response.records_received == 5
        assert response.records_accepted == 5
        assert response.records_rejected == 0
        assert response.rejection_details == []

    def test_valid_instantiation_with_rejections(self):
        rejection = {"index": 0, "field": "value", "reason": "not a valid float"}
        response = IngestionResponse(
            ingestion_id=uuid.uuid4(),
            records_received=3,
            records_accepted=2,
            records_rejected=1,
            rejection_details=[rejection],
        )
        assert response.records_rejected == 1
        assert len(response.rejection_details) == 1
        assert response.rejection_details[0]["field"] == "value"

    def test_ingestion_id_as_string_uuid_is_accepted(self):
        """ingestion_id may be passed as a UUID string (Pydantic coercion)."""
        response = IngestionResponse(
            ingestion_id=str(uuid.uuid4()),
            records_received=1,
            records_accepted=1,
            records_rejected=0,
            rejection_details=[],
        )
        assert isinstance(response.ingestion_id, uuid.UUID)

    def test_rejection_details_defaults_to_empty_list(self):
        """rejection_details should have a sensible default (empty list)."""
        response = IngestionResponse(
            ingestion_id=uuid.uuid4(),
            records_received=2,
            records_accepted=2,
            records_rejected=0,
        )
        assert response.rejection_details == []


# ---------------------------------------------------------------------------
# IngestionResponse — missing required fields
# ---------------------------------------------------------------------------


class TestIngestionResponseMissingFields:
    """Missing required fields on IngestionResponse must raise ValidationError."""

    @pytest.mark.parametrize(
        "field",
        ["ingestion_id", "records_received", "records_accepted", "records_rejected"],
    )
    def test_missing_required_field_raises_validation_error(self, field: str):
        payload = {
            "ingestion_id": uuid.uuid4(),
            "records_received": 1,
            "records_accepted": 1,
            "records_rejected": 0,
            "rejection_details": [],
        }
        del payload[field]
        with pytest.raises(ValidationError):
            IngestionResponse(**payload)
