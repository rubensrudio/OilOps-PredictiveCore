"""
ops-store/tests/test_storage_interface.py
==========================================
Unit tests for StorageInterface (TASK-005).

Criteria verified
-----------------
1. ``StorageInterface`` is importable from ``ops_store.app.storage_interface``.
2. Instantiating ``StorageInterface()`` directly raises ``TypeError`` (ABC with
   unimplemented abstract methods).
3. All eight abstract methods are declared on the class.
4. A fully-implemented concrete subclass can be instantiated without error.
5. A partially-implemented subclass (missing one method) still raises
   ``TypeError``.
"""

from __future__ import annotations

import inspect
import pytest

from ops_store.app.storage_interface import StorageInterface


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUIRED_ABSTRACT_METHODS = {
    "write_raw_readings",
    "get_raw_readings_by_asset",
    "write_feature_record",
    "get_feature_records_by_asset",
    "write_prediction",
    "get_latest_prediction",
    "write_audit_event",
    "get_audit_log",
}


def _make_concrete_class(exclude: str | None = None) -> type:
    """Return a concrete subclass of StorageInterface.

    When *exclude* is provided the corresponding method body is omitted so the
    resulting class remains abstract and cannot be instantiated.
    """

    methods: dict[str, object] = {}
    for name in REQUIRED_ABSTRACT_METHODS:
        if name == exclude:
            continue
        methods[name] = lambda self, *args, **kwargs: None  # noqa: ARG005

    return type("ConcreteStore", (StorageInterface,), methods)


# ---------------------------------------------------------------------------
# Tests -- importability and ABC enforcement
# ---------------------------------------------------------------------------


class TestStorageInterfaceImport:
    """StorageInterface is importable from ops_store.app.storage_interface."""

    def test_class_is_importable(self) -> None:
        assert StorageInterface is not None

    def test_class_is_abstract(self) -> None:
        """StorageInterface must be an ABC (has abstractmethods)."""
        assert getattr(StorageInterface, "__abstractmethods__", None), (
            "StorageInterface must declare at least one abstract method"
        )


class TestStorageInterfaceDirectInstantiation:
    """Instantiating StorageInterface directly raises TypeError (TASK-005 criterion)."""

    def test_direct_instantiation_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            StorageInterface()  # type: ignore[abstract]

    def test_error_message_mentions_abstract(self) -> None:
        """TypeError message should mention abstract methods or class."""
        with pytest.raises(TypeError, match=r"abstract"):
            StorageInterface()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Tests -- all eight abstract methods declared
# ---------------------------------------------------------------------------


class TestRequiredAbstractMethods:
    """All eight methods required by TASK-005 are declared as abstract."""

    @pytest.mark.parametrize("method_name", sorted(REQUIRED_ABSTRACT_METHODS))
    def test_method_is_abstract(self, method_name: str) -> None:
        method = getattr(StorageInterface, method_name, None)
        assert method is not None, (
            f"StorageInterface is missing method '{method_name}'"
        )
        assert getattr(method, "__isabstractmethod__", False), (
            f"'{method_name}' must be decorated with @abstractmethod"
        )

    def test_all_required_methods_present(self) -> None:
        abstract_methods: frozenset[str] = StorageInterface.__abstractmethods__
        missing = REQUIRED_ABSTRACT_METHODS - abstract_methods
        assert not missing, (
            f"The following required abstract methods are missing: {sorted(missing)}"
        )


# ---------------------------------------------------------------------------
# Tests -- concrete subclass behaviour
# ---------------------------------------------------------------------------


class TestConcreteSubclass:
    """A complete concrete subclass can be instantiated; partial cannot."""

    def test_full_implementation_instantiates_without_error(self) -> None:
        ConcreteStore = _make_concrete_class(exclude=None)
        instance = ConcreteStore()
        assert instance is not None

    @pytest.mark.parametrize("missing_method", sorted(REQUIRED_ABSTRACT_METHODS))
    def test_partial_implementation_raises_type_error(
        self, missing_method: str
    ) -> None:
        PartialStore = _make_concrete_class(exclude=missing_method)
        with pytest.raises(TypeError):
            PartialStore()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Tests -- method signatures contain expected parameters
# ---------------------------------------------------------------------------


class TestMethodSignatures:
    """Key parameters are present in the declared method signatures."""

    def test_get_raw_readings_by_asset_has_required_params(self) -> None:
        sig = inspect.signature(StorageInterface.get_raw_readings_by_asset)
        params = set(sig.parameters)
        assert {"asset_id", "from_ts", "to_ts"}.issubset(params)

    def test_get_feature_records_by_asset_has_required_params(self) -> None:
        sig = inspect.signature(StorageInterface.get_feature_records_by_asset)
        params = set(sig.parameters)
        assert {"asset_id", "from_ts", "to_ts"}.issubset(params)

    def test_write_prediction_has_audit_event_param(self) -> None:
        sig = inspect.signature(StorageInterface.write_prediction)
        assert "audit_event" in sig.parameters

    def test_get_audit_log_supports_pagination(self) -> None:
        sig = inspect.signature(StorageInterface.get_audit_log)
        params = set(sig.parameters)
        assert {"page", "page_size"}.issubset(params)

    def test_get_audit_log_has_optional_filters(self) -> None:
        sig = inspect.signature(StorageInterface.get_audit_log)
        params = set(sig.parameters)
        assert {"asset_id", "from_ts", "to_ts"}.issubset(params)
