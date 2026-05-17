"""
ops-ingest/conftest.py
======================
Pytest configuration for the ops-ingest service.

Adds the project root (D:.../OilOps-PredictiveCore) and the ops-ingest
service root to sys.path so that:

  - ``from shared.schemas.canonical import CanonicalReading`` resolves via the
    project root.
  - ``from app.schemas import IngestReading`` resolves via the service root.
  - ``from ops_ingest.contracts.events import IngestionCompletedEvent`` resolves
    via the project root (ops_ingest/ package).

This mirrors the convention used by ops-feature/conftest.py.

Additionally registers ``ops_ingest.app`` and sub-package aliases in
``sys.modules`` so that tests can use the fully-qualified import paths
``from ops_ingest.app.schemas import ...`` and
``from ops_ingest.app.adapters.mqtt_stub import MQTTAdapter`` etc. without
namespace collision when ops-ingest and ops-models are executed in the same
pytest session (both services expose a top-level ``app`` package).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Project root: the directory that contains shared/, ops-ingest/, ops_ingest/, etc.
_PROJECT_ROOT = Path(__file__).parent.parent
# Service root: ops-ingest/
_SERVICE_ROOT = Path(__file__).parent

for _path in (_PROJECT_ROOT, _SERVICE_ROOT):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

# ---------------------------------------------------------------------------
# Register ops_ingest.app alias to avoid "app" namespace collision when
# multiple services are run in the same pytest session (TASK-009 retry fix).
# ---------------------------------------------------------------------------
_APP_DIR = _SERVICE_ROOT / "app"
_ADAPTERS_DIR = _APP_DIR / "adapters"


def _register_module(qualified_name: str, file_path: Path) -> None:
    """Register a module in sys.modules under *qualified_name* if not present.

    Parameters
    ----------
    qualified_name:
        Dotted module name, e.g. ``"ops_ingest.app.adapters.mqtt_stub"``.
    file_path:
        Absolute path to the .py file implementing the module.
    """
    if qualified_name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(qualified_name, str(file_path))
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[qualified_name] = mod
        spec.loader.exec_module(mod)


if "ops_ingest.app" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "ops_ingest.app", str(_APP_DIR / "__init__.py")
    )
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules["ops_ingest.app"] = mod

# Register ops_ingest.app.schemas submodule
_register_module("ops_ingest.app.schemas", _APP_DIR / "schemas.py")

# Register ops_ingest.app.adapters package and its stubs (TASK-011)
_register_module("ops_ingest.app.adapters", _ADAPTERS_DIR / "__init__.py")
_register_module("ops_ingest.app.adapters.mqtt_stub", _ADAPTERS_DIR / "mqtt_stub.py")
_register_module("ops_ingest.app.adapters.kafka_stub", _ADAPTERS_DIR / "kafka_stub.py")
