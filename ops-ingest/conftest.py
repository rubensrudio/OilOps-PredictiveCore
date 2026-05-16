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

Additionally registers an ``ops_ingest.app`` alias in ``sys.modules`` so that
tests can use the fully-qualified import path
``from ops_ingest.app.schemas import ...`` without namespace collision when
ops-ingest and ops-models are executed in the same pytest session (both
services expose a top-level ``app`` package).
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

if "ops_ingest.app" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "ops_ingest.app", str(_APP_DIR / "__init__.py")
    )
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules["ops_ingest.app"] = mod
        # Register ops_ingest.app.schemas submodule
        schemas_spec = importlib.util.spec_from_file_location(
            "ops_ingest.app.schemas", str(_APP_DIR / "schemas.py")
        )
        if schemas_spec and schemas_spec.loader:
            schemas_mod = importlib.util.module_from_spec(schemas_spec)
            sys.modules["ops_ingest.app.schemas"] = schemas_mod
            schemas_spec.loader.exec_module(schemas_mod)
