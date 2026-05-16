"""
Tests for shared/schemas/canonical.py — CanonicalReading Pydantic model.

Covers the TASK-002 verification criterion:
  - Instantiates CanonicalReading with a valid payload (must succeed).
  - Instantiates CanonicalReading with a missing required field (must raise
    ValidationError).

Additional cases follow the OWASP principle of validating all boundaries and
the spec's RN-01 (schema canônico de telemetria).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_PAYLOAD: dict = {
    "id": str(uuid.uuid4()),
    "asset_id": "PUMP-001",
    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    "metric_name": "vibration_x",
    "value": 0.0023,
    "unit": "m/s2",
    "source_protocol": "rest_batch",
    "ingested_at": datetime.now(tz=timezone.utc).isoformat(),
    "ingestion_id": str(uuid.uuid4()),
}


def _make_payload(**overrides) -> dict:
    """Return a copy of the valid payload with selected key overrides."""
    payload = dict(_VALID_PAYLOAD)
    payload.update(overrides)
    return payload


def _drop_field(field: str) -> dict:
    """Return a copy of the valid payload without *field*."""
    payload = dict(_VALID_PAYLOAD)
    del payload[field]
    return payload


# ---------------------------------------------------------------------------
# Import guard — module must be importable
# ---------------------------------------------------------------------------


class TestCanonicalReadingImport:
    def test_module_importable(self):
        """CanonicalReading must be importable from shared.schemas.canonical."""
        from shared.schemas.canonical import CanonicalReading  # noqa: F401

    def test_init_importable(self):
        """CanonicalReading must also be re-exported from shared.schemas."""
        from shared.schemas import CanonicalReading  # noqa: F401


# ---------------------------------------------------------------------------
# Happy-path: valid payload
# ---------------------------------------------------------------------------


class TestCanonicalReadingValidPayload:
    """TASK-002 criterion — part 1: valid payload must succeed."""

    def test_instantiation_with_all_required_fields(self):
        from shared.schemas.canonical import CanonicalReading

        reading = CanonicalReading(**_VALID_PAYLOAD)
        assert reading is not None

    def test_id_is_uuid(self):
        from shared.schemas.canonical import CanonicalReading

        reading = CanonicalReading(**_VALID_PAYLOAD)
        assert isinstance(reading.id, uuid.UUID)

    def test_ingestion_id_is_uuid(self):
        from shared.schemas.canonical import CanonicalReading

        reading = CanonicalReading(**_VALID_PAYLOAD)
        assert isinstance(reading.ingestion_id, uuid.UUID)

    def test_is_backfill_defaults_to_false(self):
        """is_backfill must default to False when omitted (spec: default False)."""
        from shared.schemas.canonical import CanonicalReading

        reading = CanonicalReading(**_VALID_PAYLOAD)
        assert reading.is_backfill is False

    def test_is_backfill_can_be_set_true(self):
        from shared.schemas.canonical import CanonicalReading

        reading = CanonicalReading(**_make_payload(is_backfill=True))
        assert reading.is_backfill is True

    def test_value_is_float(self):
        from shared.schemas.canonical import CanonicalReading

        reading = CanonicalReading(**_VALID_PAYLOAD)
        assert isinstance(reading.value, float)

    def test_timestamp_is_datetime(self):
        from shared.schemas.canonical import CanonicalReading

        reading = CanonicalReading(**_VALID_PAYLOAD)
        assert isinstance(reading.timestamp, datetime)

    def test_ingested_at_is_datetime(self):
        from shared.schemas.canonical import CanonicalReading

        reading = CanonicalReading(**_VALID_PAYLOAD)
        assert isinstance(reading.ingested_at, datetime)

    def test_timestamp_is_utc(self):
        """Timestamps must be timezone-aware (UTC) after model instantiation."""
        from shared.schemas.canonical import CanonicalReading

        reading = CanonicalReading(**_VALID_PAYLOAD)
        assert reading.timestamp.tzinfo is not None

    def test_all_string_fields_preserved(self):
        from shared.schemas.canonical import CanonicalReading

        reading = CanonicalReading(**_VALID_PAYLOAD)
        assert reading.asset_id == "PUMP-001"
        assert reading.metric_name == "vibration_x"
        assert reading.unit == "m/s2"
        assert reading.source_protocol == "rest_batch"

    def test_uuid_objects_accepted(self):
        """Model must accept uuid.UUID objects directly, not only strings."""
        from shared.schemas.canonical import CanonicalReading

        payload = _make_payload(
            id=uuid.uuid4(),
            ingestion_id=uuid.uuid4(),
        )
        reading = CanonicalReading(**payload)
        assert isinstance(reading.id, uuid.UUID)


# ---------------------------------------------------------------------------
# Sad-path: missing required fields raise ValidationError
# ---------------------------------------------------------------------------


class TestCanonicalReadingInvalidPayload:
    """TASK-002 criterion — part 2: missing required field must raise ValidationError."""

    @pytest.mark.parametrize(
        "missing_field",
        [
            "id",
            "asset_id",
            "timestamp",
            "metric_name",
            "value",
            "unit",
            "source_protocol",
            "ingested_at",
            "ingestion_id",
        ],
    )
    def test_missing_required_field_raises_validation_error(self, missing_field: str):
        """Each required field, when absent, must raise pydantic.ValidationError."""
        from shared.schemas.canonical import CanonicalReading

        with pytest.raises(ValidationError) as exc_info:
            CanonicalReading(**_drop_field(missing_field))

        errors = exc_info.value.errors()
        field_names = [e["loc"][-1] for e in errors]
        assert missing_field in field_names, (
            f"Expected ValidationError mentioning '{missing_field}', "
            f"got fields: {field_names}"
        )

    def test_invalid_uuid_for_id_raises_validation_error(self):
        """Non-UUID string for 'id' field must raise ValidationError."""
        from shared.schemas.canonical import CanonicalReading

        with pytest.raises(ValidationError):
            CanonicalReading(**_make_payload(id="not-a-uuid"))

    def test_invalid_uuid_for_ingestion_id_raises_validation_error(self):
        """Non-UUID string for 'ingestion_id' field must raise ValidationError."""
        from shared.schemas.canonical import CanonicalReading

        with pytest.raises(ValidationError):
            CanonicalReading(**_make_payload(ingestion_id="not-a-uuid"))

    def test_non_float_value_raises_validation_error(self):
        """String value that cannot be coerced to float must raise ValidationError."""
        from shared.schemas.canonical import CanonicalReading

        with pytest.raises(ValidationError):
            CanonicalReading(**_make_payload(value="not-a-number"))

    def test_invalid_timestamp_raises_validation_error(self):
        """Non-parseable timestamp must raise ValidationError."""
        from shared.schemas.canonical import CanonicalReading

        with pytest.raises(ValidationError):
            CanonicalReading(**_make_payload(timestamp="not-a-date"))

    def test_empty_asset_id_raises_validation_error(self):
        """Empty string for asset_id must raise ValidationError (min_length=1)."""
        from shared.schemas.canonical import CanonicalReading

        with pytest.raises(ValidationError):
            CanonicalReading(**_make_payload(asset_id=""))

    def test_empty_metric_name_raises_validation_error(self):
        """Empty string for metric_name must raise ValidationError (min_length=1)."""
        from shared.schemas.canonical import CanonicalReading

        with pytest.raises(ValidationError):
            CanonicalReading(**_make_payload(metric_name=""))

    def test_empty_unit_raises_validation_error(self):
        """Empty string for unit must raise ValidationError (min_length=1)."""
        from shared.schemas.canonical import CanonicalReading

        with pytest.raises(ValidationError):
            CanonicalReading(**_make_payload(unit=""))

    def test_empty_source_protocol_raises_validation_error(self):
        """Empty string for source_protocol must raise ValidationError (min_length=1)."""
        from shared.schemas.canonical import CanonicalReading

        with pytest.raises(ValidationError):
            CanonicalReading(**_make_payload(source_protocol=""))


# ---------------------------------------------------------------------------
# Regression: AwareDatetime enforcement — RN-01 UTC contract (QA round 1)
# ---------------------------------------------------------------------------


class TestCanonicalReadingAwareDatetimeEnforcement:
    """Regression tests for the QA finding in round 1.

    ``timestamp`` and ``ingested_at`` must be declared as
    ``pydantic.AwareDatetime`` so that naive datetimes (those without tzinfo)
    are rejected with a ``ValidationError`` instead of being silently accepted.

    This enforces RN-01 (UTC contract) and the TIMESTAMPTZ column type
    documented in plan.md § 4.1.
    """

    def test_naive_datetime_raises_validation_error(self):
        """datetime(2026,5,16,10,0,0) without tzinfo must raise ValidationError.

        A naive datetime object has no timezone information and therefore
        cannot be guaranteed to be UTC — violating RN-01.
        """
        from shared.schemas.canonical import CanonicalReading

        naive_dt = datetime(2026, 5, 16, 10, 0, 0)  # no tzinfo
        assert naive_dt.tzinfo is None, "Precondition: datetime must be naive"

        with pytest.raises(ValidationError) as exc_info:
            CanonicalReading(**_make_payload(timestamp=naive_dt, ingested_at=naive_dt))

        errors = exc_info.value.errors()
        error_fields = [e["loc"][-1] for e in errors]
        assert "timestamp" in error_fields or "ingested_at" in error_fields, (
            f"Expected ValidationError on 'timestamp' or 'ingested_at', got: {error_fields}"
        )

    def test_iso_string_without_timezone_raises_validation_error(self):
        """ISO 8601 string without offset (e.g. '2026-05-16T10:00:00') must raise ValidationError.

        A string without a timezone offset (no 'Z', no '+HH:MM') represents a
        naive datetime and must be rejected to enforce the UTC contract of RN-01.
        """
        from shared.schemas.canonical import CanonicalReading

        naive_iso = "2026-05-16T10:00:00"  # no 'Z' and no '+00:00' offset

        with pytest.raises(ValidationError) as exc_info:
            CanonicalReading(**_make_payload(timestamp=naive_iso, ingested_at=naive_iso))

        errors = exc_info.value.errors()
        error_fields = [e["loc"][-1] for e in errors]
        assert "timestamp" in error_fields or "ingested_at" in error_fields, (
            f"Expected ValidationError on 'timestamp' or 'ingested_at', got: {error_fields}"
        )
