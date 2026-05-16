"""
ops-models/conftest.py
=======================
Pytest configuration for the ops-models service.

Adds the project root (D:.../OilOps-PredictiveCore) and the ops-models
service root to sys.path so that:

  - ``from shared.config import Settings`` resolves via the project root.
  - ``from ops_models.app.schemas import PredictionRequest`` resolves via
    the qualified alias registered below.

Namespace isolation fix
-----------------------
Both ops-models and ops-ingest ship a top-level ``app/`` package.  When
pytest runs both services in the same session (integration wave), the first
``sys.path.insert(0, <service_root>)`` wins and the wrong ``app`` package
is imported by the other service's tests.

To prevent this, this conftest registers an ``ops_models.app.schemas``
alias in ``sys.modules`` using ``importlib.util``.  Tests in this service
import schemas via the qualified name::

    from ops_models.app.schemas import PredictionResult

so that they remain unambiguous regardless of sys.path ordering.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_PROJECT_ROOT = Path(__file__).parent.parent
_SERVICE_ROOT = Path(__file__).parent  # ops-models/
_APP_DIR = _SERVICE_ROOT / "app"

for _path in (_PROJECT_ROOT, _SERVICE_ROOT):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

# ---------------------------------------------------------------------------
# Register ops_models.app alias so tests can use qualified imports and avoid
# namespace collisions with ops-ingest's "app" package in integrated sessions.
# ---------------------------------------------------------------------------

if "ops_models" not in sys.modules:
    ops_models_mod = ModuleType("ops_models")
    sys.modules["ops_models"] = ops_models_mod

if "ops_models.app" not in sys.modules:
    schemas_spec = importlib.util.spec_from_file_location(
        "ops_models.app.schemas", str(_APP_DIR / "schemas.py")
    )
    if schemas_spec and schemas_spec.loader:
        schemas_mod = importlib.util.module_from_spec(schemas_spec)
        sys.modules["ops_models.app.schemas"] = schemas_mod
        schemas_spec.loader.exec_module(schemas_mod)  # type: ignore[union-attr]
