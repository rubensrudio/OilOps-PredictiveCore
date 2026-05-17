"""Tests for ModelRegistry.

All tests use an in-memory SQLite database (``:memory:``) so they are
self-contained and leave no files on disk.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from ops_models.app.serving.model_registry import ModelRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register(
    registry: ModelRegistry,
    asset_class: str,
    version: str,
    artifact_format: str = "onnx",
    severity_thresholds: str = '{"low": 0.6, "medium": 0.75, "high": 0.9}',
) -> str:
    """Helper to register a model and return its model_id."""
    return registry.register_model(
        asset_class=asset_class,
        version=version,
        artifact_path=f"/models/{asset_class}-{version}.onnx",
        artifact_format=artifact_format,
        anomaly_threshold=0.5,
        severity_thresholds=severity_thresholds,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def registry() -> ModelRegistry:
    """Fresh in-memory registry for each test."""
    return ModelRegistry(db_path=":memory:")


# ---------------------------------------------------------------------------
# Tests: register_model
# ---------------------------------------------------------------------------

class TestRegisterModel:
    def test_returns_string_uuid(self, registry: ModelRegistry) -> None:
        model_id = _register(registry, "rotating_equipment", "1.0.0")
        assert isinstance(model_id, str)
        assert len(model_id) == 36  # UUID4 canonical form

    def test_new_model_is_inactive(self, registry: ModelRegistry) -> None:
        _register(registry, "rotating_equipment", "1.0.0")
        models = registry.list_models("rotating_equipment")
        assert len(models) == 1
        assert models[0]["is_active"] == 0

    def test_duplicate_version_raises(self, registry: ModelRegistry) -> None:
        _register(registry, "rotating_equipment", "1.0.0")
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            _register(registry, "rotating_equipment", "1.0.0")


# ---------------------------------------------------------------------------
# Tests: activate_version
# ---------------------------------------------------------------------------

class TestActivateVersion:
    def test_activate_sets_is_active_true(self, registry: ModelRegistry) -> None:
        """activate_version must set is_active=1 for the target version."""
        model_id = _register(registry, "rotating_equipment", "1.0.0")
        registry.activate_version(model_id)
        active = registry.get_active_model("rotating_equipment")
        assert active is not None
        assert active["model_id"] == model_id
        assert active["is_active"] == 1

    def test_activate_v2_deactivates_v1(self, registry: ModelRegistry) -> None:
        """Activating v2 must leave v1 with is_active=0 (criterion 2)."""
        v1_id = _register(registry, "rotating_equipment", "1.0.0")
        v2_id = _register(registry, "rotating_equipment", "2.0.0")

        registry.activate_version(v1_id)
        registry.activate_version(v2_id)

        all_models = registry.list_models("rotating_equipment")
        by_id = {m["model_id"]: m for m in all_models}

        assert by_id[v1_id]["is_active"] == 0
        assert by_id[v2_id]["is_active"] == 1

    def test_get_active_model_returns_v2_after_activation(
        self, registry: ModelRegistry
    ) -> None:
        """get_active_model must return v2 after activate_version(v2_id) (criterion 3)."""
        v1_id = _register(registry, "rotating_equipment", "1.0.0")
        v2_id = _register(registry, "rotating_equipment", "2.0.0")

        registry.activate_version(v1_id)
        registry.activate_version(v2_id)

        active = registry.get_active_model("rotating_equipment")
        assert active is not None
        assert active["model_id"] == v2_id
        assert active["version"] == "2.0.0"

    def test_activate_nonexistent_raises_value_error(
        self, registry: ModelRegistry
    ) -> None:
        """activate_version with unknown model_id must raise ValueError (criterion 4)."""
        with pytest.raises(ValueError, match="not found"):
            registry.activate_version("00000000-0000-0000-0000-000000000000")

    def test_rollback_on_internal_failure(self, registry: ModelRegistry) -> None:
        """activate_version must rollback on internal error, preserving prior state
        (criterion 5 — atomicity).

        We patch ``ModelRegistry._execute`` — a thin wrapper that the
        activation path uses for its UPDATE statements — so that the second
        call (``SET is_active = 1``) raises before COMMIT.  The except-block
        in ``activate_version`` must then issue ROLLBACK, leaving the prior
        active state unchanged.
        """
        v1_id = _register(registry, "rotating_equipment", "1.0.0")
        v2_id = _register(registry, "rotating_equipment", "2.0.0")

        # Start with v1 active.
        registry.activate_version(v1_id)

        # Patch _execute so the second UPDATE (SET is_active = 1) fails.
        original__execute = registry._execute

        def failing__execute(sql: str, params: tuple = ()) -> object:
            if "is_active = 1" in sql:
                raise RuntimeError("simulated DB failure")
            return original__execute(sql, params)

        with patch.object(registry, "_execute", side_effect=failing__execute):
            with pytest.raises(RuntimeError, match="simulated DB failure"):
                registry.activate_version(v2_id)

        # After rollback, v1 should still be active and v2 inactive.
        active = registry.get_active_model("rotating_equipment")
        assert active is not None
        assert active["model_id"] == v1_id

    def test_cross_asset_class_isolation(self, registry: ModelRegistry) -> None:
        """Activating a model for one asset_class must not affect another."""
        pump_id = _register(registry, "pump", "1.0.0")
        equip_id = _register(registry, "rotating_equipment", "1.0.0")

        registry.activate_version(pump_id)
        registry.activate_version(equip_id)

        assert registry.get_active_model("pump") is not None
        assert registry.get_active_model("pump")["model_id"] == pump_id
        assert registry.get_active_model("rotating_equipment")["model_id"] == equip_id


# ---------------------------------------------------------------------------
# Tests: get_active_model
# ---------------------------------------------------------------------------

class TestGetActiveModel:
    def test_returns_none_when_no_models(self, registry: ModelRegistry) -> None:
        assert registry.get_active_model("rotating_equipment") is None

    def test_returns_none_when_no_active(self, registry: ModelRegistry) -> None:
        _register(registry, "rotating_equipment", "1.0.0")
        assert registry.get_active_model("rotating_equipment") is None

    def test_returns_dict_after_activation(self, registry: ModelRegistry) -> None:
        model_id = _register(registry, "rotating_equipment", "1.0.0")
        registry.activate_version(model_id)
        result = registry.get_active_model("rotating_equipment")
        assert isinstance(result, dict)
        assert result["asset_class"] == "rotating_equipment"
        assert result["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# Tests: list_models
# ---------------------------------------------------------------------------

class TestListModels:
    def test_returns_all_models_ordered_by_deployed_at_desc(
        self, registry: ModelRegistry
    ) -> None:
        """list_models() must return all models ordered by deployed_at DESC (criterion 6)."""
        # Insert with a tiny sleep so deployed_at timestamps differ.
        _register(registry, "rotating_equipment", "1.0.0")
        time.sleep(0.01)
        _register(registry, "rotating_equipment", "2.0.0")
        time.sleep(0.01)
        _register(registry, "rotating_equipment", "3.0.0")

        models = registry.list_models("rotating_equipment")
        assert len(models) == 3
        # Most recent first.
        assert models[0]["version"] == "3.0.0"
        assert models[1]["version"] == "2.0.0"
        assert models[2]["version"] == "1.0.0"

    def test_list_all_when_asset_class_is_none(self, registry: ModelRegistry) -> None:
        _register(registry, "rotating_equipment", "1.0.0")
        _register(registry, "pump", "1.0.0")

        all_models = registry.list_models()
        assert len(all_models) == 2

    def test_filters_by_asset_class(self, registry: ModelRegistry) -> None:
        _register(registry, "rotating_equipment", "1.0.0")
        _register(registry, "pump", "1.0.0")

        pump_models = registry.list_models("pump")
        assert len(pump_models) == 1
        assert pump_models[0]["asset_class"] == "pump"

    def test_empty_list_for_unknown_asset_class(
        self, registry: ModelRegistry
    ) -> None:
        result = registry.list_models("unknown_class")
        assert result == []


# ---------------------------------------------------------------------------
# Tests: severity_thresholds field (B2)
# ---------------------------------------------------------------------------

class TestSeverityThresholds:
    def test_severity_thresholds_present_in_get_active_model(
        self, registry: ModelRegistry
    ) -> None:
        """get_active_model() must include severity_thresholds in the returned dict (B2)."""
        thresholds = '{"low": 0.6, "medium": 0.75, "high": 0.9}'
        model_id = registry.register_model(
            asset_class="rotating_equipment",
            version="1.0.0",
            artifact_path="/models/re-1.0.0.onnx",
            artifact_format="onnx",
            anomaly_threshold=0.5,
            severity_thresholds=thresholds,
        )
        registry.activate_version(model_id)
        active = registry.get_active_model("rotating_equipment")
        assert active is not None
        assert "severity_thresholds" in active
        assert active["severity_thresholds"] == thresholds

    def test_severity_thresholds_present_in_list_models(
        self, registry: ModelRegistry
    ) -> None:
        """list_models() must include severity_thresholds in every returned dict (B2)."""
        thresholds = '{"low": 0.5, "medium": 0.7, "high": 0.85}'
        _register(
            registry,
            "pump",
            "1.0.0",
            severity_thresholds=thresholds,
        )
        models = registry.list_models("pump")
        assert len(models) == 1
        assert "severity_thresholds" in models[0]
        assert models[0]["severity_thresholds"] == thresholds

    def test_severity_thresholds_default_is_empty_json_object(
        self, registry: ModelRegistry
    ) -> None:
        """severity_thresholds defaults to '{}' when not provided (B2)."""
        model_id = registry.register_model(
            asset_class="pump",
            version="2.0.0",
            artifact_path="/models/pump-2.0.0.onnx",
            artifact_format="onnx",
        )
        registry.activate_version(model_id)
        active = registry.get_active_model("pump")
        assert active is not None
        assert active["severity_thresholds"] == "{}"


# ---------------------------------------------------------------------------
# Tests: artifact_format field (B3)
# ---------------------------------------------------------------------------

class TestArtifactFormat:
    def test_artifact_format_present_in_get_active_model(
        self, registry: ModelRegistry
    ) -> None:
        """get_active_model() must include artifact_format in the returned dict (B3)."""
        model_id = registry.register_model(
            asset_class="rotating_equipment",
            version="3.0.0",
            artifact_path="/models/re-3.0.0.onnx",
            artifact_format="onnx",
        )
        registry.activate_version(model_id)
        active = registry.get_active_model("rotating_equipment")
        assert active is not None
        assert "artifact_format" in active
        assert active["artifact_format"] == "onnx"

    def test_artifact_format_present_in_list_models(
        self, registry: ModelRegistry
    ) -> None:
        """list_models() must include artifact_format in every returned dict (B3)."""
        _register(registry, "pipeline", "1.0.0", artifact_format="tensorflow_savedmodel")
        models = registry.list_models("pipeline")
        assert len(models) == 1
        assert "artifact_format" in models[0]
        assert models[0]["artifact_format"] == "tensorflow_savedmodel"

    def test_artifact_format_persisted_correctly(
        self, registry: ModelRegistry
    ) -> None:
        """artifact_format value is stored and retrieved without modification (B3)."""
        model_id = _register(
            registry, "pump", "3.0.0", artifact_format="tensorflow_savedmodel"
        )
        registry.activate_version(model_id)
        active = registry.get_active_model("pump")
        assert active is not None
        assert active["artifact_format"] == "tensorflow_savedmodel"
