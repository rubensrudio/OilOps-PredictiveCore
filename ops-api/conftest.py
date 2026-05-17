"""Pytest configuration for the ops-api service.

Registers all internal modules under fully-qualified ``ops_api.*`` names
using ``importlib.util.spec_from_file_location`` so that tests can import
them without adding SERVICE_ROOT to ``sys.path`` globally (which would cause
namespace collision with the plain ``app`` package name used by other services
in this monorepo).

Also registers sibling service modules (``ops_ingest.*``, ``ops_models.*``)
required by the TASK-022 routers:
  - ``ops_api.app.routers.telemetry`` imports from ``ops_ingest.app.schemas``
  - ``ops_api.app.routers.predictions`` imports from ``ops_models.app.schemas``

pytest.ini at the repository root MUST include ``addopts = --import-mode=importlib``.
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

# Insert project root so ``shared.*`` is resolvable in test sessions.
_path_str = str(_PROJECT_ROOT)
if _path_str not in sys.path:
    sys.path.insert(0, _path_str)

# Insert ops-ingest service root so ``app.*`` imports inside ops-ingest modules
# resolve correctly when executed via _register_module.
for _svc_root in (_OPS_INGEST_ROOT, _OPS_MODELS_ROOT):
    _svc_str = str(_svc_root)
    if _svc_str not in sys.path:
        sys.path.insert(0, _svc_str)


def _register_module(name: str, path: Path) -> None:
    """Register *path* in ``sys.modules`` under *name* if not already present."""
    if name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Register ``ops_api`` namespace package
# ---------------------------------------------------------------------------

if "ops_api" not in sys.modules:
    ns_pkg = types.ModuleType("ops_api")
    ns_pkg.__path__ = [str(_SERVICE_ROOT)]  # type: ignore[assignment]
    ns_pkg.__package__ = "ops_api"
    sys.modules["ops_api"] = ns_pkg

# ---------------------------------------------------------------------------
# Register ``ops_ingest.*`` FIRST — required by ops_api.app.routers.telemetry
# (must be registered before the router module is exec'd by _register_module)
# ---------------------------------------------------------------------------

_OPS_INGEST_APP = _OPS_INGEST_ROOT / "app"
_OPS_INGEST_ADAPTERS = _OPS_INGEST_APP / "adapters"

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

_register_module("ops_ingest.app.schemas", _OPS_INGEST_APP / "schemas.py")
# Note: ops_ingest.app.normalizer and rest_batch are NOT registered here because
# they carry transitive imports via ``from app.schemas import ...`` which resolve
# ambiguously when multiple service roots are on sys.path.  Only the schemas module
# is required by the ops_api routers (telemetry.py imports IngestRequest / IngestionResponse).

# ---------------------------------------------------------------------------
# Register ``ops_models.*`` FIRST — required by ops_api.app.routers.predictions
# (must be registered before the router module is exec'd by _register_module)
# ---------------------------------------------------------------------------

_OPS_MODELS_APP = _OPS_MODELS_ROOT / "app"

if "ops_models" not in sys.modules:
    ns_models = types.ModuleType("ops_models")
    ns_models.__path__ = [str(_OPS_MODELS_ROOT)]  # type: ignore[assignment]
    ns_models.__package__ = "ops_models"
    sys.modules["ops_models"] = ns_models

_register_module("ops_models.app", _OPS_MODELS_APP / "__init__.py")
_register_module("ops_models.app.schemas", _OPS_MODELS_APP / "schemas.py")

# ---------------------------------------------------------------------------
# Register ``ops_api.*`` modules
# ---------------------------------------------------------------------------

_register_module("ops_api.app", _APP_DIR / "__init__.py")
_register_module("ops_api.app.middleware", _APP_DIR / "middleware" / "__init__.py")
_register_module("ops_api.app.middleware.advisory", _APP_DIR / "middleware" / "advisory.py")
_register_module("ops_api.app.middleware.tracing", _APP_DIR / "middleware" / "tracing.py")

# Routers (TASK-022)
_ROUTERS_DIR = _APP_DIR / "routers"
_register_module("ops_api.app.routers", _ROUTERS_DIR / "__init__.py")
_register_module("ops_api.app.routers.telemetry", _ROUTERS_DIR / "telemetry.py")
_register_module("ops_api.app.routers.predictions", _ROUTERS_DIR / "predictions.py")

# Router (TASK-023) — explain router; no external schema imports needed
_register_module("ops_api.app.routers.explain", _ROUTERS_DIR / "explain.py")

# WebSocket manager and stream router (TASK-024)
_register_module("ops_api.app.websocket_manager", _APP_DIR / "websocket_manager.py")
_register_module("ops_api.app.routers.stream", _ROUTERS_DIR / "stream.py")

_register_module("ops_api.app.main", _APP_DIR / "main.py")
