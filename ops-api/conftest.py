"""Pytest configuration for the ops-api service (TASK-026 branch).

Registers all internal modules under fully-qualified ops_api.* names
using importlib.util.spec_from_file_location so tests can import them
without adding SERVICE_ROOT to sys.path globally.

This conftest is scoped to TASK-026 (GET /health router).  Sibling-task
modules (ops-ingest, ops-models) are registered only when present so that
this branch can run in isolation.

pytest.ini at the repository root MUST include addopts = --import-mode=importlib.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
_SERVICE_ROOT = Path(__file__).parent
_APP_DIR = _SERVICE_ROOT / "app"

_OPS_INGEST_ROOT = _PROJECT_ROOT / "ops-ingest"
_OPS_MODELS_ROOT = _PROJECT_ROOT / "ops-models"

# Insert project root so shared.* is resolvable in test sessions.
_path_str = str(_PROJECT_ROOT)
if _path_str not in sys.path:
    sys.path.insert(0, _path_str)

# Insert sibling service roots so their internal imports resolve.
for _svc_root in (_OPS_INGEST_ROOT, _OPS_MODELS_ROOT):
    _svc_str = str(_svc_root)
    if _svc_str not in sys.path:
        sys.path.insert(0, _svc_str)


def _register_module(name: str, path: Path) -> None:
    """Register path in sys.modules under name if not already present."""
    if name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]


def _register_if_exists(name: str, path: Path) -> None:
    """Register path only when the file exists on disk."""
    if path.exists():
        _register_module(name, path)


# ---------------------------------------------------------------------------
# Register ops_api namespace package
# ---------------------------------------------------------------------------

if "ops_api" not in sys.modules:
    ns_pkg = types.ModuleType("ops_api")
    ns_pkg.__path__ = [str(_SERVICE_ROOT)]  # type: ignore[assignment]
    ns_pkg.__package__ = "ops_api"
    sys.modules["ops_api"] = ns_pkg

# ---------------------------------------------------------------------------
# Register ops_ingest.* -- required by ops_api.app.routers.telemetry
# Guarded: present when merged from integration branch, absent in isolation.
# ---------------------------------------------------------------------------

_OPS_INGEST_APP = _OPS_INGEST_ROOT / "app"
_OPS_INGEST_ADAPTERS = _OPS_INGEST_APP / "adapters"

if (_OPS_INGEST_ROOT / "app").exists():
    if "ops_ingest" not in sys.modules:
        ns_ingest = types.ModuleType("ops_ingest")
        ns_ingest.__path__ = [str(_OPS_INGEST_ROOT)]  # type: ignore[assignment]
        ns_ingest.__package__ = "ops_ingest"
        sys.modules["ops_ingest"] = ns_ingest

    if "ops_ingest.app" not in sys.modules:
        ns_ingest_app = types.ModuleType("ops_ingest.app")
        ns_ingest_app.__path__ = [str(_OPS_INGEST_APP)]  # type: ignore[assignment]
        ns_ingest_app.__package__ = "ops_ingest.app"
        sys.modules["ops_ingest.app"] = ns_ingest_app

    _register_if_exists("ops_ingest.app.schemas", _OPS_INGEST_APP / "schemas.py")

# ---------------------------------------------------------------------------
# Register ops_models.* -- required by ops_api.app.routers.predictions
# Guarded: present when merged, absent in isolation.
# ---------------------------------------------------------------------------

_OPS_MODELS_APP = _OPS_MODELS_ROOT / "app"

if (_OPS_MODELS_ROOT / "app").exists():
    if "ops_models" not in sys.modules:
        ns_models = types.ModuleType("ops_models")
        ns_models.__path__ = [str(_OPS_MODELS_ROOT)]  # type: ignore[assignment]
        ns_models.__package__ = "ops_models"
        sys.modules["ops_models"] = ns_models

    _register_if_exists("ops_models.app", _OPS_MODELS_APP / "__init__.py")
    _register_if_exists("ops_models.app.schemas", _OPS_MODELS_APP / "schemas.py")

# ---------------------------------------------------------------------------
# Register ops_api.* modules
# ---------------------------------------------------------------------------

_register_module("ops_api.app", _APP_DIR / "__init__.py")
_register_module("ops_api.app.middleware", _APP_DIR / "middleware" / "__init__.py")
_register_module("ops_api.app.middleware.advisory", _APP_DIR / "middleware" / "advisory.py")
_register_module("ops_api.app.middleware.tracing", _APP_DIR / "middleware" / "tracing.py")

# Routers (TASK-022) -- guarded because they depend on ops-ingest/ops-models
_ROUTERS_DIR = _APP_DIR / "routers"
_register_module("ops_api.app.routers", _ROUTERS_DIR / "__init__.py")
_register_if_exists("ops_api.app.routers.telemetry", _ROUTERS_DIR / "telemetry.py")
_register_if_exists("ops_api.app.routers.predictions", _ROUTERS_DIR / "predictions.py")

# Routers from sibling tasks -- guarded
_register_if_exists("ops_api.app.routers.explain", _ROUTERS_DIR / "explain.py")  # TASK-023
_register_if_exists("ops_api.app.routers.models", _ROUTERS_DIR / "models.py")  # TASK-025

# Router (TASK-026) -- aggregated health check; always present on this branch
_register_module("ops_api.app.routers.health", _ROUTERS_DIR / "health.py")

# WebSocket manager and stream router (TASK-024) -- guarded
_register_if_exists("ops_api.app.websocket_manager", _APP_DIR / "websocket_manager.py")
_register_if_exists("ops_api.app.routers.stream", _ROUTERS_DIR / "stream.py")

_register_if_exists("ops_api.app.main", _APP_DIR / "main.py")
