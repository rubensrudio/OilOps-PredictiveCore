"""
ops-feature/conftest.py
========================
Pytest configuration for the ops-feature service (updated TASK-014).

Path registration strategy
---------------------------
This conftest adds the project root to sys.path so that
from shared.config import Settings and similar cross-service imports
resolve correctly.

Additionally it registers ops_feature.* module aliases via
importlib.util.spec_from_file_location so that tests can use the
canonical qualified form:

    from ops_feature.app.windowing import WindowingPipeline
    from ops_feature.app.extractors.vibration import VibrationFeatureExtractor

IMPORTANT: sys.modules[app] isolation
-----------------------------------------
The service root (ops-feature/) is still added to sys.path to maintain
backward compatibility with existing TASK-013 tests (test_vibration.py) that
use from app.extractors.vibration import VibrationFeatureExtractor.

ops-store registration
-----------------------
Tests that exercise the WindowingPipeline need access to
ops_store.app.db.duckdb_store.DuckDBStore. This conftest mirrors the
ops-store conftest registration of ops_store.* aliases.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_PROJECT_ROOT = Path(__file__).parent.parent
_SERVICE_ROOT = Path(__file__).parent
_APP_DIR = _SERVICE_ROOT / "app"
_EXTRACTORS_DIR = _APP_DIR / "extractors"

_OPS_STORE_ROOT = _PROJECT_ROOT / "ops-store"
_OPS_STORE_APP_DIR = _OPS_STORE_ROOT / "app"
_OPS_STORE_DB_DIR = _OPS_STORE_APP_DIR / "db"

for _path in (_PROJECT_ROOT, _SERVICE_ROOT):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)


def _register_namespace(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    mod = ModuleType(name)
    mod.__path__ = [str(path)]
    mod.__package__ = name
    sys.modules[name] = mod


def _register_module(name: str, file_path: Path, parent: str | None = None) -> None:
    if name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(name, str(file_path))
    if spec is None or spec.loader is None:
        return
    mod = importlib.util.module_from_spec(spec)
    if parent and parent in sys.modules:
        setattr(sys.modules[parent], name.rsplit(".", 1)[-1], mod)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)


# Register ops_store.* FIRST because windowing.py depends on StorageInterface
_register_namespace("ops_store", _OPS_STORE_ROOT)
_register_namespace("ops_store.app", _OPS_STORE_APP_DIR)

_register_module("ops_store.app.storage_interface", _OPS_STORE_APP_DIR / "storage_interface.py", parent="ops_store.app")

_register_namespace("ops_store.app.db", _OPS_STORE_DB_DIR)

_register_module("ops_store.app.db.duckdb_store", _OPS_STORE_DB_DIR / "duckdb_store.py", parent="ops_store.app.db")

# Register ops_feature.* AFTER ops_store.* (windowing.py imports StorageInterface)
_register_namespace("ops_feature", _SERVICE_ROOT)
_register_namespace("ops_feature.app", _APP_DIR)
_register_namespace("ops_feature.app.extractors", _EXTRACTORS_DIR)

_register_module("ops_feature.app.schemas", _APP_DIR / "schemas.py", parent="ops_feature.app")
_register_module("ops_feature.app.extractors.vibration", _EXTRACTORS_DIR / "vibration.py", parent="ops_feature.app.extractors")
_register_module("ops_feature.app.windowing", _APP_DIR / "windowing.py", parent="ops_feature.app")
